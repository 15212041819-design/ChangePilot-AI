"""候选排序诊断结果的稳定数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.evaluation_metrics import (
    CandidateRerankLane,
    CandidateScoreTrace,
    CandidateSignalTrace,
)

TruthSelectionOutcome = Literal[
    "selected",
    "not_in_pool",
    "below_pool_cutoff",
    "rerank_rejected",
    "below_selection_cutoff",
    "selection_untraced",
]


def _ratio(value: float, *, field_name: str) -> None:
    """校验比例落在闭区间 0 到 1。"""

    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} 必须位于 0 到 1 之间。")


@dataclass(frozen=True)
class TruthPathRanking:
    """一个真实受影响文件在候选池和最终选择中的位置。"""

    path: str
    pool_rank: int | None
    within_pool_cutoff: bool
    selection_rank: int | None
    total_score: float | None
    signals: tuple[CandidateSignalTrace, ...]
    outcome: TruthSelectionOutcome
    rerank_rank: int | None = None
    rerank_lane: CandidateRerankLane | None = None
    rerank_selected: bool | None = None
    rank_delta: int | None = None

    def __post_init__(self) -> None:
        """拒绝互相矛盾的排名。"""

        if not self.path.strip():
            raise ValueError("truth path 不能为空。")

        if self.pool_rank is not None and self.pool_rank <= 0:
            raise ValueError("pool_rank 必须大于 0。")

        if self.selection_rank is not None and self.selection_rank <= 0:
            raise ValueError("selection_rank 必须大于 0。")

        if self.within_pool_cutoff and self.pool_rank is None:
            raise ValueError("进入 pool cutoff 的文件必须拥有 pool_rank。")

        if (self.total_score is None) != (not self.signals):
            raise ValueError("total_score 与 signals 必须同时存在或同时缺失。")

        if self.total_score is not None and self.pool_rank is None:
            raise ValueError("拥有评分证据的文件必须拥有 pool_rank。")

        if self.rerank_selected is None:
            if any(
                value is not None
                for value in (
                    self.rerank_rank,
                    self.rerank_lane,
                    self.rank_delta,
                )
            ):
                raise ValueError("没有重排证据时不能携带重排名次、赛道或名次变化。")

        else:
            if self.pool_rank is None or self.rerank_lane is None:
                raise ValueError("重排证据必须对应候选池内文件并包含赛道。")

            if self.rerank_selected:
                if self.rerank_rank is None or self.rerank_rank <= 0:
                    raise ValueError("重排选中的文件必须包含正数 rerank_rank。")

                expected_delta = self.pool_rank - self.rerank_rank

                if self.rank_delta != expected_delta:
                    raise ValueError("rank_delta 必须等于 pool_rank - rerank_rank。")

            elif self.rerank_rank is not None or self.rank_delta is not None:
                raise ValueError("重排未选中的文件不能携带 rerank_rank 或 rank_delta。")

        expected_outcome: TruthSelectionOutcome

        if self.selected:
            expected_outcome = "selected"
        elif self.pool_rank is None:
            expected_outcome = "not_in_pool"
        elif not self.within_pool_cutoff:
            expected_outcome = "below_pool_cutoff"
        elif self.rerank_selected is False:
            expected_outcome = "rerank_rejected"
        elif self.rerank_selected is True:
            expected_outcome = "below_selection_cutoff"
        else:
            expected_outcome = "selection_untraced"

        if self.outcome != expected_outcome:
            raise ValueError(f"outcome 应为 {expected_outcome}，实际为 {self.outcome}。")

    @property
    def selected(self) -> bool:
        """返回该真实文件是否进入最终选择。"""

        return self.selection_rank is not None


@dataclass(frozen=True)
class SampleRankingDiagnostic:
    """一条评测样本的候选排序诊断。"""

    sample_id: str
    request_text: str
    analysis_commit: str
    answer_commit: str
    evaluable: bool
    existing_truth_count: int
    new_source_file_count: int
    candidate_pool_size: int
    selected_size: int
    pool_recall: float
    selection_recall: float
    truth_rankings: tuple[TruthPathRanking, ...]
    missing_from_pool: tuple[str, ...]
    missed_after_ranking: tuple[str, ...]
    false_positive_selections: tuple[str, ...]
    score_trace_available: bool
    rerank_trace_available: bool
    false_positive_score_traces: tuple[CandidateScoreTrace, ...]

    def __post_init__(self) -> None:
        """校验样本统计与路径明细保持一致。"""

        if not self.sample_id.strip():
            raise ValueError("sample_id 不能为空。")

        if not self.request_text.strip():
            raise ValueError("request_text 不能为空。")

        for field_name, value in (
            ("existing_truth_count", self.existing_truth_count),
            ("new_source_file_count", self.new_source_file_count),
            ("candidate_pool_size", self.candidate_pool_size),
            ("selected_size", self.selected_size),
        ):
            if value < 0:
                raise ValueError(f"{field_name} 不能为负数。")

        _ratio(self.pool_recall, field_name="pool_recall")
        _ratio(self.selection_recall, field_name="selection_recall")

        if len(self.truth_rankings) != self.existing_truth_count:
            raise ValueError("truth_rankings 数量必须等于 existing_truth_count。")

        if self.evaluable != (self.existing_truth_count > 0):
            raise ValueError("evaluable 必须由 existing_truth_count 决定。")

        has_truth_scores = any(item.total_score is not None for item in self.truth_rankings)

        if not self.score_trace_available and (
            has_truth_scores or self.false_positive_score_traces
        ):
            raise ValueError("无评分轨迹的样本不能携带候选评分证据。")

        has_truth_rerank = any(item.rerank_selected is not None for item in self.truth_rankings)

        if not self.rerank_trace_available and has_truth_rerank:
            raise ValueError("无重排轨迹的样本不能携带真实文件重排证据。")

        false_positive_paths = tuple(trace.path for trace in self.false_positive_score_traces)

        if false_positive_paths and false_positive_paths != self.false_positive_selections:
            raise ValueError("假阳性评分轨迹必须对应 false_positive_selections。")


@dataclass(frozen=True)
class RankingDiagnosticsReport:
    """一个历史评测报告的候选排序诊断汇总。"""

    schema_version: str
    experiment_name: str
    source_report: str
    pool_k: int
    file_k: int
    sample_count: int
    evaluable_sample_count: int
    score_trace_sample_count: int
    rerank_trace_sample_count: int
    perfect_selection_sample_count: int
    existing_truth_count: int
    pool_hit_count: int
    selected_hit_count: int
    missing_from_pool_count: int
    missed_after_ranking_count: int
    false_positive_selected_count: int
    rerank_promoted_truth_count: int
    rerank_demoted_truth_count: int
    rerank_unchanged_truth_count: int
    rerank_rejected_truth_count: int
    pool_recall_micro: float
    selection_recall_micro: float
    selection_recall_macro: float
    diagnostics: tuple[SampleRankingDiagnostic, ...]

    def __post_init__(self) -> None:
        """校验汇总计数和比例。"""

        if self.schema_version != "1.1":
            raise ValueError("排序诊断 schema_version 必须是 1.1。")

        if not self.experiment_name.strip():
            raise ValueError("experiment_name 不能为空。")

        if self.pool_k <= 0 or self.file_k <= 0:
            raise ValueError("pool_k 和 file_k 必须大于 0。")

        counters = (
            self.sample_count,
            self.evaluable_sample_count,
            self.score_trace_sample_count,
            self.rerank_trace_sample_count,
            self.perfect_selection_sample_count,
            self.existing_truth_count,
            self.pool_hit_count,
            self.selected_hit_count,
            self.missing_from_pool_count,
            self.missed_after_ranking_count,
            self.false_positive_selected_count,
            self.rerank_promoted_truth_count,
            self.rerank_demoted_truth_count,
            self.rerank_unchanged_truth_count,
            self.rerank_rejected_truth_count,
        )

        if any(value < 0 for value in counters):
            raise ValueError("排序诊断计数不能为负数。")

        if len(self.diagnostics) != self.sample_count:
            raise ValueError("diagnostics 数量必须等于 sample_count。")

        if self.score_trace_sample_count != sum(
            item.score_trace_available for item in self.diagnostics
        ):
            raise ValueError("score_trace_sample_count 与样本明细不一致。")

        if self.rerank_trace_sample_count != sum(
            item.rerank_trace_available for item in self.diagnostics
        ):
            raise ValueError("rerank_trace_sample_count 与样本明细不一致。")

        truth_items = tuple(
            truth for diagnostic in self.diagnostics for truth in diagnostic.truth_rankings
        )

        expected_rerank_counts = (
            sum((truth.rank_delta or 0) > 0 for truth in truth_items),
            sum((truth.rank_delta or 0) < 0 for truth in truth_items),
            sum(truth.rank_delta == 0 for truth in truth_items),
            sum(truth.rerank_selected is False for truth in truth_items),
        )
        actual_rerank_counts = (
            self.rerank_promoted_truth_count,
            self.rerank_demoted_truth_count,
            self.rerank_unchanged_truth_count,
            self.rerank_rejected_truth_count,
        )

        if actual_rerank_counts != expected_rerank_counts:
            raise ValueError("重排汇总计数与样本明细不一致。")

        if self.pool_hit_count + self.missing_from_pool_count != self.existing_truth_count:
            raise ValueError("pool 命中数与遗漏数必须完整划分 existing truth。")

        if (
            self.selected_hit_count + self.missing_from_pool_count + self.missed_after_ranking_count
            != self.existing_truth_count
        ):
            raise ValueError("selection 统计必须完整划分 existing truth。")

        for field_name, value in (
            ("pool_recall_micro", self.pool_recall_micro),
            ("selection_recall_micro", self.selection_recall_micro),
            ("selection_recall_macro", self.selection_recall_macro),
        ):
            _ratio(value, field_name=field_name)
