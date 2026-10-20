from typing import Literal

from pydantic import BaseModel, Field


class RequirementAnalysis(BaseModel):
    """需求分析智能体输出的结构化结果。"""

    summary: str = Field(
        description="对软件变更需求的简洁概括",
        min_length=1,
    )
    change_type: Literal[
        "feature",
        "bugfix",
        "refactor",
        "documentation",
        "test",
        "other",
    ] = Field(description="软件变更类型")
    affected_modules: list[str] = Field(
        default_factory=list,
        description="可能受到影响的模块",
    )
    acceptance_criteria: list[str] = Field(
        description="可以验证需求是否完成的验收标准",
        min_length=1,
    )
    risks: list[str] = Field(
        default_factory=list,
        description="实施该变更可能产生的风险",
    )
