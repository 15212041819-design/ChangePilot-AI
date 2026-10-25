"""完整软件变更分析相关的 HTTP API。"""

from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from langgraph.graph.state import (
    CompiledStateGraph,
)
from langgraph.types import Command

from app.graph.change_analysis import (
    change_analysis_graph,
)
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
from app.schemas.implementation_plan import (
    ImplementationPlan,
)
from app.schemas.requirement import RequirementAnalysis
from app.schemas.test_strategy import TestStrategy
from app.services.implementation_plan_agent import (
    ImplementationPlanValidationError,
)
from app.services.test_strategy_agent import (
    TestStrategyValidationError,
)
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

    if (
        not isinstance(
            raw_interrupts,
            (list, tuple),
        )
        or not raw_interrupts
    ):
        return None

    first_interrupt = raw_interrupts[0]

    interrupt_value = getattr(
        first_interrupt,
        "value",
        None,
    )

    try:
        return RequirementReviewPrompt.model_validate(interrupt_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail=("工作流返回了无效的需求审核提示。"),
        ) from exc


def build_change_analysis_response(
    result: dict[str, object],
    thread_id: str,
) -> ChangeAnalysisResponse:
    """把 LangGraph 状态转换成统一的 HTTP 响应。"""

    normalized_request = result.get("normalized_request")
    workflow_status = result.get("status")

    if not isinstance(
        normalized_request,
        str,
    ):
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail=("工作流没有生成有效的规范化需求。"),
        )

    if not isinstance(workflow_status, str):
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail="工作流没有返回有效状态。",
        )

    try:
        requirement_analysis = RequirementAnalysis.model_validate(
            result.get("requirement_analysis")
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail=("工作流没有生成有效的需求分析结果。"),
        ) from exc

    raw_review = result.get("requirement_review")

    if raw_review is None:
        requirement_review = None
    else:
        try:
            requirement_review = RequirementReviewDecision.model_validate(raw_review)
        except ValueError as exc:
            raise HTTPException(
                status_code=(status.HTTP_502_BAD_GATEWAY),
                detail=("工作流返回了无效的需求审核决定。"),
            ) from exc

    raw_code_impact = result.get("code_impact_analysis")

    if raw_code_impact is None:
        code_impact_analysis = None
    else:
        try:
            code_impact_analysis = CodeImpactAnalysis.model_validate(raw_code_impact)
        except ValueError as exc:
            raise HTTPException(
                status_code=(status.HTTP_502_BAD_GATEWAY),
                detail=("工作流返回了无效的代码影响分析结果。"),
            ) from exc

    raw_test_strategy = result.get("test_strategy")

    if raw_test_strategy is None:
        test_strategy = None
    else:
        try:
            test_strategy = TestStrategy.model_validate(raw_test_strategy)
        except ValueError as exc:
            raise HTTPException(
                status_code=(status.HTTP_502_BAD_GATEWAY),
                detail=("工作流返回了无效的测试策略。"),
            ) from exc

    raw_implementation_plan = result.get("implementation_plan")

    if raw_implementation_plan is None:
        implementation_plan = None
    else:
        try:
            implementation_plan = ImplementationPlan.model_validate(raw_implementation_plan)
        except ValueError as exc:
            raise HTTPException(
                status_code=(status.HTTP_502_BAD_GATEWAY),
                detail=("工作流返回了无效的实施计划。"),
            ) from exc

    review_prompt = extract_review_prompt(result)

    response_status = (
        "awaiting_requirement_review" if review_prompt is not None else workflow_status
    )

    # 工作流声明实施计划完成时，
    # 三个下游结果都必须存在。
    if response_status == "implementation_plan_generated" and code_impact_analysis is None:
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail=("工作流没有生成有效的代码影响分析结果。"),
        )

    if response_status == "implementation_plan_generated" and test_strategy is None:
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail=("工作流没有生成有效的测试策略。"),
        )

    if response_status == "implementation_plan_generated" and implementation_plan is None:
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail=("工作流没有生成有效的实施计划。"),
        )

    return ChangeAnalysisResponse(
        thread_id=thread_id,
        status=response_status,
        normalized_request=normalized_request,
        requirement_analysis=(requirement_analysis),
        review_prompt=review_prompt,
        requirement_review=requirement_review,
        code_impact_analysis=(code_impact_analysis),
        test_strategy=test_strategy,
        implementation_plan=(implementation_plan),
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
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail="变更需求不能为空。",
        )

    if not request.repository_name.strip():
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail="代码仓库名称不能为空。",
        )

    thread_id = uuid4().hex
    config = create_graph_config(thread_id)

    try:
        result = graph.invoke(
            {
                "request_text": (request.request_text),
                "normalized_request": "",
                "repository_name": (request.repository_name),
                "requirement_analysis": None,
                "requirement_review": None,
                "repository_snapshot": None,
                "repository_symbol_index": None,
                "repository_dependency_graph": None,
                "code_impact_analysis": None,
                "test_strategy": None,
                "implementation_plan": None,
                "status": "received",
            },
            config=config,
        )
    except (
        CodeImpactValidationError,
        TestStrategyValidationError,
        ImplementationPlanValidationError,
    ) as exc:
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail=str(exc),
        ) from exc
    except WorkspacePathError as exc:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc

    return build_change_analysis_response(
        result=dict(result),
        thread_id=thread_id,
    )


@router.post(
    "/analyze/resume",
    response_model=ChangeAnalysisResponse,
    summary=("提交人工审核决定并恢复软件变更分析"),
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

    snapshot = graph.get_state(config)

    if not snapshot.values:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail="没有找到指定的工作流。",
        )

    has_pending_interrupt = any(task.interrupts for task in snapshot.tasks)

    if not has_pending_interrupt:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=("该工作流当前不需要人工审核。"),
        )

    try:
        result = graph.invoke(
            Command(
                resume=(request.decision.model_dump(mode="json")),
            ),
            config=config,
        )
    except (
        CodeImpactValidationError,
        TestStrategyValidationError,
        ImplementationPlanValidationError,
    ) as exc:
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail=str(exc),
        ) from exc
    except WorkspacePathError as exc:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc

    return build_change_analysis_response(
        result=dict(result),
        thread_id=request.thread_id,
    )
