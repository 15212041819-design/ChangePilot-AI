"""完整软件变更分析相关的 HTTP API。"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.graph.change_analysis import change_analysis_graph
from app.schemas.change_analysis_api import (
    ChangeAnalysisRequest,
    ChangeAnalysisResponse,
    ChangeAnalysisResumeRequest,
)
from app.schemas.code_impact import CodeImpactAnalysis
from app.schemas.human_review import (
    RequirementReviewDecision,
    RequirementReviewPrompt,
)
from app.schemas.requirement import RequirementAnalysis
from app.tools.code_impact_validator import (
    CodeImpactValidationError,
)
from app.tools.workspace import WorkspacePathError

router = APIRouter(
    prefix="/changes",
    tags=["Changes"],
)


def get_change_analysis_graph() -> CompiledStateGraph:
    """返回生产环境使用的完整 LangGraph 工作流。"""

    return change_analysis_graph


def create_graph_config(
    thread_id: str,
) -> dict[str, dict[str, str]]:
    """根据工作流标识创建 LangGraph 检查点配置。"""

    # thread_id 用于定位当前工作流保存的检查点。
    # 首次运行和恢复运行必须使用同一个值。
    return {
        "configurable": {
            "thread_id": thread_id,
        }
    }


def extract_review_prompt(
    result: dict[str, object],
) -> RequirementReviewPrompt | None:
    """从 LangGraph 中断结果中提取人工审核提示。"""

    raw_interrupts = result.get("__interrupt__")

    # 不同 LangGraph 执行方式可能使用列表或元组保存中断对象。
    # 没有中断，或者数据类型不正确时，说明工作流没有暂停。
    if not isinstance(raw_interrupts, (list, tuple)) or not raw_interrupts:
        return None

    # 当前工作流一次只会产生一个需求审核中断。
    first_interrupt = raw_interrupts[0]

    # Interrupt 对象的 value 属性保存传给 interrupt() 的载荷。
    interrupt_value = getattr(
        first_interrupt,
        "value",
        None,
    )

    try:
        # 再次通过 Pydantic 校验中断载荷，
        # 防止工作流向 API 返回不完整或错误的数据。
        return RequirementReviewPrompt.model_validate(interrupt_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="工作流返回了无效的需求审核提示。",
        ) from exc


def build_change_analysis_response(
    result: dict[str, object],
    thread_id: str,
) -> ChangeAnalysisResponse:
    """把 LangGraph 状态转换成统一的 HTTP 响应。"""

    normalized_request = result.get("normalized_request")
    workflow_status = result.get("status")

    if not isinstance(normalized_request, str):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="工作流没有生成有效的规范化需求。",
        )

    if not isinstance(workflow_status, str):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="工作流没有返回有效状态。",
        )

    try:
        # model_validate 同时兼容 Pydantic 对象和普通字典。
        requirement_analysis = RequirementAnalysis.model_validate(
            result.get("requirement_analysis")
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="工作流没有生成有效的需求分析结果。",
        ) from exc

    raw_review = result.get("requirement_review")

    if raw_review is None:
        requirement_review = None
    else:
        try:
            requirement_review = RequirementReviewDecision.model_validate(raw_review)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="工作流返回了无效的需求审核决定。",
            ) from exc

    raw_code_impact = result.get("code_impact_analysis")

    if raw_code_impact is None:
        code_impact_analysis = None
    else:
        try:
            code_impact_analysis = CodeImpactAnalysis.model_validate(raw_code_impact)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="工作流返回了无效的代码影响分析结果。",
            ) from exc

    # 工作流发生中断时，从 __interrupt__ 中读取待审核内容。
    review_prompt = extract_review_prompt(result)

    # 图内部状态可能停留在 requirement_analyzed
    # 或 requirement_revised。
    # API 使用更明确的状态告诉客户端当前正在等待人工审核。
    response_status = (
        "awaiting_requirement_review" if review_prompt is not None else workflow_status
    )

    # 工作流声称已经完成代码影响分析时，结果不能为空。
    if response_status == "code_impact_analyzed" and code_impact_analysis is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="工作流没有生成有效的代码影响分析结果。",
        )

    return ChangeAnalysisResponse(
        thread_id=thread_id,
        status=response_status,
        normalized_request=normalized_request,
        requirement_analysis=requirement_analysis,
        review_prompt=review_prompt,
        requirement_review=requirement_review,
        code_impact_analysis=code_impact_analysis,
    )


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
    """启动完整的软件变更分析工作流。"""

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

    # 每次新分析都生成独立的工作流标识。
    thread_id = uuid4().hex
    config = create_graph_config(thread_id)

    try:
        result = graph.invoke(
            {
                "request_text": request.request_text,
                "normalized_request": "",
                "repository_name": request.repository_name,
                "requirement_analysis": None,
                "requirement_review": None,
                "repository_snapshot": None,
                # 符号索引将在仓库扫描完成后由工作流节点写入。
                "repository_symbol_index": None,
                "code_impact_analysis": None,
                "status": "received",
            },
            config=config,
        )
    except CodeImpactValidationError as exc:
        # 模型已经返回结构化数据，
        # 但文件路径没有通过真实仓库校验。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
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

    return build_change_analysis_response(
        result=dict(result),
        thread_id=thread_id,
    )


@router.post(
    "/analyze/resume",
    response_model=ChangeAnalysisResponse,
    summary="提交人工审核决定并恢复软件变更分析",
)
def resume_change_analysis(
    request: ChangeAnalysisResumeRequest,
    graph: Annotated[
        CompiledStateGraph,
        Depends(get_change_analysis_graph),
    ],
) -> ChangeAnalysisResponse:
    """恢复一个正在等待需求人工审核的工作流。"""

    config = create_graph_config(request.thread_id)

    # 先读取检查点，
    # 避免使用不存在的 thread_id 恢复工作流。
    snapshot = graph.get_state(config)

    if not snapshot.values:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="没有找到指定的工作流。",
        )

    # 只有当前检查点确实存在中断时，
    # 才允许提交恢复命令。
    has_pending_interrupt = any(task.interrupts for task in snapshot.tasks)

    if not has_pending_interrupt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该工作流当前不需要人工审核。",
        )

    try:
        # decision 会成为 review_requirement() 中
        # interrupt() 函数的返回值。
        result = graph.invoke(
            Command(
                resume=request.decision.model_dump(mode="json"),
            ),
            config=config,
        )
    except CodeImpactValidationError as exc:
        # 人工审核恢复后执行代码影响分析时，
        # 仍然必须拦截模型返回的不可信文件路径。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
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

    return build_change_analysis_response(
        result=dict(result),
        thread_id=request.thread_id,
    )
