"""计算离线评测指标，并为失败样本保留明确诊断。

召回率只使用分析快照中已经存在的源码文件作为 truth。目标提交新增的
源码文件无法从 ``commit^`` 候选池检索，因此只单独计数，不进入既有文件
召回率分母。测试文件会在后续测试策略专用指标中评估。
所有聚合指标均覆盖完整数据集；缺失 observation 会被记为 failed。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.schemas.evaluation import (
    EvaluationDataset,
    EvaluationSample,
)
from app.schemas.evaluation_metrics import (
    EvaluationMetricsReport,
    EvaluationObservation,
    SampleEvaluationMetrics,
)

DEFAULT_POOL_K = 50
DEFAULT_FILE_K = 12


class EvaluationMetricsError(ValueError):
    """评测输入无法形成可信指标。"""


def _safe_ratio(
    numerator: int | float,
    denominator: int | float,
) -> float:
    """计算稳定的六位小数比例；零分母返回 0。"""

    if denominator == 0:
        return 0.0

    return round(
        numerator / denominator,
        6,
    )


def _recall(
    truth_paths: tuple[str, ...],
    predicted_paths: tuple[str, ...],
) -> float:
    """计算一条样本的文件召回率。"""

    truth = set(truth_paths)
    predicted = set(predicted_paths)

    return _safe_ratio(
        len(truth & predicted),
        len(truth),
    )


def _missing_paths(
    truth_paths: tuple[str, ...],
    predicted_paths: tuple[str, ...],
) -> tuple[str, ...]:
    """返回稳定排序的漏召回文件。"""

    return tuple(sorted(set(truth_paths) - set(predicted_paths)))


def _index_observations(
    observations: Iterable[EvaluationObservation],
    known_sample_ids: set[str],
) -> dict[str, EvaluationObservation]:
    """按 sample_id 建立索引，并拒绝重复或未知结果。"""

    indexed: dict[
        str,
        EvaluationObservation,
    ] = {}

    for observation in observations:
        if observation.sample_id not in known_sample_ids:
            raise EvaluationMetricsError(
                f"observation 引用了数据集外的样本：{observation.sample_id}"
            )

        if observation.sample_id in indexed:
            raise EvaluationMetricsError(f"同一样本出现了重复 observation：{observation.sample_id}")

        indexed[observation.sample_id] = observation

    return indexed


def _missing_observation_metrics(
    sample: EvaluationSample,
) -> SampleEvaluationMetrics:
    """把没有产出结果的样本显式记为失败。"""

    return SampleEvaluationMetrics(
        sample_id=sample.sample_id,
        status="failed",
        existing_file_recall_evaluable=(bool(sample.existing_source_files)),
        existing_source_file_count=len(sample.existing_source_files),
        new_source_file_count=len(sample.new_source_files),
        pool_recall_at_k=0.0,
        file_recall_at_k=0.0,
        hallucination_rate=0.0,
        hallucination_evaluable=False,
        reported_path_count=0,
        coverage_rate=0.0,
        latency_ms=0.0,
        token_count=0,
        missing_truth_in_pool=(sample.existing_source_files),
        missing_truth_in_selection=(sample.existing_source_files),
        invalid_reported_paths=(),
        error="missing_observation",
    )


def _sample_metrics(
    sample: EvaluationSample,
    observation: EvaluationObservation,
    *,
    pool_k: int,
    file_k: int,
) -> SampleEvaluationMetrics:
    """计算一条已观察样本的指标。"""

    pool_paths = observation.candidate_pool_paths[:pool_k]

    selected_paths = observation.selected_paths[:file_k]

    reported_count = len(observation.reported_paths)

    return SampleEvaluationMetrics(
        sample_id=sample.sample_id,
        status=observation.status,
        existing_file_recall_evaluable=(bool(sample.existing_source_files)),
        existing_source_file_count=len(sample.existing_source_files),
        new_source_file_count=len(sample.new_source_files),
        pool_recall_at_k=_recall(
            sample.existing_source_files,
            pool_paths,
        ),
        file_recall_at_k=_recall(
            sample.existing_source_files,
            selected_paths,
        ),
        hallucination_rate=_safe_ratio(
            len(observation.invalid_reported_paths),
            reported_count,
        ),
        hallucination_evaluable=(reported_count > 0),
        reported_path_count=(reported_count),
        coverage_rate=_safe_ratio(
            observation.analyzed_item_count,
            observation.assigned_item_count,
        ),
        latency_ms=round(
            observation.latency_ms,
            3,
        ),
        token_count=(observation.token_count),
        missing_truth_in_pool=(
            _missing_paths(
                sample.existing_source_files,
                pool_paths,
            )
        ),
        missing_truth_in_selection=(
            _missing_paths(
                sample.existing_source_files,
                selected_paths,
            )
        ),
        invalid_reported_paths=(observation.invalid_reported_paths),
        error=observation.error,
    )


def calculate_evaluation_metrics(
    dataset: EvaluationDataset,
    observations: Iterable[EvaluationObservation],
    *,
    experiment_name: str,
    pool_k: int = DEFAULT_POOL_K,
    file_k: int = DEFAULT_FILE_K,
) -> EvaluationMetricsReport:
    """计算一个实验组合在完整数据集上的聚合指标。

    pool/file recall 使用样本级宏平均，避免大提交支配结果。
    只有包含既有源码文件的样本才进入召回率平均值。
    hallucination 和 coverage 使用全局分子/分母。
    """

    if not experiment_name.strip():
        raise EvaluationMetricsError("experiment_name 不能为空。")

    if pool_k <= 0:
        raise EvaluationMetricsError("pool_k 必须大于 0。")

    if file_k <= 0:
        raise EvaluationMetricsError("file_k 必须大于 0。")

    if not dataset.samples:
        raise EvaluationMetricsError("评测数据集不能为空。")

    known_sample_ids = {sample.sample_id for sample in dataset.samples}

    indexed = _index_observations(
        observations,
        known_sample_ids,
    )

    sample_metrics: list[SampleEvaluationMetrics] = []

    total_invalid_paths = 0
    total_reported_paths = 0
    total_analyzed_items = 0
    total_assigned_items = 0

    for sample in dataset.samples:
        observation = indexed.get(sample.sample_id)

        if observation is None:
            sample_metrics.append(_missing_observation_metrics(sample))
            continue

        metrics = _sample_metrics(
            sample,
            observation,
            pool_k=pool_k,
            file_k=file_k,
        )

        sample_metrics.append(metrics)

        total_invalid_paths += len(observation.invalid_reported_paths)

        total_reported_paths += len(observation.reported_paths)

        total_analyzed_items += observation.analyzed_item_count

        total_assigned_items += observation.assigned_item_count

    sample_count = len(sample_metrics)

    success_count = sum(item.status == "success" for item in sample_metrics)

    degraded_count = sum(item.status == "degraded" for item in sample_metrics)

    failed_count = sum(item.status == "failed" for item in sample_metrics)

    existing_file_metrics = tuple(
        item for item in sample_metrics if item.existing_file_recall_evaluable
    )

    existing_file_sample_count = len(existing_file_metrics)

    new_file_sample_count = sum(item.new_source_file_count > 0 for item in sample_metrics)

    existing_source_file_count = sum(item.existing_source_file_count for item in sample_metrics)

    new_source_file_count = sum(item.new_source_file_count for item in sample_metrics)

    return EvaluationMetricsReport(
        experiment_name=(experiment_name.strip()),
        sample_count=sample_count,
        observed_sample_count=len(indexed),
        success_count=success_count,
        degraded_count=degraded_count,
        failed_count=failed_count,
        existing_file_sample_count=(existing_file_sample_count),
        new_file_sample_count=(new_file_sample_count),
        existing_source_file_count=(existing_source_file_count),
        new_source_file_count=(new_source_file_count),
        pool_k=pool_k,
        file_k=file_k,
        pool_recall_at_k=round(
            sum(item.pool_recall_at_k for item in existing_file_metrics)
            / max(
                existing_file_sample_count,
                1,
            ),
            6,
        ),
        file_recall_at_k=round(
            sum(item.file_recall_at_k for item in existing_file_metrics)
            / max(
                existing_file_sample_count,
                1,
            ),
            6,
        ),
        hallucination_rate=(
            _safe_ratio(
                total_invalid_paths,
                total_reported_paths,
            )
        ),
        hallucination_evaluable=(total_reported_paths > 0),
        reported_path_count=(total_reported_paths),
        failure_rate=_safe_ratio(
            failed_count,
            sample_count,
        ),
        coverage_rate=_safe_ratio(
            total_analyzed_items,
            total_assigned_items,
        ),
        average_latency_ms=round(
            sum(item.latency_ms for item in sample_metrics) / sample_count,
            3,
        ),
        total_token_count=sum(item.token_count for item in sample_metrics),
        samples=tuple(sample_metrics),
    )
