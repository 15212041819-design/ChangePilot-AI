"""Evidence-grounded 正式 API 路径与状态契约测试。"""

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.analysis_report import AnalysisReport
from app.schemas.evidence_analysis_api import EvidenceAnalysisRequest
from app.services.evidence_analysis import get_evidence_analysis_executor

# 复用现有 API 测试已经验证过的最小可信报告构造器。
from tests.test_evidence_analysis_api import _build_ready_report


class FakeExecutor:
    """返回可信统一报告，避免契约测试触发真实 LLM。"""

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
    ) -> AnalysisReport:
        assert request.request_text == "为订单系统增加取消订单功能"
        return _build_ready_report()


def test_canonical_analysis_endpoint_returns_frozen_status_fields() -> None:
    """正式入口必须同时返回工作流状态和报告准入状态。"""

    app.dependency_overrides[get_evidence_analysis_executor] = FakeExecutor

    try:
        response = TestClient(app).post(
            "/api/v1/analyses",
            json={
                "request_text": "  为订单系统增加取消订单功能  ",
                "repository_name": "order_service",
                "commit": "HEAD",
            },
        )

        assert response.status_code == 200

        body = response.json()

        assert body["api_version"] == "1.0"
        assert body["workflow_status"] == "completed"
        assert body["status"] == "ready"
        assert len(body["thread_id"]) == 32
        assert body["report"]["overview"]["status"] == "ready"

    finally:
        app.dependency_overrides.pop(
            get_evidence_analysis_executor,
            None,
        )


def test_legacy_evidence_path_is_available_but_deprecated() -> None:
    """迁移期间保留旧路径，但 OpenAPI 必须明确标记 deprecated。"""

    document = TestClient(app).get("/openapi.json").json()

    canonical = document["paths"]["/api/v1/analyses"]["post"]
    legacy = document["paths"]["/api/v1/changes/evidence/analyze"]["post"]

    assert canonical.get("deprecated") is not True
    assert legacy["deprecated"] is True
