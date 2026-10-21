"""完整软件变更分析相关的 HTTP API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.graph.state import CompiledStateGraph

from app.graph.change_analysis import change_analysis_graph
from app.schemas.change_analysis_api import (
    ChangeAnalysisRequest,
    ChangeAnalysisResponse,
)
from app.schemas.code_impact import CodeImpactAnalysis
from app.schemas.requirement import RequirementAnalysis
from app.tools.workspace import WorkspacePathError

router = APIRouter(
    prefix="/changes",
    tags=["Changes"],
)


def get_change_analysis_graph() -> CompiledStateGraph:
    """返回生产环境使用的完整 LangGraph 工作流。"""

    return change_analysis_graph


@router.post(
    "/analyze",
    response_model=ChangeAnalysisResponse,
    summary="分析软件变更需求及代码影响范围",
)
def analyze_change(
    request: ChangeAnalysisRequest,
    graph: Annotated[
        CompiledStateGraph,
        Depends(get_change_analysis_graph),
    ],
) -> ChangeAnalysisResponse:
    """运行完整的软件变更分析工作流。"""

    if not request.request_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="变更需求不能为空。",
        )

    if not request.repository_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="代码仓库名称不能为空。",
        )

    try:
        result = graph.invoke(
            {
                "request_text": request.request_text,
                "normalized_request": "",
                "repository_name": request.repository_name,
                "requirement_analysis": None,
                "repository_snapshot": None,
                "code_impact_analysis": None,
                "status": "received",
            }
        )
    except WorkspacePathError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    requirement_analysis = result.get("requirement_analysis")
    code_impact_analysis = result.get("code_impact_analysis")

    if not isinstance(
        requirement_analysis,
        RequirementAnalysis,
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="工作流没有生成有效的需求分析结果。",
        )

    if not isinstance(
        code_impact_analysis,
        CodeImpactAnalysis,
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="工作流没有生成有效的代码影响分析结果。",
        )

    return ChangeAnalysisResponse(
        status=result["status"],
        normalized_request=result["normalized_request"],
        requirement_analysis=requirement_analysis,
        code_impact_analysis=code_impact_analysis,
    )
