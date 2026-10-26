"""Evidence-grounded 分析的 HTTP 入口。"""

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

router = APIRouter(
    prefix="/changes",
    tags=["Changes"],
)


@router.post(
    "/evidence/analyze",
    response_model=EvidenceAnalysisResponse,
    summary="运行证据驱动的软件变更分析",
)
def analyze_change_with_evidence(
    request: EvidenceAnalysisRequest,
    executor: Annotated[
        EvidenceAnalysisExecutor,
        Depends(get_evidence_analysis_executor),
    ],
) -> EvidenceAnalysisResponse:
    """校验 HTTP 输入，委托执行器，并返回统一报告。"""

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

    normalized_request = request.model_copy(
        update={
            "request_text": request_text,
            "repository_name": repository_name,
            "commit": commit,
        }
    )

    try:
        report = executor.analyze(normalized_request)

    except EvidenceAnalysisReviewRequiredError as exc:
        # 需求存在未确认假设时，不允许系统擅自批准。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except EvidenceAnalysisUnavailableError as exc:
        # 请求使用了尚未安全实现的能力，例如历史 commit 快照。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    except (FileNotFoundError, NotADirectoryError) as exc:
        # 仓库不存在或解析结果不是目录。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except (KeyError, TypeError, ValueError) as exc:
        # 契约、digest 或组装错误表示上游分析结果不可信。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc) or type(exc).__name__,
        ) from exc

    return EvidenceAnalysisResponse(
        thread_id=uuid4().hex,
        status=report.overview.status,
        report=report,
    )
