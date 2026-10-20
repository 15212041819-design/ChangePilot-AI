from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class ChangeAnalysisState(TypedDict):
    """软件变更分析工作流中共享的状态。"""

    request_text: str
    normalized_request: str
    status: str


def normalize_request(state: ChangeAnalysisState) -> dict[str, str]:
    """清理用户输入的变更需求。"""

    normalized_request = " ".join(state["request_text"].split())

    if not normalized_request:
        raise ValueError("变更需求不能为空。")

    return {
        "normalized_request": normalized_request,
        "status": "normalized",
    }


def mark_ready(state: ChangeAnalysisState) -> dict[str, str]:
    """将需求标记为可以进入需求分析阶段。"""

    return {
        "status": "ready_for_requirement_analysis",
    }


def build_change_analysis_graph():
    """创建并编译软件变更分析工作流。"""

    builder = StateGraph(ChangeAnalysisState)

    builder.add_node("normalize_request", normalize_request)
    builder.add_node("mark_ready", mark_ready)

    builder.add_edge(START, "normalize_request")
    builder.add_edge("normalize_request", "mark_ready")
    builder.add_edge("mark_ready", END)

    return builder.compile()


change_analysis_graph = build_change_analysis_graph()
