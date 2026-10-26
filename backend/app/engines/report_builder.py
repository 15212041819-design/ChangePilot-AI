"""把已审核的结构化结论渲染为确定性的统一分析报告。"""

import hashlib
import json
from collections import Counter
from collections.abc import Sequence

from app.graph.parallel_analysis import (
    BRANCH_ORDER,
)
from app.schemas.analysis_report import (
    AnalysisReport,
    CandidateReportSnapshot,
    ReportOverview,
    RequirementReportSnapshot,
)
from app.schemas.branch_result import (
    BranchResult,
)
from app.schemas.contracts import (
    CandidateSet,
    RequirementSpec,
)
from app.schemas.guard import GuardDigests
from app.schemas.review import ReviewReport


def _expected_digests(
    candidate_set: CandidateSet,
) -> GuardDigests:
    """从冻结候选集构造报告必须继承的三项摘要。"""

    return GuardDigests(
        set_digest=candidate_set.set_digest,
        spec_digest=candidate_set.spec_digest,
        index_digest=(candidate_set.index_digest),
    )


def _ordered_results(
    results: Sequence[BranchResult],
) -> tuple[BranchResult, ...]:
    """验证三路结果完整且唯一，并返回稳定顺序。"""

    counts = Counter(result.branch for result in results)

    duplicates = sorted(branch for branch, count in counts.items() if count > 1)

    missing = [branch for branch in BRANCH_ORDER if counts[branch] == 0]

    if duplicates:
        raise ValueError(f"报告输入包含重复分支：{', '.join(duplicates)}")

    if missing:
        raise ValueError(f"报告输入缺少分支：{', '.join(missing)}")

    by_branch = {result.branch: result for result in results}

    return tuple(by_branch[branch] for branch in BRANCH_ORDER)


def _validate_inputs(
    *,
    requirement_spec: RequirementSpec,
    candidate_set: CandidateSet,
    review_report: ReviewReport,
    branch_results: tuple[
        BranchResult,
        ...,
    ],
) -> tuple[str, ...]:
    """确保报告使用同一需求版本、候选快照和审核结果。"""

    expected = _expected_digests(candidate_set)

    if requirement_spec.spec_digest != candidate_set.spec_digest:
        raise ValueError("RequirementSpec 与 CandidateSet 的 spec_digest 不一致。")

    if review_report.reviewed_digests != expected:
        raise ValueError("ReviewReport 与 CandidateSet 的 digest 不一致。")

    for result in branch_results:
        if result.consumed_digests != expected:
            raise ValueError(f"{result.branch} 分支与 CandidateSet 的 digest 不一致。")

    active_ac_ids = tuple(
        sorted(
            criterion.id
            for criterion in (requirement_spec.acceptance_criteria)
            if criterion.status == "active"
        )
    )

    coverage_ac_ids = tuple(row.ac_id for row in (review_report.acceptance_coverage))

    if coverage_ac_ids != active_ac_ids:
        raise ValueError("ReviewReport 的 AC 覆盖行 与 active AC 不一致。")

    summaries = {summary.branch: summary for summary in (review_report.branches)}

    if tuple(summaries) != BRANCH_ORDER:
        raise ValueError("ReviewReport 的分支摘要 顺序不正确。")

    for result in branch_results:
        summary = summaries[result.branch]

        blocker_count = sum(finding.priority == "blocker" for finding in result.findings)

        if (
            summary.status != result.status
            or summary.finding_count != len(result.findings)
            or summary.blocker_count != blocker_count
            or summary.coverage_assigned != result.coverage.assigned
            or summary.coverage_analyzed != result.coverage.analyzed
            or summary.coverage_ratio != result.coverage.ratio
        ):
            raise ValueError(f"{result.branch} 分支摘要与 BranchResult 不一致。")

    return active_ac_ids


def _headline(
    status: str,
) -> str:
    """生成不依赖 LLM 的稳定首屏结论。"""

    return {
        "ready": ("分析完成，证据完整，可以进入实施计划阶段。"),
        "gated": ("分析完成，但存在需要补充或人工确认的门禁。"),
        "blocked": ("分析未通过审核，解除阻断项前不能进入实施。"),
    }[status]


def _canonical_digest(
    payload: dict[str, object],
) -> str:
    """对报告正文执行稳定 JSON 序列化和 SHA-256。"""

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return "sha256:" + hashlib.sha256(serialized).hexdigest()


def build_analysis_report(
    *,
    requirement_spec: RequirementSpec,
    candidate_set: CandidateSet,
    review_report: ReviewReport,
    branch_results: Sequence[BranchResult],
) -> AnalysisReport:
    """构建前端可直接展示、可复现且可追踪的统一报告。"""

    ordered_results = _ordered_results(branch_results)

    active_ac_ids = _validate_inputs(
        requirement_spec=requirement_spec,
        candidate_set=candidate_set,
        review_report=review_report,
        branch_results=ordered_results,
    )

    requirement_snapshot = RequirementReportSnapshot(
        spec_id=(requirement_spec.spec_id),
        spec_revision=(requirement_spec.spec_revision),
        summary=(requirement_spec.summary),
        change_type=(requirement_spec.change_type),
        active_acceptance_ids=(active_ac_ids),
    )

    ordered_paths = tuple(sorted(file_ref.path for file_ref in (candidate_set.files)))

    candidate_snapshot = CandidateReportSnapshot(
        set_id=candidate_set.set_id,
        commit=candidate_set.commit,
        file_count=len(candidate_set.files),
        source_file_count=sum(file_ref.kind == "source" for file_ref in (candidate_set.files)),
        truncated=(candidate_set.truncated),
        paths=ordered_paths,
    )

    all_findings = tuple(finding for result in ordered_results for finding in result.findings)

    # blocker 和 gate 进入门禁区。
    gates = tuple(issue for issue in review_report.issues if issue.level != "warning")

    # warning 只进入提示区，不影响最终状态。
    warnings = tuple(issue for issue in review_report.issues if issue.level == "warning")

    overview = ReportOverview(
        status=review_report.status,
        can_implement=(review_report.status == "ready"),
        requires_human_review=(review_report.status != "ready"),
        headline=_headline(review_report.status),
        active_acceptance_count=len(active_ac_ids),
        implementation_covered_count=sum(
            row.implementation_covered for row in (review_report.acceptance_coverage)
        ),
        test_covered_count=sum(row.test_covered for row in (review_report.acceptance_coverage)),
        finding_count=len(all_findings),
        blocker_finding_count=sum(finding.priority == "blocker" for finding in all_findings),
        major_finding_count=sum(finding.priority == "major" for finding in all_findings),
        gate_count=len(gates),
        branch_summaries=(review_report.branches),
    )

    reviewed_digests = _expected_digests(candidate_set)

    # report_id 和 report_digest 不参与自身摘要，
    # 其余结构化内容全部参与计算。
    payload: dict[str, object] = {
        "report_version": "1.0.0",
        "requirement": (requirement_snapshot.model_dump(mode="json")),
        "candidate_set": (candidate_snapshot.model_dump(mode="json")),
        "overview": (overview.model_dump(mode="json")),
        "gates": [gate.model_dump(mode="json") for gate in gates],
        "warnings": [warning.model_dump(mode="json") for warning in warnings],
        "acceptance_coverage": [
            row.model_dump(mode="json") for row in (review_report.acceptance_coverage)
        ],
        "branch_results": [result.model_dump(mode="json") for result in ordered_results],
        "reviewed_digests": (reviewed_digests.model_dump(mode="json")),
    }

    report_digest = _canonical_digest(payload)

    digest_hex = report_digest.removeprefix("sha256:")

    return AnalysisReport(
        report_id=(f"AR-{digest_hex[:12].upper()}"),
        report_version="1.0.0",
        requirement=(requirement_snapshot),
        candidate_set=(candidate_snapshot),
        overview=overview,
        gates=gates,
        warnings=warnings,
        acceptance_coverage=(review_report.acceptance_coverage),
        branch_results=(ordered_results),
        reviewed_digests=(reviewed_digests),
        report_digest=report_digest,
    )
