"""完整软件变更分析工作流的自动化测试。

本模块验证 LangGraph 各节点的执行顺序以及人工审核分支。

重点验证：

1. 普通工作流能够完成需求分析、仓库扫描、符号索引、
   依赖图和影响分析。
2. 空需求会在调用智能体前被拒绝。
3. 人工审核批准前不会扫描、索引或建立依赖图。
4. 人工批准后才会建立符号索引和模块依赖图。
5. 依赖图会作为正式证据传给代码影响智能体。
6. 代码影响分析会继续生成结构化测试策略。
7. 测试策略会继续生成有顺序的实施计划。
8. 人工拒绝后会修订需求并再次等待审核。
"""

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph.change_analysis import build_change_analysis_graph
from app.schemas.code_impact import (
    CodeImpactAnalysis,
    ImpactedFile,
)
from app.schemas.code_index import RepositorySymbolIndex
from app.schemas.dependency_graph import RepositoryDependencyGraph
from app.schemas.human_review import RequirementReviewDecision
from app.schemas.implementation_plan import (
    ImplementationPlan,
    ImplementationStep,
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


def fake_requirement_analyzer(
    request_text: str,
) -> RequirementAnalysis:
    """模拟首次需求分析智能体。"""

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
                content=("class OrderService:\n    pass\n"),
            ),
            RepositoryFile(
                relative_path="tests/test_service.py",
                language="python",
                size_bytes=100,
                content=(
                    "from order_app.service "
                    "import OrderService\n\n"
                    "def test_create_order():\n"
                    "    pass\n"
                ),
            ),
        ],
        scanned_file_count=2,
        truncated=False,
    )


def fake_code_impact_analyzer(
    requirement: RequirementAnalysis,
    repository: RepositorySnapshot,
    review: RequirementReviewDecision | None,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
) -> CodeImpactAnalysis:
    """模拟接收符号索引与依赖图的影响分析智能体。"""

    assert requirement.change_type == "feature"
    assert repository.repository_name == "order_service"

    # 验证工作流不是只建立索引，
    # 而是确实把索引传给代码影响分析智能体。
    assert symbol_index.repository_name == repository.repository_name
    assert symbol_index.symbol_count == 2
    assert symbol_index.parse_error_count == 0
    assert symbol_index.import_count == 1

    # 验证工作流已经根据 import 记录建立依赖图，
    # 并把依赖图作为第五个参数交给影响分析智能体。
    assert dependency_graph.repository_name == repository.repository_name
    assert dependency_graph.internal_dependency_count == 1
    assert dependency_graph.external_dependency_count == 0
    assert dependency_graph.unresolved_internal_dependency_count == 0

    dependency = dependency_graph.dependencies[0]

    assert dependency.source_module == "tests.test_service"
    assert dependency.target_module == "order_app.service"
    assert dependency.target_file == "order_app/service.py"

    # 当需求中包含假设时，
    # 必须先得到人工批准才能进入本节点。
    if requirement.assumptions:
        assert review is not None
        assert review.action == "approve"
        assert review.feedback

    return CodeImpactAnalysis(
        summary="需要修改订单服务并补充测试",
        impacted_files=[
            ImpactedFile(
                path="order_app/service.py",
                reason="订单服务缺少取消订单功能",
                suggested_changes=[
                    "增加 cancel_order 方法",
                ],
                risk_level="medium",
            )
        ],
        new_files=[],
        test_files=["tests/test_service.py"],
        dependency_changes=[],
    )


def fake_test_strategy_analyzer(
    requirement: RequirementAnalysis,
    code_impact: CodeImpactAnalysis,
    repository: RepositorySnapshot,
    review: RequirementReviewDecision | None,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
) -> StrategyResult:
    """模拟接收完整上游证据的测试策略智能体。"""

    assert "待支付订单可以取消" in requirement.acceptance_criteria
    assert code_impact.impacted_files[0].path == "order_app/service.py"
    assert repository.repository_name == "order_service"
    assert symbol_index.repository_name == repository.repository_name
    assert dependency_graph.repository_name == repository.repository_name

    if requirement.assumptions:
        assert review is not None
        assert review.action == "approve"

    return StrategyResult(
        summary="验证订单取消功能和相关回归风险",
        scenarios=[
            StrategyScenario(
                name="取消待支付订单",
                level="unit",
                category="happy_path",
                priority="high",
                target="OrderService.cancel_order",
                description="验证待支付订单能够取消",
                related_acceptance_criteria=[
                    "待支付订单可以取消",
                ],
                preconditions=[
                    "订单状态为 PENDING",
                ],
                steps=[
                    "调用 cancel_order",
                ],
                expected_result="订单状态变为 CANCELLED",
            )
        ],
        test_files=[
            StrategyFilePlan(
                path="tests/test_service.py",
                action="modify",
                reason="补充取消订单测试",
                scenario_names=[
                    "取消待支付订单",
                ],
            )
        ],
        coverage_gaps=[],
        risks=[
            "支付和取消并发需要额外验证",
        ],
    )


def fake_implementation_plan_analyzer(
    requirement: RequirementAnalysis,
    code_impact: CodeImpactAnalysis,
    test_strategy: StrategyResult,
    repository: RepositorySnapshot,
    review: RequirementReviewDecision | None,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
) -> ImplementationPlan:
    """模拟接收全部分析结果的实施计划智能体。"""

    # 实施计划不能只根据用户原始需求生成，
    # 必须能够收到前面所有分析阶段产生的真实证据。
    assert "待支付订单可以取消" in requirement.acceptance_criteria
    assert code_impact.impacted_files[0].path == "order_app/service.py"
    assert test_strategy.scenarios[0].name == "取消待支付订单"
    assert repository.repository_name == "order_service"
    assert symbol_index.repository_name == repository.repository_name
    assert dependency_graph.repository_name == repository.repository_name

    if requirement.assumptions:
        assert review is not None
        assert review.action == "approve"

    return ImplementationPlan(
        summary="先修改业务代码，再补充测试并执行验证",
        steps=[
            ImplementationStep(
                step_id="step-update-service",
                title="实现取消订单逻辑",
                action="modify",
                target_path="order_app/service.py",
                priority="high",
                description="实现订单取消业务规则",
                changes=[
                    "新增 cancel_order 方法",
                ],
                depends_on=[],
                related_acceptance_criteria=[
                    "待支付订单可以取消",
                ],
                related_test_scenarios=[],
                risk_level="high",
                verification_commands=[],
                rollback_instructions=[
                    "恢复订单服务修改前的版本",
                ],
            ),
            ImplementationStep(
                step_id="step-update-tests",
                title="补充取消订单测试",
                action="modify",
                target_path="tests/test_service.py",
                priority="high",
                description="覆盖待支付订单取消场景",
                changes=[
                    "新增取消订单测试",
                ],
                depends_on=[
                    "step-update-service",
                ],
                related_acceptance_criteria=[
                    "待支付订单可以取消",
                ],
                related_test_scenarios=[
                    "取消待支付订单",
                ],
                risk_level="medium",
                verification_commands=[
                    "python -m pytest tests/test_service.py -q",
                ],
                rollback_instructions=[
                    "删除新增测试",
                ],
            ),
            ImplementationStep(
                step_id="step-verify",
                title="执行完整验证",
                action="verify",
                target_path=None,
                priority="high",
                description="运行静态检查和自动化测试",
                changes=[
                    "执行 Ruff 和 pytest",
                ],
                depends_on=[
                    "step-update-tests",
                ],
                related_acceptance_criteria=[
                    "待支付订单可以取消",
                ],
                related_test_scenarios=[
                    "取消待支付订单",
                ],
                risk_level="low",
                verification_commands=[
                    "python -m ruff check .",
                    "python -m pytest -q",
                ],
                rollback_instructions=[
                    "根据失败步骤恢复对应文件",
                ],
            ),
        ],
        execution_order=[
            "step-update-service",
            "step-update-tests",
            "step-verify",
        ],
        final_verification_commands=[
            "python -m ruff check .",
            "python -m pytest -q",
        ],
        rollback_checkpoints=[
            "修改代码前记录 Git 状态",
        ],
        risks=[
            "支付和取消并发需要额外验证",
        ],
        assumptions=[],
    )


def build_test_graph():
    """创建不启用人工审核的测试工作流。"""

    return build_change_analysis_graph(
        requirement_analyzer=fake_requirement_analyzer,
        repository_resolver=fake_repository_resolver,
        repository_scanner=fake_repository_scanner,
        code_impact_analyzer=fake_code_impact_analyzer,
        test_strategy_analyzer=fake_test_strategy_analyzer,
        implementation_plan_analyzer=(fake_implementation_plan_analyzer),
    )


def create_initial_state() -> dict[str, object]:
    """创建普通工作流使用的初始状态。"""

    return {
        "request_text": "  为订单系统增加   取消功能  ",
        "normalized_request": "",
        "repository_name": "order_service",
        "requirement_analysis": None,
        "requirement_review": None,
        "repository_snapshot": None,
        "repository_symbol_index": None,
        "repository_dependency_graph": None,
        "code_impact_analysis": None,
        "test_strategy": None,
        "implementation_plan": None,
        "status": "received",
    }


def test_change_analysis_graph_runs_complete_workflow() -> None:
    """工作流应该完成实施计划生成前的全部分析步骤。"""

    graph = build_test_graph()

    result = graph.invoke(create_initial_state())

    requirement_analysis = result["requirement_analysis"]
    repository_snapshot = result["repository_snapshot"]
    repository_symbol_index = result["repository_symbol_index"]
    repository_dependency_graph = result["repository_dependency_graph"]
    code_impact_analysis = result["code_impact_analysis"]
    test_strategy = result["test_strategy"]
    implementation_plan = result["implementation_plan"]

    assert result["normalized_request"] == ("为订单系统增加 取消功能")
    assert result["status"] == ("implementation_plan_generated")

    assert requirement_analysis is not None
    assert repository_snapshot is not None
    assert repository_symbol_index is not None
    assert repository_dependency_graph is not None
    assert code_impact_analysis is not None
    assert test_strategy is not None
    assert implementation_plan is not None

    assert requirement_analysis.change_type == "feature"

    # 假仓库包含一个类和一个测试函数，
    # 因此符号索引总数应该为 2。
    assert repository_symbol_index.symbol_count == 2
    assert repository_symbol_index.import_count == 1
    assert repository_symbol_index.parse_error_count == 0

    # 测试文件导入订单服务，
    # 因此应该生成一条仓库内部依赖边。
    assert repository_dependency_graph.internal_dependency_count == 1
    assert len(repository_dependency_graph.modules) == 2

    indexed_file_paths = {indexed_file.file_path for indexed_file in repository_symbol_index.files}

    assert indexed_file_paths == {
        "order_app/service.py",
        "tests/test_service.py",
    }

    assert code_impact_analysis.impacted_files[0].path == "order_app/service.py"
    assert test_strategy.scenarios[0].name == "取消待支付订单"
    assert test_strategy.test_files[0].path == "tests/test_service.py"

    # 最终步骤应当是统一执行代码检查和测试，
    # 证明实施步骤具有明确的先后顺序。
    assert implementation_plan.execution_order[-1] == "step-verify"


def test_change_analysis_graph_rejects_empty_request() -> None:
    """空需求应该在进入智能体前被拒绝。"""

    graph = build_test_graph()

    initial_state = create_initial_state()
    initial_state["request_text"] = "   "

    with pytest.raises(
        ValueError,
        match="变更需求不能为空",
    ):
        graph.invoke(initial_state)


def fake_requirement_analyzer_with_assumptions(
    request_text: str,
) -> RequirementAnalysis:
    """模拟会产生未确认假设的需求分析智能体。"""

    return RequirementAnalysis(
        # summary 只概括用户明确提出的需求。
        summary=f"分析结果：{request_text}",
        change_type="feature",
        affected_modules=["订单服务"],
        acceptance_criteria=[
            "待支付订单可以取消",
        ],
        risks=[
            "订单状态可能发生冲突",
        ],
        # 该内容没有在用户原始需求中明确说明，
        # 因此必须等待人工确认。
        assumptions=[
            "取消后的订单状态命名为 cancelled",
        ],
        clarification_questions=[
            "是否确认使用 cancelled 作为取消状态值？",
        ],
    )


def fake_requirement_revision_analyzer(
    request_text: str,
    previous_analysis: RequirementAnalysis,
    review_feedback: str,
) -> RequirementAnalysis:
    """模拟根据人工拒绝反馈重新分析需求。"""

    assert request_text == ("为订单系统增加取消订单功能")

    assert previous_analysis.assumptions == ["取消后的订单状态命名为 cancelled"]

    assert review_feedback == ("状态名称需要重新讨论。")

    return RequirementAnalysis(
        summary=("为订单系统增加取消订单功能，并重新确认取消状态名称。"),
        change_type="feature",
        affected_modules=["订单服务"],
        acceptance_criteria=[
            "待支付订单可以取消",
            "取消后的订单进入明确的取消状态",
        ],
        risks=[
            "取消操作与支付操作可能发生状态冲突",
        ],
        # 修订后仍然保留待确认假设，
        # 因此工作流会再次暂停。
        assumptions=[
            "取消后的状态名称暂定为 cancelled_order",
        ],
        clarification_questions=[
            "是否批准使用 cancelled_order 作为取消状态值？",
        ],
    )


def build_reviewable_test_graph():
    """创建启用人工审核和检查点的测试工作流。"""

    return build_change_analysis_graph(
        requirement_analyzer=(fake_requirement_analyzer_with_assumptions),
        requirement_revision_analyzer=(fake_requirement_revision_analyzer),
        repository_resolver=fake_repository_resolver,
        repository_scanner=fake_repository_scanner,
        code_impact_analyzer=fake_code_impact_analyzer,
        test_strategy_analyzer=fake_test_strategy_analyzer,
        implementation_plan_analyzer=(fake_implementation_plan_analyzer),
        human_review_enabled=True,
        # 人工中断和恢复必须使用检查点。
        checkpointer=InMemorySaver(),
    )


def create_reviewable_initial_state() -> dict[str, object]:
    """创建人工审核工作流使用的初始状态。"""

    return {
        "request_text": "为订单系统增加取消订单功能",
        "normalized_request": "",
        "repository_name": "order_service",
        "requirement_analysis": None,
        "requirement_review": None,
        "repository_snapshot": None,
        "repository_symbol_index": None,
        "repository_dependency_graph": None,
        "code_impact_analysis": None,
        "test_strategy": None,
        "implementation_plan": None,
        "status": "received",
    }


def test_change_analysis_graph_continues_after_review_approval() -> None:
    """人工批准后才应该扫描、索引和建立依赖图。"""

    graph = build_reviewable_test_graph()

    config = {
        "configurable": {
            "thread_id": "requirement-review-approved",
        }
    }

    # 第一次调用会在人工审核节点暂停。
    paused_result = graph.invoke(
        create_reviewable_initial_state(),
        config=config,
    )

    assert paused_result["status"] == ("requirement_analyzed")
    assert paused_result["requirement_analysis"] is not None

    # 人工批准前不能读取、索引或分析代码仓库。
    assert paused_result["repository_snapshot"] is None
    assert paused_result["repository_symbol_index"] is None
    assert paused_result["repository_dependency_graph"] is None
    assert paused_result["code_impact_analysis"] is None
    assert paused_result["test_strategy"] is None
    assert paused_result["implementation_plan"] is None

    interrupts = paused_result["__interrupt__"]

    assert len(interrupts) == 1

    review_prompt = interrupts[0].value

    assert review_prompt["type"] == ("requirement_review")
    assert review_prompt["assumptions"] == ["取消后的订单状态命名为 cancelled"]
    assert review_prompt["clarification_questions"] == ["是否确认使用 cancelled 作为取消状态值？"]

    # 使用相同 thread_id 批准并恢复工作流。
    completed_result = graph.invoke(
        Command(
            resume={
                "action": "approve",
                "feedback": "确认使用 cancelled 状态。",
            }
        ),
        config=config,
    )

    review_decision = completed_result["requirement_review"]
    symbol_index = completed_result["repository_symbol_index"]
    dependency_graph = completed_result["repository_dependency_graph"]

    assert completed_result["status"] == ("implementation_plan_generated")
    assert review_decision is not None
    assert review_decision.action == "approve"

    assert completed_result["repository_snapshot"] is not None
    assert symbol_index is not None
    assert symbol_index.symbol_count == 2

    assert dependency_graph is not None
    assert dependency_graph.internal_dependency_count == 1

    assert completed_result["code_impact_analysis"] is not None
    assert completed_result["test_strategy"] is not None
    assert completed_result["implementation_plan"] is not None


def test_change_analysis_graph_revises_after_review_rejection() -> None:
    """人工拒绝后应修订需求并再次等待审核。"""

    graph = build_reviewable_test_graph()

    config = {
        "configurable": {
            "thread_id": "requirement-review-revised",
        }
    }

    first_paused_result = graph.invoke(
        create_reviewable_initial_state(),
        config=config,
    )

    assert "__interrupt__" in first_paused_result
    assert first_paused_result["status"] == ("requirement_analyzed")

    # 拒绝第一版需求并提供修改意见。
    second_paused_result = graph.invoke(
        Command(
            resume={
                "action": "reject",
                "feedback": "状态名称需要重新讨论。",
            }
        ),
        config=config,
    )

    # 修订后再次暂停等待审核。
    assert "__interrupt__" in second_paused_result
    assert second_paused_result["status"] == ("requirement_revised")

    # 旧审核结果已经清除。
    assert second_paused_result["requirement_review"] is None

    # 第二次批准前仍然不能扫描、索引或建立依赖图。
    assert second_paused_result["repository_snapshot"] is None
    assert second_paused_result["repository_symbol_index"] is None
    assert second_paused_result["repository_dependency_graph"] is None
    assert second_paused_result["code_impact_analysis"] is None
    assert second_paused_result["test_strategy"] is None
    assert second_paused_result["implementation_plan"] is None

    revised_analysis = second_paused_result["requirement_analysis"]

    assert revised_analysis is not None
    assert revised_analysis.summary == ("为订单系统增加取消订单功能，并重新确认取消状态名称。")
    assert revised_analysis.assumptions == ["取消后的状态名称暂定为 cancelled_order"]

    second_interrupts = second_paused_result["__interrupt__"]

    assert len(second_interrupts) == 1

    second_review_prompt = second_interrupts[0].value

    assert second_review_prompt["assumptions"] == ["取消后的状态名称暂定为 cancelled_order"]
    assert second_review_prompt["clarification_questions"] == [
        "是否批准使用 cancelled_order 作为取消状态值？"
    ]

    # 批准修订后的需求，继续同一条工作流。
    completed_result = graph.invoke(
        Command(
            resume={
                "action": "approve",
                "feedback": ("批准使用 cancelled_order 状态。"),
            }
        ),
        config=config,
    )

    review_decision = completed_result["requirement_review"]
    symbol_index = completed_result["repository_symbol_index"]
    dependency_graph = completed_result["repository_dependency_graph"]

    assert completed_result["status"] == ("implementation_plan_generated")
    assert review_decision is not None
    assert review_decision.action == "approve"

    assert completed_result["repository_snapshot"] is not None
    assert symbol_index is not None
    assert symbol_index.symbol_count == 2

    assert dependency_graph is not None
    assert dependency_graph.internal_dependency_count == 1

    assert completed_result["code_impact_analysis"] is not None
    assert completed_result["test_strategy"] is not None
    assert completed_result["implementation_plan"] is not None
