"""需求分析智能体使用的结构化数据模型。"""

from typing import Literal

from pydantic import BaseModel, Field


class RequirementAnalysis(BaseModel):
    """需求分析智能体生成的结构化结果。"""

    # 只概括用户明确表达的核心变更目标。
    summary: str = Field(
        min_length=1,
        description="对用户明确需求的简洁概括",
    )

    # 软件变更类型必须从固定范围中选择。
    change_type: Literal[
        "feature",
        "bugfix",
        "refactor",
        "documentation",
        "test",
        "other",
    ] = Field(
        description="软件变更类型",
    )

    # 根据用户需求初步判断可能涉及的业务模块。
    # 这里只描述业务范围，不代表已经确定具体代码文件。
    affected_modules: list[str] = Field(
        default_factory=list,
        description="可能受到影响的业务模块",
    )

    # 这里只能填写能够从用户原始需求中直接得到的验收条件。
    # 用户没有说明的字段、流程和技术方案不能写入这里。
    acceptance_criteria: list[str] = Field(
        min_length=1,
        description="由用户明确需求直接推导出的验收标准",
    )

    # 风险描述可能发生的问题，但不能作为新增需求使用。
    risks: list[str] = Field(
        default_factory=list,
        description="实现需求时需要关注的潜在风险",
    )

    # 用户没有明确说明，但可能影响实现方式的内容必须放在这里。
    # 这些内容在用户确认之前不能进入代码修改方案。
    assumptions: list[str] = Field(
        default_factory=list,
        description="智能体提出但尚未得到用户确认的假设",
    )

    # 针对 assumptions 生成需要用户回答的问题。
    clarification_questions: list[str] = Field(
        default_factory=list,
        description="需要用户进一步确认的问题",
    )
