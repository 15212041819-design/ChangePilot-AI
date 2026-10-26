"""ChangePilot AI 的 API 总路由。"""

from fastapi import APIRouter

from app.api.routes.changes import router as changes_router
from app.api.routes.evidence_analysis import (
    router as evidence_analysis_router,
)
from app.api.routes.health import router as health_router

# 创建 API 总路由对象。
api_router = APIRouter()

# 注册健康检查接口。
api_router.include_router(health_router)

# 注册原有的软件变更分析接口。
# 旧接口暂时保留，用于后续基线对比。
api_router.include_router(changes_router)

# 注册新的 Evidence-grounded 分析接口。
api_router.include_router(evidence_analysis_router)
