"""Guard 使用的事实快照、无效项和判定结果契约。

本文件只定义可序列化的数据结构，不读取文件系统，也不执行符号查询。
具体校验逻辑位于 ``app.guards.analysis_guard``。
"""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.contracts import (
    HASH_PATTERN,
    ChangePoint,
    FrozenContractModel,
    SymbolKind,
)

GuardBranch = Literal[
    "impact",
    "test",
    "risk",
    "map",
    "review",
]

BranchKind = Literal[
    "agent",
    "engine",
]

GuardVerdict = Literal[
    "pass",
    "repair",
    "fail",
]

NormalizeKind = Literal[
    "path_separator",
    "path_cleanup",
    "path_case",
    "duplicate_finding",
    "stable_sort",
]

GuardErrorType = Literal[
    "unknown_file",
    "path_outside_repository",
    "unknown_existing_symbol",
    "invalid_new_symbol_target",
    "inactive_acceptance_criterion",
    "invalid_evidence_path",
    "evidence_line_out_of_range",
    "definition_line_outside_symbol",
    "ambiguous_symbol_qualification",
    "unknown_caller_file",
    "digest_mismatch",
    "candidate_set_write_attempt",
]


class RepositoryFile(FrozenContractModel):
    """Guard 校验时允许引用的真实仓库文件。"""

    path: str = Field(
        min_length=1,
    )

    line_count: int = Field(
        ge=0,
    )


class SymbolLocation(FrozenContractModel):
    """SymbolIndex 中一个符号的稳定位置摘要。"""

    qualified_name: str = Field(
        min_length=1,
    )

    path: str = Field(
        min_length=1,
    )

    kind: SymbolKind

    start_line: int = Field(
        ge=1,
    )

    end_line: int = Field(
        ge=1,
    )

    @model_validator(mode="after")
    def validate_line_range(
        self,
    ) -> "SymbolLocation":
        """符号结束行不能早于开始行。"""

        if self.end_line < self.start_line:
            raise ValueError("symbol.end_line 不能小于 start_line。")

        return self


class GuardDigests(FrozenContractModel):
    """分支声称自己消费的三个上游摘要。"""

    set_digest: str = Field(
        pattern=HASH_PATTERN,
    )

    spec_digest: str = Field(
        pattern=HASH_PATTERN,
    )

    index_digest: str = Field(
        pattern=HASH_PATTERN,
    )


class GuardContext(FrozenContractModel):
    """一次 Guard 校验需要的只读仓库事实。"""

    repository_files: tuple[RepositoryFile, ...]

    symbols: tuple[SymbolLocation, ...]

    active_ac_ids: frozenset[str]


class NormalizeAction(FrozenContractModel):
    """一次不改变结论语义的机械归一化记录。"""

    kind: NormalizeKind

    path: str | None = None

    detail: str = Field(
        min_length=1,
    )


class InvalidFinding(FrozenContractModel):
    """Guard 发现的一条无效事实或需修复问题。"""

    finding_id: str = Field(
        min_length=1,
    )

    error_type: GuardErrorType

    path: str | None = None

    symbol: str | None = None

    detail: str = Field(
        min_length=1,
    )

    repairable: bool


class GuardReport(FrozenContractModel):
    """Guard 对一个分析分支的最终判定。"""

    branch: GuardBranch

    branch_kind: BranchKind

    checked_count: int = Field(
        ge=0,
    )

    normalize_actions: tuple[NormalizeAction, ...] = ()

    invalid: tuple[InvalidFinding, ...] = ()

    invalid_ratio: float = Field(
        ge=0.0,
    )

    repair_attempt: int = Field(
        ge=0,
        le=1,
    )

    verdict: GuardVerdict

    blocking_reasons: tuple[str, ...] = ()

    repair_hints: tuple[str, ...] = ()

    digests_echo: GuardDigests

    guard_version: str = "1.0.0"

    @model_validator(mode="after")
    def validate_verdict_payload(
        self,
    ) -> "GuardReport":
        """保证三态判定携带与自身语义一致的数据。"""

        if self.verdict == "pass" and self.invalid:
            raise ValueError("verdict=pass 时 invalid 必须为空。")

        if self.verdict == "repair" and not self.repair_hints:
            raise ValueError("verdict=repair 时 repair_hints 不能为空。")

        if self.verdict == "fail" and not self.blocking_reasons:
            raise ValueError("verdict=fail 时 blocking_reasons 不能为空。")

        return self


class GuardEvaluation(FrozenContractModel):
    """归一化后的变更点和对应 Guard 报告。"""

    change_points: tuple[ChangePoint, ...]

    report: GuardReport
