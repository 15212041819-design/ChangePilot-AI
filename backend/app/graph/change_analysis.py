"""ChangePilot AI 软件变更分析工作流。"""

from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.schemas.code_impact import CodeImpactAnalysis
from app.schemas.repository import RepositorySnapshot
from app.schemas.requirement import RequirementAnalysis
from app.services.code_impact_agent import analyze_code_impact
from app.services.requirement_agent import analyze_requirement
from app.tools.repository_scanner import scan_repository
from app.tools.workspace import resolve_repository_directory


class ChangeAnalysisState(TypedDict):
    """软件变更分析工作流中共享的完整状态。"""

    # 用户提交的原始软件变更需求。
    request_text: str

    # 去除多余空格和换行后的需求。
    normalized_request: str

    # 用户选择的工作区仓库名称。
    repository_name: str

    # 第一个智能体生成的结构化需求分析。
    requirement_analysis: RequirementAnalysis | None

    # 仓库扫描节点生成的真实代码快照。
    repository_snapshot: RepositorySnapshot | None

    # 第二个智能体生成的代码影响分析。
    code_impact_analysis: CodeImpactAnalysis | None

    # 工作流目前执行到的阶段。
    status: str


# 下面的 Callable 类型定义了各个组件必须遵守的调用形式。
# 使用依赖注入后，测试可以传入假组件，避免调用真实 DeepSeek。
RequirementAnalyzer = Callable[[str], RequirementAnalysis]
RepositoryResolver = Callable[[str], Path]
RepositoryScanner = Callable[[str | Path], RepositorySnapshot]
CodeImpactAnalyzer = Callable[
    [RequirementAnalysis, RepositorySnapshot],
    CodeImpactAnalysis,
]


def normalize_request(
    state: ChangeAnalysisState,
) -> dict[str, str]:
    """清理用户输入的软件变更需求。"""

    normalized_request = " ".join(state["request_text"].split())

    if not normalized_request:
        raise ValueError("变更需求不能为空。")

    return {
        "normalized_request": normalized_request,
        "status": "request_normalized",
    }


def create_requirement_analysis_node(
    analyzer: RequirementAnalyzer,
) -> Callable[[ChangeAnalysisState], dict[str, object]]:
    """创建需求分析智能体节点。"""

    def requirement_analysis_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """调用第一个智能体分析规范化需求。"""

        analysis = analyzer(state["normalized_request"])

        return {
            "requirement_analysis": analysis,
            "status": "requirement_analyzed",
        }

    return requirement_analysis_node


def create_repository_scan_node(
    resolver: RepositoryResolver,
    scanner: RepositoryScanner,
) -> Callable[[ChangeAnalysisState], dict[str, object]]:
    """创建安全解析并扫描代码仓库的节点。"""

    def repository_scan_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """解析仓库名称并读取真实代码文件。"""

        # resolver 会保证最终路径位于允许的工作区内部。
        repository_path = resolver(state["repository_name"])

        # scanner 会过滤缓存、依赖、密钥和过大文件。
        repository_snapshot = scanner(repository_path)

        return {
            "repository_snapshot": repository_snapshot,
            "status": "repository_scanned",
        }

    return repository_scan_node


def create_code_impact_analysis_node(
    analyzer: CodeImpactAnalyzer,
) -> Callable[[ChangeAnalysisState], dict[str, object]]:
    """创建代码影响分析智能体节点。"""

    def code_impact_analysis_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """根据需求分析和仓库快照判断代码影响范围。"""

        requirement_analysis = state["requirement_analysis"]
        repository_snapshot = state["repository_snapshot"]

        # 如果前面的节点没有产生结果，说明工作流状态异常。
        if requirement_analysis is None:
            raise RuntimeError("工作流缺少需求分析结果。")

        if repository_snapshot is None:
            raise RuntimeError("工作流缺少代码仓库快照。")

        impact_analysis = analyzer(
            requirement_analysis,
            repository_snapshot,
        )

        return {
            "code_impact_analysis": impact_analysis,
            "status": "code_impact_analyzed",
        }

    return code_impact_analysis_node


def build_change_analysis_graph(
    requirement_analyzer: RequirementAnalyzer = (analyze_requirement),
    repository_resolver: RepositoryResolver = (resolve_repository_directory),
    repository_scanner: RepositoryScanner = (scan_repository),
    code_impact_analyzer: CodeImpactAnalyzer = (analyze_code_impact),
):
    """创建并编译完整的软件变更分析工作流。"""

    builder = StateGraph(ChangeAnalysisState)

    # 注册四个工作流节点。
    builder.add_node(
        "normalize_request",
        normalize_request,
    )
    builder.add_node(
        "analyze_requirement",
        create_requirement_analysis_node(requirement_analyzer),
    )
    builder.add_node(
        "scan_repository",
        create_repository_scan_node(
            repository_resolver,
            repository_scanner,
        ),
    )
    builder.add_node(
        "analyze_code_impact",
        create_code_impact_analysis_node(code_impact_analyzer),
    )

    # 定义节点的执行顺序。
    builder.add_edge(
        START,
        "normalize_request",
    )
    builder.add_edge(
        "normalize_request",
        "analyze_requirement",
    )
    builder.add_edge(
        "analyze_requirement",
        "scan_repository",
    )
    builder.add_edge(
        "scan_repository",
        "analyze_code_impact",
    )
    builder.add_edge(
        "analyze_code_impact",
        END,
    )

    return builder.compile()


# 生产环境使用真实解析器、扫描器和两个 DeepSeek 智能体。
change_analysis_graph = build_change_analysis_graph()
