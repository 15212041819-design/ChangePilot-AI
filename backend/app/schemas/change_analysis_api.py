"""完整软件变更分析 API 的请求和响应模型。"""

from pydantic import BaseModel, Field

from app.schemas.code_impact import CodeImpactAnalysis
from app.schemas.human_review import (
    RequirementReviewDecision,
    RequirementReviewPrompt,
)
from app.schemas.requirement import RequirementAnalysis
from app.schemas.test_strategy import TestStrategy


class ChangeAnalysisRequest(BaseModel):
    """客户端提交的完整软件变更分析请求。"""

    # 用户希望实现的软件变更。
    request_text: str = Field(
        min_length=1,
        max_length=10_000,
        description="需要分析的软件变更需求",
        examples=["为订单系统增加取消订单功能"],
    )

    # 工作区内部的仓库名称，不接受本地绝对路径。
    repository_name: str = Field(
        min_length=1,
        max_length=200,
        description="需要分析的工作区代码仓库名称",
        examples=["order_service"],
    )


class ChangeAnalysisResumeRequest(BaseModel):
    """客户端恢复已暂停工作流时提交的请求。"""

    # 首次调用分析接口时由服务端生成。
    # 恢复工作流必须使用相同的 thread_id。
    thread_id: str = Field(
        min_length=1,
        max_length=200,
        description="需要恢复的 LangGraph 工作流标识",
        examples=["7db85490e9094a11a07ef3a071703f89"],
    )

    # 用户对需求假设作出的批准或拒绝决定。
    decision: RequirementReviewDecision = Field(
        description="需求人工审核决定",
    )


class ChangeAnalysisResponse(BaseModel):
    """软件变更分析接口的统一响应。"""

    # 当前工作流使用的检查点标识。
    # 每次分析都会由服务端生成工作流标识。
    # 暂停和恢复接口使用该值定位同一个检查点。
    thread_id: str = Field(
        min_length=1,
        description="LangGraph 工作流及检查点标识",
    )

    # 工作流当前所处的阶段。
    # 可能是等待审核、审核拒绝或测试策略生成完成。
    status: str = Field(
        description="工作流当前状态",
        examples=[
            "awaiting_requirement_review",
            "requirement_rejected",
            "test_strategy_generated",
        ],
    )

    # 清理过多余空格和换行后的需求。
    normalized_request: str = Field(
        description="经过规范化处理的需求",
    )

    # 第一个智能体生成的需求分析结果。
    # 工作流在进入人工审核前已经完成该部分。
    requirement_analysis: RequirementAnalysis = Field(
        description="结构化需求分析结果",
    )

    # 工作流暂停时返回给用户的人工审核内容。
    # 不需要人工审核或者审核已经完成时，该字段为 None。
    review_prompt: RequirementReviewPrompt | None = Field(
        default=None,
        description="等待用户处理的需求审核提示",
    )

    # 用户已经提交的审核决定。
    # 等待用户审核时，该字段为 None。
    requirement_review: RequirementReviewDecision | None = Field(
        default=None,
        description="已经完成的需求人工审核决定",
    )

    # 第二个智能体生成的代码影响分析结果。
    # 等待审核或审核被拒绝时，该字段为 None。
    code_impact_analysis: CodeImpactAnalysis | None = Field(
        default=None,
        description="基于真实仓库生成的代码影响分析结果",
    )

    # 第三个智能体生成的测试策略。
    # 等待审核或代码影响分析尚未完成时，该字段为 None。
    test_strategy: TestStrategy | None = Field(
        default=None,
        description=("基于需求、代码影响和真实仓库证据生成的测试策略"),
    )
