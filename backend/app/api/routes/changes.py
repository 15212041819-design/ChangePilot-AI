"""软件变更分析相关的 HTTP API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.graph.state import CompiledStateGraph

from app.graph.change_analysis import change_analysis_graph
from app.schemas.change_analysis_api import (
    ChangeAnalysisRequest,
    ChangeAnalysisResponse,
)
from app.schemas.requirement import RequirementAnalysis

# 创建一个路由分组。
# 最终接口地址会由主路由统一添加 /api/v1 前缀。
router = APIRouter(
    prefix="/changes",
    tags=["Changes"],
)


def get_change_analysis_graph() -> CompiledStateGraph:
    """返回已经编译完成的软件变更分析工作流。

    将工作流包装为 FastAPI 依赖，方便测试时替换成不调用
    DeepSeek 的测试工作流。
    """

    return change_analysis_graph


@router.post(
    "/analyze",
    response_model=ChangeAnalysisResponse,
    summary="分析软件变更需求",
    description="使用 LangGraph 和需求分析智能体分析软件变更需求。",
)
def analyze_change(
    request: ChangeAnalysisRequest,
    graph: Annotated[
        CompiledStateGraph,
        Depends(get_change_analysis_graph),
    ],
) -> ChangeAnalysisResponse:
    """执行软件变更需求分析工作流。"""

    # Pydantic 可以拦截空字符串，但无法自动拦截只包含空格的字符串，
    # 因此这里需要再次检查。
    if not request.request_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="变更需求不能为空。",
        )

    # 创建 LangGraph 的初始共享状态并运行完整工作流。
    result = graph.invoke(
        {
            "request_text": request.request_text,
            "normalized_request": "",
            "requirement_analysis": None,
            "status": "received",
        }
    )

    # 从工作流最终状态中取出需求分析智能体的结果。
    analysis = result.get("requirement_analysis")

    # 正常情况下这里一定是 RequirementAnalysis。
    # 如果不是，说明工作流没有按约定返回结果，属于后端服务异常。
    if not isinstance(analysis, RequirementAnalysis):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="需求分析工作流没有生成有效结果。",
        )

    # 将 LangGraph 状态转换为稳定的 API 响应。
    return ChangeAnalysisResponse(
        status=result["status"],
        normalized_request=result["normalized_request"],
        analysis=analysis,
    )
