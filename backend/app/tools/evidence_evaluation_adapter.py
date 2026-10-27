"""证据驱动检索与确定性 Impact 引擎的真实评测适配器。

该适配器运行 Requirement Agent；语义模式还会运行 Semantic Mapping Agent。
候选真实性校验和 Impact 分析仍使用确定性实现。它只分析
``EvaluationCaseContext`` 提供的 ``analysis_commit`` 快照，最终把内部契约
转换成统一 ``EvaluationObservation``。

适配器支持两个彼此独立的上下文模式：

* ``deterministic``：只运行确定性宽召回和冻结，作为稳定基线；
* ``semantic``：在同一份宽召回结果上运行受控 Semantic Mapping。

两种模式共享仓库快照、符号索引、依赖图、Requirement Agent 和 Impact Engine，
因此 A/B 只改变语义映射这一项变量。
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from langchain_core.language_models.chat_models import (
    BaseChatModel,
)

from app.engines.impact_engine import (
    analyze_impact_evidence,
)
from app.schemas.contracts import (
    Budget,
    RequirementSpec,
)
from app.schemas.evaluation_metrics import (
    CandidateScoreTrace,
    CandidateSignalTrace,
    EvaluationObservation,
)
from app.schemas.guard import GuardDigests
from app.services.requirement_spec_agent import (
    RequirementSpecAgentResult,
    analyze_requirement_spec,
)
from app.services.semantic_context import (
    build_semantic_context,
)
from app.tools.context_builder import (
    DEFAULT_POOL_LIMIT,
    build_repository_vocabulary,
    compute_repository_index_digest,
    freeze_candidate_set,
    retrieve_candidate_pool,
)
from app.tools.contract_digest import (
    compute_registry_digest,
    compute_spec_digest,
    normalize_digest_path,
)
from app.tools.evaluation_runner import (
    EvaluationCaseContext,
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


@dataclass(frozen=True)
class EvidenceImpactAdapterConfig:
    """真实适配器的资源、候选预算和上下文模式配置。"""

    max_repository_files: int = 200
    max_file_size_bytes: int = 50_000
    pool_limit: int = DEFAULT_POOL_LIMIT
    context_mode: Literal[
        "deterministic",
        "semantic",
    ] = "deterministic"

    def __post_init__(self) -> None:
        """阻止无意义或突破候选池契约的配置。"""

        if self.max_repository_files <= 0:
            raise ValueError("max_repository_files 必须大于 0。")

        if self.max_file_size_bytes <= 0:
            raise ValueError("max_file_size_bytes 必须大于 0。")

        if not 1 <= self.pool_limit <= 60:
            raise ValueError("pool_limit 必须在 1 到 60 之间。")

        if self.context_mode not in {
            "deterministic",
            "semantic",
        }:
            raise ValueError("context_mode 必须是 deterministic 或 semantic。")


class EvidenceGroundedImpactEvaluationAdapter:
    """执行可切换上下文检索与确定性 Impact Engine 的适配器。"""

    def __init__(
        self,
        *,
        model: BaseChatModel | None = None,
        config: EvidenceImpactAdapterConfig | None = None,
    ) -> None:
        self._model = model
        self._config = config or EvidenceImpactAdapterConfig()

    @staticmethod
    def _prepare_offline_requirement(
        result: RequirementSpecAgentResult,
    ) -> RequirementSpec:
        """为无人值守离线 A/B 生成同一份已批准需求契约。

        生产 API 必须等待真人确认假设；历史提交评测没有交互用户，
        如果只让语义组经过审核门禁，两组失败条件会不同。

        因此这里只在评测适配器内部把假设标记为 accepted，并按照正式
        digest 规则重新计算 spec_digest。确定性组和语义组共同使用这份
        需求规范，从而保证实验只改变 Semantic Mapping 这一项变量。
        """

        spec = result.spec

        if all(assumption.status == "accepted" for assumption in spec.assumptions):
            return spec

        approved = spec.model_copy(
            update={
                "assumptions": tuple(
                    assumption.model_copy(
                        update={
                            "status": "accepted",
                        }
                    )
                    for assumption in spec.assumptions
                ),
            }
        )

        registry_digest = compute_registry_digest(
            result.registry,
        )

        return approved.model_copy(
            update={
                "spec_digest": compute_spec_digest(
                    approved,
                    registry_digest=registry_digest,
                ),
            }
        )

    def __call__(
        self,
        context: EvaluationCaseContext,
    ) -> EvaluationObservation:
        """分析一条隔离样本并返回指标层可消费的事实。"""

        started_at = perf_counter()

        repository = scan_repository(
            context.snapshot_path,
            max_files=self._config.max_repository_files,
            max_file_size_bytes=self._config.max_file_size_bytes,
        )

        if not repository.files:
            raise ValueError("评测快照扫描结果为空。")

        # 每条样本只执行一次 AST 解析。
        # Requirement、检索、Semantic Mapping 和 Impact
        # 共享同一份符号索引。
        symbol_index = index_python_symbols(
            repository,
        )

        dependency_graph = build_python_dependency_graph(
            symbol_index,
        )

        repository_vocabulary = build_repository_vocabulary(
            repository,
            symbol_index,
        )

        index_digest = compute_repository_index_digest(
            repository,
            symbol_index,
            dependency_graph,
        )

        requirement_result = analyze_requirement_spec(
            context.sample.request_text,
            repository_vocabulary=set(
                repository_vocabulary,
            ),
            model=self._model,
        )

        # 离线评测没有真人交互，因此两组实验统一使用
        # 自动批准并重新计算 digest 的需求规范。
        requirement = self._prepare_offline_requirement(
            requirement_result,
        )

        if self._config.context_mode == "semantic":
            # 语义模式运行：
            #
            # deterministic retrieval
            #     → bounded semantic mapping
            #     → context freeze
            #
            # 同时复用上面已经建立的索引、依赖图和词表，
            # 避免重复扫描、重复解析仓库。
            semantic_context = build_semantic_context(
                requirement,
                repository,
                commit=context.snapshot_commit,
                model=self._model,
                prebuilt_symbol_index=symbol_index,
                prebuilt_dependency_graph=dependency_graph,
                prebuilt_repository_vocabulary=(repository_vocabulary),
                prebuilt_cochange_index=(context.cochange_index),
                pool_limit=self._config.pool_limit,
            )

            candidate_pool = semantic_context.candidate_pool
            candidate_set = semantic_context.candidate_set

        else:
            # 确定性模式保留原来的行为，
            # 作为语义模式的正式基线。
            candidate_pool = retrieve_candidate_pool(
                requirement,
                repository,
                symbol_index,
                dependency_graph,
                commit=context.snapshot_commit,
                index_digest=index_digest,
                cochange_index=context.cochange_index,
                pool_limit=self._config.pool_limit,
            )

            budget = Budget(
                max_files=(requirement.scope_hints.max_files),
                max_chars_per_file=(requirement.scope_hints.max_chars_per_file),
                max_symbols_per_file=10,
            )

            candidate_set = freeze_candidate_set(
                candidate_pool,
                repository,
                budget=budget,
                selected_by="baseline",
            )

        if not candidate_set.files:
            raise ValueError(f"{self._config.context_mode} 模式没有生成可冻结的 CandidateSet。")

        digests = GuardDigests(
            set_digest=candidate_set.set_digest,
            spec_digest=candidate_set.spec_digest,
            index_digest=candidate_set.index_digest,
        )

        resources: dict[str, object] = {
            "requirement_spec": requirement,
            "repository_snapshot": repository,
            "symbol_index": symbol_index,
            "dependency_graph": dependency_graph,
            "cochange_index": context.cochange_index,
        }

        # 两种模式都运行相同的确定性 Impact Engine，
        # 防止检索实验被不同分析器干扰。
        impact_result = analyze_impact_evidence(
            candidate_set,
            digests,
            resources,
        )

        pool_paths = tuple(entry.path for entry in candidate_pool.entries)

        # 直接保存检索器计算出的评分证据。
        # 不在评测层重新计算分数，避免诊断逻辑
        # 与真实排序逻辑形成两套实现。
        candidate_score_traces = tuple(
            CandidateScoreTrace(
                path=entry.path,
                pool_rank=index,
                total_score=entry.pre_score,
                signals=tuple(
                    CandidateSignalTrace(
                        name=signal.name,
                        value=signal.value,
                    )
                    for signal in entry.signals
                ),
            )
            for index, entry in enumerate(
                candidate_pool.entries,
                start=1,
            )
        )

        selected_paths = tuple(file_ref.path for file_ref in candidate_set.files)

        reported_paths = tuple(
            sorted(
                {
                    *(
                        normalize_digest_path(finding.path)
                        for finding in (impact_result.branch_result.findings)
                    ),
                    *(
                        normalize_digest_path(change_point.path)
                        for change_point in (impact_result.change_points)
                    ),
                }
            )
        )

        repository_paths = {
            normalize_digest_path(repository_file.relative_path)
            for repository_file in repository.files
        }

        invalid_reported_paths = tuple(
            path for path in reported_paths if path not in repository_paths
        )

        coverage = impact_result.branch_result.coverage

        elapsed_ms = max(
            0.0,
            (perf_counter() - started_at) * 1000,
        )

        return EvaluationObservation(
            sample_id=context.sample.sample_id,
            status=impact_result.branch_result.status,
            candidate_pool_paths=pool_paths,
            selected_paths=selected_paths,
            reported_paths=reported_paths,
            invalid_reported_paths=(invalid_reported_paths),
            analyzed_item_count=coverage.analyzed,
            assigned_item_count=coverage.assigned,
            latency_ms=round(
                elapsed_ms,
                3,
            ),
            # 当前 LangChain 调用没有统一暴露 token usage，
            # 因此先显式记录 0，不能根据文本长度猜测。
            token_count=0,
            candidate_score_traces=(candidate_score_traces),
        )


def create_evidence_impact_adapter(
    *,
    model: BaseChatModel | None = None,
    config: EvidenceImpactAdapterConfig | None = None,
) -> EvidenceGroundedImpactEvaluationAdapter:
    """创建可直接传给评测运行器的真实适配器。"""

    return EvidenceGroundedImpactEvaluationAdapter(
        model=model,
        config=config,
    )
