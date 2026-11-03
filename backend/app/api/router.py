"""ChangePilot AI 的 API 总路由。"""

from fastapi import APIRouter

from app.api.routes.evidence_analysis import router as evidence_analysis_router
from app.api.routes.health import router as health_router
from app.api.routes.repositories import router as repositories_router

api_router = APIRouter()

# 基础可用性接口。
api_router.include_router(health_router)
api_router.include_router(repositories_router)

# 正式产品主流程。
# 前端使用本路由的创建、历史列表、详情与审核接口。
api_router.include_router(evidence_analysis_router)
