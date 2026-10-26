"""生产 EvidenceAnalysisExecutor 的真实组装测试。"""

import json
import subprocess
from pathlib import Path

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
from app.tools.git_cochange import (
    CoChangeLeakReport,
)


def _git(
    repository: Path,
    *arguments: str,
) -> str:
    """在临时仓库中执行 Git，为 worktree 测试提供真实 commit。"""

    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    return completed.stdout.strip()


def _requirement_response(
    *,
    with_assumption: bool = False,
) -> str:
    """构造 Requirement Agent 的结构化响应。"""

    assumptions = [{"text": ("取消后是否恢复库存尚未确认。")}] if with_assumption else []

    return json.dumps(
        {
            "summary": ("订单所属用户可以取消待支付订单。"),
            "change_type": "feature",
            "acceptance_criteria": [
                {
                    "description": ("订单所属用户可以取消待支付订单。"),
                    "verification": "test",
                    "priority": "P0",
                }
            ],
            "entity_hints": [
                {
                    "concept": "取消订单",
                    "lexical_variants": [
                        "cancel order",
                    ],
                    "symbol_candidates": [
                        "cancel_order",
                    ],
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
            "explanation": ("源码和测试文件均有真实符号与导入证据。"),
        },
        ensure_ascii=False,
    )


def _workspace(
    tmp_path: Path,
) -> Path:
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
        "from order_app.service "
        "import OrderService\n\n"
        "def test_cancel_order() -> None:\n"
        "    OrderService().cancel_order('1')\n",
        encoding="utf-8",
    )

    # 生产执行器只分析精确 Git 快照，
    # 因此测试也使用真实仓库。
    _git(
        repository,
        "init",
    )
    _git(
        repository,
        "config",
        "user.email",
        "tests@example.com",
    )
    _git(
        repository,
        "config",
        "user.name",
        "ChangePilot Tests",
    )
    _git(
        repository,
        "add",
        ".",
    )
    _git(
        repository,
        "commit",
        "-m",
        "initial order service",
    )

    return root


def test_production_executor_returns_reviewed_unified_report(
    tmp_path: Path,
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
            request_text=("为订单系统增加取消订单功能"),
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
    tmp_path: Path,
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
                request_text=("为订单系统增加取消订单功能"),
                repository_name=("order_service"),
            )
        )


def test_executor_analyzes_exact_historical_commit_in_worktree(
    tmp_path: Path,
) -> None:
    """历史 commit 应在隔离 worktree 中完成全链路分析。"""

    workspace_root = _workspace(tmp_path)

    repository = workspace_root / "order_service"

    historical_commit = _git(
        repository,
        "rev-parse",
        "HEAD",
    )

    # 在当前工作区中创建新提交，
    # 确保目标 commit 确实是历史版本。
    (repository / "newer.py").write_text(
        "NEWER = True\n",
        encoding="utf-8",
    )

    _git(
        repository,
        "add",
        "newer.py",
    )
    _git(
        repository,
        "commit",
        "-m",
        "newer unrelated change",
    )

    current_commit = _git(
        repository,
        "rev-parse",
        "HEAD",
    )

    executor = ProductionEvidenceAnalysisExecutor(
        workspace_root=workspace_root,
        model=FakeListChatModel(
            responses=[
                _requirement_response(),
                _semantic_response(),
            ]
        ),
    )

    report = executor.analyze(
        EvidenceAnalysisRequest(
            request_text=("为订单系统增加取消订单功能"),
            repository_name="order_service",
            commit=historical_commit,
        )
    )

    assert report.overview.status == "ready"
    assert report.candidate_set.commit == historical_commit

    # 当前仓库仍然停留在最新提交，
    # 没有被历史分析切换。
    assert (
        _git(
            repository,
            "rev-parse",
            "HEAD",
        )
        == current_commit
    )

    assert (repository / "newer.py").is_file()


def test_executor_stops_when_cochange_leak_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共变历史未通过泄漏自检时不得进入 LLM 和分析分支。"""

    monkeypatch.setattr(
        "app.services.evidence_analysis.check_cochange_leak",
        lambda *_: CoChangeLeakReport(
            passed=False,
            target_commit_matches=True,
            target_commit_excluded=False,
            checked_commit_count=1,
            unexpected_commits=("f" * 40,),
        ),
    )

    executor = ProductionEvidenceAnalysisExecutor(
        workspace_root=_workspace(tmp_path),
        # 没有提供任何模型响应。
        # 如果代码错误地继续调用 LLM，
        # 测试会立即失败。
        model=FakeListChatModel(responses=[]),
    )

    with pytest.raises(
        EvidenceAnalysisUnavailableError,
        match="时间泄漏",
    ):
        executor.analyze(
            EvidenceAnalysisRequest(
                request_text=("为订单系统增加取消订单功能"),
                repository_name=("order_service"),
            )
        )
