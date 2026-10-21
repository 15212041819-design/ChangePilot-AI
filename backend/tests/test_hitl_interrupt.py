"""验证 LangGraph 工作流的人工暂停与恢复机制。"""

from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class HitlState(TypedDict):
    """定义人工审核演示工作流使用的状态结构。"""

    # 当前等待审核的需求文本。
    request_text: str

    # 人工审核结果，初始值为 pending。
    review_status: str


def human_review_node(state: HitlState) -> dict[str, str]:
    """暂停工作流，等待人工批准或拒绝当前需求。

    首次执行到 interrupt() 时，LangGraph 会保存当前状态并暂停。
    恢复工作流时，Command(resume=...) 中的数据会成为
    interrupt() 的返回值，然后节点继续计算审核结果。
    """

    decision = interrupt(
        {
            # type 用于让 API 或前端识别当前中断的业务类型。
            "type": "requirement_review",
            # question 是展示给审核人员的提示语。
            "question": "是否批准继续分析这条需求？",
            # 将待审核的原始需求一并返回，方便人工确认。
            "request_text": state["request_text"],
        }
    )

    # 根据人工提交的 approved 字段生成明确的审核状态。
    review_status = "approved" if decision["approved"] else "rejected"

    # 节点只返回需要更新的状态字段。
    return {"review_status": review_status}


def build_hitl_graph():
    """创建带有内存检查点的人工审核测试工作流。"""

    # 创建状态图构建器，并指定工作流状态的数据结构。
    builder = StateGraph(HitlState)

    # 注册人工审核节点。
    builder.add_node("human_review", human_review_node)

    # 工作流启动后直接进入人工审核节点。
    builder.add_edge(START, "human_review")

    # 人工审核完成后结束当前工作流。
    builder.add_edge("human_review", END)

    # interrupt() 必须配合检查点使用。
    # InMemorySaver 适合本地开发和自动化测试，
    # 服务重启后数据会丢失，因此以后生产环境需要换成持久化检查点。
    return builder.compile(checkpointer=InMemorySaver())


def test_hitl_workflow_can_be_approved() -> None:
    """验证人工批准后，工作流能够从中断位置恢复。"""

    graph = build_hitl_graph()

    # thread_id 是检查点的唯一标识。
    # 暂停和恢复必须使用同一个 thread_id。
    config = {
        "configurable": {
            "thread_id": "hitl-approved-test",
        }
    }

    # 第一次调用会运行到 interrupt() 并暂停。
    paused_result = graph.invoke(
        {
            "request_text": "为订单系统增加取消订单功能。",
            "review_status": "pending",
        },
        config=config,
    )

    # invoke() 遇到 interrupt() 后，会通过 __interrupt__ 返回中断信息。
    assert "__interrupt__" in paused_result

    # 读取人工审核节点发出的中断载荷。
    interrupt_payload = paused_result["__interrupt__"][0].value

    assert interrupt_payload["type"] == "requirement_review"
    assert interrupt_payload["request_text"] == "为订单系统增加取消订单功能。"

    # 使用相同的 thread_id 恢复工作流，并提交人工批准结果。
    completed_result = graph.invoke(
        Command(resume={"approved": True}),
        config=config,
    )

    assert completed_result["review_status"] == "approved"


def test_hitl_workflow_can_be_rejected() -> None:
    """验证人工拒绝后，工作流能够记录拒绝结果。"""

    graph = build_hitl_graph()

    # 每个独立工作流实例应使用不同的 thread_id，
    # 防止读取到其他测试产生的检查点。
    config = {
        "configurable": {
            "thread_id": "hitl-rejected-test",
        }
    }

    # 启动工作流并等待人工审核。
    paused_result = graph.invoke(
        {
            "request_text": "删除全部订单数据。",
            "review_status": "pending",
        },
        config=config,
    )

    assert "__interrupt__" in paused_result

    # 模拟人工拒绝当前需求。
    completed_result = graph.invoke(
        Command(resume={"approved": False}),
        config=config,
    )

    assert completed_result["review_status"] == "rejected"
