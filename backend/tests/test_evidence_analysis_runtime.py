"""生产 EvidenceAnalysisExecutor 的真实组装测试。"""

import json

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.schemas.evidence_analysis_api import (
    EvidenceAnalysisRequest,
)
from app.services.evidence_analysis import (
    EvidenceAnalysisReviewRequiredError,
    EvidenceAnalysisUnavailableError,
    ProductionEvidenceAnalysisExecutor,
)


def _requirement_response(
    *,
    with_assumption: bool = False,
) -> str:
    """构造 Requirement Agent 的结构化响应。"""

    assumptions = [{"text": "取消后是否恢复库存尚未确认。"}] if with_assumption else []

    return json.dumps(
        {
            "summary": "订单所属用户可以取消待支付订单。",
            "change_type": "feature",
            "acceptance_criteria": [
                {
                    "description": "订单所属用户可以取消待支付订单。",
                    "verification": "test",
                    "priority": "P0",
                }
            ],
            "entity_hints": [
                {
                    "concept": "取消订单",
                    "lexical_variants": ["cancel order"],
                    "symbol_candidates": ["cancel_order"],
                    "role": "action_target",
                }
            ],
            "constraints": [
                "仅允许取消待支付订单",
            ],
            "assumptions": assumptions,
        },
        ensure_ascii=False,
    )


def _semantic_response() -> str:
    """构造 Semantic Mapping Agent 的最终选择。"""

    return json.dumps(
        {
            "needs_more_context": False,
            "selected_paths": [
                "order_app/service.py",
                "tests/test_service.py",
            ],
            "queries": [],
            "explanation": "源码和测试文件均有真实符号与导入证据。",
        },
        ensure_ascii=False,
    )


def _workspace(tmp_path):
    """创建生产执行器可以安全扫描的临时工作区。"""

    root = tmp_path / "sample_repos"
    repository = root / "order_service"
    source_dir = repository / "order_app"
    tests_dir = repository / "tests"

    source_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    (source_dir / "service.py").write_text(
        "class OrderService:\n    def cancel_order(self, order_id: str) -> None:\n        pass\n",
        encoding="utf-8",
    )

    (tests_dir / "test_service.py").write_text(
        "from order_app.service import OrderService\n\n"
        "def test_cancel_order() -> None:\n"
        "    OrderService().cancel_order('1')\n",
        encoding="utf-8",
    )

    return root


def test_production_executor_returns_reviewed_unified_report(
    tmp_path,
) -> None:
    """真实工具链应完成扫描、映射、三路分析、审核和报告。"""

    executor = ProductionEvidenceAnalysisExecutor(
        workspace_root=_workspace(tmp_path),
        model=FakeListChatModel(
            responses=[
                _requirement_response(),
                _semantic_response(),
            ]
        ),
    )

    report = executor.analyze(
        EvidenceAnalysisRequest(
            request_text="为订单系统增加取消订单功能",
            repository_name="order_service",
            commit="HEAD",
        )
    )

    assert report.overview.status == "ready"
    assert report.overview.can_implement is True
    assert report.requirement.active_acceptance_ids == ("AC-001",)
    assert report.candidate_set.paths == (
        "order_app/service.py",
        "tests/test_service.py",
    )
    assert tuple(result.branch for result in report.branch_results) == (
        "impact",
        "test",
        "risk",
    )
    assert report.acceptance_coverage[0].implementation_covered is True
    assert report.acceptance_coverage[0].test_covered is True


def test_executor_stops_before_semantic_mapping_when_review_is_required(
    tmp_path,
) -> None:
    """未确认假设不能被生产执行器自动批准。"""

    executor = ProductionEvidenceAnalysisExecutor(
        workspace_root=_workspace(tmp_path),
        model=FakeListChatModel(
            responses=[
                _requirement_response(
                    with_assumption=True,
                ),
            ]
        ),
    )

    with pytest.raises(
        EvidenceAnalysisReviewRequiredError,
        match="AS-001",
    ):
        executor.analyze(
            EvidenceAnalysisRequest(
                request_text="为订单系统增加取消订单功能",
                repository_name="order_service",
            )
        )


def test_executor_rejects_historical_commit_until_worktree_is_available(
    tmp_path,
) -> None:
    """历史 commit 不能错误读取当前工作区并冒充历史快照。"""

    executor = ProductionEvidenceAnalysisExecutor(
        workspace_root=_workspace(tmp_path),
        model=FakeListChatModel(responses=[]),
    )

    with pytest.raises(
        EvidenceAnalysisUnavailableError,
        match="git worktree",
    ):
        executor.analyze(
            EvidenceAnalysisRequest(
                request_text="为订单系统增加取消订单功能",
                repository_name="order_service",
                commit="abc123",
            )
        )
