"""完整软件变更分析 API 的自动化测试。"""

from pathlib import Path

from fastapi.testclient import TestClient
from langgraph.graph.state import CompiledStateGraph

from app.api.routes.changes import get_change_analysis_graph
from app.graph.change_analysis import (
    build_change_analysis_graph,
)
from app.main import app
from app.schemas.code_impact import (
    CodeImpactAnalysis,
    ImpactedFile,
)
from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.schemas.requirement import RequirementAnalysis


def fake_requirement_analyzer(
    request_text: str,
) -> RequirementAnalysis:
    """模拟需求分析智能体。"""

    return RequirementAnalysis(
        summary=request_text,
        change_type="feature",
        affected_modules=["订单服务"],
        acceptance_criteria=["待支付订单可以取消"],
        risks=["订单状态可能发生冲突"],
    )


def fake_repository_resolver(
    repository_name: str,
) -> Path:
    """模拟安全仓库路径解析。"""

    return Path("workspace") / repository_name


def fake_repository_scanner(
    repository_path: str | Path,
) -> RepositorySnapshot:
    """模拟仓库扫描结果。"""

    return RepositorySnapshot(
        repository_name=Path(repository_path).name,
        files=[
            RepositoryFile(
                relative_path="order_app/service.py",
                language="python",
                size_bytes=100,
                content="class OrderService: ...\n",
            ),
            RepositoryFile(
                relative_path="tests/test_service.py",
                language="python",
                size_bytes=100,
                content="def test_create_order(): ...\n",
            ),
        ],
        scanned_file_count=2,
        truncated=False,
    )


def fake_code_impact_analyzer(
    requirement: RequirementAnalysis,
    repository: RepositorySnapshot,
) -> CodeImpactAnalysis:
    """模拟代码影响分析智能体。"""

    return CodeImpactAnalysis(
        summary=(f"{requirement.summary} 将影响 {repository.repository_name}"),
        impacted_files=[
            ImpactedFile(
                path="order_app/service.py",
                reason="需要增加取消订单功能",
                suggested_changes=["增加 cancel_order 方法"],
                risk_level="medium",
            )
        ],
        new_files=[],
        test_files=["tests/test_service.py"],
        dependency_changes=[],
    )


def get_test_change_analysis_graph() -> CompiledStateGraph:
    """创建 API 测试使用的完整假工作流。"""

    return build_change_analysis_graph(
        requirement_analyzer=fake_requirement_analyzer,
        repository_resolver=fake_repository_resolver,
        repository_scanner=fake_repository_scanner,
        code_impact_analyzer=fake_code_impact_analyzer,
    )


def test_analyze_change_endpoint() -> None:
    """接口应该返回两个智能体的完整结果。"""

    app.dependency_overrides[get_change_analysis_graph] = get_test_change_analysis_graph

    try:
        client = TestClient(app)

        response = client.post(
            "/api/v1/changes/analyze",
            json={
                "request_text": ("  为订单系统增加   取消功能  "),
                "repository_name": "order_service",
            },
        )

        assert response.status_code == 200

        response_body = response.json()

        assert response_body["status"] == "code_impact_analyzed"
        assert response_body["normalized_request"] == "为订单系统增加 取消功能"
        assert response_body["requirement_analysis"]["change_type"] == "feature"
        assert (
            response_body["code_impact_analysis"]["impacted_files"][0]["path"]
            == "order_app/service.py"
        )
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )


def test_analyze_change_endpoint_rejects_blank_request() -> None:
    """接口应该拒绝只包含空格的需求。"""

    app.dependency_overrides[get_change_analysis_graph] = get_test_change_analysis_graph

    try:
        client = TestClient(app)

        response = client.post(
            "/api/v1/changes/analyze",
            json={
                "request_text": "   ",
                "repository_name": "order_service",
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "变更需求不能为空。"
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )
