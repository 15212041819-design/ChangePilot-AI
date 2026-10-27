"""证据驱动 Impact 评测适配器测试。"""

import json
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.schemas.evaluation import (
    EvaluationDataset,
    EvaluationSample,
)
from app.tools.evaluation_requirements import (
    create_recording_requirement_store,
    load_requirement_artifact,
    write_requirement_artifact,
)
from app.tools.evaluation_runner import EvaluationCaseContext
from app.tools.evidence_evaluation_adapter import (
    EvidenceGroundedImpactEvaluationAdapter,
    EvidenceImpactAdapterConfig,
    create_evidence_impact_adapter,
)
from app.tools.git_cochange import CoChangeIndex

ZERO_HISTORY_DIGEST = "sha256:" + "0" * 64


def _model_response() -> str:
    """返回能命中真实 cancel_order 方法的需求结果。"""

    return """
    {
        "summary": "订单所属用户可以取消待支付订单。",
        "change_type": "feature",
        "acceptance_criteria": [
            {
                "description": "订单所属用户可以取消待支付订单。",
                "verification": "test",
                "priority": "P0"
            }
        ],
        "entity_hints": [
            {
                "concept": "取消订单",
                "lexical_variants": [
                    "cancel order"
                ],
                "symbol_candidates": [
                    "cancel_order"
                ],
                "role": "action_target"
            }
        ],
        "constraints": [],
        "assumptions": []
    }
    """


def _model_response_with_assumption() -> str:
    """返回包含未确认假设的需求结果，模拟真实历史提交评测。"""

    return _model_response().replace(
        '"assumptions": []',
        '"assumptions": [{"text": "默认沿用现有订单权限模型。"}]',
    )


def _semantic_decision(
    *selected_paths: str,
) -> str:
    """返回不扩池、只选择真实候选路径的语义映射结果。"""

    return json.dumps(
        {
            "needs_more_context": False,
            "selected_paths": list(selected_paths),
            "queries": [],
            "explanation": ("根据需求实体、符号和依赖证据选择目标文件。"),
        },
        ensure_ascii=False,
    )


def _semantic_query_decision() -> str:
    """返回一次可审计的受控标识符查询。"""

    return json.dumps(
        {
            "needs_more_context": True,
            "selected_paths": [],
            "queries": [
                {
                    "tool": "search_identifiers",
                    "terms": ["cancel_order"],
                    "symbol": None,
                    "module": None,
                    "reason": ("确认取消订单符号对应的真实仓库文件。"),
                }
            ],
            "explanation": "先通过白名单工具核对真实符号。",
        },
        ensure_ascii=False,
    )


def _context(
    tmp_path: Path,
) -> EvaluationCaseContext:
    """创建已固定到同一 commit 的评测上下文。"""

    commit = "a" * 40
    repository = tmp_path / "order_service"

    service = repository / "order_app" / "service.py"
    test_file = repository / "tests" / "test_service.py"

    service.parent.mkdir(
        parents=True,
    )

    test_file.parent.mkdir(
        parents=True,
    )

    service.write_text(
        ("class OrderService:\n    def cancel_order(self, order_id: str) -> None:\n        pass\n"),
        encoding="utf-8",
    )

    test_file.write_text(
        (
            "from order_app.service import OrderService\n\n"
            "def test_cancel_order() -> None:\n"
            "    OrderService().cancel_order('1')\n"
        ),
        encoding="utf-8",
    )

    sample = EvaluationSample(
        sample_id="git-bbbbbbbbbbbb",
        repository_relative_path="",
        request_text=("为订单系统实现所属用户取消待支付订单功能"),
        analysis_commit=commit,
        answer_commit="b" * 40,
        changed_files=(
            "order_app/service.py",
            "tests/test_service.py",
        ),
        source_files=("order_app/service.py",),
        test_files=("tests/test_service.py",),
    )

    cochange_index = CoChangeIndex(
        repository_relative_path="",
        target_commit=commit,
        history_commits=(),
        available_commit_count=0,
        analyzed_commit_count=0,
        skipped_merge_commit_count=0,
        skipped_large_commit_count=0,
        file_commit_counts=(),
        pairs=(),
        history_truncated=False,
        pairs_truncated=False,
        history_digest=ZERO_HISTORY_DIGEST,
    )

    return EvaluationCaseContext(
        sample=sample,
        snapshot_path=repository,
        snapshot_commit=commit,
        cochange_index=cochange_index,
    )


def test_adapter_runs_real_retrieval_guard_and_impact_engine(
    tmp_path: Path,
) -> None:
    """确定性模式应召回并报告仓库中存在的目标源码文件。"""

    adapter = create_evidence_impact_adapter(
        model=FakeListChatModel(
            responses=[
                _model_response(),
            ]
        ),
    )

    observation = adapter(
        _context(tmp_path),
    )

    assert observation.status == "success"
    assert "order_app/service.py" in observation.candidate_pool_paths
    assert "order_app/service.py" in observation.selected_paths
    assert observation.reported_paths == ("order_app/service.py",)
    assert observation.invalid_reported_paths == ()
    assert observation.analyzed_item_count >= 1

    assert observation.assigned_item_count >= observation.analyzed_item_count

    assert observation.latency_ms >= 0

    # 评分轨迹必须完整对应候选池顺序。
    assert (
        tuple(trace.path for trace in observation.candidate_score_traces)
        == observation.candidate_pool_paths
    )

    assert tuple(trace.pool_rank for trace in observation.candidate_score_traces) == tuple(
        range(
            1,
            len(observation.candidate_pool_paths) + 1,
        )
    )

    service_trace = next(
        trace
        for trace in observation.candidate_score_traces
        if trace.path == "order_app/service.py"
    )

    service_signals = {signal.name: signal.value for signal in service_trace.signals}

    assert "keyword" in service_signals
    assert "symbol" in service_signals

    assert service_trace.total_score == round(
        sum(service_signals.values()),
        3,
    )


def test_adapter_indexes_python_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement、检索和 Impact 必须共享同一份 AST 索引。"""

    import app.tools.evidence_evaluation_adapter as adapter_module

    real_indexer = adapter_module.index_python_symbols

    call_count = 0

    def counting_indexer(
        repository: object,
    ) -> object:
        nonlocal call_count

        call_count += 1

        return real_indexer(repository)

    monkeypatch.setattr(
        adapter_module,
        "index_python_symbols",
        counting_indexer,
    )

    adapter = EvidenceGroundedImpactEvaluationAdapter(
        model=FakeListChatModel(
            responses=[
                _model_response(),
            ]
        ),
    )

    adapter(
        _context(tmp_path),
    )

    assert call_count == 1


def test_adapter_runs_semantic_context_mode_without_changing_impact_engine(
    tmp_path: Path,
) -> None:
    """语义模式应使用 Agent 选文件，但继续复用确定性 Impact 引擎。"""

    adapter = create_evidence_impact_adapter(
        model=FakeListChatModel(
            responses=[
                _model_response_with_assumption(),
                _semantic_decision(
                    "order_app/service.py",
                ),
            ]
        ),
        config=EvidenceImpactAdapterConfig(
            context_mode="semantic",
        ),
    )

    observation = adapter(
        _context(tmp_path),
    )

    assert observation.status == "success"

    assert "order_app/service.py" in observation.candidate_pool_paths

    assert observation.selected_paths == ("order_app/service.py",)

    assert observation.reported_paths == ("order_app/service.py",)

    assert observation.invalid_reported_paths == ()

    # 语义重排不能破坏候选池评分证据。
    assert (
        tuple(trace.path for trace in observation.candidate_score_traces)
        == observation.candidate_pool_paths
    )

    # 没有申请工具时，查询次数和轨迹都应为空。
    assert observation.semantic_query_count == 0
    assert observation.semantic_tool_traces == ()


def test_semantic_adapter_records_complete_tool_trace(
    tmp_path: Path,
) -> None:
    """语义查询必须把参数、返回路径和扩池结果写入 Observation。"""

    adapter = create_evidence_impact_adapter(
        model=FakeListChatModel(
            responses=[
                _model_response_with_assumption(),
                _semantic_query_decision(),
                _semantic_decision(
                    "order_app/service.py",
                ),
            ]
        ),
        config=EvidenceImpactAdapterConfig(
            context_mode="semantic",
        ),
    )

    observation = adapter(
        _context(tmp_path),
    )

    assert observation.status == "success"
    assert observation.semantic_query_count == 1

    trace = observation.semantic_tool_traces[0]

    # 查询编号、轮次和工具名称必须被保留。
    assert trace.query_index == 1
    assert trace.round == 1
    assert trace.tool == "search_identifiers"

    # 工具参数必须与模型发起的请求一致。
    assert trace.terms == ("cancel_order",)
    assert trace.symbol is None
    assert trace.module is None

    assert trace.reason == ("确认取消订单符号对应的真实仓库文件。")

    # 工具确实在真实索引中找到了服务文件。
    assert "order_app/service.py" in trace.returned_paths

    # 该文件已经在候选池中，所以不能重复写入。
    assert trace.accepted_paths == ()

    assert any(item.startswith("DUPLICATE_PATH:") for item in trace.rejected)

    # 重复结果没有改变候选池大小。
    assert trace.pool_size_before == trace.pool_size_after


def test_semantic_adapter_replays_recorded_requirement_without_second_requirement_call(
    tmp_path: Path,
) -> None:
    """A/B 两组必须复用同一份 RequirementSpec。"""

    context = _context(tmp_path)

    dataset = EvaluationDataset(
        repository_relative_path="",
        requested_ref="HEAD",
        resolved_ref="c" * 40,
        scanned_commit_count=1,
        samples=(context.sample,),
        rejected_root_count=0,
        rejected_merge_count=0,
        rejected_short_message_count=0,
        rejected_file_count_count=0,
        rejected_pure_test_count=0,
    )

    store = create_recording_requirement_store(
        dataset,
    )

    deterministic = create_evidence_impact_adapter(
        model=FakeListChatModel(
            responses=[
                _model_response_with_assumption(),
            ]
        ),
        requirement_store=store,
    )

    deterministic(context)

    artifact_path = write_requirement_artifact(
        store,
        tmp_path / "requirements.json",
    )

    replay_store = load_requirement_artifact(
        artifact_path,
        dataset,
    )

    # 回放时假模型只有一条 Semantic Mapping 输出。
    # 如果适配器错误地先调用 Requirement Agent，
    # 该输出会无法解析成 RequirementSpecDraft，测试必然失败。
    semantic = create_evidence_impact_adapter(
        model=FakeListChatModel(
            responses=[
                _semantic_decision(
                    "order_app/service.py",
                ),
            ]
        ),
        config=EvidenceImpactAdapterConfig(
            context_mode="semantic",
        ),
        requirement_store=replay_store,
    )

    observation = semantic(context)

    assert observation.status == "success"

    assert observation.selected_paths == ("order_app/service.py",)


def test_adapter_rejects_empty_repository(
    tmp_path: Path,
) -> None:
    """空快照不能生成看似成功的评测结果。"""

    commit = "a" * 40
    repository = tmp_path / "empty"

    repository.mkdir()

    sample = EvaluationSample(
        sample_id="git-bbbbbbbbbbbb",
        repository_relative_path="",
        request_text="实现一个有意义的软件功能变更",
        analysis_commit=commit,
        answer_commit="b" * 40,
        changed_files=("app/service.py",),
        source_files=("app/service.py",),
        test_files=(),
    )

    context = EvaluationCaseContext(
        sample=sample,
        snapshot_path=repository,
        snapshot_commit=commit,
        cochange_index=CoChangeIndex(
            repository_relative_path="",
            target_commit=commit,
            history_commits=(),
            available_commit_count=0,
            analyzed_commit_count=0,
            skipped_merge_commit_count=0,
            skipped_large_commit_count=0,
            file_commit_counts=(),
            pairs=(),
            history_truncated=False,
            pairs_truncated=False,
            history_digest=ZERO_HISTORY_DIGEST,
        ),
    )

    adapter = create_evidence_impact_adapter(
        model=FakeListChatModel(
            responses=[
                _model_response(),
            ]
        ),
    )

    with pytest.raises(
        ValueError,
        match="扫描结果为空",
    ):
        adapter(context)


@pytest.mark.parametrize(
    (
        "field",
        "value",
        "message",
    ),
    [
        (
            "max_repository_files",
            0,
            "max_repository_files",
        ),
        (
            "max_file_size_bytes",
            0,
            "max_file_size_bytes",
        ),
        (
            "pool_limit",
            61,
            "pool_limit",
        ),
    ],
)
def test_adapter_config_rejects_invalid_limits(
    field: str,
    value: int,
    message: str,
) -> None:
    """适配器预算不能绕过扫描和 CandidatePool 上限。"""

    values = {
        "max_repository_files": 200,
        "max_file_size_bytes": 50_000,
        "pool_limit": 50,
    }

    values[field] = value

    with pytest.raises(
        ValueError,
        match=message,
    ):
        EvidenceImpactAdapterConfig(
            **values,
        )


def test_adapter_config_rejects_unknown_context_mode() -> None:
    """未知模式必须在扫描仓库和调用模型前被拒绝。"""

    with pytest.raises(
        ValueError,
        match="context_mode",
    ):
        EvidenceImpactAdapterConfig(
            context_mode="unknown",  # type: ignore[arg-type]
        )
