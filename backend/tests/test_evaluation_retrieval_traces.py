"""离线评测全量候选检索轨迹测试。"""

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.schemas.evaluation import EvaluationSample
from app.schemas.evaluation_metrics import (
    CandidateRerankTrace,
    EvaluationObservation,
    RepositoryFileRetrievalTrace,
)
from app.tools.evaluation_runner import EvaluationCaseContext
from app.tools.evidence_evaluation_adapter import create_evidence_impact_adapter
from app.tools.git_cochange import CoChangeIndex

ZERO_HISTORY_DIGEST = "sha256:" + "0" * 64


def _model_response() -> str:
    """返回能命中真实 cancel_order 方法的需求规范。"""

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


def _context(tmp_path: Path) -> EvaluationCaseContext:
    """创建包含选中文件、无信号文件和文档的评测快照。"""

    commit = "a" * 40
    repository = tmp_path / "order_service"
    service = repository / "order_app" / "service.py"
    test_file = repository / "tests" / "test_service.py"
    audit_file = repository / "infra" / "audit.py"
    readme_file = repository / "README.md"

    service.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    audit_file.parent.mkdir(parents=True)

    service.write_text(
        "class OrderService:\n    def cancel_order(self, order_id: str) -> None:\n        pass\n",
        encoding="utf-8",
    )
    test_file.write_text(
        "from order_app.service import OrderService\n\n"
        "def test_cancel_order() -> None:\n"
        "    OrderService().cancel_order('1')\n",
        encoding="utf-8",
    )
    audit_file.write_text(
        "def record_event() -> None:\n    pass\n",
        encoding="utf-8",
    )
    readme_file.write_text(
        "# Order service\n",
        encoding="utf-8",
    )

    sample = EvaluationSample(
        sample_id="git-bbbbbbbbbbbb",
        repository_relative_path="",
        request_text="为订单系统实现所属用户取消待支付订单功能",
        analysis_commit=commit,
        answer_commit="b" * 40,
        changed_files=("order_app/service.py",),
        source_files=("order_app/service.py",),
        test_files=(),
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


def test_adapter_records_reason_for_every_snapshot_file(tmp_path: Path) -> None:
    """评测 Observation 必须解释快照中每个文件是否进入候选池。"""

    adapter = create_evidence_impact_adapter(
        model=FakeListChatModel(responses=[_model_response()]),
    )

    observation = adapter(_context(tmp_path))
    traces = {trace.path: trace for trace in observation.baseline_retrieval_traces}

    assert tuple(trace.path for trace in observation.baseline_retrieval_traces) == tuple(
        sorted(traces)
    )

    assert set(traces) == {
        "README.md",
        "infra/audit.py",
        "order_app/service.py",
        "tests/test_service.py",
    }
    assert traces["order_app/service.py"].outcome == "selected"
    assert traces["order_app/service.py"].full_rank is not None
    assert traces["infra/audit.py"].outcome == "no_signal"
    assert traces["README.md"].outcome == "document"

    rerank_traces = observation.candidate_rerank_traces

    assert tuple(trace.path for trace in rerank_traces) == observation.candidate_pool_paths

    assert tuple(trace.original_rank for trace in rerank_traces) == tuple(
        range(1, len(rerank_traces) + 1)
    )

    assert {trace.path for trace in rerank_traces if trace.selected} == set(
        observation.selected_paths
    )

    assert all(trace.rerank_rank is not None for trace in rerank_traces if trace.selected)


def test_observation_rejects_unsorted_baseline_retrieval_traces() -> None:
    """评测契约必须拒绝路径顺序不稳定的全量轨迹。"""

    with pytest.raises(ValueError, match="按路径稳定排序"):
        EvaluationObservation(
            sample_id="sample-1",
            status="success",
            candidate_pool_paths=(),
            selected_paths=(),
            reported_paths=(),
            invalid_reported_paths=(),
            analyzed_item_count=0,
            assigned_item_count=0,
            latency_ms=0.0,
            baseline_retrieval_traces=(
                RepositoryFileRetrievalTrace(
                    path="z.py",
                    kind="source",
                    outcome="no_signal",
                ),
                RepositoryFileRetrievalTrace(
                    path="a.py",
                    kind="source",
                    outcome="no_signal",
                ),
            ),
        )


def test_observation_rejects_rerank_trace_selection_mismatch() -> None:
    """重排轨迹不能宣称选中了 CandidateSet 之外的文件。"""

    with pytest.raises(ValueError, match="选中文件必须等于 selected_paths"):
        EvaluationObservation(
            sample_id="sample-1",
            status="success",
            candidate_pool_paths=("app/a.py",),
            selected_paths=(),
            reported_paths=(),
            invalid_reported_paths=(),
            analyzed_item_count=0,
            assigned_item_count=0,
            latency_ms=0.0,
            candidate_rerank_traces=(
                CandidateRerankTrace(
                    path="app/a.py",
                    original_rank=1,
                    rerank_rank=1,
                    pre_score=1.0,
                    lane="source",
                    selected=True,
                ),
            ),
        )
