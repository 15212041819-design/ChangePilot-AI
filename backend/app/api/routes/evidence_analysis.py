"""Evidence-grounded 正式分析 API 的 HTTP 入口。"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.evidence_analysis_api import (
    EvidenceAnalysisRequest,
    EvidenceAnalysisResponse,
)
from app.services.evidence_analysis import (
    EvidenceAnalysisExecutor,
    EvidenceAnalysisReviewRequiredError,
    EvidenceAnalysisUnavailableError,
    get_evidence_analysis_executor,
)

router = APIRouter(tags=["Analysis"])


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
    executor: EvidenceAnalysisExecutor,
) -> EvidenceAnalysisResponse:
    """执行统一错误映射，并构造冻结后的成功响应。"""

    normalized_request = _normalize_request(request)

    try:
        report = executor.analyze(normalized_request)

    except EvidenceAnalysisReviewRequiredError as exc:
        # 第 3 步接入 PostgreSQL 后，这个状态会被保存并允许恢复。
        # 当前同步接口先使用 409 明确表示需要人工介入。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except EvidenceAnalysisUnavailableError as exc:
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
        thread_id=uuid4().hex,
        workflow_status="completed",
        status=report.overview.status,
        report=report,
    )


@router.post(
    "/analyses",
    response_model=EvidenceAnalysisResponse,
    summary="创建证据驱动的软件变更分析",
)
def create_evidence_analysis(
    request: EvidenceAnalysisRequest,
    executor: Annotated[
        EvidenceAnalysisExecutor,
        Depends(get_evidence_analysis_executor),
    ],
) -> EvidenceAnalysisResponse:
    """正式产品入口；前端和后续持久化层只依赖这个路径。"""

    return _execute_analysis(request, executor)


@router.post(
    "/changes/evidence/analyze",
    response_model=EvidenceAnalysisResponse,
    summary="兼容旧的 Evidence-grounded 分析路径",
    deprecated=True,
)
def analyze_change_with_evidence_legacy(
    request: EvidenceAnalysisRequest,
    executor: Annotated[
        EvidenceAnalysisExecutor,
        Depends(get_evidence_analysis_executor),
    ],
) -> EvidenceAnalysisResponse:
    """兼容迁移前调用方；第 4 步清理旧基线时删除。"""

    return _execute_analysis(request, executor)
