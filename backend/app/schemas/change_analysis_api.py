"""软件变更分析 API 使用的请求和响应数据模型。"""

from pydantic import BaseModel, Field

from app.schemas.requirement import RequirementAnalysis


class ChangeAnalysisRequest(BaseModel):
    """客户端提交的软件变更分析请求。"""

    request_text: str = Field(
        min_length=1,
        max_length=10_000,
        description="需要分析的软件变更需求文本",
        examples=["为订单系统增加取消订单功能，用户只能取消尚未支付的订单。"],
    )


class ChangeAnalysisResponse(BaseModel):
    """软件变更分析接口返回的结果。"""

    # LangGraph 工作流执行完成后的状态。
    status: str = Field(
        description="工作流当前状态",
        examples=["requirement_analyzed"],
    )

    # 去除多余空格和换行后的标准化需求。
    normalized_request: str = Field(
        description="经过规范化处理的软件变更需求",
    )

    # 需求分析智能体产生的结构化结果。
    analysis: RequirementAnalysis = Field(
        description="需求分析智能体生成的分析结果",
    )
