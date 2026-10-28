"""服务存活状态与数据库就绪状态接口。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_database_session

router = APIRouter(
    prefix="/health",
    tags=["Health"],
)


class HealthResponse(BaseModel):
    """不依赖外部服务的存活检查响应。"""

    status: Literal["ok"]
    service: str
    version: str


class ReadinessResponse(BaseModel):
    """包含 PostgreSQL 状态的就绪检查响应。"""

    status: Literal["ready"]
    service: str
    version: str
    database: Literal["ok"]


@router.get("", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """检查 Web 进程是否仍然可以接收请求。"""

    settings = get_settings()

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version="0.1.0",
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="检查服务及 PostgreSQL 是否可以处理分析请求",
)
def readiness_check(
    session: Annotated[Session, Depends(get_database_session)],
) -> ReadinessResponse:
    """执行最小查询，数据库不可用时返回 503。"""

    settings = get_settings()

    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL 暂不可用。",
        ) from exc

    return ReadinessResponse(
        status="ready",
        service=settings.app_name,
        version="0.1.0",
        database="ok",
    )
