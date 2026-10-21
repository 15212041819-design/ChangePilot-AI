"""完整软件变更分析工作流的自动化测试。"""

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

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


def fake_requirement_analyzer_with_assumptions(
    request_text: str,
) -> RequirementAnalysis:
    """模拟会产生未确认假设的需求分析智能体。"""

    return RequirementAnalysis(
        # summary 只概括用户明确提出的需求。
        summary=f"分析结果：{request_text}",
        change_type="feature",
        affected_modules=["订单服务"],
        acceptance_criteria=["待支付订单可以取消"],
        risks=["订单状态可能发生冲突"],
        # 该内容没有在用户原始需求中明确说明，
        # 因此必须等待人工确认后才能进入代码影响分析。
        assumptions=["取消后的订单状态命名为 cancelled"],
        # 澄清问题用于帮助用户确认或否定上面的假设。
        clarification_questions=["是否确认使用 cancelled 作为取消状态值？"],
    )


def build_reviewable_test_graph():
    """创建启用了人工审核和内存检查点的测试工作流。"""

    return build_change_analysis_graph(
        requirement_analyzer=fake_requirement_analyzer_with_assumptions,
        repository_resolver=fake_repository_resolver,
        repository_scanner=fake_repository_scanner,
        code_impact_analyzer=fake_code_impact_analyzer,
        # 开启需求人工审核节点。
        human_review_enabled=True,
        # 保存工作流暂停时的状态，以便使用同一个 thread_id 恢复。
        checkpointer=InMemorySaver(),
    )


def create_reviewable_initial_state() -> dict[str, object]:
    """创建人工审核工作流测试使用的初始状态。"""

    return {
        "request_text": "为订单系统增加取消订单功能",
        "normalized_request": "",
        "repository_name": "order_service",
        "requirement_analysis": None,
        "requirement_review": None,
        "repository_snapshot": None,
        "code_impact_analysis": None,
        "status": "received",
    }


def test_change_analysis_graph_continues_after_review_approval() -> None:
    """人工批准需求假设后，工作流应该继续分析代码影响。"""

    graph = build_reviewable_test_graph()

    # thread_id 用于唯一标识本次工作流及其检查点。
    config = {
        "configurable": {
            "thread_id": "requirement-review-approved",
        }
    }

    # 第一次调用会在人工审核节点的 interrupt() 处暂停。
    paused_result = graph.invoke(
        create_reviewable_initial_state(),
        config=config,
    )

    # 暂停前已经完成需求分析，但尚未扫描代码仓库。
    assert paused_result["status"] == "requirement_analyzed"
    assert paused_result["requirement_analysis"] is not None
    assert paused_result["repository_snapshot"] is None
    assert paused_result["code_impact_analysis"] is None

    # invoke() 使用 __interrupt__ 返回需要展示给用户的审核信息。
    interrupts = paused_result["__interrupt__"]

    assert len(interrupts) == 1

    review_prompt = interrupts[0].value

    assert review_prompt["type"] == "requirement_review"
    assert review_prompt["assumptions"] == ["取消后的订单状态命名为 cancelled"]
    assert review_prompt["clarification_questions"] == ["是否确认使用 cancelled 作为取消状态值？"]

    # 使用完全相同的 thread_id 恢复工作流。
    # resume 中的数据会由 RequirementReviewDecision 负责校验。
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

    # 批准后应该完成仓库扫描和代码影响分析。
    assert completed_result["status"] == "code_impact_analyzed"
    assert review_decision is not None
    assert review_decision.action == "approve"
    assert completed_result["repository_snapshot"] is not None
    assert completed_result["code_impact_analysis"] is not None


def test_change_analysis_graph_stops_after_review_rejection() -> None:
    """人工拒绝需求假设后，工作流不应扫描仓库。"""

    graph = build_reviewable_test_graph()

    # 拒绝测试必须使用独立的 thread_id，
    # 防止读取批准测试产生的检查点状态。
    config = {
        "configurable": {
            "thread_id": "requirement-review-rejected",
        }
    }

    # 首次运行到人工审核节点后暂停。
    paused_result = graph.invoke(
        create_reviewable_initial_state(),
        config=config,
    )

    assert "__interrupt__" in paused_result

    # 模拟用户拒绝当前假设。
    rejected_result = graph.invoke(
        Command(
            resume={
                "action": "reject",
                "feedback": "状态名称需要重新讨论。",
            }
        ),
        config=config,
    )

    review_decision = rejected_result["requirement_review"]

    # 拒绝后工作流应该直接结束。
    assert rejected_result["status"] == "requirement_rejected"
    assert review_decision is not None
    assert review_decision.action == "reject"

    # 仓库扫描和代码影响分析都不应执行。
    assert rejected_result["repository_snapshot"] is None
    assert rejected_result["code_impact_analysis"] is None
