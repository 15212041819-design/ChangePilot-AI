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
from app.services.requirement_agent import (
    analyze_requirement,
    revise_requirement,
)
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

    # 需求分析智能体生成的结构化需求分析。
    requirement_analysis: RequirementAnalysis | None

    # 人工审核人员提交的批准或拒绝决定。
    # 尚未进入人工审核阶段时，该字段为 None。
    requirement_review: RequirementReviewDecision | None

    # 仓库扫描节点生成的真实代码快照。
    repository_snapshot: RepositorySnapshot | None

    # 代码影响智能体生成的代码影响分析。
    code_impact_analysis: CodeImpactAnalysis | None

    # 工作流目前执行到的阶段。
    status: str


# 下面的 Callable 类型定义了各个组件必须遵守的调用形式。
# 使用依赖注入后，测试可以传入假组件，避免调用真实 DeepSeek。
RequirementAnalyzer = Callable[[str], RequirementAnalysis]

# 需求修订智能体接收原始需求、上一版分析结果和人工反馈。
RequirementRevisionAnalyzer = Callable[
    [
        str,
        RequirementAnalysis,
        str,
    ],
    RequirementAnalysis,
]

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
        """调用需求分析智能体分析规范化后的需求。"""

        analysis = analyzer(state["normalized_request"])

        return {
            "requirement_analysis": analysis,
            "status": "requirement_analyzed",
        }

    return requirement_analysis_node


def create_requirement_revision_node(
    analyzer: RequirementRevisionAnalyzer,
) -> Callable[[ChangeAnalysisState], dict[str, object]]:
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
        # 工作流才可以进入需求修订节点。
        if review_decision is None or review_decision.action != "reject":
            raise RuntimeError("工作流缺少有效的需求拒绝决定。")

        # 拒绝反馈是修订需求的重要输入。
        # Pydantic 模型已经负责校验，这里再次检查，
        # 防止工作流节点被脱离 API 单独调用时收到无效数据。
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
            # 清除上一轮拒绝结果，避免旧结果影响下一轮人工审核。
            "requirement_review": None,
            "status": "requirement_revised",
        }

    return requirement_revision_node


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
    # API 和前端可以直接使用这些字段展示审核界面。
    review_prompt = RequirementReviewPrompt(
        summary=requirement_analysis.summary,
        assumptions=requirement_analysis.assumptions,
        clarification_questions=(requirement_analysis.clarification_questions),
    )

    # interrupt() 会暂停当前工作流并保存检查点。
    # 恢复工作流时，Command(resume=...) 中的数据会成为返回值。
    # response_schema 使用 Pydantic 模型校验人工提交的数据。
    decision = interrupt(
        review_prompt.model_dump(mode="json"),
        response_schema=RequirementReviewDecision,
    )

    # 批准和拒绝都会暂时记录到工作流状态中。
    # 拒绝结果随后会由需求修订节点读取。
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
    """根据人工审核决定继续分析或重新修订需求。"""

    decision = state["requirement_review"]

    # 正常情况下，审核节点一定会写入审核结果。
    # 显式检查可以避免异常状态进入后续流程。
    if decision is None:
        raise RuntimeError("工作流缺少需求人工审核结果。")

    # 人工批准后，才允许读取仓库并分析代码影响。
    if decision.action == "approve":
        return "continue"

    # 人工拒绝后，根据反馈重新生成需求分析，
    # 而不是直接结束整个工作流。
    return "revise"


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
        # 例如关闭人工审核的旧流程，或者没有审核上下文的独立测试。
        requirement_review = state.get("requirement_review")

        # 如果前面的节点没有产生结果，说明工作流状态异常。
        if requirement_analysis is None:
            raise RuntimeError("工作流缺少需求分析结果。")

        if repository_snapshot is None:
            raise RuntimeError("工作流缺少代码仓库快照。")

        # 将人工审核结果作为第三个参数传给代码影响智能体。
        # 智能体可以使用最终批准的需求和人工补充信息。
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
    requirement_revision_analyzer: RequirementRevisionAnalyzer = (revise_requirement),
    repository_resolver: RepositoryResolver = (resolve_repository_directory),
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

    # 注册代码影响分析智能体节点。
    builder.add_node(
        "analyze_code_impact",
        create_code_impact_analysis_node(code_impact_analyzer),
    )

    # 工作流首先规范化用户输入，然后执行首次需求分析。
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

        # 需求修订节点负责使用人工拒绝反馈重新分析需求。
        builder.add_node(
            "revise_requirement",
            create_requirement_revision_node(
                requirement_revision_analyzer,
            ),
        )

        # 首次需求分析完成后进入人工审核。
        builder.add_edge(
            "analyze_requirement",
            "review_requirement",
        )

        # 审核批准后扫描仓库，拒绝后进入需求修订节点。
        builder.add_conditional_edges(
            "review_requirement",
            route_after_requirement_review,
            {
                "continue": "scan_repository",
                "revise": "revise_requirement",
            },
        )

        # 修订完成后再次进入人工审核。
        # 由此形成“审核—修订—再审核”的需求确认闭环。
        builder.add_edge(
            "revise_requirement",
            "review_requirement",
        )
    else:
        # 关闭人工审核时，需求分析完成后直接扫描仓库。
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
# InMemorySaver 适合当前本地开发阶段，服务重启后检查点会丢失。
# 后续接入 PostgreSQL 时，再替换为持久化检查点。
change_analysis_graph = build_change_analysis_graph(
    human_review_enabled=True,
    checkpointer=InMemorySaver(),
)
