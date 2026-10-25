"""实施计划智能体的自动化测试。"""

import json

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.schemas.code_impact import (
    CodeImpactAnalysis,
    ImpactedFile,
)
from app.schemas.dependency_graph import (
    RepositoryDependencyGraph,
)
from app.schemas.human_review import (
    RequirementReviewDecision,
)
from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.schemas.requirement import RequirementAnalysis
from app.schemas.test_strategy import (
    TestFilePlan as StrategyFilePlan,
)
from app.schemas.test_strategy import (
    TestScenario as StrategyScenario,
)
from app.schemas.test_strategy import (
    TestStrategy as StrategyResult,
)
from app.services.implementation_plan_agent import (
    ImplementationPlanValidationError as PlanValidationError,
)
from app.services.implementation_plan_agent import (
    generate_implementation_plan,
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
            "订单服务",
            "审计日志",
        ],
        acceptance_criteria=[
            "只有待支付订单可以取消",
            "取消成功后必须记录审计日志",
        ],
        risks=[
            "支付与取消并发时可能发生状态冲突",
        ],
    )


def build_repository() -> RepositorySnapshot:
    """创建包含生产代码和现有测试的仓库快照。"""

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
    """创建经过真实性校验的代码影响分析。"""

    return CodeImpactAnalysis(
        summary="需要修改订单服务并补充测试",
        impacted_files=[
            ImpactedFile(
                path="order_app/service.py",
                reason="实现取消订单业务规则",
                suggested_changes=[
                    "新增 cancel_order 方法",
                ],
                risk_level="high",
            )
        ],
        new_files=[],
        test_files=[
            "tests/test_service.py",
        ],
        dependency_changes=[],
    )


def build_test_strategy() -> StrategyResult:
    """创建经过真实性校验的测试策略。"""

    return StrategyResult(
        summary="覆盖取消规则和审计日志",
        scenarios=[
            StrategyScenario(
                name="取消待支付订单",
                level="unit",
                category="happy_path",
                priority="high",
                target="OrderService.cancel_order",
                description="验证待支付订单可以取消",
                related_acceptance_criteria=[
                    "只有待支付订单可以取消",
                ],
                preconditions=[
                    "订单状态为 PENDING",
                ],
                steps=[
                    "调用 cancel_order",
                ],
                expected_result=("订单状态变为 CANCELLED"),
            ),
            StrategyScenario(
                name="取消成功写入审计日志",
                level="integration",
                category="regression",
                priority="high",
                target="OrderService 与审计日志组件",
                description="验证取消成功后写入审计记录",
                related_acceptance_criteria=[
                    "取消成功后必须记录审计日志",
                ],
                preconditions=[
                    "订单状态为 PENDING",
                ],
                steps=[
                    "取消订单",
                    "读取审计记录",
                ],
                expected_result=("存在对应订单的取消审计记录"),
            ),
        ],
        test_files=[
            StrategyFilePlan(
                path="tests/test_service.py",
                action="modify",
                reason="补充订单服务单元测试",
                scenario_names=[
                    "取消待支付订单",
                ],
            ),
            StrategyFilePlan(
                path=("tests/test_cancel_integration.py"),
                action="create",
                reason="新增取消与审计日志集成测试",
                scenario_names=[
                    "取消成功写入审计日志",
                ],
            ),
        ],
        coverage_gaps=[],
        risks=[
            "并发场景需要额外测试环境",
        ],
    )


def build_symbol_index():
    """根据测试仓库建立真实符号索引。"""

    return index_python_symbols(build_repository())


def build_dependency_graph() -> RepositoryDependencyGraph:
    """根据测试仓库建立真实依赖图。"""

    return build_python_dependency_graph(build_symbol_index())


def build_valid_plan_payload() -> dict[str, object]:
    """创建满足全部追踪和顺序约束的实施计划。"""

    return {
        "summary": ("先实现业务逻辑，再补充测试，最后执行完整验证"),
        "steps": [
            {
                "step_id": "step-update-service",
                "title": "实现取消订单逻辑",
                "action": "modify",
                "target_path": ("order_app\\service.py"),
                "priority": "high",
                "description": ("在订单服务中实现状态校验和审计记录"),
                "changes": [
                    "新增 cancel_order 方法",
                    "拒绝取消非 PENDING 订单",
                    "取消成功后记录审计日志",
                ],
                "depends_on": [],
                "related_acceptance_criteria": [
                    "只有待支付订单可以取消",
                    "取消成功后必须记录审计日志",
                ],
                "related_test_scenarios": [],
                "risk_level": "high",
                "verification_commands": [],
                "rollback_instructions": [
                    "恢复订单服务修改前的版本",
                ],
            },
            {
                "step_id": ("step-update-unit-tests"),
                "title": "补充取消订单单元测试",
                "action": "modify",
                "target_path": ("tests/test_service.py"),
                "priority": "high",
                "description": ("验证待支付状态和拒绝规则"),
                "changes": [
                    "增加取消待支付订单测试",
                ],
                "depends_on": [
                    "step-update-service",
                ],
                "related_acceptance_criteria": [
                    "只有待支付订单可以取消",
                ],
                "related_test_scenarios": [
                    "取消待支付订单",
                ],
                "risk_level": "medium",
                "verification_commands": [
                    "python -m pytest tests/test_service.py -q",
                ],
                "rollback_instructions": [
                    "删除本步骤新增的单元测试",
                ],
            },
            {
                "step_id": ("step-create-integration-tests"),
                "title": "增加取消审计集成测试",
                "action": "create",
                "target_path": ("tests/test_cancel_integration.py"),
                "priority": "high",
                "description": ("验证订单取消与审计日志之间的协作"),
                "changes": [
                    "新增取消成功后写入审计日志的集成测试",
                ],
                "depends_on": [
                    "step-update-service",
                ],
                "related_acceptance_criteria": [
                    "取消成功后必须记录审计日志",
                ],
                "related_test_scenarios": [
                    "取消成功写入审计日志",
                ],
                "risk_level": "medium",
                "verification_commands": [
                    "python -m pytest tests/test_cancel_integration.py -q",
                ],
                "rollback_instructions": [
                    "删除新建的集成测试文件",
                ],
            },
            {
                "step_id": ("step-run-verification"),
                "title": "执行完整质量检查",
                "action": "verify",
                "target_path": None,
                "priority": "high",
                "description": ("运行静态检查和完整自动化测试"),
                "changes": [
                    "执行 Ruff",
                    "执行完整 pytest",
                ],
                "depends_on": [
                    "step-update-unit-tests",
                    "step-create-integration-tests",
                ],
                "related_acceptance_criteria": [
                    "只有待支付订单可以取消",
                    "取消成功后必须记录审计日志",
                ],
                "related_test_scenarios": [
                    "取消待支付订单",
                    "取消成功写入审计日志",
                ],
                "risk_level": "low",
                "verification_commands": [
                    "python -m ruff check .",
                    "python -m pytest -q",
                ],
                "rollback_instructions": [
                    "根据失败步骤恢复对应文件",
                ],
            },
        ],
        "execution_order": [
            "step-update-service",
            "step-update-unit-tests",
            "step-create-integration-tests",
            "step-run-verification",
        ],
        "final_verification_commands": [
            "python -m ruff check .",
            "python -m pytest -q",
        ],
        "rollback_checkpoints": [
            "修改生产代码前记录 Git 状态",
            "完整测试通过后创建提交",
        ],
        "risks": [
            "支付和取消并发需要单独验证",
        ],
        "assumptions": [],
    }


def build_model(
    payload: dict[str, object] | None = None,
) -> FakeListChatModel:
    """创建返回指定实施计划的假模型。"""

    response_payload = payload or build_valid_plan_payload()

    return FakeListChatModel(
        responses=[
            json.dumps(
                response_payload,
                ensure_ascii=False,
            )
        ]
    )


def generate_with_model(
    model: FakeListChatModel,
):
    """使用统一测试数据调用实施计划智能体。"""

    return generate_implementation_plan(
        requirement=build_requirement(),
        code_impact=build_code_impact(),
        test_strategy=build_test_strategy(),
        repository=build_repository(),
        symbol_index=build_symbol_index(),
        dependency_graph=(build_dependency_graph()),
        model=model,
    )


def test_agent_returns_validated_implementation_plan() -> None:
    """Agent 应返回顺序明确且路径真实的实施计划。"""

    result = generate_with_model(build_model())

    assert len(result.steps) == 4
    assert result.steps[0].target_path == ("order_app/service.py")
    assert result.steps[-1].action == "verify"
    assert result.execution_order[-1] == ("step-run-verification")


def test_agent_rejects_empty_repository() -> None:
    """没有源码时不能生成有依据的实施计划。"""

    empty_repository = RepositorySnapshot(
        repository_name="empty",
        files=[],
        scanned_file_count=0,
        truncated=False,
    )

    with pytest.raises(
        ValueError,
        match="没有可用于生成实施计划的文件",
    ):
        generate_implementation_plan(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            test_strategy=build_test_strategy(),
            repository=empty_repository,
            model=build_model(),
        )


def test_agent_rejects_unapproved_requirement() -> None:
    """人工拒绝需求后不能继续生成实施计划。"""

    review = RequirementReviewDecision(
        action="reject",
        feedback="业务规则仍需确认。",
    )

    with pytest.raises(
        ValueError,
        match="需求尚未通过人工审核",
    ):
        generate_implementation_plan(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            test_strategy=build_test_strategy(),
            repository=build_repository(),
            review=review,
            model=build_model(),
        )


def test_agent_rejects_unknown_modified_file() -> None:
    """修改步骤不能引用仓库中不存在的文件。"""

    payload = build_valid_plan_payload()
    payload["steps"][0]["target_path"] = "order_app/missing.py"

    with pytest.raises(
        PlanValidationError,
        match="需要修改的文件在仓库中不存在",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_unconfirmed_existing_file() -> None:
    """修改步骤不能扩大到上游分析未确认的现有文件。"""

    payload = build_valid_plan_payload()
    payload["steps"][0]["target_path"] = "order_app/models.py"

    with pytest.raises(
        PlanValidationError,
        match="上游分析未确认的文件",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_existing_file_as_new_file() -> None:
    """create 步骤不能引用已经存在的文件。"""

    payload = build_valid_plan_payload()
    payload["steps"][2]["target_path"] = "tests/test_service.py"

    with pytest.raises(
        PlanValidationError,
        match="建议新建的文件在仓库中已经存在",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_unsafe_target_path() -> None:
    """实施步骤不能使用目录穿越路径。"""

    payload = build_valid_plan_payload()
    payload["steps"][2]["target_path"] = "../outside/test_cancel.py"

    with pytest.raises(
        PlanValidationError,
        match="目录穿越路径",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_duplicate_step_ids() -> None:
    """每个实施步骤必须有唯一标识。"""

    payload = build_valid_plan_payload()
    payload["steps"][1]["step_id"] = "step-update-service"

    with pytest.raises(
        PlanValidationError,
        match="steps 包含重复步骤标识",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_incomplete_execution_order() -> None:
    """执行顺序不能遗漏任何实施步骤。"""

    payload = build_valid_plan_payload()
    payload["execution_order"].remove("step-run-verification")

    with pytest.raises(
        PlanValidationError,
        match="execution_order 遗漏步骤",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_dependency_that_runs_later() -> None:
    """前置步骤必须排在当前步骤之前。"""

    payload = build_valid_plan_payload()
    payload["steps"][0]["depends_on"] = ["step-run-verification"]

    with pytest.raises(
        PlanValidationError,
        match="前置步骤没有排在它之前",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_unknown_acceptance_criterion() -> None:
    """实施步骤不能虚构需求验收条件。"""

    payload = build_valid_plan_payload()
    payload["steps"][0]["related_acceptance_criteria"] = ["系统自动退款"]

    with pytest.raises(
        PlanValidationError,
        match="需求中不存在的验收条件",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_unknown_test_scenario() -> None:
    """实施步骤不能引用测试策略中不存在的场景。"""

    payload = build_valid_plan_payload()
    payload["steps"][1]["related_test_scenarios"] = ["不存在的场景"]

    with pytest.raises(
        PlanValidationError,
        match="不存在的测试场景",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_uncovered_test_scenario() -> None:
    """实施计划必须关联测试策略中的全部场景。"""

    payload = build_valid_plan_payload()
    payload["steps"][2]["related_test_scenarios"] = []
    payload["steps"][3]["related_test_scenarios"] = ["取消待支付订单"]

    with pytest.raises(
        PlanValidationError,
        match="没有关联全部测试场景",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_plan_without_verify_step() -> None:
    """实施计划必须包含最终验证步骤。"""

    payload = build_valid_plan_payload()
    payload["steps"].pop()
    payload["execution_order"].pop()

    with pytest.raises(
        PlanValidationError,
        match="至少需要一个 verify 步骤",
    ):
        generate_with_model(build_model(payload))


def test_agent_rejects_mismatched_symbol_index() -> None:
    """其他仓库的符号索引不能用于当前实施计划。"""

    mismatched_index = build_symbol_index().model_copy(
        update={"repository_name": ("another_repository")}
    )

    with pytest.raises(
        ValueError,
        match=("符号索引与代码仓库快照不属于同一个代码仓库"),
    ):
        generate_implementation_plan(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            test_strategy=build_test_strategy(),
            repository=build_repository(),
            symbol_index=mismatched_index,
            model=build_model(),
        )


def test_agent_rejects_mismatched_dependency_graph() -> None:
    """其他仓库的依赖图不能用于当前实施计划。"""

    mismatched_graph = build_dependency_graph().model_copy(
        update={"repository_name": ("another_repository")}
    )

    with pytest.raises(
        ValueError,
        match=("模块依赖图与代码仓库快照不属于同一个代码仓库"),
    ):
        generate_implementation_plan(
            requirement=build_requirement(),
            code_impact=build_code_impact(),
            test_strategy=build_test_strategy(),
            repository=build_repository(),
            symbol_index=build_symbol_index(),
            dependency_graph=mismatched_graph,
            model=build_model(),
        )
