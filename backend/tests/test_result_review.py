"""三路分析结果审核引擎测试。"""

import pytest

from app.engines.branch_result import build_branch_result
from app.engines.result_review import (
    BLOCKER_FINDING_RULE,
    BRANCH_DEGRADED_RULE,
    DIGEST_MISMATCH_RULE,
    FINDING_COLLISION_RULE,
    IMPLEMENTATION_GAP_RULE,
    TEST_GAP_RULE,
    UNKNOWN_AC_RULE,
    review_parallel_results,
)
from app.schemas.branch_result import (
    AnalysisBranch,
    BranchFinding,
    BranchResult,
    FindingPriority,
)
from app.schemas.contracts import (
    AcceptanceCriterion,
    ChangePoint,
    EntityHint,
    EvidencePointer,
    RequirementSpec,
    ScopeHints,
    SymbolRef,
)
from app.schemas.guard import GuardDigests

ZERO_HASH = "sha256:" + "0" * 64
ONE_HASH = "sha256:" + "1" * 64


def digests(
    *,
    alternate: bool = False,
) -> GuardDigests:
    """创建测试使用的三项上下文摘要。"""

    digest = ONE_HASH if alternate else ZERO_HASH

    return GuardDigests(
        set_digest=digest,
        spec_digest=digest,
        index_digest=digest,
    )


def requirement_spec() -> RequirementSpec:
    """创建包含两个 active AC 和一个 dropped AC 的需求。"""

    return RequirementSpec(
        spec_id="RS-0001",
        spec_revision=1,
        summary="订单所属用户可以取消待支付订单。",
        change_type="feature",
        acceptance_criteria=(
            AcceptanceCriterion(
                id="AC-001",
                description=("订单所属用户可以取消待支付订单。"),
                verification="test",
                priority="P0",
            ),
            AcceptanceCriterion(
                id="AC-002",
                description=("取消成功后记录审计日志。"),
                verification="test",
                priority="P1",
            ),
            AcceptanceCriterion(
                id="AC-003",
                description=("旧版本不再实现的验收标准。"),
                verification="manual",
                priority="P2",
                status="dropped",
            ),
        ),
        entity_hints=(
            EntityHint(
                concept_id="EH-001",
                concept="取消订单",
                lexical_variants=("cancel order",),
                symbol_candidates=("cancel_order",),
                role="action_target",
                in_repo_vocab=True,
            ),
        ),
        scope_hints=ScopeHints(),
        source_text=("为订单系统增加取消订单功能。"),
        spec_digest=ZERO_HASH,
    )


def change_points(
    *,
    cover_ac2: bool = True,
) -> tuple[ChangePoint, ...]:
    """创建已经通过 Guard 的实施变更点。"""

    points = [
        ChangePoint(
            cp_id="CP-0001",
            path="order_app/service.py",
            target=SymbolRef(
                name="cancel_order",
                parent_symbol=("order_app.service.OrderService"),
                expect="new",
                kind="method",
            ),
            change_type="add",
            ac_ids=("AC-001",),
            evidence=(
                EvidencePointer(
                    path="order_app/service.py",
                    line=10,
                    kind="def",
                ),
            ),
            confidence=0.9,
            source="impact",
        )
    ]

    if cover_ac2:
        points.append(
            ChangePoint(
                cp_id="CP-0002",
                path="order_app/audit.py",
                target=SymbolRef(
                    name="record",
                    parent_symbol=("order_app.audit.AuditLogger"),
                    expect="existing",
                    kind="method",
                ),
                change_type="semantic",
                ac_ids=("AC-002",),
                evidence=(
                    EvidencePointer(
                        path="order_app/audit.py",
                        line=8,
                        kind="def",
                    ),
                ),
                confidence=0.9,
                source="impact",
            )
        )

    return tuple(points)


def finding(
    finding_id: str,
    *,
    path: str,
    ac_id: str,
    priority: FindingPriority = "major",
    summary: str = "需要修改目标代码。",
) -> BranchFinding:
    """创建审核测试使用的 finding。"""

    return BranchFinding(
        finding_id=finding_id,
        priority=priority,
        path=path,
        summary=summary,
        ac_ids=(ac_id,),
        evidence_ids=(f"DEF:{path}:1",),
        rule_ids=("TEST-RULE",),
    )


def branch_result(
    branch: AnalysisBranch,
    *,
    findings: tuple[
        BranchFinding,
        ...,
    ] = (),
    consumed: GuardDigests | None = None,
    degraded: bool = False,
) -> BranchResult:
    """创建 success 或 degraded 的分支结果。"""

    assigned = (
        "order_app/service.py",
        "order_app/audit.py",
    )

    analyzed = assigned[:1] if degraded else assigned

    return build_branch_result(
        branch=branch,
        assigned_items=assigned,
        analyzed_items=analyzed,
        unit="file",
        findings=findings,
        consumed_digests=(consumed or digests()),
    )


def successful_results() -> tuple[
    BranchResult,
    ...,
]:
    """创建覆盖全部 active AC 的三路成功结果。"""

    return (
        branch_result(
            "impact",
            findings=(
                finding(
                    "IMP-001",
                    path="order_app/service.py",
                    ac_id="AC-001",
                ),
                finding(
                    "IMP-002",
                    path="order_app/audit.py",
                    ac_id="AC-002",
                ),
            ),
        ),
        branch_result(
            "test",
            findings=(
                finding(
                    "TEST-001",
                    path="tests/test_service.py",
                    ac_id="AC-001",
                ),
                finding(
                    "TEST-002",
                    path="tests/test_service.py",
                    ac_id="AC-002",
                ),
            ),
        ),
        branch_result("risk"),
    )


def issue_codes(
    report,
) -> set[str]:
    """返回报告中的审核规则编号集合。"""

    return {issue.code for issue in report.issues}


def test_ready_when_all_active_acceptance_criteria_are_covered() -> None:
    """三路成功且实施与测试证据完整时应允许继续。"""

    report = review_parallel_results(
        requirement_spec=requirement_spec(),
        change_points=change_points(),
        branch_results=successful_results(),
        expected_digests=digests(),
    )

    assert report.status == "ready"
    assert report.issues == ()

    assert tuple(row.ac_id for row in report.acceptance_coverage) == (
        "AC-001",
        "AC-002",
    )

    assert tuple(summary.branch for summary in report.branches) == (
        "impact",
        "test",
        "risk",
    )


def test_missing_change_point_blocks_implementation() -> None:
    """active AC 没有 Guard 通过的变更点时必须阻断实施。"""

    report = review_parallel_results(
        requirement_spec=requirement_spec(),
        change_points=change_points(cover_ac2=False),
        branch_results=successful_results(),
        expected_digests=digests(),
    )

    assert report.status == "blocked"
    assert IMPLEMENTATION_GAP_RULE in issue_codes(report)

    gap = next(issue for issue in report.issues if issue.code == IMPLEMENTATION_GAP_RULE)

    assert gap.ac_ids == ("AC-002",)


def test_missing_test_evidence_creates_gate() -> None:
    """实施证据完整但测试证据缺失时应等待补充。"""

    results = list(successful_results())

    results[1] = branch_result(
        "test",
        findings=(
            finding(
                "TEST-001",
                path="tests/test_service.py",
                ac_id="AC-001",
            ),
        ),
    )

    report = review_parallel_results(
        requirement_spec=requirement_spec(),
        change_points=change_points(),
        branch_results=results,
        expected_digests=digests(),
    )

    assert report.status == "gated"
    assert TEST_GAP_RULE in issue_codes(report)


def test_degraded_branch_creates_gate_and_preserves_coverage() -> None:
    """分支降级必须公开进入门禁并保留覆盖率。"""

    results = list(successful_results())

    results[2] = branch_result(
        "risk",
        degraded=True,
    )

    report = review_parallel_results(
        requirement_spec=requirement_spec(),
        change_points=change_points(),
        branch_results=results,
        expected_digests=digests(),
    )

    assert report.status == "gated"

    assert BRANCH_DEGRADED_RULE in issue_codes(report)

    assert report.branches[2].coverage_ratio == 0.5


def test_digest_mismatch_blocks_review() -> None:
    """任何分支消费错误快照时都必须阻断。"""

    results = list(successful_results())

    results[2] = branch_result(
        "risk",
        consumed=digests(alternate=True),
    )

    report = review_parallel_results(
        requirement_spec=requirement_spec(),
        change_points=change_points(),
        branch_results=results,
        expected_digests=digests(),
    )

    assert report.status == "blocked"

    assert DIGEST_MISMATCH_RULE in issue_codes(report)


def test_unknown_acceptance_reference_blocks_review() -> None:
    """分析结果引用未知 AC 时必须阻断。"""

    results = list(successful_results())

    results[2] = branch_result(
        "risk",
        findings=(
            finding(
                "RISK-001",
                path="order_app/api.py",
                ac_id="AC-999",
            ),
        ),
    )

    report = review_parallel_results(
        requirement_spec=requirement_spec(),
        change_points=change_points(),
        branch_results=results,
        expected_digests=digests(),
    )

    assert report.status == "blocked"
    assert UNKNOWN_AC_RULE in issue_codes(report)


def test_same_finding_id_with_different_meaning_is_a_conflict() -> None:
    """同一 finding_id 表达不同含义时视为冲突。"""

    results = list(successful_results())

    results[2] = branch_result(
        "risk",
        findings=(
            finding(
                "IMP-001",
                path="order_app/api.py",
                ac_id="AC-001",
                summary="上游调用方被遗漏。",
            ),
        ),
    )

    report = review_parallel_results(
        requirement_spec=requirement_spec(),
        change_points=change_points(),
        branch_results=results,
        expected_digests=digests(),
    )

    assert report.status == "blocked"

    assert FINDING_COLLISION_RULE in issue_codes(report)


def test_blocker_finding_blocks_review() -> None:
    """任一分支给出 blocker finding 时必须阻断。"""

    results = list(successful_results())

    results[2] = branch_result(
        "risk",
        findings=(
            finding(
                "RISK-001",
                path="order_app/api.py",
                ac_id="AC-001",
                priority="blocker",
            ),
        ),
    )

    report = review_parallel_results(
        requirement_spec=requirement_spec(),
        change_points=change_points(),
        branch_results=results,
        expected_digests=digests(),
    )

    assert report.status == "blocked"

    assert BLOCKER_FINDING_RULE in issue_codes(report)


def test_input_order_does_not_change_report_order() -> None:
    """fan-in 完成顺序不能影响审核结果。"""

    results = successful_results()

    report = review_parallel_results(
        requirement_spec=requirement_spec(),
        change_points=change_points(),
        branch_results=(
            results[2],
            results[0],
            results[1],
        ),
        expected_digests=digests(),
    )

    assert tuple(summary.branch for summary in report.branches) == (
        "impact",
        "test",
        "risk",
    )


def test_missing_branch_is_rejected_before_review() -> None:
    """三路结果不完整时不能生成完整报告。"""

    results = successful_results()

    with pytest.raises(
        ValueError,
        match="缺少分支",
    ):
        review_parallel_results(
            requirement_spec=requirement_spec(),
            change_points=change_points(),
            branch_results=results[:2],
            expected_digests=digests(),
        )
