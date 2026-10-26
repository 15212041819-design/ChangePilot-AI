"""供 API 和前端直接消费的统一分析报告契约。"""

from pydantic import Field, model_validator

from app.schemas.branch_result import BranchResult
from app.schemas.contracts import (
    HASH_PATTERN,
    ChangeType,
    FrozenContractModel,
)
from app.schemas.guard import GuardDigests
from app.schemas.review import (
    AcceptanceCoverage,
    BranchReviewSummary,
    ReviewIssue,
    ReviewStatus,
)


class RequirementReportSnapshot(FrozenContractModel):
    """报告需要展示的最小需求快照。"""

    spec_id: str = Field(pattern=r"^RS-\d{4}$")

    spec_revision: int = Field(ge=1)

    summary: str = Field(min_length=1)

    change_type: ChangeType

    active_acceptance_ids: tuple[
        str,
        ...,
    ]


class CandidateReportSnapshot(FrozenContractModel):
    """报告对应的冻结候选集快照。"""

    set_id: str = Field(pattern=r"^CS-\d{4}$")

    commit: str = Field(min_length=1)

    file_count: int = Field(ge=0)

    source_file_count: int = Field(ge=0)

    truncated: bool

    paths: tuple[str, ...]


class ReportOverview(FrozenContractModel):
    """前端首屏展示的一页摘要。"""

    status: ReviewStatus

    can_implement: bool

    requires_human_review: bool

    headline: str = Field(min_length=1)

    active_acceptance_count: int = Field(ge=0)

    implementation_covered_count: int = Field(ge=0)

    test_covered_count: int = Field(ge=0)

    finding_count: int = Field(ge=0)

    blocker_finding_count: int = Field(ge=0)

    major_finding_count: int = Field(ge=0)

    gate_count: int = Field(ge=0)

    branch_summaries: tuple[
        BranchReviewSummary,
        ...,
    ]

    @model_validator(mode="after")
    def validate_status_flags(
        self,
    ) -> "ReportOverview":
        """最终状态必须与是否可实施、是否需人工审核一致。"""

        expected_can_implement = self.status == "ready"

        expected_human_review = self.status != "ready"

        if self.can_implement != expected_can_implement:
            raise ValueError("overview.can_implement 与 status 不一致。")

        if self.requires_human_review != expected_human_review:
            raise ValueError("overview.requires_human_review 与 status 不一致。")

        return self


class AnalysisReport(FrozenContractModel):
    """统一报告：摘要在 overview，详细证据保留在其余字段。"""

    report_id: str = Field(pattern=r"^AR-[0-9A-F]{12}$")

    report_version: str = "1.0.0"

    requirement: RequirementReportSnapshot

    candidate_set: CandidateReportSnapshot

    overview: ReportOverview

    gates: tuple[
        ReviewIssue,
        ...,
    ] = ()

    warnings: tuple[
        ReviewIssue,
        ...,
    ] = ()

    acceptance_coverage: tuple[
        AcceptanceCoverage,
        ...,
    ]

    branch_results: tuple[
        BranchResult,
        ...,
    ]

    reviewed_digests: GuardDigests

    report_digest: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_report_identity_and_gates(
        self,
    ) -> "AnalysisReport":
        """锁定报告 ID、摘要状态与门禁内容之间的关系。"""

        digest_hex = self.report_digest.removeprefix("sha256:")

        expected_id = f"AR-{digest_hex[:12].upper()}"

        if self.report_id != expected_id:
            raise ValueError("report_id 与 report_digest 不一致。")

        gate_levels = {gate.level for gate in self.gates}

        if "warning" in gate_levels:
            raise ValueError("warning 不能进入 gates。")

        if self.overview.status == "ready" and self.gates:
            raise ValueError("ready 报告不能包含门禁。")

        if self.overview.status == "gated" and gate_levels != {"gate"}:
            raise ValueError("gated 报告必须且只能包含 gate 级门禁。")

        if self.overview.status == "blocked" and "blocker" not in gate_levels:
            raise ValueError("blocked 报告必须包含 blocker 门禁。")

        return self
