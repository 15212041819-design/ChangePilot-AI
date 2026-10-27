"""离线评测运行记录与指标结果契约。

指标层只接收结构化事实，不负责执行检索器或 Agent。这样同一套指标
可以公平比较旧检索、新检索、LLM Agent 和确定性引擎四种组合。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum
from typing import Literal

EvaluationRunStatus = Literal[
    "success",
    "degraded",
    "failed",
]

CandidateSignalName = Literal[
    "keyword",
    "symbol",
    "dep_closure",
    "cochange",
    "testmap",
]

CandidateRetrievalOutcome = Literal[
    "selected",
    "below_cutoff",
    "no_signal",
    "scope_excluded",
    "document",
]

CandidateFileKind = Literal[
    "source",
    "test",
    "config",
    "doc",
]


@dataclass(frozen=True)
class CandidateSignalTrace:
    """一个候选文件的单项确定性评分证据。"""

    name: CandidateSignalName
    value: float

    def __post_init__(
        self,
    ) -> None:
        """信号分数必须非负并统一到三位小数。"""

        if self.value < 0:
            raise ValueError("候选信号分数不能为负数。")

        if round(self.value, 3) != self.value:
            raise ValueError("候选信号分数最多保留三位小数。")


@dataclass(frozen=True)
class CandidateScoreTrace:
    """一个 CandidatePool 条目的可复算评分轨迹。"""

    path: str
    pool_rank: int
    total_score: float

    signals: tuple[
        CandidateSignalTrace,
        ...,
    ]

    def __post_init__(
        self,
    ) -> None:
        """确保排名、信号顺序和总分没有被篡改。"""

        if not self.path.strip():
            raise ValueError("候选评分路径不能为空。")

        if self.pool_rank <= 0:
            raise ValueError("pool_rank 必须大于 0。")

        if not self.signals:
            raise ValueError("候选评分轨迹必须至少包含一个信号。")

        signal_names = tuple(signal.name for signal in self.signals)

        if signal_names != tuple(sorted(signal_names)):
            raise ValueError("候选评分信号必须按名称稳定排序。")

        if len(signal_names) != len(set(signal_names)):
            raise ValueError("候选评分信号名称不能重复。")

        expected_total = round(
            fsum(sorted(signal.value for signal in self.signals)),
            3,
        )

        if self.total_score != expected_total:
            raise ValueError("total_score 必须等于所有候选信号的稳定求和结果。")


@dataclass(frozen=True)
class RepositoryFileRetrievalTrace:
    """仓库文件在基础确定性检索中的完整诊断轨迹。"""

    path: str
    kind: CandidateFileKind
    outcome: CandidateRetrievalOutcome

    # 只有 selected 和 below_cutoff 才拥有排名。
    full_rank: int | None = None

    # 未参与评分的文件保持为 0。
    total_score: float = 0.0

    signals: tuple[
        CandidateSignalTrace,
        ...,
    ] = ()

    def __post_init__(
        self,
    ) -> None:
        """验证排名、信号和排除原因之间没有矛盾。"""

        if not self.path.strip():
            raise ValueError("仓库文件检索轨迹路径不能为空。")

        names = tuple(signal.name for signal in self.signals)

        if names != tuple(sorted(names)):
            raise ValueError("仓库文件检索信号必须按名称稳定排序。")

        if len(names) != len(set(names)):
            raise ValueError("仓库文件检索信号名称不能重复。")

        ranked = self.outcome in {
            "selected",
            "below_cutoff",
        }

        if ranked:
            if self.full_rank is None or self.full_rank <= 0 or not self.signals:
                raise ValueError("已评分文件必须包含正数 full_rank 和至少一个信号。")

            expected_total = round(
                fsum(sorted(signal.value for signal in self.signals)),
                3,
            )

            if self.total_score != expected_total:
                raise ValueError("文件轨迹 total_score 必须等于信号稳定求和结果。")

        elif self.full_rank is not None or self.signals or self.total_score != 0.0:
            raise ValueError("未评分文件不能携带排名、分数或信号。")


def _validate_unique_paths(
    field_name: str,
    paths: tuple[str, ...],
) -> None:
    """路径列表必须保持首次出现顺序且不能重复。"""

    if any(not path.strip() for path in paths):
        raise ValueError(f"{field_name} 不能包含空路径。")

    if len(paths) != len(set(paths)):
        raise ValueError(f"{field_name} 不能包含重复路径。")


@dataclass(frozen=True)
class EvaluationObservation:
    """一条样本经过某个实验组合后产生的可度量事实。"""

    sample_id: str
    status: EvaluationRunStatus

    candidate_pool_paths: tuple[
        str,
        ...,
    ]

    selected_paths: tuple[
        str,
        ...,
    ]

    reported_paths: tuple[
        str,
        ...,
    ]

    invalid_reported_paths: tuple[
        str,
        ...,
    ]

    analyzed_item_count: int
    assigned_item_count: int
    latency_ms: float
    token_count: int = 0
    error: str | None = None

    # 进入最终 CandidatePool 的文件评分轨迹。
    # 路径和顺序必须与 candidate_pool_paths 完全一致。
    candidate_score_traces: tuple[
        CandidateScoreTrace,
        ...,
    ] = ()

    # 基础确定性检索必须解释扫描快照中的每个文件。
    #
    # semantic 模式可能在基础候选池之后继续受控扩池，
    # 因此这里明确保存 baseline 阶段的轨迹，
    # 避免把 LLM 扩池结果误认为确定性检索结果。
    baseline_retrieval_traces: tuple[
        RepositoryFileRetrievalTrace,
        ...,
    ] = ()

    def __post_init__(
        self,
    ) -> None:
        """阻止重复路径、伪造覆盖率和矛盾状态进入评测。"""

        if not self.sample_id.strip():
            raise ValueError("sample_id 不能为空。")

        for field_name, paths in (
            (
                "candidate_pool_paths",
                self.candidate_pool_paths,
            ),
            (
                "selected_paths",
                self.selected_paths,
            ),
            (
                "reported_paths",
                self.reported_paths,
            ),
            (
                "invalid_reported_paths",
                self.invalid_reported_paths,
            ),
        ):
            _validate_unique_paths(
                field_name,
                paths,
            )

        if not set(self.selected_paths).issubset(self.candidate_pool_paths):
            raise ValueError("selected_paths 必须来自 candidate_pool_paths。")

        if not set(self.invalid_reported_paths).issubset(self.reported_paths):
            raise ValueError("invalid_reported_paths 必须来自 reported_paths。")

        if self.analyzed_item_count < 0 or self.assigned_item_count < 0:
            raise ValueError("覆盖数量不能为负数。")

        if self.analyzed_item_count > self.assigned_item_count:
            raise ValueError("analyzed_item_count 不能大于 assigned_item_count。")

        if self.latency_ms < 0:
            raise ValueError("latency_ms 不能为负数。")

        if self.token_count < 0:
            raise ValueError("token_count 不能为负数。")

        if self.status == "failed" and not (self.error or "").strip():
            raise ValueError("failed 运行必须提供 error。")

        if self.status != "failed" and self.error is not None:
            raise ValueError("只有 failed 运行可以提供 error。")

        if self.candidate_score_traces:
            trace_paths = tuple(trace.path for trace in (self.candidate_score_traces))

            trace_ranks = tuple(trace.pool_rank for trace in (self.candidate_score_traces))

            if trace_paths != self.candidate_pool_paths:
                raise ValueError("candidate_score_traces 必须逐项对应 candidate_pool_paths。")

            expected_ranks = tuple(
                range(
                    1,
                    len(trace_ranks) + 1,
                )
            )

            if trace_ranks != expected_ranks:
                raise ValueError("候选评分轨迹的 pool_rank 必须从 1 连续递增。")

        if self.baseline_retrieval_traces:
            baseline_paths = tuple(trace.path for trace in (self.baseline_retrieval_traces))

            # 全量轨迹不按分数排序，而是按路径排序。
            # 这样仓库文件输入顺序不会改变评测 JSON。
            if baseline_paths != tuple(sorted(baseline_paths)):
                raise ValueError("baseline_retrieval_traces 必须按路径稳定排序。")

            if len(baseline_paths) != len(set(baseline_paths)):
                raise ValueError("baseline_retrieval_traces 不能包含重复路径。")

            ranked_traces = tuple(
                trace for trace in (self.baseline_retrieval_traces) if trace.full_rank is not None
            )

            ranked_values = tuple(sorted(trace.full_rank for trace in ranked_traces))

            expected_values = tuple(
                range(
                    1,
                    len(ranked_values) + 1,
                )
            )

            if ranked_values != expected_values:
                raise ValueError("基础检索轨迹的 full_rank 必须从 1 连续递增。")


@dataclass(frozen=True)
class SampleEvaluationMetrics:
    """单条样本的诊断指标。"""

    sample_id: str
    status: EvaluationRunStatus

    existing_file_recall_evaluable: bool
    existing_source_file_count: int
    new_source_file_count: int

    pool_recall_at_k: float
    file_recall_at_k: float

    hallucination_rate: float
    hallucination_evaluable: bool
    reported_path_count: int

    coverage_rate: float
    latency_ms: float
    token_count: int

    missing_truth_in_pool: tuple[
        str,
        ...,
    ]

    missing_truth_in_selection: tuple[
        str,
        ...,
    ]

    invalid_reported_paths: tuple[
        str,
        ...,
    ]

    error: str | None


@dataclass(frozen=True)
class EvaluationMetricsReport:
    """一个实验组合在完整数据集上的聚合指标。"""

    experiment_name: str

    sample_count: int
    observed_sample_count: int

    success_count: int
    degraded_count: int
    failed_count: int

    existing_file_sample_count: int
    new_file_sample_count: int

    existing_source_file_count: int
    new_source_file_count: int

    pool_k: int
    file_k: int

    pool_recall_at_k: float
    file_recall_at_k: float

    hallucination_rate: float
    hallucination_evaluable: bool
    reported_path_count: int

    failure_rate: float
    coverage_rate: float
    average_latency_ms: float
    total_token_count: int

    samples: tuple[
        SampleEvaluationMetrics,
        ...,
    ]

    def __post_init__(
        self,
    ) -> None:
        """确保聚合计数覆盖数据集里的每一条样本。"""

        if not self.experiment_name.strip():
            raise ValueError("experiment_name 不能为空。")

        if self.sample_count <= 0:
            raise ValueError("sample_count 必须大于 0。")

        if self.observed_sample_count > self.sample_count:
            raise ValueError("observed_sample_count 不能大于 sample_count。")

        status_count = self.success_count + self.degraded_count + self.failed_count

        if status_count != self.sample_count:
            raise ValueError("状态计数必须覆盖全部样本。")

        if len(self.samples) != self.sample_count:
            raise ValueError("单样本指标数量必须等于 sample_count。")
