"""ChangePilot AI 软件变更分析工作流。

本模块负责使用 LangGraph 编排完整的软件变更分析过程。

当前主要流程为：

1. 规范化用户输入。
2. 使用需求分析智能体生成结构化需求。
3. 根据需求假设决定是否暂停并等待人工审核。
4. 安全扫描真实代码仓库。
5. 使用 Python AST 为仓库建立结构化符号索引。
6. 使用代码影响智能体分析需要修改的文件。
7. 结束工作流并返回统一结果。

符号索引会保存在 LangGraph 状态中，
并作为代码影响智能体的正式输入，帮助模型定位真实代码符号。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.schemas.code_impact import CodeImpactAnalysis
from app.schemas.code_index import RepositorySymbolIndex
from app.schemas.human_review import (
    RequirementReviewDecision,
    RequirementReviewPrompt,
)
from app.schemas.repository import RepositorySnapshot
from app.schemas.requirement import RequirementAnalysis
from app.services.code_impact_agent import analyze_code_impact
from app.services.requirement_agent import (
    analyze_requirement,
    revise_requirement,
)
from app.tools.python_symbol_indexer import (
    index_python_symbols,
)
from app.tools.repository_scanner import scan_repository
from app.tools.workspace import resolve_repository_directory


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
    # 尚未进入人工审核阶段时为 None。
    requirement_review: RequirementReviewDecision | None

    # 仓库扫描节点生成的真实代码快照。
    repository_snapshot: RepositorySnapshot | None

    # Python 符号索引节点生成的结构化代码索引。
    # 仓库尚未扫描或索引时为 None。
    repository_symbol_index: RepositorySymbolIndex | None

    # 代码影响智能体生成的代码影响分析。
    code_impact_analysis: CodeImpactAnalysis | None

    # 工作流目前执行到的阶段。
    status: str


# 需求分析智能体接收规范化后的需求文本。
RequirementAnalyzer = Callable[
    [str],
    RequirementAnalysis,
]

# 需求修订智能体接收原始需求、
# 上一版分析结果以及人工拒绝反馈。
RequirementRevisionAnalyzer = Callable[
    [
        str,
        RequirementAnalysis,
        str,
    ],
    RequirementAnalysis,
]

# 工作区解析器把用户提供的仓库名称
# 转换成经过安全校验的本地目录。
RepositoryResolver = Callable[
    [str],
    Path,
]

# 仓库扫描器读取真实代码并生成有限、安全的快照。
RepositoryScanner = Callable[
    [str | Path],
    RepositorySnapshot,
]

# 符号索引器接收仓库快照，
# 返回类、函数、方法和导入关系。
RepositorySymbolIndexer = Callable[
    [RepositorySnapshot],
    RepositorySymbolIndex,
]

# 代码影响智能体接收需求分析、仓库快照、
# 人工审核结果和确定性的 Python 符号索引。
CodeImpactAnalyzer = Callable[
    [
        RequirementAnalysis,
        RepositorySnapshot,
        RequirementReviewDecision | None,
        RepositorySymbolIndex,
    ],
    CodeImpactAnalysis,
]


def normalize_request(
    state: ChangeAnalysisState,
) -> dict[str, str]:
    """清理用户输入的软件变更需求。"""

    # split() 会按任意空白字符切分文本。
    # 使用单个空格重新连接，可以清除多余空格、
    # 换行符和制表符。
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

        # 需求修订必须建立在已有分析结果之上。
        if previous_analysis is None:
            raise RuntimeError("工作流缺少待修订的需求分析结果。")

        # 只有人工明确拒绝当前需求分析时，
        # 工作流才允许进入需求修订节点。
        if review_decision is None or review_decision.action != "reject":
            raise RuntimeError("工作流缺少有效的需求拒绝决定。")

        # 拒绝反馈是需求修订的重要输入。
        # Pydantic 模型已经校验过一次，
        # 这里再次检查以保护节点的独立调用。
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

    # 人工审核节点必须建立在需求分析结果之上。
    if requirement_analysis is None:
        raise RuntimeError("工作流缺少需求分析结果。")

    # 如果没有任何待确认假设，
    # 系统可以自动批准并继续扫描仓库。
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

    # 将需求摘要、假设和澄清问题
    # 转换成前端可以直接展示的审核载荷。
    review_prompt = RequirementReviewPrompt(
        summary=requirement_analysis.summary,
        assumptions=(requirement_analysis.assumptions),
        clarification_questions=(requirement_analysis.clarification_questions),
    )

    # interrupt() 会暂停当前 LangGraph 工作流。
    # 恢复时，Command(resume=...) 中的数据
    # 会成为这里的返回值。
    decision = interrupt(
        review_prompt.model_dump(mode="json"),
        response_schema=RequirementReviewDecision,
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

    # 批准后才允许读取仓库并分析代码。
    if decision.action == "approve":
        return "continue"

    # 拒绝后返回需求修订节点，
    # 而不是结束整个工作流。
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

        # resolver 会确保最终目录
        # 位于允许访问的工作区内部。
        repository_path = resolver(state["repository_name"])

        # scanner 会过滤缓存目录、依赖目录、
        # 密钥文件和体积过大的文件。
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

        # 符号索引只能在仓库扫描完成后执行。
        # 缺少快照通常意味着图的边连接错误。
        if repository_snapshot is None:
            raise RuntimeError("工作流缺少代码仓库快照，无法建立符号索引。")

        # 索引器是确定性工具，不调用大模型，
        # 也不会执行仓库中的 Python 代码。
        repository_symbol_index = indexer(repository_snapshot)

        return {
            "repository_symbol_index": (repository_symbol_index),
            "status": "repository_indexed",
        }

    return repository_symbol_index_node


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
        """根据需求、仓库快照和符号索引判断代码影响范围。"""

        requirement_analysis = state["requirement_analysis"]
        repository_snapshot = state["repository_snapshot"]
        repository_symbol_index = state["repository_symbol_index"]

        # 人工审核结果在关闭 HITL 的旧流程中
        # 可能为 None。
        requirement_review = state.get("requirement_review")

        if requirement_analysis is None:
            raise RuntimeError("工作流缺少需求分析结果。")

        if repository_snapshot is None:
            raise RuntimeError("工作流缺少代码仓库快照。")

        # 代码影响智能体必须使用确定性的符号索引，
        # 不能只依赖模型阅读原始源码后自行猜测类和函数。
        if repository_symbol_index is None:
            raise RuntimeError("工作流缺少代码仓库符号索引。")

        # 把人工确认后的需求、真实仓库快照和 AST 符号证据
        # 一起交给代码影响智能体。
        impact_analysis = analyzer(
            requirement_analysis,
            repository_snapshot,
            requirement_review,
            repository_symbol_index,
        )

        return {
            "code_impact_analysis": impact_analysis,
            "status": "code_impact_analyzed",
        }

    return code_impact_analysis_node


def build_change_analysis_graph(
    requirement_analyzer: RequirementAnalyzer = (analyze_requirement),
    requirement_revision_analyzer: (RequirementRevisionAnalyzer) = revise_requirement,
    repository_resolver: RepositoryResolver = (resolve_repository_directory),
    repository_scanner: RepositoryScanner = (scan_repository),
    repository_symbol_indexer: (RepositorySymbolIndexer) = index_python_symbols,
    code_impact_analyzer: CodeImpactAnalyzer = (analyze_code_impact),
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
    """

    builder = StateGraph(ChangeAnalysisState)

    # 注册需求规范化节点。
    builder.add_node(
        "normalize_request",
        normalize_request,
    )

    # 注册首次需求分析智能体节点。
    builder.add_node(
        "analyze_requirement",
        create_requirement_analysis_node(requirement_analyzer),
    )

    # 注册安全仓库扫描节点。
    builder.add_node(
        "scan_repository",
        create_repository_scan_node(
            repository_resolver,
            repository_scanner,
        ),
    )

    # 注册确定性的 Python 符号索引节点。
    builder.add_node(
        "index_repository_symbols",
        create_repository_symbol_index_node(repository_symbol_indexer),
    )

    # 注册代码影响分析智能体节点。
    builder.add_node(
        "analyze_code_impact",
        create_code_impact_analysis_node(code_impact_analyzer),
    )

    # 工作流入口首先清理用户输入。
    builder.add_edge(
        START,
        "normalize_request",
    )

    # 输入规范化后执行首次需求分析。
    builder.add_edge(
        "normalize_request",
        "analyze_requirement",
    )

    if human_review_enabled:
        # 启用人工审核时，
        # 在需求分析和仓库扫描之间加入审核节点。
        builder.add_node(
            "review_requirement",
            review_requirement,
        )

        # 注册人工拒绝后的需求修订节点。
        builder.add_node(
            "revise_requirement",
            create_requirement_revision_node(requirement_revision_analyzer),
        )

        # 首次需求分析完成后进入人工审核。
        builder.add_edge(
            "analyze_requirement",
            "review_requirement",
        )

        # 批准后扫描仓库；
        # 拒绝后根据反馈修订需求。
        builder.add_conditional_edges(
            "review_requirement",
            route_after_requirement_review,
            {
                "continue": "scan_repository",
                "revise": "revise_requirement",
            },
        )

        # 修订完成后重新进入人工审核，
        # 形成“审核—修订—再审核”闭环。
        builder.add_edge(
            "revise_requirement",
            "review_requirement",
        )
    else:
        # 关闭人工审核时，
        # 需求分析完成后直接扫描仓库。
        builder.add_edge(
            "analyze_requirement",
            "scan_repository",
        )

    # 仓库扫描完成后建立结构化符号索引。
    builder.add_edge(
        "scan_repository",
        "index_repository_symbols",
    )

    # 符号索引完成后再调用代码影响智能体。
    # 这保证工作流状态中已经存在索引结果。
    builder.add_edge(
        "index_repository_symbols",
        "analyze_code_impact",
    )

    # 代码影响分析完成后结束工作流。
    builder.add_edge(
        "analyze_code_impact",
        END,
    )

    # 没有人工中断时可以不提供检查点。
    # 启用人工审核并需要恢复工作流时，
    # 应传入 InMemorySaver 或持久化检查点。
    return builder.compile(checkpointer=checkpointer)


# 生产 API 正式启用需求人工审核。
# InMemorySaver 适合当前本地开发阶段，
# 但服务重启后检查点会丢失。
# 后续将替换成 SQLite 或 PostgreSQL 检查点。
change_analysis_graph = build_change_analysis_graph(
    human_review_enabled=True,
    checkpointer=InMemorySaver(),
)
