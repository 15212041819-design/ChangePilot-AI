"""ChangePilot AI 的 API 总路由。"""

from fastapi import APIRouter

from app.api.routes.changes import router as changes_router
from app.api.routes.health import router as health_router

# 创建 API 总路由对象。
api_router = APIRouter()

# 注册健康检查接口。
api_router.include_router(health_router)

# 注册软件变更分析接口。
api_router.include_router(changes_router)
