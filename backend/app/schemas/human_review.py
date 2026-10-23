"""需求人工审核阶段使用的数据模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequirementReviewPrompt(BaseModel):
    """工作流暂停时展示给审核人员的需求分析信息。"""

    # 禁止接收模型中没有声明的字段，避免前后端字段拼写错误被静默忽略。
    model_config = ConfigDict(extra="forbid")

    # 固定的消息类型，方便 API 和前端识别当前中断原因。
    type: Literal["requirement_review"] = "requirement_review"

    # 展示给审核人员的操作提示。
    question: str = Field(
        default="需求分析中存在尚未确认的假设，是否批准继续分析？",
        min_length=1,
        description="人工审核提示语",
    )

    # 第一个智能体对用户明确需求的概括。
    summary: str = Field(
        min_length=1,
        description="待审核的需求摘要",
    )

    # 智能体提出、但尚未得到用户确认的假设。
    assumptions: list[str] = Field(
        min_length=1,
        description="需要人工确认的需求假设",
    )

    # 与假设对应的澄清问题，帮助用户判断是否批准。
    clarification_questions: list[str] = Field(
        default_factory=list,
        description="建议由用户回答的需求澄清问题",
    )


class RequirementReviewDecision(BaseModel):
    """审核人员恢复工作流时提交的审核决定。"""

    # 拒绝未知字段，防止无效数据进入后续代码影响分析。
    model_config = ConfigDict(extra="forbid")

    # approve 表示接受当前假设并继续分析。
    # reject 表示不接受当前假设并停止本次工作流。
    action: Literal["approve", "reject"] = Field(
        description="人工审核决定",
    )

    # 审核人员可以填写补充说明。
    # 当前阶段先保存该信息，后续可以用于重新分析或审计记录。
    feedback: str | None = Field(
        default=None,
        description="人工审核补充说明",
    )

    @model_validator(mode="after")
    def validate_rejection_feedback(
        self,
    ) -> "RequirementReviewDecision":
        """拒绝需求分析时必须提供具体的修改反馈。"""

        # 去除反馈两端多余空格，避免只包含空格的内容通过校验。
        normalized_feedback = self.feedback.strip() if self.feedback is not None else ""

        # approve 允许不填写反馈。
        # reject 必须说明需求分析需要如何修订。
        if self.action == "reject" and not normalized_feedback:
            raise ValueError("拒绝需求分析时必须提供修改反馈。")

        # 把清理后的反馈保存回模型。
        # 空的批准反馈统一保存为 None。
        self.feedback = normalized_feedback or None

        return self
