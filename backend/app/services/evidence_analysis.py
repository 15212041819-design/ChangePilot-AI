"""Evidence-grounded 分析 API 与生产执行器之间的边界。

生产执行器负责组装完整链路，但大型仓库索引始终保存在 LangGraph state 外：

1. 安全解析并扫描工作区仓库；
2. 建立符号索引、依赖图、仓库词表和历史共变索引；
3. 对共变索引执行时间泄漏自检；
4. 调用 Requirement Agent 和 Semantic Mapping Agent；
5. 生成并 Guard 校验 ChangePoint；
6. 运行 Impact、Test、Risk 分支；
7. 执行结果审核并返回统一报告。

每个请求的 Git ref 都会先解析为精确 commit SHA，再在临时脱离式
worktree 中分析。该过程不切换用户分支，也不修改用户当前工作区。
"""

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from langchain_core.language_models.chat_models import (
    BaseChatModel,
)

from app.engines.impact_engine import (
    analyze_impact_evidence,
)
from app.engines.risk_engine import (
    analyze_risks,
)
from app.engines.test_strategy_engine import (
    analyze_test_strategy,
)
from app.graph.parallel_analysis import (
    build_parallel_analysis_graph,
    create_parallel_analysis_state,
)
from app.schemas.analysis_report import (
    AnalysisReport,
)
from app.schemas.branch_result import (
    BranchResult,
)
from app.schemas.contracts import (
    CandidateSet,
)
from app.schemas.evidence_analysis_api import (
    EvidenceAnalysisRequest,
)
from app.schemas.guard import GuardDigests
from app.services.llm import get_chat_model
from app.services.requirement_spec_agent import (
    analyze_requirement_spec,
)
from app.services.semantic_context import (
    MIN_INDEX_COVERAGE,
    build_semantic_context,
)
from app.tools.context_builder import (
    build_repository_vocabulary,
)
from app.tools.git_cochange import (
    CoChangeError,
    build_cochange_index,
    check_cochange_leak,
)
from app.tools.git_snapshot import (
    GitSnapshotError,
    checkout_repository_snapshot,
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
    DEFAULT_WORKSPACE_ROOT,
    resolve_repository_directory,
)


class EvidenceAnalysisUnavailableError(RuntimeError):
    """请求需要尚未接入且不能安全模拟的运行时能力。"""


class EvidenceAnalysisReviewRequiredError(RuntimeError):
    """需求包含尚未确认的假设，必须先完成人工审核。"""


class EvidenceAnalysisExecutor(Protocol):
    """路由所依赖的最小执行器协议。"""

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
    ) -> AnalysisReport:
        """执行完整分析并返回已审核的统一报告。"""


class ProductionEvidenceAnalysisExecutor:
    """组装真实仓库工具、两个 LLM 步骤和三个确定性分析分支。"""

    def __init__(
        self,
        *,
        workspace_root: str | Path = (DEFAULT_WORKSPACE_ROOT),
        model: BaseChatModel | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._model = model

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
    ) -> AnalysisReport:
        """在隔离的 Git 快照上执行完整分析。"""

        repository_path = resolve_repository_directory(
            request.repository_name,
            workspace_root=self._workspace_root,
        )

        try:
            with checkout_repository_snapshot(
                repository_path,
                request.commit,
            ) as checkout:
                return self._analyze_snapshot(
                    request,
                    repository_path=(checkout.repository_path),
                    resolved_commit=(checkout.resolved_commit),
                )

        except (
            GitSnapshotError,
            CoChangeError,
        ) as exc:
            raise EvidenceAnalysisUnavailableError(f"无法创建或读取可信 Git 证据：{exc}") from exc

    def _analyze_snapshot(
        self,
        request: EvidenceAnalysisRequest,
        *,
        repository_path: Path,
        resolved_commit: str,
    ) -> AnalysisReport:
        """分析已经隔离且固定到精确 commit 的仓库目录。"""

        repository = scan_repository(repository_path)

        if not repository.files:
            raise ValueError("仓库扫描结果为空，不能执行证据分析。")

        # 共变索引必须在任何 LLM 调用之前建立并校验。
        # 一旦泄漏检查失败，本次分析立即终止。
        cochange_index = build_cochange_index(
            repository_path,
            resolved_commit,
        )

        leak_report = check_cochange_leak(
            repository_path,
            cochange_index,
        )

        if not leak_report.passed:
            raise EvidenceAnalysisUnavailableError("Git 共变历史未通过时间泄漏自检，已停止分析。")

        # 只解析一次 AST。
        # Requirement Agent、Semantic Mapping、三个分支
        # 全部共享这一份索引和由它构建的依赖图。
        symbol_index = index_python_symbols(repository)

        dependency_graph = build_python_dependency_graph(symbol_index)

        repository_vocabulary = build_repository_vocabulary(
            repository,
            symbol_index,
        )

        selected_model = self._model or get_chat_model()

        requirement_result = analyze_requirement_spec(
            request.request_text,
            repository_vocabulary=set(repository_vocabulary),
            model=selected_model,
        )

        unresolved_assumptions = tuple(
            assumption.id
            for assumption in requirement_result.spec.assumptions
            if assumption.status != "accepted"
        )

        if unresolved_assumptions:
            raise EvidenceAnalysisReviewRequiredError(
                "需求分析包含尚未确认的假设："
                + "、".join(unresolved_assumptions)
                + "；请先通过需求审核接口确认"
                + "或修订后再运行证据分析。"
            )

        semantic_context = build_semantic_context(
            requirement_result.spec,
            repository,
            commit=resolved_commit,
            model=selected_model,
            prebuilt_symbol_index=(symbol_index),
            prebuilt_dependency_graph=(dependency_graph),
            prebuilt_repository_vocabulary=(repository_vocabulary),
            prebuilt_cochange_index=(cochange_index),
        )

        if semantic_context.index_coverage < MIN_INDEX_COVERAGE:
            raise ValueError("Python AST 索引覆盖率低于 70%，禁止输出看似完整的分析报告。")

        candidate_set = semantic_context.candidate_set

        if not candidate_set.files:
            raise ValueError("冻结 CandidateSet 为空，不能启动并行分析。")

        digests = GuardDigests(
            set_digest=(candidate_set.set_digest),
            spec_digest=(candidate_set.spec_digest),
            index_digest=(candidate_set.index_digest),
        )

        base_resources: dict[
            str,
            object,
        ] = {
            "requirement_spec": (requirement_result.spec),
            "repository_snapshot": (repository),
            "symbol_index": (symbol_index),
            "dependency_graph": (dependency_graph),
            "cochange_index": (cochange_index),
            # schema 索引器尚未接入时显式为空，
            # Risk Engine 不会猜测模型字段。
            "schema_models": (),
        }

        # ChangePoint 是三个分支共享的中性、
        # 已验证证据。
        impact_evidence = analyze_impact_evidence(
            candidate_set,
            digests,
            base_resources,
        )

        resource_id = f"evidence-{uuid4().hex}"

        branch_resources = {
            **base_resources,
            "validated_change_points": (impact_evidence.change_points),
        }

        resources: dict[
            str,
            object,
        ] = {
            resource_id: branch_resources,
        }

        def cached_impact_runner(
            branch_candidate_set: CandidateSet,
            branch_digests: GuardDigests,
            _: Mapping[str, object],
        ) -> BranchResult:
            """把预先 Guard 通过的 Impact 结果送入并行 fan-in。"""

            if branch_candidate_set != candidate_set or branch_digests != digests:
                raise ValueError("Impact 缓存与并行工作流输入不一致。")

            return impact_evidence.branch_result

        graph = build_parallel_analysis_graph(
            impact_runner=(cached_impact_runner),
            test_runner=(analyze_test_strategy),
            risk_runner=(analyze_risks),
            resources=resources,
        )

        final_state = graph.invoke(
            create_parallel_analysis_state(
                candidate_set,
                resource_id=resource_id,
            )
        )

        raw_report = final_state.get("analysis_report")

        if raw_report is None:
            error = final_state.get("report_error") or final_state.get("review_error")

            raise ValueError(f"统一报告生成失败：{error or '未知错误'}")

        return AnalysisReport.model_validate(raw_report)


# 保留旧名称作为兼容别名，
# 避免外部导入在一次升级中立即失效。
UnconfiguredEvidenceAnalysisExecutor = ProductionEvidenceAnalysisExecutor


@lru_cache
def get_evidence_analysis_executor() -> EvidenceAnalysisExecutor:
    """返回已经接入真实工具链的生产执行器。"""

    return ProductionEvidenceAnalysisExecutor()
