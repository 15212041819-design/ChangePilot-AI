"""ChangePilot AI 软件变更分析工作流。

本模块负责使用 LangGraph 编排完整的软件变更分析过程。

当前主要流程为：

1. 规范化用户输入。
2. 使用需求分析智能体生成结构化需求。
3. 根据需求假设决定是否暂停并等待人工审核。
4. 安全扫描真实代码仓库。
5. 使用 Python AST 为仓库建立结构化符号索引。
6. 根据 Python 导入关系建立模块依赖图。
7. 使用代码影响智能体分析需要修改的文件。
8. 使用测试策略智能体生成可追踪的测试计划。
9. 使用实施计划智能体生成有顺序的开发任务清单。
10. 结束工作流并返回统一结果。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.schemas.code_impact import CodeImpactAnalysis
from app.schemas.code_index import RepositorySymbolIndex
from app.schemas.dependency_graph import (
    RepositoryDependencyGraph,
)
from app.schemas.human_review import (
    RequirementReviewDecision,
    RequirementReviewPrompt,
)
from app.schemas.implementation_plan import (
    ImplementationPlan,
)
from app.schemas.repository import RepositorySnapshot
from app.schemas.requirement import RequirementAnalysis
from app.schemas.test_strategy import TestStrategy
from app.services.code_impact_agent import (
    analyze_code_impact,
)
from app.services.implementation_plan_agent import (
    generate_implementation_plan,
)
from app.services.requirement_agent import (
    analyze_requirement,
    revise_requirement,
)
from app.services.test_strategy_agent import (
    generate_test_strategy,
)
from app.tools.python_dependency_graph import (
    build_python_dependency_graph,
)
from app.tools.python_symbol_indexer import (
    index_python_symbols,
)
from app.tools.repository_scanner import (
    scan_repository,
)
from app.tools.workspace import (
    resolve_repository_directory,
)


class ChangeAnalysisState(TypedDict):
    """软件变更分析工作流中共享的完整状态。"""

    # 用户提交的原始软件变更需求。
    request_text: str

    # 清理多余空格和换行后的规范化需求。
    normalized_request: str

    # 用户选择的工作区仓库名称。
    repository_name: str

    # 需求分析智能体生成的结构化需求分析。
    requirement_analysis: RequirementAnalysis | None

    # 人工审核人员提交的批准或拒绝决定。
    requirement_review: RequirementReviewDecision | None

    # 仓库扫描节点生成的真实代码快照。
    repository_snapshot: RepositorySnapshot | None

    # Python 符号索引节点生成的结构化代码索引。
    repository_symbol_index: RepositorySymbolIndex | None

    # Python 依赖图节点生成的模块导入关系。
    repository_dependency_graph: RepositoryDependencyGraph | None

    # 代码影响智能体生成的代码影响分析。
    code_impact_analysis: CodeImpactAnalysis | None

    # 测试策略智能体生成的结构化测试计划。
    test_strategy: TestStrategy | None

    # 实施计划智能体根据全部上游分析结果
    # 生成的有顺序开发任务清单。
    implementation_plan: ImplementationPlan | None

    # 工作流目前执行到的阶段。
    status: str


RequirementAnalyzer = Callable[
    [str],
    RequirementAnalysis,
]

RequirementRevisionAnalyzer = Callable[
    [
        str,
        RequirementAnalysis,
        str,
    ],
    RequirementAnalysis,
]

RepositoryResolver = Callable[
    [str],
    Path,
]

RepositoryScanner = Callable[
    [str | Path],
    RepositorySnapshot,
]

RepositorySymbolIndexer = Callable[
    [RepositorySnapshot],
    RepositorySymbolIndex,
]

RepositoryDependencyGraphBuilder = Callable[
    [RepositorySymbolIndex],
    RepositoryDependencyGraph,
]

CodeImpactAnalyzer = Callable[
    [
        RequirementAnalysis,
        RepositorySnapshot,
        RequirementReviewDecision | None,
        RepositorySymbolIndex,
        RepositoryDependencyGraph,
    ],
    CodeImpactAnalysis,
]

TestStrategyAnalyzer = Callable[
    [
        RequirementAnalysis,
        CodeImpactAnalysis,
        RepositorySnapshot,
        RequirementReviewDecision | None,
        RepositorySymbolIndex,
        RepositoryDependencyGraph,
    ],
    TestStrategy,
]

ImplementationPlanAnalyzer = Callable[
    [
        RequirementAnalysis,
        CodeImpactAnalysis,
        TestStrategy,
        RepositorySnapshot,
        RequirementReviewDecision | None,
        RepositorySymbolIndex,
        RepositoryDependencyGraph,
    ],
    ImplementationPlan,
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
) -> Callable[
    [ChangeAnalysisState],
    dict[str, object],
]:
    """创建首次需求分析节点。"""

    def requirement_analysis_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """调用需求分析智能体分析规范化需求。"""

        analysis = analyzer(state["normalized_request"])

        return {
            "requirement_analysis": analysis,
            "status": "requirement_analyzed",
        }

    return requirement_analysis_node


def create_requirement_revision_node(
    analyzer: RequirementRevisionAnalyzer,
) -> Callable[
    [ChangeAnalysisState],
    dict[str, object],
]:
    """创建根据人工反馈重新分析需求的节点。"""

    def requirement_revision_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """使用人工拒绝反馈修订上一版需求分析。"""

        previous_analysis = state["requirement_analysis"]
        review_decision = state["requirement_review"]

        if previous_analysis is None:
            raise RuntimeError("工作流缺少待修订的需求分析结果。")

        if review_decision is None or review_decision.action != "reject":
            raise RuntimeError("工作流缺少有效的需求拒绝决定。")

        review_feedback = review_decision.feedback

        if not review_feedback:
            raise RuntimeError("拒绝需求分析时必须提供修改意见。")

        revised_analysis = analyzer(
            state["normalized_request"],
            previous_analysis,
            review_feedback,
        )

        return {
            "requirement_analysis": revised_analysis,
            # 清除上一轮拒绝结果，
            # 防止它影响下一次人工审核。
            "requirement_review": None,
            "status": "requirement_revised",
        }

    return requirement_revision_node


def review_requirement(
    state: ChangeAnalysisState,
) -> dict[str, object]:
    """审核需求分析中的未确认假设。"""

    requirement_analysis = state["requirement_analysis"]

    if requirement_analysis is None:
        raise RuntimeError("工作流缺少需求分析结果。")

    # 没有待确认假设时自动批准。
    if not requirement_analysis.assumptions:
        return {
            "requirement_review": (
                RequirementReviewDecision(
                    action="approve",
                    feedback=("需求分析中没有待确认假设，系统自动继续。"),
                )
            ),
            "status": "requirement_approved",
        }

    review_prompt = RequirementReviewPrompt(
        summary=requirement_analysis.summary,
        assumptions=(requirement_analysis.assumptions),
        clarification_questions=(requirement_analysis.clarification_questions),
    )

    # 暂停工作流，等待用户批准或拒绝。
    decision = interrupt(
        review_prompt.model_dump(mode="json"),
        response_schema=(RequirementReviewDecision),
    )

    review_status = (
        "requirement_approved" if decision.action == "approve" else "requirement_rejected"
    )

    return {
        "requirement_review": decision,
        "status": review_status,
    }


def route_after_requirement_review(
    state: ChangeAnalysisState,
) -> Literal["continue", "revise"]:
    """根据人工审核决定继续分析或修订需求。"""

    decision = state["requirement_review"]

    if decision is None:
        raise RuntimeError("工作流缺少需求人工审核结果。")

    if decision.action == "approve":
        return "continue"

    return "revise"


def create_repository_scan_node(
    resolver: RepositoryResolver,
    scanner: RepositoryScanner,
) -> Callable[
    [ChangeAnalysisState],
    dict[str, object],
]:
    """创建安全解析并扫描代码仓库的节点。"""

    def repository_scan_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """解析仓库名称并读取真实代码文件。"""

        repository_path = resolver(state["repository_name"])
        repository_snapshot = scanner(repository_path)

        return {
            "repository_snapshot": (repository_snapshot),
            "status": "repository_scanned",
        }

    return repository_scan_node


def create_repository_symbol_index_node(
    indexer: RepositorySymbolIndexer,
) -> Callable[
    [ChangeAnalysisState],
    dict[str, object],
]:
    """创建 Python 仓库符号索引节点。"""

    def repository_symbol_index_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """从仓库快照提取类、函数、方法和导入关系。"""

        repository_snapshot = state["repository_snapshot"]

        if repository_snapshot is None:
            raise RuntimeError("工作流缺少代码仓库快照，无法建立符号索引。")

        repository_symbol_index = indexer(repository_snapshot)

        return {
            "repository_symbol_index": (repository_symbol_index),
            "status": "repository_indexed",
        }

    return repository_symbol_index_node


def create_repository_dependency_graph_node(
    graph_builder: (RepositoryDependencyGraphBuilder),
) -> Callable[
    [ChangeAnalysisState],
    dict[str, object],
]:
    """创建 Python 模块依赖图节点。"""

    def repository_dependency_graph_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """根据符号索引中的导入记录建立模块依赖图。"""

        repository_symbol_index = state["repository_symbol_index"]

        if repository_symbol_index is None:
            raise RuntimeError("工作流缺少代码仓库符号索引，无法建立模块依赖图。")

        repository_dependency_graph = graph_builder(repository_symbol_index)

        return {
            "repository_dependency_graph": (repository_dependency_graph),
            "status": ("repository_dependencies_mapped"),
        }

    return repository_dependency_graph_node


def create_code_impact_analysis_node(
    analyzer: CodeImpactAnalyzer,
) -> Callable[
    [ChangeAnalysisState],
    dict[str, object],
]:
    """创建代码影响分析智能体节点。"""

    def code_impact_analysis_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """根据需求、源码、符号索引和依赖图判断影响范围。"""

        requirement_analysis = state["requirement_analysis"]
        repository_snapshot = state["repository_snapshot"]
        repository_symbol_index = state["repository_symbol_index"]
        repository_dependency_graph = state["repository_dependency_graph"]
        requirement_review = state.get("requirement_review")

        if requirement_analysis is None:
            raise RuntimeError("工作流缺少需求分析结果。")

        if repository_snapshot is None:
            raise RuntimeError("工作流缺少代码仓库快照。")

        if repository_symbol_index is None:
            raise RuntimeError("工作流缺少代码仓库符号索引。")

        if repository_dependency_graph is None:
            raise RuntimeError("工作流缺少代码仓库模块依赖图。")

        impact_analysis = analyzer(
            requirement_analysis,
            repository_snapshot,
            requirement_review,
            repository_symbol_index,
            repository_dependency_graph,
        )

        return {
            "code_impact_analysis": (impact_analysis),
            "status": "code_impact_analyzed",
        }

    return code_impact_analysis_node


def create_test_strategy_node(
    analyzer: TestStrategyAnalyzer,
) -> Callable[
    [ChangeAnalysisState],
    dict[str, object],
]:
    """创建测试策略智能体节点。"""

    def test_strategy_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """根据需求、代码影响和仓库证据生成测试策略。"""

        requirement_analysis = state["requirement_analysis"]
        code_impact_analysis = state["code_impact_analysis"]
        repository_snapshot = state["repository_snapshot"]
        repository_symbol_index = state["repository_symbol_index"]
        repository_dependency_graph = state["repository_dependency_graph"]
        requirement_review = state.get("requirement_review")

        if requirement_analysis is None:
            raise RuntimeError("工作流缺少需求分析结果。")

        if code_impact_analysis is None:
            raise RuntimeError("工作流缺少代码影响分析结果。")

        if repository_snapshot is None:
            raise RuntimeError("工作流缺少代码仓库快照。")

        if repository_symbol_index is None:
            raise RuntimeError("工作流缺少代码仓库符号索引。")

        if repository_dependency_graph is None:
            raise RuntimeError("工作流缺少代码仓库模块依赖图。")

        test_strategy = analyzer(
            requirement_analysis,
            code_impact_analysis,
            repository_snapshot,
            requirement_review,
            repository_symbol_index,
            repository_dependency_graph,
        )

        return {
            "test_strategy": test_strategy,
            "status": "test_strategy_generated",
        }

    return test_strategy_node


def create_implementation_plan_node(
    analyzer: ImplementationPlanAnalyzer,
) -> Callable[
    [ChangeAnalysisState],
    dict[str, object],
]:
    """创建实施计划智能体节点。"""

    def implementation_plan_node(
        state: ChangeAnalysisState,
    ) -> dict[str, object]:
        """根据全部分析证据生成有顺序的开发任务清单。"""

        requirement_analysis = state["requirement_analysis"]
        code_impact_analysis = state["code_impact_analysis"]
        test_strategy = state["test_strategy"]
        repository_snapshot = state["repository_snapshot"]
        repository_symbol_index = state["repository_symbol_index"]
        repository_dependency_graph = state["repository_dependency_graph"]
        requirement_review = state.get("requirement_review")

        # 实施计划是最终分析结果，
        # 必须建立在完整的上游证据之上。
        if requirement_analysis is None:
            raise RuntimeError("工作流缺少需求分析结果。")

        if code_impact_analysis is None:
            raise RuntimeError("工作流缺少代码影响分析结果。")

        if test_strategy is None:
            raise RuntimeError("工作流缺少测试策略。")

        if repository_snapshot is None:
            raise RuntimeError("工作流缺少代码仓库快照。")

        if repository_symbol_index is None:
            raise RuntimeError("工作流缺少代码仓库符号索引。")

        if repository_dependency_graph is None:
            raise RuntimeError("工作流缺少代码仓库模块依赖图。")

        implementation_plan = analyzer(
            requirement_analysis,
            code_impact_analysis,
            test_strategy,
            repository_snapshot,
            requirement_review,
            repository_symbol_index,
            repository_dependency_graph,
        )

        return {
            "implementation_plan": (implementation_plan),
            "status": ("implementation_plan_generated"),
        }

    return implementation_plan_node


def build_change_analysis_graph(
    requirement_analyzer: (RequirementAnalyzer) = analyze_requirement,
    requirement_revision_analyzer: (RequirementRevisionAnalyzer) = revise_requirement,
    repository_resolver: RepositoryResolver = (resolve_repository_directory),
    repository_scanner: RepositoryScanner = (scan_repository),
    repository_symbol_indexer: (RepositorySymbolIndexer) = index_python_symbols,
    repository_dependency_graph_builder: (
        RepositoryDependencyGraphBuilder
    ) = build_python_dependency_graph,
    code_impact_analyzer: (CodeImpactAnalyzer) = analyze_code_impact,
    test_strategy_analyzer: (TestStrategyAnalyzer) = generate_test_strategy,
    implementation_plan_analyzer: (ImplementationPlanAnalyzer) = generate_implementation_plan,
    human_review_enabled: bool = False,
    checkpointer: InMemorySaver | None = None,
):
    """创建并编译完整的软件变更分析工作流。

    human_review_enabled 为 True 时，
    工作流会在需求分析后进入人工审核。

    checkpointer 用于保存中断状态，
    使工作流可以通过同一个 thread_id 恢复。

    repository_symbol_indexer 使用 Python AST
    从仓库快照中提取结构化代码信息。

    repository_dependency_graph_builder 根据符号索引中的
    import 记录建立确定性的模块依赖图。

    test_strategy_analyzer 根据已确认需求、代码影响结果
    和仓库证据生成结构化测试计划。

    implementation_plan_analyzer 根据全部分析结果
    生成带依赖、验证命令和回滚说明的开发任务清单。
    """

    builder = StateGraph(ChangeAnalysisState)

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
        "index_repository_symbols",
        create_repository_symbol_index_node(repository_symbol_indexer),
    )

    builder.add_node(
        "map_repository_dependencies",
        create_repository_dependency_graph_node(repository_dependency_graph_builder),
    )

    builder.add_node(
        "analyze_code_impact",
        create_code_impact_analysis_node(code_impact_analyzer),
    )

    builder.add_node(
        "generate_test_strategy",
        create_test_strategy_node(test_strategy_analyzer),
    )

    builder.add_node(
        "generate_implementation_plan",
        create_implementation_plan_node(implementation_plan_analyzer),
    )

    builder.add_edge(
        START,
        "normalize_request",
    )

    builder.add_edge(
        "normalize_request",
        "analyze_requirement",
    )

    if human_review_enabled:
        builder.add_node(
            "review_requirement",
            review_requirement,
        )

        builder.add_node(
            "revise_requirement",
            create_requirement_revision_node(requirement_revision_analyzer),
        )

        builder.add_edge(
            "analyze_requirement",
            "review_requirement",
        )

        builder.add_conditional_edges(
            "review_requirement",
            route_after_requirement_review,
            {
                "continue": "scan_repository",
                "revise": "revise_requirement",
            },
        )

        builder.add_edge(
            "revise_requirement",
            "review_requirement",
        )
    else:
        builder.add_edge(
            "analyze_requirement",
            "scan_repository",
        )

    builder.add_edge(
        "scan_repository",
        "index_repository_symbols",
    )

    builder.add_edge(
        "index_repository_symbols",
        "map_repository_dependencies",
    )

    builder.add_edge(
        "map_repository_dependencies",
        "analyze_code_impact",
    )

    builder.add_edge(
        "analyze_code_impact",
        "generate_test_strategy",
    )

    builder.add_edge(
        "generate_test_strategy",
        "generate_implementation_plan",
    )

    builder.add_edge(
        "generate_implementation_plan",
        END,
    )

    return builder.compile(checkpointer=checkpointer)


# 生产 API 正式启用需求人工审核。
# InMemorySaver 适合当前本地开发阶段，
# 服务重启后检查点会丢失。
change_analysis_graph = build_change_analysis_graph(
    human_review_enabled=True,
    checkpointer=InMemorySaver(),
)
