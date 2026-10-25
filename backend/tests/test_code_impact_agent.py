"""代码影响分析智能体的自动化测试。

本模块使用 FakeListChatModel，
避免测试过程中发送真实 DeepSeek 请求。

测试覆盖：

1. 返回结构化代码影响分析。
2. 使用真实仓库文件路径。
3. 使用 Python AST 符号索引。
4. 使用 Python 模块依赖图。
5. 拒绝空仓库。
6. 拒绝不存在的受影响文件。
7. 使用人工批准反馈。
8. 拒绝未通过审核的需求。
9. 拒绝来自其他仓库的符号索引。
10. 拒绝无符号索引支持的依赖图。
11. 拒绝来自其他仓库的依赖图。
12. 拒绝包含未知文件的依赖图。
"""

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.schemas.dependency_graph import PythonModuleNode
from app.schemas.human_review import (
    RequirementReviewDecision,
)
from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.schemas.requirement import RequirementAnalysis
from app.services.code_impact_agent import (
    analyze_code_impact,
    format_repository_dependency_graph,
    format_repository_symbol_index,
    format_requirement_review,
)
from app.tools.python_dependency_graph import (
    build_python_dependency_graph,
)
from app.tools.python_symbol_indexer import (
    index_python_symbols,
)


def build_requirement() -> RequirementAnalysis:
    """创建测试使用的结构化需求。"""

    return RequirementAnalysis(
        summary="为订单系统增加取消订单功能",
        change_type="feature",
        affected_modules=[
            "订单模型",
            "订单服务",
            "审计日志",
        ],
        acceptance_criteria=[
            "只有待支付订单可以取消",
            "取消订单后必须记录审计日志",
        ],
        risks=["支付和取消同时发生可能导致状态冲突"],
    )


def build_repository() -> RepositorySnapshot:
    """创建包含业务代码和测试代码的仓库快照。"""

    return RepositorySnapshot(
        repository_name="order_service",
        files=[
            RepositoryFile(
                relative_path="order_app/models.py",
                language="python",
                size_bytes=100,
                content=('class OrderStatus:\n    PENDING = "pending"\n    PAID = "paid"\n'),
            ),
            RepositoryFile(
                relative_path="order_app/service.py",
                language="python",
                size_bytes=100,
                content=(
                    "from .models import OrderStatus\n\n"
                    "class OrderService:\n"
                    "    def pay_order(\n"
                    "        self,\n"
                    "        order_id: str,\n"
                    "    ) -> None:\n"
                    "        pass\n"
                ),
            ),
            RepositoryFile(
                relative_path="tests/test_service.py",
                language="python",
                size_bytes=100,
                content=("def test_pay_pending_order():\n    pass\n"),
            ),
        ],
        scanned_file_count=3,
        truncated=False,
    )


def build_symbol_index():
    """根据测试仓库生成真实 Python AST 符号索引。"""

    return index_python_symbols(build_repository())


def build_dependency_graph():
    """根据测试仓库的符号索引生成真实模块依赖图。"""

    return build_python_dependency_graph(build_symbol_index())


def build_valid_fake_model() -> FakeListChatModel:
    """创建返回有效代码影响分析的假模型。"""

    return FakeListChatModel(
        responses=[
            """
            {
                "summary": "需要扩展订单状态和订单业务服务",
                "impacted_files": [
                    {
                        "path": "order_app/models.py",
                        "reason": "订单缺少已取消状态",
                        "suggested_changes": [
                            "增加 CANCELLED 订单状态"
                        ],
                        "risk_level": "medium"
                    },
                    {
                        "path": "order_app/service.py",
                        "reason": "订单服务缺少取消操作",
                        "suggested_changes": [
                            "增加取消待支付订单的方法",
                            "取消成功后记录审计日志"
                        ],
                        "risk_level": "high"
                    },
                    {
                        "path": "tests/test_service.py",
                        "reason": "需要验证取消订单业务规则",
                        "suggested_changes": [
                            "增加取消待支付订单测试",
                            "增加已支付订单不能取消的测试"
                        ],
                        "risk_level": "low"
                    }
                ],
                "new_files": [],
                "test_files": [
                    "tests/test_service.py"
                ],
                "dependency_changes": []
            }
            """
        ]
    )


def test_code_impact_agent_returns_structured_result() -> None:
    """Agent 应返回基于源码、符号索引和依赖图的结果。"""

    repository = build_repository()
    symbol_index = index_python_symbols(repository)
    dependency_graph = build_python_dependency_graph(symbol_index)

    result = analyze_code_impact(
        requirement=build_requirement(),
        repository=repository,
        symbol_index=symbol_index,
        dependency_graph=dependency_graph,
        model=build_valid_fake_model(),
    )

    assert len(result.impacted_files) == 3
    assert result.impacted_files[0].path == ("order_app/models.py")
    assert result.impacted_files[1].risk_level == "high"
    assert result.test_files == ["tests/test_service.py"]
    assert result.dependency_changes == []


def test_code_impact_agent_rejects_empty_repository() -> None:
    """Agent 应拒绝没有任何源码文件的仓库。"""

    empty_repository = RepositorySnapshot(
        repository_name="empty",
        files=[],
        scanned_file_count=0,
        truncated=False,
    )

    fake_model = FakeListChatModel(responses=["{}"])

    with pytest.raises(
        ValueError,
        match="代码仓库中没有可分析的文件",
    ):
        analyze_code_impact(
            requirement=build_requirement(),
            repository=empty_repository,
            model=fake_model,
        )


def test_code_impact_agent_rejects_unknown_file() -> None:
    """Agent 不应把不存在的文件当作现有文件。"""

    fake_model = FakeListChatModel(
        responses=[
            """
            {
                "summary": "错误地引用了不存在的文件",
                "impacted_files": [
                    {
                        "path": "order_app/cancel.py",
                        "reason": "错误地认为该文件存在",
                        "suggested_changes": [
                            "修改取消订单逻辑"
                        ],
                        "risk_level": "medium"
                    }
                ],
                "new_files": [],
                "test_files": [],
                "dependency_changes": []
            }
            """
        ]
    )

    with pytest.raises(
        ValueError,
        match="仓库中不存在的文件",
    ):
        analyze_code_impact(
            requirement=build_requirement(),
            repository=build_repository(),
            symbol_index=build_symbol_index(),
            model=fake_model,
        )


def test_format_requirement_review_includes_approved_feedback() -> None:
    """人工批准动作和反馈都应该进入模型上下文。"""

    review = RequirementReviewDecision(
        action="approve",
        feedback=("审计日志需要记录订单号、取消时间和操作结果。"),
    )

    formatted_review = format_requirement_review(review)

    assert '"action": "approve"' in formatted_review
    assert "审计日志需要记录订单号、取消时间和操作结果" in formatted_review


def test_format_symbol_index_includes_code_evidence() -> None:
    """符号索引上下文应包含模块、类、方法和导入关系。"""

    symbol_index = build_symbol_index()

    formatted_index = format_repository_symbol_index(symbol_index)

    assert '"repository_name": "order_service"' in formatted_index
    assert "order_app.service.OrderService" in formatted_index
    assert "order_app.service.OrderService.pay_order" in formatted_index
    assert '"module": "models"' in formatted_index
    assert '"imported_name": "OrderStatus"' in formatted_index


def test_format_symbol_index_handles_missing_index() -> None:
    """没有索引时应明确提醒 Agent 不要猜测符号。"""

    formatted_index = format_repository_symbol_index(None)

    assert "未提供 Python 符号索引" in formatted_index
    assert "不要猜测不存在的代码符号" in formatted_index


def test_format_dependency_graph_includes_dependency_evidence() -> None:
    """依赖图上下文应包含模块、文件和内部依赖证据。"""

    dependency_graph = build_dependency_graph()

    formatted_graph = format_repository_dependency_graph(dependency_graph)

    assert '"repository_name": "order_service"' in formatted_graph
    assert '"source_module": "order_app.service"' in formatted_graph
    assert '"target_module": "order_app.models"' in formatted_graph
    assert '"target_file": "order_app/models.py"' in formatted_graph
    assert '"kind": "internal"' in formatted_graph


def test_format_dependency_graph_handles_missing_graph() -> None:
    """没有依赖图时应明确提醒 Agent 不要猜测模块关系。"""

    formatted_graph = format_repository_dependency_graph(None)

    assert "未提供 Python 模块依赖图" in formatted_graph
    assert "不要猜测不存在的模块依赖" in formatted_graph


def test_code_impact_agent_rejects_unapproved_requirement() -> None:
    """人工拒绝需求后不应该继续分析代码影响。"""

    rejected_review = RequirementReviewDecision(
        action="reject",
        feedback="审计日志字段还需要进一步确认。",
    )

    fake_model = FakeListChatModel(
        responses=[
            """
            {
                "summary": "不应该生成该结果",
                "impacted_files": [],
                "new_files": [],
                "test_files": [],
                "dependency_changes": []
            }
            """
        ]
    )

    with pytest.raises(
        ValueError,
        match="需求尚未通过人工审核",
    ):
        analyze_code_impact(
            requirement=build_requirement(),
            repository=build_repository(),
            review=rejected_review,
            symbol_index=build_symbol_index(),
            model=fake_model,
        )


def test_code_impact_agent_rejects_mismatched_symbol_index() -> None:
    """其他仓库的符号索引不能用于当前代码分析。"""

    symbol_index = build_symbol_index()

    # 创建一个内容相同但仓库名称不同的索引，
    # 模拟工作流错误地混用了两个项目的数据。
    mismatched_index = symbol_index.model_copy(update={"repository_name": "another_repository"})

    fake_model = build_valid_fake_model()

    with pytest.raises(
        ValueError,
        match="不属于同一个代码仓库",
    ):
        analyze_code_impact(
            requirement=build_requirement(),
            repository=build_repository(),
            symbol_index=mismatched_index,
            model=fake_model,
        )


def test_code_impact_agent_rejects_dependency_graph_without_symbol_index() -> None:
    """依赖图不能脱离生成它的符号索引单独使用。"""

    with pytest.raises(
        ValueError,
        match="必须同时提供 Python 符号索引",
    ):
        analyze_code_impact(
            requirement=build_requirement(),
            repository=build_repository(),
            dependency_graph=build_dependency_graph(),
            model=build_valid_fake_model(),
        )


def test_code_impact_agent_rejects_mismatched_dependency_graph() -> None:
    """其他仓库的依赖图不能用于当前代码分析。"""

    dependency_graph = build_dependency_graph()
    mismatched_graph = dependency_graph.model_copy(update={"repository_name": "another_repository"})

    with pytest.raises(
        ValueError,
        match=("模块依赖图与代码仓库快照不属于同一个代码仓库"),
    ):
        analyze_code_impact(
            requirement=build_requirement(),
            repository=build_repository(),
            symbol_index=build_symbol_index(),
            dependency_graph=mismatched_graph,
            model=build_valid_fake_model(),
        )


def test_code_impact_agent_rejects_unknown_dependency_graph_file() -> None:
    """依赖图不能引用当前仓库快照中不存在的文件。"""

    dependency_graph = build_dependency_graph()
    invalid_modules = [
        *dependency_graph.modules,
        PythonModuleNode(
            module_name="order_app.missing",
            file_path="order_app/missing.py",
            has_parse_error=False,
        ),
    ]
    invalid_graph = dependency_graph.model_copy(update={"modules": invalid_modules})

    with pytest.raises(
        ValueError,
        match=("模块依赖图包含代码仓库快照中不存在的文件"),
    ):
        analyze_code_impact(
            requirement=build_requirement(),
            repository=build_repository(),
            symbol_index=build_symbol_index(),
            dependency_graph=invalid_graph,
            model=build_valid_fake_model(),
        )
