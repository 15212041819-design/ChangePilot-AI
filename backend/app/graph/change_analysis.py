from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.schemas.requirement import RequirementAnalysis
from app.services.requirement_agent import analyze_requirement


# 状态定义
class ChangeAnalysisState(TypedDict):
    """软件变更分析工作流中共享的状态。"""

    request_text: str
    normalized_request: str
    requirement_analysis: RequirementAnalysis | None
    status: str


# 需求分析器。
RequirementAnalyzer = Callable[[str], RequirementAnalysis]


# 清理用户输入的变更需求。
def normalize_request(state: ChangeAnalysisState) -> dict[str, str]:
    """清理用户输入的变更需求。"""

    normalized_request = " ".join(state["request_text"].split())

    if not normalized_request:
        raise ValueError("变更需求不能为空。")

    return {
        "normalized_request": normalized_request,
        "status": "normalized",
    }


# 创建需求分析节点。
def create_requirement_analysis_node(
    analyzer: RequirementAnalyzer,
) -> Callable[[ChangeAnalysisState], dict[str, object]]:
    """创建需求分析节点。"""

    def requirement_analysis_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        analysis = analyzer(state["normalized_request"])

        return {
            "requirement_analysis": analysis,
            "status": "requirement_analyzed",
        }

    return requirement_analysis_node


# 创建并编译软件变更分析工作流。
def build_change_analysis_graph(
    analyzer: RequirementAnalyzer = analyze_requirement,
):
    """创建并编译软件变更分析工作流。"""

    builder = StateGraph(ChangeAnalysisState)

    builder.add_node("normalize_request", normalize_request)
    builder.add_node(
        "analyze_requirement",
        create_requirement_analysis_node(analyzer),
    )

    builder.add_edge(START, "normalize_request")
    builder.add_edge("normalize_request", "analyze_requirement")
    builder.add_edge("analyze_requirement", END)

    return builder.compile()


change_analysis_graph = build_change_analysis_graph()
