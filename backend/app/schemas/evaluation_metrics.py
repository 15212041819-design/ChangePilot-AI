"""离线评测运行记录与指标结果契约。

指标层只接收结构化事实，不负责执行检索器或 Agent。这样同一套指标
可以公平比较旧检索、新检索、LLM Agent 和确定性引擎四种组合。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EvaluationRunStatus = Literal["success", "degraded", "failed"]


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
    candidate_pool_paths: tuple[str, ...]
    selected_paths: tuple[str, ...]
    reported_paths: tuple[str, ...]
    invalid_reported_paths: tuple[str, ...]
    analyzed_item_count: int
    assigned_item_count: int
    latency_ms: float
    token_count: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        """阻止重复路径、伪造覆盖率和矛盾状态进入评测。"""

        if not self.sample_id.strip():
            raise ValueError("sample_id 不能为空。")

        for field_name, paths in (
            ("candidate_pool_paths", self.candidate_pool_paths),
            ("selected_paths", self.selected_paths),
            ("reported_paths", self.reported_paths),
            ("invalid_reported_paths", self.invalid_reported_paths),
        ):
            _validate_unique_paths(field_name, paths)

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


@dataclass(frozen=True)
class SampleEvaluationMetrics:
    """单条样本的诊断指标。"""

    sample_id: str
    status: EvaluationRunStatus
    pool_recall_at_k: float
    file_recall_at_k: float
    hallucination_rate: float
    coverage_rate: float
    latency_ms: float
    token_count: int
    missing_truth_in_pool: tuple[str, ...]
    missing_truth_in_selection: tuple[str, ...]
    invalid_reported_paths: tuple[str, ...]
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
    pool_k: int
    file_k: int
    pool_recall_at_k: float
    file_recall_at_k: float
    hallucination_rate: float
    failure_rate: float
    coverage_rate: float
    average_latency_ms: float
    total_token_count: int
    samples: tuple[SampleEvaluationMetrics, ...]

    def __post_init__(self) -> None:
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
