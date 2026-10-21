from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

# 路由分组
router = APIRouter(
    prefix="/health",
    tags=["Health"],
)


# 响应数据格式
class HealthResponse(BaseModel):
    """健康检查接口的响应格式。"""

    status: str  # 服务状态，约定 "ok" 表示正常
    service: str  # 服务名称
    version: str  # 服务版本


# 健康检查接口
@router.get("", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """检查后端服务是否正常运行。"""

    settings = get_settings()

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version="0.1.0",
    )
