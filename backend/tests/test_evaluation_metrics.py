"""离线评测指标计算器的单元测试。"""

import pytest

from app.schemas.evaluation import (
    EvaluationDataset,
    EvaluationSample,
)
from app.schemas.evaluation_metrics import (
    EvaluationObservation,
)
from app.tools.evaluation_metrics import (
    EvaluationMetricsError,
    calculate_evaluation_metrics,
)


def _sample(
    sample_id: str,
    answer_character: str,
    source_files: tuple[str, ...],
) -> EvaluationSample:
    """创建用于指标测试的稳定样本。"""

    test_files = (f"tests/test_{sample_id}.py",)

    return EvaluationSample(
        sample_id=sample_id,
        repository_relative_path="",
        request_text=(f"implement meaningful behavior for {sample_id}"),
        analysis_commit="a" * 40,
        answer_commit=answer_character * 40,
        changed_files=tuple(sorted((*source_files, *test_files))),
        source_files=tuple(sorted(source_files)),
        test_files=test_files,
    )


def _dataset() -> EvaluationDataset:
    """创建包含两个样本的小型数据集。"""

    samples = (
        _sample(
            "sample-1",
            "b",
            ("app/a.py", "app/b.py"),
        ),
        _sample(
            "sample-2",
            "c",
            ("app/c.py",),
        ),
    )

    return EvaluationDataset(
        repository_relative_path="",
        requested_ref="HEAD",
        resolved_ref="d" * 40,
        scanned_commit_count=2,
        samples=samples,
        rejected_root_count=0,
        rejected_merge_count=0,
        rejected_short_message_count=0,
        rejected_file_count_count=0,
        rejected_pure_test_count=0,
    )


def test_metrics_calculate_recall_hallucination_failure_and_coverage() -> None:
    """核心指标必须使用冻结的分子和分母定义。"""

    observations = (
        EvaluationObservation(
            sample_id="sample-1",
            status="success",
            candidate_pool_paths=(
                "app/a.py",
                "app/b.py",
                "app/extra.py",
            ),
            selected_paths=(
                "app/a.py",
                "app/extra.py",
            ),
            reported_paths=(
                "app/a.py",
                "missing.py",
            ),
            invalid_reported_paths=("missing.py",),
            analyzed_item_count=4,
            assigned_item_count=4,
            latency_ms=100.0,
            token_count=30,
        ),
        EvaluationObservation(
            sample_id="sample-2",
            status="degraded",
            candidate_pool_paths=("app/other.py",),
            selected_paths=("app/other.py",),
            reported_paths=("app/other.py",),
            invalid_reported_paths=(),
            analyzed_item_count=1,
            assigned_item_count=2,
            latency_ms=300.0,
            token_count=10,
        ),
    )

    report = calculate_evaluation_metrics(
        _dataset(),
        observations,
        experiment_name=("new-retrieval-new-engine"),
    )

    assert report.pool_recall_at_k == 0.5
    assert report.file_recall_at_k == 0.25
    assert report.hallucination_rate == pytest.approx(
        1 / 3,
        abs=1e-6,
    )
    assert report.failure_rate == 0.0
    assert report.coverage_rate == pytest.approx(
        5 / 6,
        abs=1e-6,
    )
    assert report.average_latency_ms == 200.0
    assert report.total_token_count == 40
    assert report.success_count == 1
    assert report.degraded_count == 1
    assert report.failed_count == 0

    assert report.samples[0].missing_truth_in_selection == ("app/b.py",)
    assert report.samples[1].missing_truth_in_pool == ("app/c.py",)


def test_missing_observation_is_counted_as_failure() -> None:
    """流水线漏掉样本时不能静默改善成功率。"""

    observation = EvaluationObservation(
        sample_id="sample-1",
        status="success",
        candidate_pool_paths=(
            "app/a.py",
            "app/b.py",
        ),
        selected_paths=(
            "app/a.py",
            "app/b.py",
        ),
        reported_paths=(
            "app/a.py",
            "app/b.py",
        ),
        invalid_reported_paths=(),
        analyzed_item_count=2,
        assigned_item_count=2,
        latency_ms=50.0,
    )

    report = calculate_evaluation_metrics(
        _dataset(),
        (observation,),
        experiment_name="partial-run",
    )

    assert report.observed_sample_count == 1
    assert report.failed_count == 1
    assert report.failure_rate == 0.5
    assert report.pool_recall_at_k == 0.5
    assert report.samples[1].error == "missing_observation"


def test_recall_respects_pool_and_file_cutoffs() -> None:
    """排在 K 之后的正确文件不能计入 recall@K。"""

    dataset = EvaluationDataset(
        repository_relative_path="",
        requested_ref="HEAD",
        resolved_ref="d" * 40,
        scanned_commit_count=1,
        samples=(
            _sample(
                "sample-1",
                "b",
                ("app/a.py",),
            ),
        ),
        rejected_root_count=0,
        rejected_merge_count=0,
        rejected_short_message_count=0,
        rejected_file_count_count=0,
        rejected_pure_test_count=0,
    )

    observation = EvaluationObservation(
        sample_id="sample-1",
        status="success",
        candidate_pool_paths=(
            "app/other.py",
            "app/a.py",
        ),
        selected_paths=(
            "app/other.py",
            "app/a.py",
        ),
        reported_paths=(),
        invalid_reported_paths=(),
        analyzed_item_count=1,
        assigned_item_count=1,
        latency_ms=1.0,
    )

    report = calculate_evaluation_metrics(
        dataset,
        (observation,),
        experiment_name="cutoff-test",
        pool_k=1,
        file_k=1,
    )

    assert report.pool_recall_at_k == 0.0
    assert report.file_recall_at_k == 0.0


def test_metrics_reject_duplicate_observations() -> None:
    """同一样本重复上报会扭曲指标，必须拒绝。"""

    observation = EvaluationObservation(
        sample_id="sample-1",
        status="success",
        candidate_pool_paths=(),
        selected_paths=(),
        reported_paths=(),
        invalid_reported_paths=(),
        analyzed_item_count=0,
        assigned_item_count=0,
        latency_ms=0.0,
    )

    with pytest.raises(
        EvaluationMetricsError,
        match="重复",
    ):
        calculate_evaluation_metrics(
            _dataset(),
            (observation, observation),
            experiment_name="duplicate-test",
        )


def test_metrics_reject_unknown_sample() -> None:
    """数据集外的运行结果不能混入当前实验。"""

    observation = EvaluationObservation(
        sample_id="unknown",
        status="success",
        candidate_pool_paths=(),
        selected_paths=(),
        reported_paths=(),
        invalid_reported_paths=(),
        analyzed_item_count=0,
        assigned_item_count=0,
        latency_ms=0.0,
    )

    with pytest.raises(
        EvaluationMetricsError,
        match="数据集外",
    ):
        calculate_evaluation_metrics(
            _dataset(),
            (observation,),
            experiment_name="unknown-test",
        )


def test_observation_rejects_selection_outside_pool() -> None:
    """冻结后的选择结果必须来自候选池。"""

    with pytest.raises(
        ValueError,
        match="必须来自",
    ):
        EvaluationObservation(
            sample_id="sample-1",
            status="success",
            candidate_pool_paths=("app/a.py",),
            selected_paths=("app/b.py",),
            reported_paths=(),
            invalid_reported_paths=(),
            analyzed_item_count=0,
            assigned_item_count=0,
            latency_ms=0.0,
        )


def test_failed_observation_requires_error() -> None:
    """失败状态必须解释失败原因。"""

    with pytest.raises(
        ValueError,
        match="必须提供 error",
    ):
        EvaluationObservation(
            sample_id="sample-1",
            status="failed",
            candidate_pool_paths=(),
            selected_paths=(),
            reported_paths=(),
            invalid_reported_paths=(),
            analyzed_item_count=0,
            assigned_item_count=0,
            latency_ms=0.0,
        )


def test_observation_rejects_invalid_coverage_counts() -> None:
    """已分析数量不能超过分配数量。"""

    with pytest.raises(
        ValueError,
        match="不能大于",
    ):
        EvaluationObservation(
            sample_id="sample-1",
            status="success",
            candidate_pool_paths=(),
            selected_paths=(),
            reported_paths=(),
            invalid_reported_paths=(),
            analyzed_item_count=2,
            assigned_item_count=1,
            latency_ms=0.0,
        )
