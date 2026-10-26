"""Impact、Test、Risk 三路结果的确定性审核引擎。

本模块只审核结论之间的关系和验收标准覆盖情况，不重复执行路径、符号、
行号真实性检查。仓库事实真实性仍由 Analysis Guard 负责。
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

from app.graph.parallel_analysis import BRANCH_ORDER
from app.schemas.branch_result import AnalysisBranch, BranchFinding, BranchResult
from app.schemas.contracts import ChangePoint, RequirementSpec
from app.schemas.guard import GuardDigests
from app.schemas.review import (
    AcceptanceCoverage,
    BranchReviewSummary,
    ReviewIssue,
    ReviewReport,
)

BRANCH_FAILED_RULE = "REVIEW-BRANCH-FAILED"
BRANCH_DEGRADED_RULE = "REVIEW-BRANCH-DEGRADED"
DIGEST_MISMATCH_RULE = "REVIEW-DIGEST-MISMATCH"
UNKNOWN_AC_RULE = "REVIEW-UNKNOWN-AC"
FINDING_COLLISION_RULE = "REVIEW-FINDING-ID-COLLISION"
BLOCKER_FINDING_RULE = "REVIEW-BLOCKER-FINDING"
IMPLEMENTATION_GAP_RULE = "REVIEW-IMPLEMENTATION-GAP"
TEST_GAP_RULE = "REVIEW-TEST-GAP"


def _ordered_results(
    results: Sequence[BranchResult],
) -> tuple[BranchResult, ...]:
    """校验三路结果完整且唯一，并按固定顺序返回。"""

    counts = Counter(result.branch for result in results)

    duplicates = sorted(branch for branch, count in counts.items() if count > 1)

    missing = [branch for branch in BRANCH_ORDER if counts[branch] == 0]

    if duplicates:
        raise ValueError(f"审核输入包含重复分支：{', '.join(duplicates)}")

    if missing:
        raise ValueError(f"审核输入缺少分支：{', '.join(missing)}")

    unexpected = sorted(set(counts) - set(BRANCH_ORDER))

    if unexpected:
        raise ValueError(f"审核输入包含未知分支：{', '.join(unexpected)}")

    by_branch = {result.branch: result for result in results}

    return tuple(by_branch[branch] for branch in BRANCH_ORDER)


def _active_ac_ids(
    spec: RequirementSpec,
) -> tuple[str, ...]:
    """只返回当前仍然有效的验收标准 ID。"""

    return tuple(
        sorted(
            criterion.id for criterion in spec.acceptance_criteria if criterion.status == "active"
        )
    )


def _all_findings(
    results: tuple[BranchResult, ...],
) -> tuple[
    tuple[AnalysisBranch, BranchFinding],
    ...,
]:
    """按固定分支顺序展开所有 finding。"""

    return tuple(
        (
            result.branch,
            finding,
        )
        for result in results
        for finding in result.findings
    )


def _unknown_ac_issue(
    *,
    known_ac_ids: set[str],
    change_points: tuple[ChangePoint, ...],
    findings: tuple[
        tuple[AnalysisBranch, BranchFinding],
        ...,
    ],
) -> ReviewIssue | None:
    """检查 ChangePoint 和 finding 是否引用了不存在或非 active 的 AC。"""

    referenced = {ac_id for change_point in change_points for ac_id in change_point.ac_ids}

    referenced.update(ac_id for _, finding in findings for ac_id in finding.ac_ids)

    unknown = tuple(sorted(referenced - known_ac_ids))

    if not unknown:
        return None

    return ReviewIssue(
        code=UNKNOWN_AC_RULE,
        level="blocker",
        message=("分析结果引用了 RequirementSpec 中不存在或非 active 的验收标准。"),
        ac_ids=unknown,
    )


def _finding_collision_issues(
    findings: tuple[
        tuple[AnalysisBranch, BranchFinding],
        ...,
    ],
) -> tuple[ReviewIssue, ...]:
    """同一 finding_id 出现不同内容时视为跨分支结论冲突。"""

    grouped: dict[
        str,
        list[
            tuple[
                AnalysisBranch,
                BranchFinding,
            ]
        ],
    ] = defaultdict(list)

    for branch, finding in findings:
        grouped[finding.finding_id].append(
            (
                branch,
                finding,
            )
        )

    issues: list[ReviewIssue] = []

    for finding_id in sorted(grouped):
        entries = grouped[finding_id]

        if len(entries) < 2:
            continue

        # 相同 ID 的 finding 必须表达完全相同的结构化结论。
        signatures = {
            (
                finding.priority,
                finding.path,
                finding.symbol,
                finding.summary,
                tuple(sorted(finding.ac_ids)),
                tuple(sorted(finding.evidence_ids)),
                tuple(sorted(finding.rule_ids)),
            )
            for _, finding in entries
        }

        if len(signatures) == 1:
            continue

        branches = tuple(
            branch
            for branch in BRANCH_ORDER
            if any(entry_branch == branch for entry_branch, _ in entries)
        )

        issues.append(
            ReviewIssue(
                code=FINDING_COLLISION_RULE,
                level="blocker",
                message=(f"finding_id={finding_id} 在多个分支中表示了不同结论。"),
                finding_ids=(finding_id,),
                branches=branches,
            )
        )

    return tuple(issues)


def _acceptance_coverage(
    *,
    active_ac_ids: tuple[str, ...],
    change_points: tuple[ChangePoint, ...],
    results: tuple[BranchResult, ...],
) -> tuple[AcceptanceCoverage, ...]:
    """构造每条 active AC 的实施、测试和风险证据映射。"""

    findings_by_branch = {result.branch: result.findings for result in results}

    rows: list[AcceptanceCoverage] = []

    for ac_id in active_ac_ids:
        cp_ids = tuple(
            sorted(
                change_point.cp_id for change_point in change_points if ac_id in change_point.ac_ids
            )
        )

        finding_ids: dict[
            AnalysisBranch,
            tuple[str, ...],
        ] = {
            branch: tuple(
                sorted(
                    finding.finding_id
                    for finding in findings_by_branch[branch]
                    if ac_id in finding.ac_ids
                )
            )
            for branch in BRANCH_ORDER
        }

        rows.append(
            AcceptanceCoverage(
                ac_id=ac_id,
                change_point_ids=cp_ids,
                impact_finding_ids=(finding_ids["impact"]),
                test_finding_ids=(finding_ids["test"]),
                risk_finding_ids=(finding_ids["risk"]),
                implementation_covered=bool(cp_ids),
                test_covered=bool(finding_ids["test"]),
            )
        )

    return tuple(rows)


def _coverage_issues(
    coverage: tuple[AcceptanceCoverage, ...],
) -> tuple[ReviewIssue, ...]:
    """把实施证据缺口和测试覆盖缺口转换为门禁。"""

    implementation_gaps = tuple(row.ac_id for row in coverage if not row.implementation_covered)

    test_gaps = tuple(row.ac_id for row in coverage if not row.test_covered)

    issues: list[ReviewIssue] = []

    if implementation_gaps:
        issues.append(
            ReviewIssue(
                code=IMPLEMENTATION_GAP_RULE,
                level="blocker",
                message=("部分验收标准没有通过 Guard 的 ChangePoint，不能生成可执行实施步骤。"),
                ac_ids=implementation_gaps,
            )
        )

    if test_gaps:
        issues.append(
            ReviewIssue(
                code=TEST_GAP_RULE,
                level="gate",
                message=("部分验收标准没有测试分支证据，需要补充测试策略或人工确认。"),
                ac_ids=test_gaps,
                branches=("test",),
            )
        )

    return tuple(issues)


def _branch_issues(
    results: tuple[BranchResult, ...],
) -> tuple[ReviewIssue, ...]:
    """把分支失败、降级及 blocker finding 转换为审核问题。"""

    issues: list[ReviewIssue] = []

    for result in results:
        if result.status == "failed":
            issues.append(
                ReviewIssue(
                    code=BRANCH_FAILED_RULE,
                    level="blocker",
                    message=(f"{result.branch} 分支执行失败，不能形成完整分析结论。"),
                    branches=(result.branch,),
                )
            )

        elif result.status == "degraded":
            issues.append(
                ReviewIssue(
                    code=BRANCH_DEGRADED_RULE,
                    level="gate",
                    message=(
                        f"{result.branch} 分支仅覆盖 "
                        f"{result.coverage.analyzed}/"
                        f"{result.coverage.assigned} 个"
                        f"{result.coverage.unit}分析单元。"
                    ),
                    branches=(result.branch,),
                )
            )

        blockers = tuple(
            finding.finding_id for finding in result.findings if finding.priority == "blocker"
        )

        if blockers:
            issues.append(
                ReviewIssue(
                    code=BLOCKER_FINDING_RULE,
                    level="blocker",
                    message=(f"{result.branch} 分支存在 blocker 级结论。"),
                    finding_ids=blockers,
                    branches=(result.branch,),
                )
            )

    return tuple(issues)


def _digest_issues(
    results: tuple[BranchResult, ...],
    expected: GuardDigests,
) -> tuple[ReviewIssue, ...]:
    """审核阶段再次确认所有分支消费了同一冻结上下文。"""

    mismatched = tuple(result.branch for result in results if result.consumed_digests != expected)

    if not mismatched:
        return ()

    return (
        ReviewIssue(
            code=DIGEST_MISMATCH_RULE,
            level="blocker",
            message=("一个或多个分支消费的 digest 与审核输入不一致。"),
            branches=mismatched,
        ),
    )


def _branch_summaries(
    results: tuple[BranchResult, ...],
) -> tuple[BranchReviewSummary, ...]:
    """生成供统一报告直接渲染的稳定分支摘要。"""

    return tuple(
        BranchReviewSummary(
            branch=result.branch,
            status=result.status,
            finding_count=len(result.findings),
            blocker_count=sum(finding.priority == "blocker" for finding in result.findings),
            coverage_assigned=(result.coverage.assigned),
            coverage_analyzed=(result.coverage.analyzed),
            coverage_ratio=(result.coverage.ratio),
        )
        for result in results
    )


def _review_status(
    issues: Iterable[ReviewIssue],
) -> str:
    """根据最严重问题确定 ready、gated 或 blocked。"""

    levels = {issue.level for issue in issues}

    if "blocker" in levels:
        return "blocked"

    if "gate" in levels:
        return "gated"

    return "ready"


def review_parallel_results(
    *,
    requirement_spec: RequirementSpec,
    change_points: Sequence[ChangePoint],
    branch_results: Sequence[BranchResult],
    expected_digests: GuardDigests,
) -> ReviewReport:
    """审核三路结果的一致性、AC 覆盖和执行门禁。"""

    results = _ordered_results(branch_results)

    validated_change_points = tuple(change_points)

    active_ac_ids = _active_ac_ids(requirement_spec)

    active_ac_set = set(active_ac_ids)

    findings = _all_findings(results)

    coverage = _acceptance_coverage(
        active_ac_ids=active_ac_ids,
        change_points=validated_change_points,
        results=results,
    )

    issues: list[ReviewIssue] = []

    issues.extend(
        _digest_issues(
            results,
            expected_digests,
        )
    )

    issues.extend(_branch_issues(results))

    issues.extend(_finding_collision_issues(findings))

    unknown_ac = _unknown_ac_issue(
        known_ac_ids=active_ac_set,
        change_points=validated_change_points,
        findings=findings,
    )

    if unknown_ac is not None:
        issues.append(unknown_ac)

    issues.extend(_coverage_issues(coverage))

    # 固定问题输出顺序，避免并行完成顺序影响报告。
    normalized_issues = tuple(
        sorted(
            issues,
            key=lambda issue: (
                {
                    "blocker": 0,
                    "gate": 1,
                    "warning": 2,
                }[issue.level],
                issue.code,
                issue.ac_ids,
                issue.finding_ids,
                issue.branches,
            ),
        )
    )

    return ReviewReport(
        status=_review_status(normalized_issues),
        issues=normalized_issues,
        acceptance_coverage=coverage,
        branches=_branch_summaries(results),
        reviewed_digests=expected_digests,
    )
