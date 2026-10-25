"""并行分析分支共享的输出、覆盖率和错误契约。

Impact、Test 和 Risk 三个分支必须返回同一种 BranchResult，
这样 fan-in、Guard 和统一报告不需要猜测每个分支的私有字段。
"""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.contracts import FrozenContractModel
from app.schemas.guard import GuardDigests

AnalysisBranch = Literal[
    "impact",
    "test",
    "risk",
]

BranchStatus = Literal[
    "success",
    "degraded",
    "failed",
]

CoverageUnit = Literal[
    "file",
    "symbol",
]

FindingPriority = Literal[
    "blocker",
    "major",
    "minor",
    "info",
]


class CoverageMissing(FrozenContractModel):
    """一个未完成分析的文件或符号及其原因。"""

    path: str = Field(
        min_length=1,
    )

    reason: str = Field(
        min_length=1,
    )


class Coverage(FrozenContractModel):
    """一个分支对分配分析单元的实际覆盖情况。"""

    assigned: int = Field(
        ge=0,
    )

    analyzed: int = Field(
        ge=0,
    )

    ratio: float = Field(
        ge=0.0,
        le=1.0,
    )

    unit: CoverageUnit

    missing: tuple[CoverageMissing, ...] = ()

    note: str | None = None

    @model_validator(mode="after")
    def validate_coverage_math(
        self,
    ) -> "Coverage":
        """拒绝分子、分母、比例和 missing 数量互相矛盾的对象。"""

        if self.analyzed > self.assigned:
            raise ValueError("coverage.analyzed 不能大于 assigned。")

        if self.assigned == 0:
            if self.analyzed != 0 or self.ratio != 1.0:
                raise ValueError("assigned=0 时 analyzed 必须为 0，且 ratio 必须为 1.0。")

            if self.note != "no_items":
                raise ValueError("assigned=0 时 note 必须为 no_items。")

            if self.missing:
                raise ValueError("assigned=0 时 missing 必须为空。")

            return self

        expected_ratio = round(
            self.analyzed / self.assigned,
            3,
        )

        if self.ratio != expected_ratio:
            raise ValueError("coverage.ratio 与 analyzed/assigned 不一致。")

        if len(self.missing) != self.assigned - self.analyzed:
            raise ValueError("coverage.missing 数量与未分析数量不一致。")

        return self


class BranchFinding(FrozenContractModel):
    """三个分析分支都可以输出的最小 finding 结构。"""

    finding_id: str = Field(
        min_length=1,
    )

    priority: FindingPriority

    path: str = Field(
        min_length=1,
    )

    symbol: str | None = None

    summary: str = Field(
        min_length=1,
    )

    ac_ids: tuple[str, ...] = ()

    evidence_ids: tuple[str, ...] = ()

    rule_ids: tuple[str, ...] = ()


class BranchError(FrozenContractModel):
    """分支捕获的一条结构化错误。"""

    code: str = Field(
        min_length=1,
    )

    message: str = Field(
        min_length=1,
    )

    path: str | None = None


class BranchResult(FrozenContractModel):
    """Impact、Test 和 Risk 分支统一返回的结果。"""

    branch: AnalysisBranch

    status: BranchStatus

    findings: tuple[BranchFinding, ...] = ()

    errors: tuple[BranchError, ...] = ()

    coverage: Coverage

    consumed_digests: GuardDigests

    degraded_reasons: tuple[str, ...] = ()

    elapsed_ms: int = Field(
        default=0,
        ge=0,
    )

    @model_validator(mode="after")
    def validate_status_against_coverage(
        self,
    ) -> "BranchResult":
        """保证状态、覆盖率和错误信息不会互相矛盾。"""

        if self.coverage.assigned == 0 or self.coverage.ratio == 1.0:
            expected_status = "success"

        elif self.coverage.ratio == 0.0:
            expected_status = "failed"

        else:
            expected_status = "degraded"

        if self.status != expected_status:
            raise ValueError(
                f"branch.status 与 coverage 不一致：期望 {expected_status}，实际 {self.status}。"
            )

        if self.status == "degraded" and not self.degraded_reasons:
            raise ValueError("status=degraded 时 degraded_reasons 不能为空。")

        if self.status == "failed" and not self.errors:
            raise ValueError("status=failed 时 errors 不能为空。")

        if self.status == "success" and self.degraded_reasons:
            raise ValueError("status=success 时不能携带 degraded_reasons。")

        return self
