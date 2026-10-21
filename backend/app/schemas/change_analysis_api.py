"""完整软件变更分析 API 的请求和响应模型。"""

from pydantic import BaseModel, Field

from app.schemas.code_impact import CodeImpactAnalysis
from app.schemas.requirement import RequirementAnalysis


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


class ChangeAnalysisResponse(BaseModel):
    """完整软件变更分析接口的响应。"""

    # 工作流最终执行状态。
    status: str = Field(
        description="工作流最终状态",
        examples=["code_impact_analyzed"],
    )

    # 清理过多余空格和换行的需求。
    normalized_request: str = Field(
        description="经过规范化处理的需求",
    )

    # 第一个智能体生成的需求分析结果。
    requirement_analysis: RequirementAnalysis = Field(
        description="结构化需求分析结果",
    )

    # 第二个智能体生成的代码影响分析结果。
    code_impact_analysis: CodeImpactAnalysis = Field(
        description="基于真实仓库的代码影响分析结果",
    )
