"""构建稳定 BranchResult 的确定性公共引擎。

本模块没有 LLM 调用。它负责：

1. 从 CandidateSet 提取三个并行分支共同的源码文件分母；
2. 计算 coverage；
3. 根据 coverage 决定 success、degraded 或 failed；
4. 稳定排序 findings；
5. 把未分析原因和异常转换为结构化结果。
"""

from collections.abc import Iterable, Mapping

from app.schemas.branch_result import (
    AnalysisBranch,
    BranchError,
    BranchFinding,
    BranchResult,
    BranchStatus,
    Coverage,
    CoverageMissing,
    CoverageUnit,
)
from app.schemas.contracts import CandidateSet
from app.schemas.guard import GuardDigests

PRIORITY_RANK = {
    "blocker": 0,
    "major": 1,
    "minor": 2,
    "info": 3,
}


def source_file_assignments(
    candidate_set: CandidateSet,
) -> tuple[str, ...]:
    """返回 CandidateSet 中三个并行分支共享的源码文件分母。"""

    return tuple(
        sorted({file_ref.path for file_ref in candidate_set.files if file_ref.kind == "source"})
    )


def stable_sort_findings(
    findings: Iterable[BranchFinding],
) -> tuple[BranchFinding, ...]:
    """按契约固定的优先级、路径、符号和 ID 排序。"""

    return tuple(
        sorted(
            findings,
            key=lambda finding: (
                PRIORITY_RANK[finding.priority],
                finding.path,
                finding.symbol or "",
                finding.finding_id,
            ),
        )
    )


def build_coverage(
    assigned_items: Iterable[str],
    analyzed_items: Iterable[str],
    *,
    unit: CoverageUnit,
    missing_reasons: Mapping[
        str,
        str,
    ]
    | None = None,
) -> Coverage:
    """根据分配项和已分析项构造严格一致的 Coverage。"""

    assigned = tuple(sorted(set(assigned_items)))

    analyzed = tuple(sorted(set(analyzed_items)))

    assigned_set = set(assigned)
    analyzed_set = set(analyzed)

    unknown_items = analyzed_set - assigned_set

    if unknown_items:
        rendered = ", ".join(sorted(unknown_items))

        raise ValueError(f"analyzed_items 包含未分配项：{rendered}")

    if not assigned:
        return Coverage(
            assigned=0,
            analyzed=0,
            ratio=1.0,
            unit=unit,
            note="no_items",
        )

    reasons = missing_reasons or {}

    missing_paths = tuple(sorted(assigned_set - analyzed_set))

    missing = tuple(
        CoverageMissing(
            path=path,
            reason=reasons.get(
                path,
                "分支未返回该分析单元的明确结论。",
            ),
        )
        for path in missing_paths
    )

    return Coverage(
        assigned=len(assigned),
        analyzed=len(analyzed),
        ratio=round(
            len(analyzed) / len(assigned),
            3,
        ),
        unit=unit,
        missing=missing,
    )


def status_from_coverage(
    coverage: Coverage,
) -> BranchStatus:
    """根据覆盖率得到唯一合法分支状态。"""

    if coverage.assigned == 0 or coverage.ratio == 1.0:
        return "success"

    if coverage.ratio == 0.0:
        return "failed"

    return "degraded"


def build_branch_result(
    *,
    branch: AnalysisBranch,
    assigned_items: Iterable[str],
    analyzed_items: Iterable[str],
    unit: CoverageUnit,
    findings: Iterable[BranchFinding],
    consumed_digests: GuardDigests,
    missing_reasons: Mapping[
        str,
        str,
    ]
    | None = None,
    errors: Iterable[BranchError] = (),
    degraded_reasons: Iterable[str] = (),
    elapsed_ms: int = 0,
) -> BranchResult:
    """构造排序稳定、状态与 coverage 一致的 BranchResult。"""

    coverage = build_coverage(
        assigned_items,
        analyzed_items,
        unit=unit,
        missing_reasons=missing_reasons,
    )

    status = status_from_coverage(coverage)

    normalized_errors = tuple(errors)

    normalized_degraded_reasons = tuple(dict.fromkeys(degraded_reasons))

    if status == "degraded" and not normalized_degraded_reasons:
        normalized_degraded_reasons = (
            (
                f"{branch} 分支仅覆盖 "
                f"{coverage.analyzed}/"
                f"{coverage.assigned} "
                f"个{coverage.unit}分析单元。"
            ),
        )

    if status == "failed" and not normalized_errors:
        normalized_errors = (
            BranchError(
                code="NO_ITEMS_ANALYZED",
                message=(f"{branch} 分支未完成任何已分配的{coverage.unit}分析单元。"),
            ),
        )

    return BranchResult(
        branch=branch,
        status=status,
        findings=stable_sort_findings(findings),
        errors=normalized_errors,
        coverage=coverage,
        consumed_digests=(consumed_digests),
        degraded_reasons=(normalized_degraded_reasons if status == "degraded" else ()),
        elapsed_ms=elapsed_ms,
    )


def build_candidate_set_branch_result(
    *,
    branch: AnalysisBranch,
    candidate_set: CandidateSet,
    analyzed_paths: Iterable[str],
    findings: Iterable[BranchFinding],
    consumed_digests: GuardDigests,
    missing_reasons: Mapping[
        str,
        str,
    ]
    | None = None,
    errors: Iterable[BranchError] = (),
    degraded_reasons: Iterable[str] = (),
    elapsed_ms: int = 0,
) -> BranchResult:
    """使用 CandidateSet 源码文件作为并行分支 coverage 分母。"""

    return build_branch_result(
        branch=branch,
        assigned_items=(source_file_assignments(candidate_set)),
        analyzed_items=analyzed_paths,
        unit="file",
        findings=findings,
        consumed_digests=(consumed_digests),
        missing_reasons=missing_reasons,
        errors=errors,
        degraded_reasons=(degraded_reasons),
        elapsed_ms=elapsed_ms,
    )


def build_failed_branch_result(
    *,
    branch: AnalysisBranch,
    assigned_items: Iterable[str],
    unit: CoverageUnit,
    consumed_digests: GuardDigests,
    error: Exception,
    elapsed_ms: int = 0,
) -> BranchResult:
    """把分支抛出的异常转换为结构化 failed 结果。"""

    assigned = tuple(sorted(set(assigned_items)))

    # 没有分配项时，契约要求 success + no_items。
    # 异常仍进入 errors 供诊断使用，但不能伪造 failed coverage。
    if not assigned:
        return build_branch_result(
            branch=branch,
            assigned_items=(),
            analyzed_items=(),
            unit=unit,
            findings=(),
            consumed_digests=(consumed_digests),
            errors=(
                BranchError(
                    code=(type(error).__name__),
                    message=(str(error) or type(error).__name__),
                ),
            ),
            elapsed_ms=elapsed_ms,
        )

    return build_branch_result(
        branch=branch,
        assigned_items=assigned,
        analyzed_items=(),
        unit=unit,
        findings=(),
        consumed_digests=(consumed_digests),
        errors=(
            BranchError(
                code=type(error).__name__,
                message=(str(error) or type(error).__name__),
            ),
        ),
        elapsed_ms=elapsed_ms,
    )
