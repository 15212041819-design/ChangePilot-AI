"""“新检索 + 确定性 Impact 引擎”评测适配器测试。"""

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.schemas.evaluation import (
    EvaluationSample,
)
from app.tools.evaluation_runner import (
    EvaluationCaseContext,
)
from app.tools.evidence_evaluation_adapter import (
    EvidenceGroundedImpactEvaluationAdapter,
    EvidenceImpactAdapterConfig,
    create_evidence_impact_adapter,
)
from app.tools.git_cochange import (
    CoChangeIndex,
)

ZERO_HISTORY_DIGEST = "sha256:" + "0" * 64


def _model_response() -> str:
    """返回能命中真实 cancel_order 方法的 RequirementSpecDraft。"""

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
                "lexical_variants": ["cancel order"],
                "symbol_candidates": ["cancel_order"],
                "role": "action_target"
            }
        ],
        "constraints": [],
        "assumptions": []
    }
    """


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
            "from order_app.service "
            "import OrderService\n\n"
            "def test_cancel_order() -> None:\n"
            "    OrderService()"
            ".cancel_order('1')\n"
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
        history_digest=(ZERO_HISTORY_DIGEST),
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
    """真实链路应召回并报告仓库中存在的目标源码文件。"""

    adapter = create_evidence_impact_adapter(
        model=FakeListChatModel(responses=[_model_response()]),
    )

    observation = adapter(_context(tmp_path))

    assert observation.status == "success"
    assert "order_app/service.py" in observation.candidate_pool_paths
    assert "order_app/service.py" in observation.selected_paths
    assert observation.reported_paths == ("order_app/service.py",)
    assert observation.invalid_reported_paths == ()
    assert observation.analyzed_item_count >= 1
    assert observation.assigned_item_count >= observation.analyzed_item_count
    assert observation.latency_ms >= 0


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
        model=FakeListChatModel(responses=[_model_response()]),
    )

    adapter(_context(tmp_path))

    assert call_count == 1


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
        request_text=("实现一个有意义的软件功能变更"),
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
            history_digest=(ZERO_HISTORY_DIGEST),
        ),
    )

    adapter = create_evidence_impact_adapter(
        model=FakeListChatModel(responses=[_model_response()]),
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
        EvidenceImpactAdapterConfig(**values)
