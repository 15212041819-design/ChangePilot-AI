"""候选文件评分轨迹契约测试。"""

import pytest

from app.schemas.evaluation_metrics import (
    CandidateScoreTrace,
    CandidateSignalTrace,
    EvaluationObservation,
)


def _trace(
    path: str = "app/service.py",
    *,
    rank: int = 1,
) -> CandidateScoreTrace:
    """创建可复算的候选评分轨迹。"""

    return CandidateScoreTrace(
        path=path,
        pool_rank=rank,
        total_score=3.5,
        signals=(
            CandidateSignalTrace(
                name="keyword",
                value=1.5,
            ),
            CandidateSignalTrace(
                name="symbol",
                value=2.0,
            ),
        ),
    )


def test_candidate_score_trace_is_recomputable() -> None:
    """合法轨迹应保留稳定排名、总分和分项信号。"""

    trace = _trace()

    assert trace.pool_rank == 1
    assert trace.total_score == 3.5
    assert tuple(signal.name for signal in trace.signals) == (
        "keyword",
        "symbol",
    )


def test_candidate_score_trace_rejects_wrong_total() -> None:
    """总分与信号求和不一致时必须拒绝。"""

    with pytest.raises(
        ValueError,
        match="total_score",
    ):
        CandidateScoreTrace(
            path="app/service.py",
            pool_rank=1,
            total_score=99.0,
            signals=(
                CandidateSignalTrace(
                    name="keyword",
                    value=1.5,
                ),
            ),
        )


def test_observation_requires_trace_paths_to_match_pool() -> None:
    """轨迹路径必须逐项对应真实候选池顺序。"""

    with pytest.raises(
        ValueError,
        match="逐项对应",
    ):
        EvaluationObservation(
            sample_id="sample-1",
            status="success",
            candidate_pool_paths=("app/service.py",),
            selected_paths=("app/service.py",),
            reported_paths=(),
            invalid_reported_paths=(),
            analyzed_item_count=1,
            assigned_item_count=1,
            latency_ms=1.0,
            candidate_score_traces=(_trace("app/other.py"),),
        )


def test_observation_rejects_non_continuous_trace_ranks() -> None:
    """候选评分排名必须从 1 连续递增。"""

    with pytest.raises(
        ValueError,
        match="连续递增",
    ):
        EvaluationObservation(
            sample_id="sample-1",
            status="success",
            candidate_pool_paths=("app/service.py",),
            selected_paths=("app/service.py",),
            reported_paths=(),
            invalid_reported_paths=(),
            analyzed_item_count=1,
            assigned_item_count=1,
            latency_ms=1.0,
            candidate_score_traces=(_trace(rank=2),),
        )


def test_observation_allows_legacy_report_without_traces() -> None:
    """旧评测报告没有评分轨迹时仍应保持兼容。"""

    observation = EvaluationObservation(
        sample_id="sample-1",
        status="success",
        candidate_pool_paths=("app/service.py",),
        selected_paths=("app/service.py",),
        reported_paths=(),
        invalid_reported_paths=(),
        analyzed_item_count=1,
        assigned_item_count=1,
        latency_ms=1.0,
    )

    assert observation.candidate_score_traces == ()
