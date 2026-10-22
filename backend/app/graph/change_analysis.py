"""ChangePilot AI 软件变更分析工作流。"""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.schemas.code_impact import CodeImpactAnalysis
from app.schemas.human_review import (
    RequirementReviewDecision,
    RequirementReviewPrompt,
)
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

    # 人工审核人员提交的批准或拒绝决定。
    # 尚未进入人工审核阶段时，该字段为 None。
    requirement_review: RequirementReviewDecision | None

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
# 代码影响智能体接收需求分析、仓库快照和人工审核结果。
CodeImpactAnalyzer = Callable[
    [
        RequirementAnalysis,
        RepositorySnapshot,
        RequirementReviewDecision | None,
    ],
    CodeImpactAnalysis,
]


def normalize_request(
    state: ChangeAnalysisState,
) -> dict[str, str]:
    """清理用户输入的软件变更需求。"""

    # split() 会按任意空白字符切分文本。
    # 再使用单个空格连接，可以清理多余空格、换行和制表符。
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


def review_requirement(
    state: ChangeAnalysisState,
) -> dict[str, object]:
    """审核需求分析中的未确认假设。"""

    requirement_analysis = state["requirement_analysis"]

    # 人工审核节点依赖前一个节点产生的需求分析结果。
    # 如果结果不存在，说明工作流连接或状态发生了异常。
    if requirement_analysis is None:
        raise RuntimeError("工作流缺少需求分析结果。")

    # 如果需求分析没有提出任何假设，就没有必要打断用户。
    # 此时系统自动生成批准结果，让工作流继续扫描仓库。
    if not requirement_analysis.assumptions:
        return {
            "requirement_review": RequirementReviewDecision(
                action="approve",
                feedback="需求分析中没有待确认假设，系统自动继续。",
            ),
            "status": "requirement_approved",
        }

    # 将需求摘要、假设和澄清问题整理成结构化中断载荷。
    # 后续 API 和前端可以直接使用这些字段展示审核界面。
    review_prompt = RequirementReviewPrompt(
        summary=requirement_analysis.summary,
        assumptions=requirement_analysis.assumptions,
        clarification_questions=(requirement_analysis.clarification_questions),
    )

    # interrupt() 会暂停当前工作流并保存检查点。
    # 恢复工作流时，Command(resume=...) 中的数据会成为这里的返回值。
    # response_schema 会使用 Pydantic 模型校验人工提交的数据。
    decision = interrupt(
        review_prompt.model_dump(mode="json"),
        response_schema=RequirementReviewDecision,
    )

    # 根据人工决定记录当前工作流状态。
    review_status = (
        "requirement_approved" if decision.action == "approve" else "requirement_rejected"
    )

    return {
        "requirement_review": decision,
        "status": review_status,
    }


def route_after_requirement_review(
    state: ChangeAnalysisState,
) -> Literal["continue", "stop"]:
    """根据人工审核决定选择后续工作流分支。"""

    decision = state["requirement_review"]

    # 正常情况下，审核节点一定会写入审核结果。
    # 显式检查可以避免异常状态继续进入仓库分析阶段。
    if decision is None:
        raise RuntimeError("工作流缺少需求人工审核结果。")

    # 只有人工批准后才允许读取仓库并分析代码影响。
    if decision.action == "approve":
        return "continue"

    # 人工拒绝后直接结束本次工作流。
    return "stop"


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

        # 人工审核结果可能不存在。
        # 例如关闭 HITL 的旧流程或没有审核上下文的独立测试。
        requirement_review = state.get("requirement_review")

        # 如果前面的节点没有产生结果，说明工作流状态异常。
        if requirement_analysis is None:
            raise RuntimeError("工作流缺少需求分析结果。")

        if repository_snapshot is None:
            raise RuntimeError("工作流缺少代码仓库快照。")

        # 将人工审核结果作为第三个参数传给代码影响智能体。
        # 审核批准后，智能体可以使用已确认假设和人工反馈。
        impact_analysis = analyzer(
            requirement_analysis,
            repository_snapshot,
            requirement_review,
        )

        return {
            "code_impact_analysis": impact_analysis,
            "status": "code_impact_analyzed",
        }

    return code_impact_analysis_node


def build_change_analysis_graph(
    requirement_analyzer: RequirementAnalyzer = analyze_requirement,
    repository_resolver: RepositoryResolver = resolve_repository_directory,
    repository_scanner: RepositoryScanner = scan_repository,
    code_impact_analyzer: CodeImpactAnalyzer = analyze_code_impact,
    human_review_enabled: bool = False,
    checkpointer: InMemorySaver | None = None,
):
    """创建并编译完整的软件变更分析工作流。

    human_review_enabled 为 True 时启用需求人工审核节点。
    checkpointer 用于保存中断时的工作流状态，以便稍后恢复。
    """

    builder = StateGraph(ChangeAnalysisState)

    # 注册需求规范化节点。
    builder.add_node(
        "normalize_request",
        normalize_request,
    )

    # 注册需求分析智能体节点。
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

    # 注册代码影响分析智能体节点。
    builder.add_node(
        "analyze_code_impact",
        create_code_impact_analysis_node(code_impact_analyzer),
    )

    # 工作流首先规范化用户输入，然后执行需求分析。
    builder.add_edge(
        START,
        "normalize_request",
    )
    builder.add_edge(
        "normalize_request",
        "analyze_requirement",
    )

    if human_review_enabled:
        # 启用人工审核时，在需求分析和仓库扫描之间加入审核节点。
        builder.add_node(
            "review_requirement",
            review_requirement,
        )
        builder.add_edge(
            "analyze_requirement",
            "review_requirement",
        )

        # 审核批准后继续扫描仓库，拒绝后直接结束工作流。
        builder.add_conditional_edges(
            "review_requirement",
            route_after_requirement_review,
            {
                "continue": "scan_repository",
                "stop": END,
            },
        )
    else:
        # 暂时保留原有执行路径，确保现有 API 在改造完成前继续可用。
        builder.add_edge(
            "analyze_requirement",
            "scan_repository",
        )

    # 仓库扫描完成后执行代码影响分析。
    builder.add_edge(
        "scan_repository",
        "analyze_code_impact",
    )

    # 代码影响分析完成后结束工作流。
    builder.add_edge(
        "analyze_code_impact",
        END,
    )

    # 没有人工中断时可以不提供检查点。
    # 启用人工审核并需要恢复工作流时，应传入 InMemorySaver。
    return builder.compile(checkpointer=checkpointer)


# 生产 API 正式启用需求人工审核。
# InMemorySaver 适合当前本地开发阶段；服务重启后检查点会丢失。
# 后续接入 PostgreSQL 时再替换为持久化检查点。
change_analysis_graph = build_change_analysis_graph(
    human_review_enabled=True,
    checkpointer=InMemorySaver(),
)
