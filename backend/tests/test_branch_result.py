"""BranchResult、Coverage 和稳定排序规则测试。"""

import pytest
from pydantic import ValidationError

from app.engines.branch_result import (
    build_branch_result,
    build_candidate_set_branch_result,
    build_failed_branch_result,
)
from app.schemas.branch_result import (
    BranchFinding,
    BranchResult,
    Coverage,
    CoverageMissing,
)
from app.schemas.contracts import (
    Budget,
    CandidateSet,
    FileRef,
)
from app.schemas.guard import GuardDigests

ZERO_HASH = "sha256:" + "0" * 64


def digests() -> GuardDigests:
    """创建三个一致的上游摘要。"""

    return GuardDigests(
        set_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        index_digest=ZERO_HASH,
    )


def finding(
    finding_id: str,
    *,
    priority: str,
    path: str,
    symbol: str | None = None,
) -> BranchFinding:
    """创建测试使用的分支 finding。"""

    return BranchFinding(
        finding_id=finding_id,
        priority=priority,
        path=path,
        symbol=symbol,
        summary=(f"测试结论 {finding_id}"),
        ac_ids=("AC-001",),
        rule_ids=("RULE-001",),
    )


def candidate_set() -> CandidateSet:
    """创建包含两个源码文件和一个测试文件的候选集。"""

    return CandidateSet(
        set_id="CS-0001",
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        files=(
            FileRef(
                path="app/models.py",
                content_sha256=ZERO_HASH,
                kind="source",
                reason="命中模型",
                score=1.0,
                confidence=0.9,
                selected_by="semantic_map",
            ),
            FileRef(
                path="app/service.py",
                content_sha256=ZERO_HASH,
                kind="source",
                reason="命中服务",
                score=1.0,
                confidence=0.9,
                selected_by="semantic_map",
            ),
            FileRef(
                path=("tests/test_service.py"),
                content_sha256=ZERO_HASH,
                kind="test",
                reason="相关测试",
                score=0.8,
                confidence=0.8,
                selected_by="semantic_map",
            ),
        ),
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )


def test_t25_partial_coverage_is_degraded_and_visible() -> None:
    """T-25：分析 9/12 时必须 degraded，并显示覆盖不足。"""

    assigned = tuple(
        f"app/module_{index:02d}.py"
        for index in range(
            1,
            13,
        )
    )

    analyzed = assigned[:9]

    result = build_branch_result(
        branch="risk",
        assigned_items=assigned,
        analyzed_items=analyzed,
        unit="file",
        findings=(),
        consumed_digests=digests(),
    )

    assert result.status == "degraded"

    assert result.coverage.assigned == 12

    assert result.coverage.analyzed == 9

    assert result.coverage.ratio == 0.75

    assert len(result.coverage.missing) == 3

    assert "9/12" in result.degraded_reasons[0]


def test_t26_empty_assignment_is_success_with_no_items_note() -> None:
    """T-26：没有分配项时返回 success，而不是误报失败。"""

    result = build_branch_result(
        branch="impact",
        assigned_items=(),
        analyzed_items=(),
        unit="file",
        findings=(),
        consumed_digests=digests(),
    )

    assert result.status == "success"

    assert result.coverage.assigned == 0

    assert result.coverage.analyzed == 0

    assert result.coverage.ratio == 1.0

    assert result.coverage.note == "no_items"

    assert result.coverage.missing == ()


def test_findings_are_sorted_by_priority_path_symbol_and_id() -> None:
    """finding 输入顺序不影响 BranchResult 输出顺序。"""

    result = build_branch_result(
        branch="impact",
        assigned_items=("a.py",),
        analyzed_items=("a.py",),
        unit="file",
        findings=(
            finding(
                "F-003",
                priority="minor",
                path="b.py",
            ),
            finding(
                "F-002",
                priority="blocker",
                path="b.py",
            ),
            finding(
                "F-001",
                priority="blocker",
                path="a.py",
            ),
        ),
        consumed_digests=digests(),
    )

    assert tuple(item.finding_id for item in result.findings) == (
        "F-001",
        "F-002",
        "F-003",
    )


def test_all_parallel_branches_use_candidate_source_files_as_denominator() -> None:
    """Risk 分支不能使用 Impact ChangePoint 数量作为初始分母。"""

    results = tuple(
        build_candidate_set_branch_result(
            branch=branch,
            candidate_set=candidate_set(),
            analyzed_paths=("app/service.py",),
            findings=(),
            consumed_digests=digests(),
        )
        for branch in (
            "impact",
            "test",
            "risk",
        )
    )

    assert {result.coverage.assigned for result in results} == {
        2,
    }

    assert {result.coverage.ratio for result in results} == {
        0.5,
    }


def test_branch_exception_becomes_structured_failed_result() -> None:
    """分支异常不得冒泡导致整个并行 superstep 失败。"""

    result = build_failed_branch_result(
        branch="test",
        assigned_items=(
            "app/models.py",
            "app/service.py",
        ),
        unit="file",
        consumed_digests=digests(),
        error=RuntimeError("测试映射失败"),
    )

    assert result.status == "failed"

    assert result.coverage.ratio == 0.0

    assert result.errors[0].code == "RuntimeError"

    assert result.errors[0].message == "测试映射失败"


def test_contract_rejects_status_that_conflicts_with_coverage() -> None:
    """契约层拒绝把部分覆盖伪装成 success。"""

    coverage = Coverage(
        assigned=2,
        analyzed=1,
        ratio=0.5,
        unit="file",
        missing=(
            CoverageMissing(
                path="app/models.py",
                reason="没有分析结果",
            ),
        ),
    )

    with pytest.raises(
        ValidationError,
        match="coverage",
    ):
        BranchResult(
            branch="risk",
            status="success",
            coverage=coverage,
            consumed_digests=digests(),
        )
