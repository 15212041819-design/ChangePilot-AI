"""测试策略智能体的自动化测试。"""

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.schemas.code_impact import (
    CodeImpactAnalysis,
    ImpactedFile,
)
from app.schemas.dependency_graph import RepositoryDependencyGraph
from app.schemas.human_review import RequirementReviewDecision
from app.schemas.repository import RepositoryFile, RepositorySnapshot
from app.schemas.requirement import RequirementAnalysis
from app.services.test_strategy_agent import (
    TestStrategyValidationError as StrategyValidationError,
)
from app.services.test_strategy_agent import (
    generate_test_strategy,
)
from app.tools.python_dependency_graph import (
    build_python_dependency_graph,
)
from app.tools.python_symbol_indexer import index_python_symbols


def build_requirement() -> RequirementAnalysis:
    """创建测试使用的结构化需求。"""

    return RequirementAnalysis(
        summary="为订单系统增加取消订单功能",
        change_type="feature",
        affected_modules=["订单服务", "审计日志"],
        acceptance_criteria=[
            "只有待支付订单可以取消",
            "取消成功后必须记录审计日志",
        ],
        risks=["支付与取消并发时可能发生状态冲突"],
    )


def build_repository() -> RepositorySnapshot:
    """创建包含业务代码和现有测试的仓库快照。"""

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
                size_bytes=200,
                content=(
                    "from .models import OrderStatus\n\n"
                    "class OrderService:\n"
                    "    def pay_order("
                    "self, order_id: str"
                    ") -> None:\n"
                    "        pass\n"
                ),
            ),
            RepositoryFile(
                relative_path="tests/test_service.py",
                language="python",
                size_bytes=100,
                content=(
                    "from order_app.service "
                    "import OrderService\n\n"
                    "def test_pay_order():\n"
                    "    pass\n"
                ),
            ),
        ],
        scanned_file_count=3,
        truncated=False,
    )


def build_code_impact() -> CodeImpactAnalysis:
    """创建已经通过真实性校验的代码影响分析。"""

    return CodeImpactAnalysis(
        summary="需要扩展订单状态、订单服务和现有测试",
        impacted_files=[
            ImpactedFile(
                path="order_app/models.py",
                reason="需要新增已取消状态",
                suggested_changes=["新增 CANCELLED 状态"],
                risk_level="medium",
            ),
            ImpactedFile(
                path="order_app/service.py",
                reason="需要实现取消订单逻辑",
                suggested_changes=["新增 cancel_order 方法"],
                risk_level="high",
            ),
            ImpactedFile(
                path="tests/test_service.py",
                reason="需要补充取消订单测试",
                suggested_changes=["增加取消场景测试"],
                risk_level="low",
            ),
        ],
        new_files=[],
        test_files=["tests/test_service.py"],
        dependency_changes=[],
    )


def build_symbol_index():
    """根据测试仓库建立真实符号索引。"""

    return index_python_symbols(build_repository())


def build_dependency_graph() -> RepositoryDependencyGraph:
    """根据测试仓库建立真实依赖图。"""

    return build_python_dependency_graph(build_symbol_index())


def build_valid_model() -> FakeListChatModel:
    """创建返回有效测试策略的假模型。"""

    return FakeListChatModel(
        responses=[
            """
            {
                "summary": "优先验证取消规则和审计日志",
                "scenarios": [
                    {
                        "name": "取消待支付订单",
                        "level": "unit",
                        "category": "happy_path",
                        "priority": "high",
                        "target": "OrderService.cancel_order",
                        "description": "验证待支付订单可以被取消",
                        "related_acceptance_criteria": [
                            "只有待支付订单可以取消"
                        ],
                        "preconditions": [
                            "订单状态为 PENDING"
                        ],
                        "steps": [
                            "调用 cancel_order"
                        ],
                        "expected_result": "订单进入 CANCELLED 状态"
                    },
                    {
                        "name": "取消成功写入审计日志",
                        "level": "integration",
                        "category": "regression",
                        "priority": "high",
                        "target": "OrderService 与审计日志组件",
                        "description": "验证取消成功后的审计记录",
                        "related_acceptance_criteria": [
                            "取消成功后必须记录审计日志"
                        ],
                        "preconditions": [
                            "订单状态为 PENDING"
                        ],
                        "steps": [
                            "取消订单",
                            "读取审计记录"
                        ],
                        "expected_result": "存在对应订单的取消审计记录"
                    }
                ],
                "test_files": [
                    {
                        "path": "tests/test_service.py",
                        "action": "modify",
                        "reason": "补充订单服务单元测试",
                        "scenario_names": [
                            "取消待支付订单"
                        ]
                    },
                    {
                        "path": "tests/test_cancel_integration.py",
                        "action": "create",
                        "reason": "新增取消订单与审计日志集成测试",
                        "scenario_names": [
                            "取消成功写入审计日志"
                        ]
                    }
                ],
                "coverage_gaps": [
                    "当前仓库没有真实数据库实现"
                ],
                "risks": [
                    "并发支付与取消需要额外测试环境"
                ]
            }
            """
        ]
    )


def test_agent_returns_validated_test_strategy() -> None:
    """Agent 应返回可追踪且路径真实的测试策略。"""

    result = generate_test_strategy(
        requirement=build_requirement(),
        code_impact=build_code_impact(),
        repository=build_repository(),
        symbol_index=build_symbol_index(),
        dependency_graph=build_dependency_graph(),
        model=build_valid_model(),
    )

    assert len(result.scenarios) == 2
    assert result.scenarios[0].level == "unit"
    assert result.scenarios[1].level == "integration"
    assert result.test_files[0].path == "tests/test_service.py"
    assert result.test_files[0].action == "modify"
    assert result.test_files[1].action == "create"


def test_agent_rejects_empty_repository() -> None:
    """没有源码时不能生成有依据的测试策略。"""

    empty_repository = RepositorySnapshot(
        repository_name="empty",
        files=[],
        scanned_file_count=0,
        truncated=False,
    )

    with pytest.raises(
        ValueError,
        match="没有可用于设计测试策略的文件",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=empty_repository,
            model=build_valid_model(),
        )


def test_agent_rejects_unapproved_requirement() -> None:
    """人工拒绝需求后不能继续生成测试策略。"""

    review = RequirementReviewDecision(
        action="reject",
        feedback="取消状态仍需确认。",
    )

    with pytest.raises(
        ValueError,
        match="需求尚未通过人工审核",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=build_repository(),
            review=review,
            model=build_valid_model(),
        )


def test_agent_rejects_missing_existing_test_file() -> None:
    """modify 计划必须引用仓库中存在的测试文件。"""

    model = FakeListChatModel(
        responses=[
            build_valid_model()
            .responses[0]
            .replace(
                "tests/test_service.py",
                "tests/test_missing.py",
            )
        ]
    )

    with pytest.raises(
        StrategyValidationError,
        match="需要修改的测试文件在仓库中不存在",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=build_repository(),
            model=model,
        )


def test_agent_rejects_existing_file_as_new_file() -> None:
    """create 计划不能引用仓库中已经存在的文件。"""

    model = FakeListChatModel(
        responses=[
            build_valid_model()
            .responses[0]
            .replace(
                '"path": "tests/test_cancel_integration.py"',
                '"path": "tests/test_service.py"',
            )
        ]
    )

    with pytest.raises(
        StrategyValidationError,
        match="建议新建的测试文件在仓库中已经存在",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=build_repository(),
            model=model,
        )


def test_agent_rejects_unknown_acceptance_criterion() -> None:
    """场景不能虚构需求中不存在的验收条件。"""

    model = FakeListChatModel(
        responses=[
            build_valid_model()
            .responses[0]
            .replace(
                "只有待支付订单可以取消",
                "系统必须自动退款",
            )
        ]
    )

    with pytest.raises(
        StrategyValidationError,
        match="需求中不存在的验收条件",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=build_repository(),
            model=model,
        )


def test_agent_rejects_unknown_scenario_reference() -> None:
    """测试文件不能引用不存在的场景名称。"""

    model = FakeListChatModel(
        responses=[
            build_valid_model()
            .responses[0]
            .replace(
                '"取消待支付订单"\n                        ]',
                '"不存在的场景"\n                        ]',
                1,
            )
        ]
    )

    with pytest.raises(
        StrategyValidationError,
        match="不存在的测试场景",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=build_repository(),
            model=model,
        )


def test_agent_rejects_unsafe_test_path() -> None:
    """测试文件计划不能通过目录穿越逃离仓库。"""

    model = FakeListChatModel(
        responses=[
            build_valid_model()
            .responses[0]
            .replace(
                "tests/test_cancel_integration.py",
                "../outside/test_cancel.py",
            )
        ]
    )

    with pytest.raises(
        StrategyValidationError,
        match="目录穿越路径",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=build_repository(),
            model=model,
        )


def test_agent_rejects_dependency_graph_without_symbol_index() -> None:
    """依赖图不能脱离符号索引单独进入测试策略 Agent。"""

    with pytest.raises(
        ValueError,
        match="必须同时提供 Python 符号索引",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=build_repository(),
            dependency_graph=build_dependency_graph(),
            model=build_valid_model(),
        )


def test_agent_rejects_mismatched_symbol_index() -> None:
    """其他仓库的符号索引不能用于当前测试策略。"""

    mismatched_index = build_symbol_index().model_copy(
        update={
            "repository_name": "another_repository",
        }
    )

    with pytest.raises(
        ValueError,
        match="符号索引与代码仓库快照不属于同一个代码仓库",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=build_repository(),
            symbol_index=mismatched_index,
            model=build_valid_model(),
        )


def test_agent_rejects_mismatched_dependency_graph() -> None:
    """其他仓库的依赖图不能用于当前测试策略。"""

    mismatched_graph = build_dependency_graph().model_copy(
        update={
            "repository_name": "another_repository",
        }
    )

    with pytest.raises(
        ValueError,
        match="模块依赖图与代码仓库快照不属于同一个代码仓库",
    ):
        generate_test_strategy(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            repository=build_repository(),
            symbol_index=build_symbol_index(),
            dependency_graph=mismatched_graph,
            model=build_valid_model(),
        )
