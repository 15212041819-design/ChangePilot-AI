"""ChangePilot AI 的 API 总路由。"""

from fastapi import APIRouter

from app.api.routes.changes import router as changes_router
from app.api.routes.evidence_analysis import router as evidence_analysis_router
from app.api.routes.health import router as health_router

api_router = APIRouter()

# 基础可用性接口。
api_router.include_router(health_router)

# 正式产品主流程。
# 前端只允许依赖 POST /api/v1/analyses。
api_router.include_router(evidence_analysis_router)

# 旧 LangGraph 基线暂时保留用于评测和迁移验证。
# 第 4 步会把它从默认 Router 中移除。
api_router.include_router(changes_router)
