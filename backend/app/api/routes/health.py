from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(
    prefix="/health",
    tags=["Health"],
)


class HealthResponse(BaseModel):
    """健康检查接口的响应格式。"""

    status: str
    service: str
    version: str


@router.get("", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """检查后端服务是否正常运行。"""

    settings = get_settings()

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version="0.1.0",
    )
