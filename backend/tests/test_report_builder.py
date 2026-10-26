"""统一分析报告构建器测试。"""

import json

import pytest

from app.engines.branch_result import (
    build_branch_result,
)
from app.engines.report_builder import (
    build_analysis_report,
)
from app.schemas.branch_result import (
    AnalysisBranch,
    BranchFinding,
    BranchResult,
    FindingPriority,
)
from app.schemas.contracts import (
    AcceptanceCriterion,
    Budget,
    CandidateSet,
    EntityHint,
    FileRef,
    RequirementSpec,
    ScopeHints,
)
from app.schemas.guard import GuardDigests
from app.schemas.review import (
    AcceptanceCoverage,
    BranchReviewSummary,
    ReviewIssue,
    ReviewReport,
    ReviewStatus,
)

ZERO_HASH = "sha256:" + "0" * 64
ONE_HASH = "sha256:" + "1" * 64


def digests(
    *,
    alternate: bool = False,
) -> GuardDigests:
    """创建报告测试使用的摘要。"""

    value = ONE_HASH if alternate else ZERO_HASH

    return GuardDigests(
        set_digest=value,
        spec_digest=value,
        index_digest=value,
    )


def requirement_spec() -> RequirementSpec:
    """创建带两个 active AC 和一个 dropped AC 的需求。"""

    return RequirementSpec(
        spec_id="RS-0001",
        spec_revision=2,
        summary=("订单所属用户可以取消待支付订单并记录审计日志。"),
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
                description=("已经移出本次范围。"),
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


def candidate_set() -> CandidateSet:
    """创建统一报告对应的冻结候选集。"""

    return CandidateSet(
        set_id="CS-0001",
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        files=(
            FileRef(
                path="order_app/service.py",
                content_sha256=ZERO_HASH,
                kind="source",
                reason="命中订单服务",
                score=1.0,
                confidence=0.9,
                selected_by="semantic_map",
            ),
            FileRef(
                path="tests/test_service.py",
                content_sha256=ZERO_HASH,
                kind="test",
                reason="命中服务测试",
                score=0.8,
                confidence=0.8,
                selected_by="semantic_map",
            ),
        ),
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )


def finding(
    finding_id: str,
    *,
    ac_id: str,
    path: str,
    priority: FindingPriority = "major",
) -> BranchFinding:
    """创建报告详情中的 finding。"""

    return BranchFinding(
        finding_id=finding_id,
        priority=priority,
        path=path,
        summary=(f"{finding_id} 的分析结论。"),
        ac_ids=(ac_id,),
        rule_ids=("REPORT-TEST",),
    )


def branch_result(
    branch: AnalysisBranch,
    *,
    findings: tuple[
        BranchFinding,
        ...,
    ] = (),
    consumed: GuardDigests | None = None,
) -> BranchResult:
    """创建完整覆盖的分支结果。"""

    return build_branch_result(
        branch=branch,
        assigned_items=("order_app/service.py",),
        analyzed_items=("order_app/service.py",),
        unit="file",
        findings=findings,
        consumed_digests=(consumed or digests()),
    )


def branch_results(
    *,
    test_covers_ac2: bool = True,
) -> tuple[BranchResult, ...]:
    """创建 Impact、Test、Risk 三路结果。"""

    test_findings = [
        finding(
            "TEST-001",
            ac_id="AC-001",
            path="tests/test_service.py",
        )
    ]

    if test_covers_ac2:
        test_findings.append(
            finding(
                "TEST-002",
                ac_id="AC-002",
                path="tests/test_service.py",
                priority="minor",
            )
        )

    return (
        branch_result(
            "impact",
            findings=(
                finding(
                    "IMP-001",
                    ac_id="AC-001",
                    path="order_app/service.py",
                ),
                finding(
                    "IMP-002",
                    ac_id="AC-002",
                    path="order_app/service.py",
                ),
            ),
        ),
        branch_result(
            "test",
            findings=tuple(test_findings),
        ),
        branch_result("risk"),
    )


def branch_summaries(
    results: tuple[
        BranchResult,
        ...,
    ],
) -> tuple[
    BranchReviewSummary,
    ...,
]:
    """根据真实分支结果构造匹配的审核摘要。"""

    return tuple(
        BranchReviewSummary(
            branch=result.branch,
            status=result.status,
            finding_count=len(result.findings),
            blocker_count=sum(item.priority == "blocker" for item in (result.findings)),
            coverage_assigned=(result.coverage.assigned),
            coverage_analyzed=(result.coverage.analyzed),
            coverage_ratio=(result.coverage.ratio),
        )
        for result in results
    )


def review_report(
    status: ReviewStatus = "ready",
) -> tuple[
    ReviewReport,
    tuple[BranchResult, ...],
]:
    """创建 ready、gated 或 blocked 的审核结果及对应分支结果。"""

    results = branch_results(test_covers_ac2=(status != "gated"))

    if status == "ready":
        issues = ()
        ac2_change_points = ("CP-0002",)
        ac2_test_findings = ("TEST-002",)

    elif status == "gated":
        issues = (
            ReviewIssue(
                code="REVIEW-TEST-GAP",
                level="gate",
                message=("AC-002 缺少测试证据。"),
                ac_ids=("AC-002",),
                branches=("test",),
            ),
        )
        ac2_change_points = ("CP-0002",)
        ac2_test_findings = ()

    else:
        issues = (
            ReviewIssue(
                code=("REVIEW-IMPLEMENTATION-GAP"),
                level="blocker",
                message=("AC-002 缺少实施变更点。"),
                ac_ids=("AC-002",),
            ),
        )
        ac2_change_points = ()
        ac2_test_findings = ("TEST-002",)

    report = ReviewReport(
        status=status,
        issues=issues,
        acceptance_coverage=(
            AcceptanceCoverage(
                ac_id="AC-001",
                change_point_ids=("CP-0001",),
                impact_finding_ids=("IMP-001",),
                test_finding_ids=("TEST-001",),
                implementation_covered=True,
                test_covered=True,
            ),
            AcceptanceCoverage(
                ac_id="AC-002",
                change_point_ids=(ac2_change_points),
                impact_finding_ids=("IMP-002",),
                test_finding_ids=(ac2_test_findings),
                implementation_covered=bool(ac2_change_points),
                test_covered=bool(ac2_test_findings),
            ),
        ),
        branches=branch_summaries(results),
        reviewed_digests=digests(),
    )

    return report, results


def test_ready_report_contains_frontend_summary_and_details() -> None:
    """ready 报告同时提供首屏摘要和完整详情。"""

    review, results = review_report("ready")

    report = build_analysis_report(
        requirement_spec=(requirement_spec()),
        candidate_set=candidate_set(),
        review_report=review,
        branch_results=results,
    )

    assert report.overview.status == "ready"

    assert report.overview.can_implement is True

    assert report.overview.requires_human_review is False

    assert report.overview.active_acceptance_count == 2

    assert report.overview.implementation_covered_count == 2

    assert report.overview.test_covered_count == 2

    assert report.overview.finding_count == 4

    assert report.gates == ()

    assert tuple(result.branch for result in (report.branch_results)) == (
        "impact",
        "test",
        "risk",
    )


def test_gated_report_exposes_gate_and_disables_implementation() -> None:
    """测试覆盖不足时报告必须显式提示人工门禁。"""

    review, results = review_report("gated")

    report = build_analysis_report(
        requirement_spec=(requirement_spec()),
        candidate_set=candidate_set(),
        review_report=review,
        branch_results=results,
    )

    assert report.overview.status == "gated"

    assert report.overview.can_implement is False

    assert report.overview.requires_human_review is True

    assert report.overview.test_covered_count == 1

    assert report.gates[0].code == "REVIEW-TEST-GAP"


def test_blocked_report_contains_blocker_gate() -> None:
    """实施证据缺失时统一报告必须保留 blocker。"""

    review, results = review_report("blocked")

    report = build_analysis_report(
        requirement_spec=(requirement_spec()),
        candidate_set=candidate_set(),
        review_report=review,
        branch_results=results,
    )

    assert report.overview.status == "blocked"

    assert report.overview.implementation_covered_count == 1

    assert report.gates[0].level == "blocker"


def test_report_is_stable_when_branch_input_order_changes() -> None:
    """三路输入完成顺序不能改变报告 ID 和 digest。"""

    review, results = review_report("ready")

    first = build_analysis_report(
        requirement_spec=(requirement_spec()),
        candidate_set=candidate_set(),
        review_report=review,
        branch_results=results,
    )

    second = build_analysis_report(
        requirement_spec=(requirement_spec()),
        candidate_set=candidate_set(),
        review_report=review,
        branch_results=(
            results[2],
            results[0],
            results[1],
        ),
    )

    assert first.report_id == second.report_id

    assert first.report_digest == second.report_digest

    assert first == second


def test_report_digest_changes_when_structured_evidence_changes() -> None:
    """结构化结论变化时必须产生不同报告摘要。"""

    review, results = review_report("ready")

    original = build_analysis_report(
        requirement_spec=(requirement_spec()),
        candidate_set=candidate_set(),
        review_report=review,
        branch_results=results,
    )

    changed_impact = results[0].model_copy(
        update={
            "findings": (
                *results[0].findings,
                finding(
                    "IMP-003",
                    ac_id="AC-001",
                    path=("order_app/api.py"),
                    priority="minor",
                ),
            )
        }
    )

    changed_results = (
        changed_impact,
        results[1],
        results[2],
    )

    changed_review = review.model_copy(update={"branches": (branch_summaries(changed_results))})

    changed = build_analysis_report(
        requirement_spec=(requirement_spec()),
        candidate_set=candidate_set(),
        review_report=changed_review,
        branch_results=changed_results,
    )

    assert original.report_digest != changed.report_digest

    assert original.report_id != changed.report_id


def test_mismatched_review_digest_is_rejected() -> None:
    """审核结果来自其他候选快照时不能生成报告。"""

    review, results = review_report("ready")

    mismatched = review.model_copy(update={"reviewed_digests": (digests(alternate=True))})

    with pytest.raises(
        ValueError,
        match="ReviewReport",
    ):
        build_analysis_report(
            requirement_spec=(requirement_spec()),
            candidate_set=(candidate_set()),
            review_report=mismatched,
            branch_results=results,
        )


def test_mismatched_branch_summary_is_rejected() -> None:
    """审核摘要与真实 BranchResult 不一致时不能静默渲染。"""

    review, results = review_report("ready")

    wrong_summary = review.branches[0].model_copy(update={"finding_count": 99})

    mismatched = review.model_copy(
        update={
            "branches": (
                wrong_summary,
                *review.branches[1:],
            )
        }
    )

    with pytest.raises(
        ValueError,
        match="impact 分支摘要",
    ):
        build_analysis_report(
            requirement_spec=(requirement_spec()),
            candidate_set=(candidate_set()),
            review_report=mismatched,
            branch_results=results,
        )


def test_report_is_plain_json_serializable() -> None:
    """统一报告必须能直接作为 API JSON 响应。"""

    review, results = review_report("ready")

    report = build_analysis_report(
        requirement_spec=(requirement_spec()),
        candidate_set=candidate_set(),
        review_report=review,
        branch_results=results,
    )

    payload = report.model_dump(mode="json")

    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )

    assert report.report_id in rendered

    assert "订单所属用户可以取消待支付订单" in rendered
