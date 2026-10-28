"""Evidence-grounded 正式分析 API 的 HTTP 入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_database_session
from app.repositories.analysis_context import (
    AnalysisContextConflictError,
    AnalysisContextPersistenceError,
    SQLAlchemyAnalysisContextRepository,
)
from app.schemas.analysis_context import StoredAnalysisContext
from app.schemas.evidence_analysis_api import (
    EvidenceAnalysisRequest,
    EvidenceAnalysisResponse,
)
from app.services.analysis_lifecycle import (
    EvidenceAnalysisLifecycleService,
    PersistedEvidenceAnalysisReviewRequiredError,
)
from app.services.evidence_analysis import (
    EvidenceAnalysisExecutor,
    EvidenceAnalysisReviewRequiredError,
    EvidenceAnalysisUnavailableError,
    get_evidence_analysis_executor,
)

router = APIRouter(tags=["Analysis"])


def get_analysis_lifecycle_service(
    executor: Annotated[
        EvidenceAnalysisExecutor,
        Depends(get_evidence_analysis_executor),
    ],
    session: Annotated[
        Session,
        Depends(get_database_session),
    ],
) -> EvidenceAnalysisLifecycleService:
    """为一次 HTTP 请求装配执行器、Session 与生命周期服务。"""

    return EvidenceAnalysisLifecycleService(
        executor=executor,
        repository=SQLAlchemyAnalysisContextRepository(session),
    )


def _normalize_request(
    request: EvidenceAnalysisRequest,
) -> EvidenceAnalysisRequest:
    """统一清理公开输入，禁止空白字符串进入执行层。"""

    request_text = request.request_text.strip()
    repository_name = request.repository_name.strip()
    commit = request.commit.strip()

    if not request_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="变更需求不能为空。",
        )

    if not repository_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="代码仓库名称不能为空。",
        )

    if not commit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Git commit 不能为空。",
        )

    return request.model_copy(
        update={
            "request_text": request_text,
            "repository_name": repository_name,
            "commit": commit,
        }
    )


def _execute_analysis(
    request: EvidenceAnalysisRequest,
    lifecycle: EvidenceAnalysisLifecycleService,
) -> EvidenceAnalysisResponse:
    """执行统一错误映射，并构造冻结后的成功响应。"""

    normalized_request = _normalize_request(request)

    try:
        execution = lifecycle.execute(normalized_request)

    except PersistedEvidenceAnalysisReviewRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "thread_id": exc.thread_id,
                "workflow_status": "awaiting_requirement_review",
                "message": str(exc),
            },
        ) from exc

    except EvidenceAnalysisReviewRequiredError as exc:
        # 兼容尚未经过生命周期服务持久化的旧执行器。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except EvidenceAnalysisUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    except AnalysisContextConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except AnalysisContextPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except (KeyError, TypeError, ValueError) as exc:
        # digest、契约或结果组装错误不能伪装成成功报告。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc) or type(exc).__name__,
        ) from exc

    return EvidenceAnalysisResponse(
        thread_id=execution.thread_id,
        workflow_status="completed",
        status=execution.report.overview.status,
        report=execution.report,
    )


@router.post(
    "/analyses",
    response_model=EvidenceAnalysisResponse,
    summary="创建证据驱动的软件变更分析",
)
def create_evidence_analysis(
    request: EvidenceAnalysisRequest,
    lifecycle: Annotated[
        EvidenceAnalysisLifecycleService,
        Depends(get_analysis_lifecycle_service),
    ],
) -> EvidenceAnalysisResponse:
    """正式产品入口；前端和后续持久化层只依赖这个路径。"""

    return _execute_analysis(request, lifecycle)


@router.get(
    "/analyses/{thread_id}",
    response_model=StoredAnalysisContext,
    summary="查询已持久化的软件变更分析上下文",
)
def get_evidence_analysis_context(
    thread_id: str,
    lifecycle: Annotated[
        EvidenceAnalysisLifecycleService,
        Depends(get_analysis_lifecycle_service),
    ],
) -> StoredAnalysisContext:
    """供前端刷新、恢复和展示分析时间线。"""

    context = lifecycle.get_context(thread_id)

    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"分析会话不存在：{thread_id}",
        )

    return context


@router.post(
    "/changes/evidence/analyze",
    response_model=EvidenceAnalysisResponse,
    summary="兼容旧的 Evidence-grounded 分析路径",
    deprecated=True,
)
def analyze_change_with_evidence_legacy(
    request: EvidenceAnalysisRequest,
    lifecycle: Annotated[
        EvidenceAnalysisLifecycleService,
        Depends(get_analysis_lifecycle_service),
    ],
) -> EvidenceAnalysisResponse:
    """兼容迁移前调用方；第 4 步清理旧基线时删除。"""

    return _execute_analysis(request, lifecycle)
