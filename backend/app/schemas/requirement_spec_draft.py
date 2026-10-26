"""Requirement Agent 输出的无 ID、无 digest 需求草稿契约。

LLM 只负责语义提取；AC ID、假设 ID、仓库词表命中和 digest
全部由后续确定性代码填充。
"""

from pydantic import Field

from app.schemas.contracts import (
    AcceptanceCriterionDraft,
    ChangeType,
    FrozenContractModel,
)


class EntityHintDraft(FrozenContractModel):
    """LLM 从需求文本中提取的概念与检索词。"""

    concept: str = Field(
        min_length=1,
        max_length=100,
    )

    lexical_variants: tuple[str, ...] = Field(
        default=(),
        max_length=10,
    )

    symbol_candidates: tuple[str, ...] = Field(
        default=(),
        max_length=10,
    )

    role: str = Field(
        pattern=r"^(subject|object|action_target|constraint|quantity)$",
    )


class AssumptionDraft(FrozenContractModel):
    """LLM 识别出、但尚未经人工确认的假设。"""

    text: str = Field(
        min_length=1,
        max_length=300,
    )


class RequirementSpecDraft(FrozenContractModel):
    """RequirementSpec 在代码分配身份信息前的语义草稿。"""

    summary: str = Field(
        min_length=1,
        max_length=200,
    )

    change_type: ChangeType

    acceptance_criteria: tuple[
        AcceptanceCriterionDraft,
        ...,
    ] = Field(
        min_length=1,
    )

    entity_hints: tuple[
        EntityHintDraft,
        ...,
    ] = Field(
        min_length=1,
        max_length=20,
    )

    constraints: tuple[str, ...] = Field(
        default=(),
        max_length=10,
    )

    assumptions: tuple[
        AssumptionDraft,
        ...,
    ] = Field(
        default=(),
        max_length=10,
    )
