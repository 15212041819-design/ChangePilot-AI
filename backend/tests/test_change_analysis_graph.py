"""完整软件变更分析工作流的自动化测试。"""

from pathlib import Path

import pytest

from app.graph.change_analysis import (
    build_change_analysis_graph,
)
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
    """模拟第一个需求分析智能体。"""

    return RequirementAnalysis(
        summary=f"分析结果：{request_text}",
        change_type="feature",
        affected_modules=["订单服务"],
        acceptance_criteria=["待支付订单可以取消"],
        risks=["订单状态可能发生冲突"],
    )


def fake_repository_resolver(
    repository_name: str,
) -> Path:
    """模拟安全工作区路径解析器。"""

    assert repository_name == "order_service"

    return Path("virtual_workspace/order_service")


def fake_repository_scanner(
    repository_path: str | Path,
) -> RepositorySnapshot:
    """模拟代码仓库扫描工具。"""

    assert Path(repository_path).name == "order_service"

    return RepositorySnapshot(
        repository_name="order_service",
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
    """模拟第二个代码影响分析智能体。"""

    assert requirement.change_type == "feature"
    assert repository.repository_name == "order_service"

    return CodeImpactAnalysis(
        summary="需要修改订单服务并补充测试",
        impacted_files=[
            ImpactedFile(
                path="order_app/service.py",
                reason="订单服务缺少取消订单功能",
                suggested_changes=["增加 cancel_order 方法"],
                risk_level="medium",
            )
        ],
        new_files=[],
        test_files=["tests/test_service.py"],
        dependency_changes=[],
    )


def build_test_graph():
    """创建全部使用假依赖的测试工作流。"""

    return build_change_analysis_graph(
        requirement_analyzer=fake_requirement_analyzer,
        repository_resolver=fake_repository_resolver,
        repository_scanner=fake_repository_scanner,
        code_impact_analyzer=fake_code_impact_analyzer,
    )


def test_change_analysis_graph_runs_complete_workflow() -> None:
    """工作流应该依次完成需求分析和代码影响分析。"""

    graph = build_test_graph()

    result = graph.invoke(
        {
            "request_text": "  为订单系统增加   取消功能  ",
            "normalized_request": "",
            "repository_name": "order_service",
            "requirement_analysis": None,
            "repository_snapshot": None,
            "code_impact_analysis": None,
            "status": "received",
        }
    )

    requirement_analysis = result["requirement_analysis"]
    code_impact_analysis = result["code_impact_analysis"]

    assert result["normalized_request"] == "为订单系统增加 取消功能"
    assert result["status"] == "code_impact_analyzed"
    assert requirement_analysis is not None
    assert code_impact_analysis is not None
    assert requirement_analysis.change_type == "feature"
    assert code_impact_analysis.impacted_files[0].path == "order_app/service.py"


def test_change_analysis_graph_rejects_empty_request() -> None:
    """空需求应该在进入智能体之前被拒绝。"""

    graph = build_test_graph()

    with pytest.raises(
        ValueError,
        match="变更需求不能为空",
    ):
        graph.invoke(
            {
                "request_text": "   ",
                "normalized_request": "",
                "repository_name": "order_service",
                "requirement_analysis": None,
                "repository_snapshot": None,
                "code_impact_analysis": None,
                "status": "received",
            }
        )
