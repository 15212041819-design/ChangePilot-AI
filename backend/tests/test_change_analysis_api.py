"""软件变更分析 API 的自动化测试。"""

from fastapi.testclient import TestClient
from langgraph.graph.state import CompiledStateGraph

from app.api.routes.changes import get_change_analysis_graph
from app.graph.change_analysis import build_change_analysis_graph
from app.main import app
from app.schemas.requirement import RequirementAnalysis


def fake_requirement_analyzer(
    request_text: str,
) -> RequirementAnalysis:
    """模拟需求分析智能体，防止测试调用真实 DeepSeek API。"""

    return RequirementAnalysis(
        summary=f"测试分析：{request_text}",
        change_type="feature",
        affected_modules=["order"],
        acceptance_criteria=["未支付订单可以被取消"],
        risks=["订单状态可能出现并发冲突"],
    )


def get_test_change_analysis_graph() -> CompiledStateGraph:
    """创建仅供接口测试使用的 LangGraph 工作流。"""

    return build_change_analysis_graph(analyzer=fake_requirement_analyzer)


def test_analyze_change_endpoint() -> None:
    """接口应该返回经过 LangGraph 处理的结构化分析结果。"""

    # 用测试工作流替换生产环境的真实工作流。
    app.dependency_overrides[get_change_analysis_graph] = get_test_change_analysis_graph

    try:
        client = TestClient(app)

        response = client.post(
            "/api/v1/changes/analyze",
            json={
                "request_text": "  为订单系统增加   取消订单功能  ",
            },
        )

        assert response.status_code == 200

        response_body = response.json()

        assert response_body["status"] == "requirement_analyzed"
        assert response_body["normalized_request"] == "为订单系统增加 取消订单功能"
        assert response_body["analysis"]["change_type"] == "feature"
        assert response_body["analysis"]["affected_modules"] == ["order"]
    finally:
        # 测试完成后必须清除依赖替换，避免影响其他测试。
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )


def test_analyze_change_endpoint_rejects_blank_request() -> None:
    """接口应该拒绝只包含空格的需求文本。"""

    app.dependency_overrides[get_change_analysis_graph] = get_test_change_analysis_graph

    try:
        client = TestClient(app)

        response = client.post(
            "/api/v1/changes/analyze",
            json={
                "request_text": "   ",
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "变更需求不能为空。"
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )
