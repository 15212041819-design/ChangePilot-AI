"""完整软件变更分析 API 的自动化测试。"""

from pathlib import Path

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.api.routes.changes import (
    get_change_analysis_graph,
)
from app.graph.change_analysis import (
    build_change_analysis_graph,
)
from app.main import app
from app.schemas.code_impact import (
    CodeImpactAnalysis,
    ImpactedFile,
)
from app.schemas.code_index import RepositorySymbolIndex
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
from app.services.test_strategy_agent import (
    TestStrategyValidationError as StrategyValidationError,
)
from app.tools.code_impact_validator import (
    CodeImpactValidationError,
)


def fake_requirement_analyzer(
    request_text: str,
) -> RequirementAnalysis:
    """模拟不包含待确认假设的需求分析智能体。"""

    return RequirementAnalysis(
        summary=request_text,
        change_type="feature",
        affected_modules=["订单服务"],
        acceptance_criteria=[
            "待支付订单可以取消",
        ],
        risks=[
            "订单状态可能发生冲突",
        ],
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
                content=("class OrderService: ...\n"),
            ),
            RepositoryFile(
                relative_path="tests/test_service.py",
                language="python",
                size_bytes=100,
                content=(
                    "from order_app.service import OrderService\n\ndef test_create_order(): ...\n"
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
    """模拟接收审核结果、符号索引和依赖图的影响分析智能体。"""

    # API 工作流必须把当前仓库对应的索引传入智能体。
    assert symbol_index.repository_name == repository.repository_name
    assert symbol_index.symbol_count == 2
    assert symbol_index.parse_error_count == 0
    assert symbol_index.import_count == 1

    # API 工作流必须建立并传递当前仓库的依赖图。
    assert dependency_graph.repository_name == repository.repository_name
    assert dependency_graph.internal_dependency_count == 1

    dependency = dependency_graph.dependencies[0]

    assert dependency.source_module == "tests.test_service"
    assert dependency.target_module == "order_app.service"
    assert dependency.target_file == "order_app/service.py"

    # 人工审核模式下，
    # 包含假设的需求只能在批准后执行。
    if requirement.assumptions:
        assert review is not None
        assert review.action == "approve"
        assert review.feedback

    return CodeImpactAnalysis(
        summary=(f"{requirement.summary} 将影响 {repository.repository_name}"),
        impacted_files=[
            ImpactedFile(
                path="order_app/service.py",
                reason="需要增加取消订单功能",
                suggested_changes=[
                    "增加 cancel_order 方法",
                ],
                risk_level="medium",
            )
        ],
        new_files=[],
        test_files=[
            "tests/test_service.py",
        ],
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
    """模拟根据完整工作流证据生成测试策略。"""

    assert requirement.acceptance_criteria
    assert code_impact.impacted_files[0].path == "order_app/service.py"
    assert repository.repository_name == "order_service"
    assert symbol_index.repository_name == repository.repository_name
    assert dependency_graph.repository_name == repository.repository_name

    if requirement.assumptions:
        assert review is not None
        assert review.action == "approve"

    return StrategyResult(
        summary="验证订单取消功能",
        scenarios=[
            StrategyScenario(
                name="取消待支付订单",
                level="unit",
                category="happy_path",
                priority="high",
                target="OrderService.cancel_order",
                description="验证待支付订单能够取消",
                related_acceptance_criteria=[
                    requirement.acceptance_criteria[0],
                ],
                preconditions=[
                    "订单状态为 PENDING",
                ],
                steps=[
                    "调用 cancel_order",
                ],
                expected_result=("订单状态变为 CANCELLED"),
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
            "并发支付场景需要额外环境",
        ],
    )


def fake_invalid_test_strategy_analyzer(
    requirement: RequirementAnalysis,
    code_impact: CodeImpactAnalysis,
    repository: RepositorySnapshot,
    review: RequirementReviewDecision | None,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
) -> StrategyResult:
    """模拟测试策略没有通过真实性校验。"""

    assert requirement.change_type == "feature"
    assert code_impact.impacted_files
    assert repository.repository_name == "order_service"
    assert review is None
    assert symbol_index.repository_name == repository.repository_name
    assert dependency_graph.repository_name == repository.repository_name

    raise StrategyValidationError("需要修改的测试文件在仓库中不存在：tests/test_missing.py。")


def fake_invalid_code_impact_analyzer(
    requirement: RequirementAnalysis,
    repository: RepositorySnapshot,
    review: RequirementReviewDecision | None,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
) -> CodeImpactAnalysis:
    """模拟代码影响分析结果没有通过真实性校验。"""

    # 确认工作流已经完成需求分析、
    # 仓库扫描、符号索引和依赖图构建。
    assert requirement.change_type == "feature"
    assert repository.repository_name == "order_service"
    assert review is None
    assert symbol_index.repository_name == repository.repository_name
    assert symbol_index.symbol_count == 2
    assert dependency_graph.repository_name == repository.repository_name
    assert dependency_graph.internal_dependency_count == 1

    raise CodeImpactValidationError("impacted_files 包含仓库中不存在的文件：order_app/missing.py")


def get_test_change_analysis_graph() -> CompiledStateGraph:
    """创建不启用人工审核的 API 测试工作流。"""

    return build_change_analysis_graph(
        requirement_analyzer=(fake_requirement_analyzer),
        repository_resolver=(fake_repository_resolver),
        repository_scanner=(fake_repository_scanner),
        code_impact_analyzer=(fake_code_impact_analyzer),
        test_strategy_analyzer=(fake_test_strategy_analyzer),
    )


def test_analyze_change_endpoint() -> None:
    """接口应该返回需求、代码影响和测试策略结果。"""

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

        assert response_body["status"] == ("test_strategy_generated")
        assert response_body["normalized_request"] == "为订单系统增加 取消功能"
        assert response_body["requirement_analysis"]["change_type"] == "feature"
        assert response_body["code_impact_analysis"]["impacted_files"][0]["path"] == (
            "order_app/service.py"
        )
        assert response_body["test_strategy"]["scenarios"][0]["name"] == ("取消待支付订单")
        assert response_body["test_strategy"]["test_files"][0]["path"] == ("tests/test_service.py")
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
        assert response.json()["detail"] == ("变更需求不能为空。")
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )


def test_analyze_change_endpoint_rejects_untrusted_impact_result() -> None:
    """接口应该把不可信的 AI 分析结果转换成 502 响应。"""

    def get_invalid_impact_graph() -> CompiledStateGraph:
        """创建会产生真实性校验错误的测试工作流。"""

        return build_change_analysis_graph(
            requirement_analyzer=(fake_requirement_analyzer),
            repository_resolver=(fake_repository_resolver),
            repository_scanner=(fake_repository_scanner),
            code_impact_analyzer=(fake_invalid_code_impact_analyzer),
            test_strategy_analyzer=(fake_test_strategy_analyzer),
        )

    app.dependency_overrides[get_change_analysis_graph] = get_invalid_impact_graph

    try:
        client = TestClient(app)

        response = client.post(
            "/api/v1/changes/analyze",
            json={
                "request_text": ("为订单系统增加取消订单功能"),
                "repository_name": "order_service",
            },
        )

        assert response.status_code == 502
        assert response.json()["detail"] == (
            "impacted_files 包含仓库中不存在的文件：order_app/missing.py"
        )
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )


def test_analyze_change_endpoint_rejects_untrusted_test_strategy() -> None:
    """接口应该把不可信测试策略转换成 502 响应。"""

    def get_invalid_strategy_graph() -> CompiledStateGraph:
        """创建会产生测试策略真实性错误的工作流。"""

        return build_change_analysis_graph(
            requirement_analyzer=(fake_requirement_analyzer),
            repository_resolver=(fake_repository_resolver),
            repository_scanner=(fake_repository_scanner),
            code_impact_analyzer=(fake_code_impact_analyzer),
            test_strategy_analyzer=(fake_invalid_test_strategy_analyzer),
        )

    app.dependency_overrides[get_change_analysis_graph] = get_invalid_strategy_graph

    try:
        client = TestClient(app)

        response = client.post(
            "/api/v1/changes/analyze",
            json={
                "request_text": ("为订单系统增加取消订单功能"),
                "repository_name": "order_service",
            },
        )

        assert response.status_code == 502
        assert response.json()["detail"] == (
            "需要修改的测试文件在仓库中不存在：tests/test_missing.py。"
        )
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )


def fake_requirement_analyzer_with_assumptions(
    request_text: str,
) -> RequirementAnalysis:
    """模拟包含未确认假设的需求分析结果。"""

    return RequirementAnalysis(
        summary=request_text,
        change_type="feature",
        affected_modules=[
            "订单服务",
        ],
        acceptance_criteria=[
            "待支付订单可以取消",
        ],
        risks=[
            "订单状态可能发生冲突",
        ],
        # 该假设会触发正式工作流中的人工审核节点。
        assumptions=[
            "取消后的订单状态命名为 cancelled",
        ],
        # API 会把该问题包含在 review_prompt 中返回。
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

    # 确认 API 工作流把原始需求正确传给修订智能体。
    assert request_text == ("为订单系统增加取消订单功能")

    # 确认修订智能体收到的是上一版需求分析。
    assert previous_analysis.assumptions == ["取消后的订单状态命名为 cancelled"]

    # 确认用户提交的拒绝反馈没有在 API 层丢失。
    assert review_feedback == ("状态名称需要重新讨论。")

    return RequirementAnalysis(
        summary=("为订单系统增加取消订单功能，并重新确认取消状态名称。"),
        change_type="feature",
        affected_modules=[
            "订单服务",
        ],
        acceptance_criteria=[
            "待支付订单可以取消",
            "取消后的订单进入明确的取消状态",
        ],
        risks=[
            "取消操作与支付操作可能发生状态冲突",
        ],
        # 修订后仍然包含待确认假设，
        # 因此 API 应再次返回待审核状态。
        assumptions=[
            "取消后的状态名称暂定为 cancelled_order",
        ],
        clarification_questions=[
            "是否批准使用 cancelled_order 作为取消状态值？",
        ],
    )


def build_reviewable_api_graph() -> CompiledStateGraph:
    """创建支持人工暂停、修订和恢复的 API 测试工作流。"""

    return build_change_analysis_graph(
        requirement_analyzer=(fake_requirement_analyzer_with_assumptions),
        # 使用假的需求修订智能体，
        # 避免 API 测试调用真实 DeepSeek。
        requirement_revision_analyzer=(fake_requirement_revision_analyzer),
        repository_resolver=(fake_repository_resolver),
        repository_scanner=(fake_repository_scanner),
        code_impact_analyzer=(fake_code_impact_analyzer),
        test_strategy_analyzer=(fake_test_strategy_analyzer),
        # 启用正式需求人工审核节点。
        human_review_enabled=True,
        # 同一个图实例必须保留检查点，
        # 否则后续 HTTP 请求无法恢复暂停的工作流。
        checkpointer=InMemorySaver(),
    )


def test_analyze_change_endpoint_pauses_for_requirement_review() -> None:
    """存在未确认假设时，分析接口应该返回待审核状态。"""

    reviewable_graph = build_reviewable_api_graph()

    def get_reviewable_graph() -> CompiledStateGraph:
        """在本测试中始终返回同一个工作流实例。"""

        return reviewable_graph

    app.dependency_overrides[get_change_analysis_graph] = get_reviewable_graph

    try:
        client = TestClient(app)

        response = client.post(
            "/api/v1/changes/analyze",
            json={
                "request_text": ("为订单系统增加取消订单功能"),
                "repository_name": "order_service",
            },
        )

        assert response.status_code == 200

        response_body = response.json()

        # 服务端应该生成可恢复工作流的 thread_id。
        assert isinstance(
            response_body["thread_id"],
            str,
        )
        assert response_body["thread_id"]

        # 工作流应该暂停在仓库扫描之前。
        assert response_body["status"] == ("awaiting_requirement_review")
        assert response_body["code_impact_analysis"] is None
        assert response_body["test_strategy"] is None
        assert response_body["requirement_review"] is None

        # 中断载荷应该包含假设和澄清问题。
        assert response_body["review_prompt"]["type"] == "requirement_review"
        assert response_body["review_prompt"]["assumptions"] == ["取消后的订单状态命名为 cancelled"]
        assert response_body["review_prompt"]["clarification_questions"] == [
            "是否确认使用 cancelled 作为取消状态值？"
        ]
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )


def test_resume_change_analysis_endpoint_approves_workflow() -> None:
    """人工批准后，恢复接口应该完成全部分析。"""

    reviewable_graph = build_reviewable_api_graph()

    def get_reviewable_graph() -> CompiledStateGraph:
        """返回持有当前测试检查点的工作流。"""

        return reviewable_graph

    app.dependency_overrides[get_change_analysis_graph] = get_reviewable_graph

    try:
        client = TestClient(app)

        # 首次请求运行到人工审核节点并暂停。
        analyze_response = client.post(
            "/api/v1/changes/analyze",
            json={
                "request_text": ("为订单系统增加取消订单功能"),
                "repository_name": "order_service",
            },
        )

        assert analyze_response.status_code == 200

        thread_id = analyze_response.json()["thread_id"]

        # 使用首次响应中的 thread_id 批准并恢复。
        resume_response = client.post(
            "/api/v1/changes/analyze/resume",
            json={
                "thread_id": thread_id,
                "decision": {
                    "action": "approve",
                    "feedback": ("确认使用 cancelled 状态。"),
                },
            },
        )

        assert resume_response.status_code == 200

        response_body = resume_response.json()

        # 恢复前后必须属于同一个工作流。
        assert response_body["thread_id"] == thread_id

        # 批准后应该继续扫描仓库并完成分析。
        assert response_body["status"] == ("test_strategy_generated")
        assert response_body["review_prompt"] is None
        assert response_body["requirement_review"]["action"] == "approve"
        assert response_body["code_impact_analysis"] is not None
        assert response_body["test_strategy"] is not None
        assert response_body["code_impact_analysis"]["impacted_files"][0]["path"] == (
            "order_app/service.py"
        )
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )


def test_resume_change_analysis_endpoint_revises_workflow() -> None:
    """人工拒绝后，恢复接口应该返回修订后的待审核需求。"""

    reviewable_graph = build_reviewable_api_graph()

    def get_reviewable_graph() -> CompiledStateGraph:
        """返回持有当前测试检查点的工作流。"""

        return reviewable_graph

    app.dependency_overrides[get_change_analysis_graph] = get_reviewable_graph

    try:
        client = TestClient(app)

        # 启动并暂停一条新的工作流。
        analyze_response = client.post(
            "/api/v1/changes/analyze",
            json={
                "request_text": ("为订单系统增加取消订单功能"),
                "repository_name": "order_service",
            },
        )

        assert analyze_response.status_code == 200

        thread_id = analyze_response.json()["thread_id"]

        # 拒绝第一版需求分析并提交修改意见。
        revision_response = client.post(
            "/api/v1/changes/analyze/resume",
            json={
                "thread_id": thread_id,
                "decision": {
                    "action": "reject",
                    "feedback": ("状态名称需要重新讨论。"),
                },
            },
        )

        assert revision_response.status_code == 200

        revision_body = revision_response.json()

        # 拒绝后工作流会修订需求并再次待审核。
        assert revision_body["thread_id"] == thread_id
        assert revision_body["status"] == ("awaiting_requirement_review")

        # 修订节点会清除上一轮拒绝决定。
        assert revision_body["requirement_review"] is None
        assert revision_body["code_impact_analysis"] is None
        assert revision_body["test_strategy"] is None

        # API 应返回修订后的需求分析。
        assert revision_body["requirement_analysis"]["summary"] == (
            "为订单系统增加取消订单功能，并重新确认取消状态名称。"
        )
        assert revision_body["requirement_analysis"]["assumptions"] == [
            "取消后的状态名称暂定为 cancelled_order"
        ]

        # 新的审核提示应来自修订后的分析。
        assert revision_body["review_prompt"]["assumptions"] == [
            "取消后的状态名称暂定为 cancelled_order"
        ]
        assert revision_body["review_prompt"]["clarification_questions"] == [
            "是否批准使用 cancelled_order 作为取消状态值？"
        ]

        # 再次使用同一个 thread_id 批准修订需求。
        approval_response = client.post(
            "/api/v1/changes/analyze/resume",
            json={
                "thread_id": thread_id,
                "decision": {
                    "action": "approve",
                    "feedback": ("批准使用 cancelled_order 状态。"),
                },
            },
        )

        assert approval_response.status_code == 200

        approval_body = approval_response.json()

        # 第二次审核批准后才会完成代码影响分析和测试策略。
        assert approval_body["thread_id"] == thread_id
        assert approval_body["status"] == ("test_strategy_generated")
        assert approval_body["review_prompt"] is None
        assert approval_body["requirement_review"]["action"] == "approve"
        assert approval_body["code_impact_analysis"] is not None
        assert approval_body["test_strategy"] is not None
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )


def test_resume_change_analysis_endpoint_rejects_unknown_thread() -> None:
    """恢复接口应该拒绝不存在的工作流标识。"""

    reviewable_graph = build_reviewable_api_graph()

    def get_reviewable_graph() -> CompiledStateGraph:
        """返回带有空检查点存储的测试工作流。"""

        return reviewable_graph

    app.dependency_overrides[get_change_analysis_graph] = get_reviewable_graph

    try:
        client = TestClient(app)

        response = client.post(
            "/api/v1/changes/analyze/resume",
            json={
                "thread_id": ("unknown-workflow-thread"),
                "decision": {
                    "action": "approve",
                    "feedback": None,
                },
            },
        )

        assert response.status_code == 404
        assert response.json()["detail"] == ("没有找到指定的工作流。")
    finally:
        app.dependency_overrides.pop(
            get_change_analysis_graph,
            None,
        )
