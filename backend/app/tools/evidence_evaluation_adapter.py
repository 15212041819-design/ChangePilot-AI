"""“新检索 + 确定性 Impact 引擎”的真实评测适配器。

该适配器运行 Requirement Agent，但代码检索、候选冻结、Impact 分析和
事实校验全部使用确定性实现。它只分析 ``EvaluationCaseContext`` 提供的
``analysis_commit`` 快照，最终把内部契约转换成统一 ``EvaluationObservation``。

这里刻意不运行 Semantic Mapping Agent。这样本实验格只衡量确定性新检索
与确定性 Impact Engine，后续可以单独加入语义映射，避免一次改变多个变量。
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from langchain_core.language_models.chat_models import (
    BaseChatModel,
)

from app.engines.impact_engine import (
    analyze_impact_evidence,
)
from app.schemas.contracts import Budget
from app.schemas.evaluation_metrics import (
    CandidateScoreTrace,
    CandidateSignalTrace,
    EvaluationObservation,
)
from app.schemas.guard import GuardDigests
from app.services.requirement_spec_agent import (
    analyze_requirement_spec,
)
from app.tools.context_builder import (
    DEFAULT_POOL_LIMIT,
    build_repository_vocabulary,
    compute_repository_index_digest,
    freeze_candidate_set,
    retrieve_candidate_pool,
)
from app.tools.contract_digest import (
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
    """真实适配器的资源与候选预算配置。"""

    max_repository_files: int = 200
    max_file_size_bytes: int = 50_000
    pool_limit: int = DEFAULT_POOL_LIMIT

    def __post_init__(self) -> None:
        """阻止无意义或突破候选池契约的配置。"""

        if self.max_repository_files <= 0:
            raise ValueError("max_repository_files 必须大于 0。")

        if self.max_file_size_bytes <= 0:
            raise ValueError("max_file_size_bytes 必须大于 0。")

        if not 1 <= self.pool_limit <= 60:
            raise ValueError("pool_limit 必须在 1 到 60 之间。")


class EvidenceGroundedImpactEvaluationAdapter:
    """执行新确定性检索与确定性 Impact Engine 的适配器。"""

    def __init__(
        self,
        *,
        model: BaseChatModel | None = None,
        config: EvidenceImpactAdapterConfig | None = None,
    ) -> None:
        self._model = model
        self._config = config or EvidenceImpactAdapterConfig()

    def __call__(
        self,
        context: EvaluationCaseContext,
    ) -> EvaluationObservation:
        """分析一条隔离样本并返回指标层可消费的事实。"""

        started_at = perf_counter()

        repository = scan_repository(
            context.snapshot_path,
            max_files=(self._config.max_repository_files),
            max_file_size_bytes=(self._config.max_file_size_bytes),
        )

        if not repository.files:
            raise ValueError("评测快照扫描结果为空。")

        # AST 只解析一次。
        # Requirement、检索和 Impact
        # 共享同一份符号索引。
        symbol_index = index_python_symbols(repository)

        dependency_graph = build_python_dependency_graph(symbol_index)

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
            repository_vocabulary=set(repository_vocabulary),
            model=self._model,
        )

        requirement = requirement_result.spec

        candidate_pool = retrieve_candidate_pool(
            requirement,
            repository,
            symbol_index,
            dependency_graph,
            commit=context.snapshot_commit,
            index_digest=index_digest,
            cochange_index=(context.cochange_index),
            pool_limit=(self._config.pool_limit),
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
            raise ValueError("确定性检索没有生成可冻结的 CandidateSet。")

        digests = GuardDigests(
            set_digest=(candidate_set.set_digest),
            spec_digest=(candidate_set.spec_digest),
            index_digest=(candidate_set.index_digest),
        )

        resources: dict[str, object] = {
            "requirement_spec": requirement,
            "repository_snapshot": repository,
            "symbol_index": symbol_index,
            "dependency_graph": dependency_graph,
            "cochange_index": (context.cochange_index),
        }

        impact_result = analyze_impact_evidence(
            candidate_set,
            digests,
            resources,
        )

        pool_paths = tuple(entry.path for entry in candidate_pool.entries)

        # 把确定性检索器已经计算出的分项信号
        # 原样写入评测 observation。
        # 这里不重新计算分数，避免诊断数据
        # 与真实排序逻辑出现双重实现。
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
            sample_id=(context.sample.sample_id),
            status=(impact_result.branch_result.status),
            candidate_pool_paths=(pool_paths),
            selected_paths=(selected_paths),
            reported_paths=(reported_paths),
            invalid_reported_paths=(invalid_reported_paths),
            analyzed_item_count=(coverage.analyzed),
            assigned_item_count=(coverage.assigned),
            latency_ms=round(
                elapsed_ms,
                3,
            ),
            # 当前 LangChain 调用没有统一暴露
            # token usage，因此先显式记 0。
            # 后续接入模型观测层后再填充，
            # 不能根据文本长度猜测。
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
