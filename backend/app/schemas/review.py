"""三路分析结果审核阶段使用的稳定契约。"""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.branch_result import AnalysisBranch, BranchStatus
from app.schemas.contracts import FrozenContractModel
from app.schemas.guard import GuardDigests

ReviewStatus = Literal[
    "ready",
    "gated",
    "blocked",
]

ReviewIssueLevel = Literal[
    "blocker",
    "gate",
    "warning",
]


class ReviewIssue(FrozenContractModel):
    """审核阶段发现的一项覆盖缺口、冲突或门禁原因。"""

    code: str = Field(min_length=1)

    level: ReviewIssueLevel

    message: str = Field(min_length=1)

    ac_ids: tuple[str, ...] = ()

    finding_ids: tuple[str, ...] = ()

    branches: tuple[AnalysisBranch, ...] = ()


class AcceptanceCoverage(FrozenContractModel):
    """一条 active AC 在实施、测试和风险三条证据链中的覆盖情况。"""

    ac_id: str = Field(pattern=r"^AC-\d{3}$")

    change_point_ids: tuple[str, ...] = ()

    impact_finding_ids: tuple[str, ...] = ()

    test_finding_ids: tuple[str, ...] = ()

    risk_finding_ids: tuple[str, ...] = ()

    implementation_covered: bool

    test_covered: bool


class BranchReviewSummary(FrozenContractModel):
    """统一报告可直接使用的单分支审核摘要。"""

    branch: AnalysisBranch

    status: BranchStatus

    finding_count: int = Field(ge=0)

    blocker_count: int = Field(ge=0)

    coverage_assigned: int = Field(ge=0)

    coverage_analyzed: int = Field(ge=0)

    coverage_ratio: float = Field(ge=0.0, le=1.0)


class ReviewReport(FrozenContractModel):
    """结果审核引擎输出的最终门禁报告。"""

    status: ReviewStatus

    issues: tuple[ReviewIssue, ...] = ()

    acceptance_coverage: tuple[AcceptanceCoverage, ...]

    branches: tuple[BranchReviewSummary, ...]

    reviewed_digests: GuardDigests

    review_version: str = "1.0.0"

    @model_validator(mode="after")
    def validate_status_against_issues(self) -> "ReviewReport":
        """禁止最终状态与审核问题级别互相矛盾。"""

        levels = {issue.level for issue in self.issues}

        if "blocker" in levels:
            expected = "blocked"
        elif "gate" in levels:
            expected = "gated"
        else:
            expected = "ready"

        if self.status != expected:
            raise ValueError(
                f"review.status 与 issues 不一致：期望 {expected}，实际 {self.status}。"
            )

        return self
