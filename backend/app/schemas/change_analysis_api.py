"""完整软件变更分析 API 的请求和响应模型。"""

from pydantic import BaseModel, Field

from app.schemas.code_impact import CodeImpactAnalysis
from app.schemas.human_review import (
    RequirementReviewDecision,
    RequirementReviewPrompt,
)
from app.schemas.implementation_plan import (
    ImplementationPlan,
)
from app.schemas.requirement import RequirementAnalysis
from app.schemas.test_strategy import TestStrategy


class ChangeAnalysisRequest(BaseModel):
    """客户端提交的完整软件变更分析请求。"""

    request_text: str = Field(
        min_length=1,
        max_length=10_000,
        description="需要分析的软件变更需求",
        examples=["为订单系统增加取消订单功能"],
    )

    repository_name: str = Field(
        min_length=1,
        max_length=200,
        description="需要分析的工作区代码仓库名称",
        examples=["order_service"],
    )


class ChangeAnalysisResumeRequest(BaseModel):
    """客户端恢复已暂停工作流时提交的请求。"""

    thread_id: str = Field(
        min_length=1,
        max_length=200,
        description="需要恢复的 LangGraph 工作流标识",
        examples=["7db85490e9094a11a07ef3a071703f89"],
    )

    decision: RequirementReviewDecision = Field(
        description="需求人工审核决定",
    )


class ChangeAnalysisResponse(BaseModel):
    """软件变更分析接口的统一响应。"""

    thread_id: str = Field(
        min_length=1,
        description="LangGraph 工作流及检查点标识",
    )

    # 可能是等待审核、审核拒绝或实施计划生成完成。
    status: str = Field(
        description="工作流当前状态",
        examples=[
            "awaiting_requirement_review",
            "requirement_rejected",
            "implementation_plan_generated",
        ],
    )

    normalized_request: str = Field(
        description="经过规范化处理的需求",
    )

    requirement_analysis: RequirementAnalysis = Field(
        description="结构化需求分析结果",
    )

    review_prompt: RequirementReviewPrompt | None = Field(
        default=None,
        description="等待用户处理的需求审核提示",
    )

    requirement_review: RequirementReviewDecision | None = Field(
        default=None,
        description="已经完成的需求人工审核决定",
    )

    # 第二个智能体生成的代码影响分析结果。
    code_impact_analysis: CodeImpactAnalysis | None = Field(
        default=None,
        description=("基于真实仓库生成的代码影响分析结果"),
    )

    # 第三个智能体生成的测试策略。
    test_strategy: TestStrategy | None = Field(
        default=None,
        description=("基于需求、代码影响和真实仓库证据生成的测试策略"),
    )

    # 第四个智能体生成的实施计划。
    implementation_plan: ImplementationPlan | None = Field(
        default=None,
        description=("基于全部分析结果生成的有序开发任务清单"),
    )
