"""从历史评测 JSON 生成候选排序诊断，不调用 LLM。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from app.schemas.evaluation_metrics import (
    CandidateRerankLane,
    CandidateRerankTrace,
    CandidateScoreTrace,
    CandidateSignalName,
    CandidateSignalTrace,
)
from app.schemas.ranking_diagnostics import (
    RankingDiagnosticsReport,
    SampleRankingDiagnostic,
    TruthPathRanking,
    TruthSelectionOutcome,
)


class RankingDiagnosticsError(ValueError):
    """评测报告缺少生成可信排序诊断所需的事实。"""


def _mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    """读取必需的 JSON 对象。"""

    if not isinstance(value, Mapping):
        raise RankingDiagnosticsError(f"{field_name} 必须是 JSON 对象。")

    return value


def _sequence(
    value: object,
    *,
    field_name: str,
) -> Sequence[object]:
    """读取必需的 JSON 数组。"""

    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes),
    ):
        raise RankingDiagnosticsError(f"{field_name} 必须是 JSON 数组。")

    return value


def _string(value: object, *, field_name: str) -> str:
    """读取必需的非空字符串。"""

    if not isinstance(value, str) or not value.strip():
        raise RankingDiagnosticsError(f"{field_name} 必须是非空字符串。")

    return value.strip()


def _integer(value: object, *, field_name: str) -> int:
    """读取整数并拒绝 bool。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise RankingDiagnosticsError(f"{field_name} 必须是整数。")

    return value


def _number(value: object, *, field_name: str) -> float:
    """读取评分数字并拒绝 bool。"""

    if isinstance(value, bool) or not isinstance(
        value,
        (int, float),
    ):
        raise RankingDiagnosticsError(f"{field_name} 必须是数字。")

    return float(value)


def _paths(
    value: object,
    *,
    field_name: str,
) -> tuple[str, ...]:
    """读取去重且保持顺序的路径数组。"""

    paths: list[str] = []

    for index, item in enumerate(
        _sequence(
            value,
            field_name=field_name,
        )
    ):
        if not isinstance(item, str) or not item.strip():
            raise RankingDiagnosticsError(f"{field_name}[{index}] 必须是非空字符串。")

        paths.append(item)

    if len(paths) != len(set(paths)):
        raise RankingDiagnosticsError(f"{field_name} 不能包含重复路径。")

    return tuple(paths)


def _score_traces(
    observation: Mapping[str, Any] | None,
    pool_paths: tuple[str, ...],
) -> tuple[CandidateScoreTrace, ...]:
    """读取并验证候选评分轨迹；旧报告没有该字段时返回空元组。"""

    if observation is None:
        return ()

    raw_traces = _sequence(
        observation.get("candidate_score_traces", ()),
        field_name="candidate_score_traces",
    )
    allowed_names = {
        "keyword",
        "symbol",
        "dep_closure",
        "cochange",
        "testmap",
    }
    traces: list[CandidateScoreTrace] = []

    for trace_index, raw_trace in enumerate(raw_traces):
        prefix = f"candidate_score_traces[{trace_index}]"
        trace = _mapping(
            raw_trace,
            field_name=prefix,
        )
        signal_models: list[CandidateSignalTrace] = []

        for signal_index, raw_signal in enumerate(
            _sequence(
                trace.get("signals"),
                field_name=f"{prefix}.signals",
            )
        ):
            signal_prefix = f"{prefix}.signals[{signal_index}]"
            signal = _mapping(
                raw_signal,
                field_name=signal_prefix,
            )
            name = _string(
                signal.get("name"),
                field_name=f"{signal_prefix}.name",
            )

            if name not in allowed_names:
                raise RankingDiagnosticsError(f"{signal_prefix}.name 非法：{name}")

            signal_models.append(
                CandidateSignalTrace(
                    name=cast(CandidateSignalName, name),
                    value=_number(
                        signal.get("value"),
                        field_name=f"{signal_prefix}.value",
                    ),
                )
            )

        traces.append(
            CandidateScoreTrace(
                path=_string(
                    trace.get("path"),
                    field_name=f"{prefix}.path",
                ),
                pool_rank=_integer(
                    trace.get("pool_rank"),
                    field_name=f"{prefix}.pool_rank",
                ),
                total_score=_number(
                    trace.get("total_score"),
                    field_name=f"{prefix}.total_score",
                ),
                signals=tuple(signal_models),
            )
        )

    result = tuple(traces)

    if result:
        if tuple(trace.path for trace in result) != pool_paths:
            raise RankingDiagnosticsError(
                "candidate_score_traces 必须逐项对应 candidate_pool_paths。"
            )

        if tuple(trace.pool_rank for trace in result) != tuple(range(1, len(result) + 1)):
            raise RankingDiagnosticsError("候选评分轨迹排名必须从 1 连续递增。")

    return result


def _rerank_traces(
    observation: Mapping[str, Any] | None,
    pool_paths: tuple[str, ...],
    selected_paths: tuple[str, ...],
) -> tuple[CandidateRerankTrace, ...]:
    """读取二阶段重排轨迹；旧报告没有该字段时返回空元组。"""

    if observation is None:
        return ()

    raw_traces = _sequence(
        observation.get("candidate_rerank_traces", ()),
        field_name="candidate_rerank_traces",
    )
    traces: list[CandidateRerankTrace] = []

    for trace_index, raw_trace in enumerate(raw_traces):
        prefix = f"candidate_rerank_traces[{trace_index}]"
        trace = _mapping(
            raw_trace,
            field_name=prefix,
        )
        lane = _string(
            trace.get("lane"),
            field_name=f"{prefix}.lane",
        )

        if lane not in {"source", "test"}:
            raise RankingDiagnosticsError(f"{prefix}.lane 非法：{lane}")

        selected = trace.get("selected")

        if not isinstance(selected, bool):
            raise RankingDiagnosticsError(f"{prefix}.selected 必须是布尔值。")

        raw_rerank_rank = trace.get("rerank_rank")
        rerank_rank = (
            None
            if raw_rerank_rank is None
            else _integer(
                raw_rerank_rank,
                field_name=f"{prefix}.rerank_rank",
            )
        )

        traces.append(
            CandidateRerankTrace(
                path=_string(
                    trace.get("path"),
                    field_name=f"{prefix}.path",
                ),
                original_rank=_integer(
                    trace.get("original_rank"),
                    field_name=f"{prefix}.original_rank",
                ),
                rerank_rank=rerank_rank,
                pre_score=_number(
                    trace.get("pre_score"),
                    field_name=f"{prefix}.pre_score",
                ),
                lane=cast(CandidateRerankLane, lane),
                selected=selected,
            )
        )

    result = tuple(traces)

    if not result:
        return result

    if tuple(trace.path for trace in result) != pool_paths:
        raise RankingDiagnosticsError("candidate_rerank_traces 必须逐项对应 candidate_pool_paths。")

    if tuple(trace.original_rank for trace in result) != tuple(range(1, len(result) + 1)):
        raise RankingDiagnosticsError("重排轨迹 original_rank 必须从 1 连续递增。")

    selected_traces = tuple(trace for trace in result if trace.selected)
    rerank_ranks = tuple(sorted(cast(int, trace.rerank_rank) for trace in selected_traces))

    if rerank_ranks != tuple(range(1, len(selected_traces) + 1)):
        raise RankingDiagnosticsError("重排轨迹 rerank_rank 必须从 1 连续递增。")

    if {trace.path for trace in selected_traces} != set(selected_paths):
        raise RankingDiagnosticsError("重排轨迹的选中文件集合必须等于 selected_paths。")

    return result


def _ratio(numerator: int, denominator: int) -> float:
    """返回稳定的六位小数比例。"""

    if denominator == 0:
        return 0.0

    return round(numerator / denominator, 6)


def load_ranking_source(
    report_path: str | Path,
) -> dict[str, Any]:
    """读取包含 existing/new 分类的新版评测报告。"""

    resolved = Path(report_path).expanduser().resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"评测报告不存在：{resolved}")

    if not resolved.is_file():
        raise IsADirectoryError(f"评测报告路径不是文件：{resolved}")

    try:
        payload = json.loads(
            resolved.read_text(
                encoding="utf-8",
            )
        )
    except json.JSONDecodeError as exc:
        raise RankingDiagnosticsError(f"评测报告不是合法 JSON：{exc}") from exc

    result = dict(
        _mapping(
            payload,
            field_name="report",
        )
    )
    _mapping(
        result.get("dataset"),
        field_name="dataset",
    )
    _sequence(
        result.get("observations"),
        field_name="observations",
    )
    _mapping(
        result.get("metrics"),
        field_name="metrics",
    )
    return result


def _index_observations(
    payload: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """按 sample_id 索引 observation 并拒绝重复。"""

    indexed: dict[str, Mapping[str, Any]] = {}

    for index, raw in enumerate(
        _sequence(
            payload.get("observations"),
            field_name="observations",
        )
    ):
        item = _mapping(
            raw,
            field_name=f"observations[{index}]",
        )
        sample_id = _string(
            item.get("sample_id"),
            field_name=f"observations[{index}].sample_id",
        )

        if sample_id in indexed:
            raise RankingDiagnosticsError(f"重复 observation：{sample_id}")

        indexed[sample_id] = item

    return indexed


def _rank(
    path: str,
    paths: tuple[str, ...],
) -> int | None:
    """返回一基排名；路径不存在时返回 None。"""

    try:
        return paths.index(path) + 1
    except ValueError:
        return None


def _sample_diagnostic(
    raw_sample: object,
    observation: Mapping[str, Any] | None,
    *,
    index: int,
    pool_k: int,
    file_k: int,
) -> SampleRankingDiagnostic:
    """计算单条样本的真实路径排名和遗漏阶段。"""

    sample = _mapping(
        raw_sample,
        field_name=f"dataset.samples[{index}]",
    )
    prefix = f"dataset.samples[{index}]"

    if "existing_source_files" not in sample or "new_source_files" not in sample:
        raise RankingDiagnosticsError(
            "报告没有 existing_source_files/new_source_files；请先使用 app.cli.rescore 升级报告。"
        )

    truth = _paths(
        sample.get("existing_source_files"),
        field_name=f"{prefix}.existing_source_files",
    )
    new_files = _paths(
        sample.get("new_source_files"),
        field_name=f"{prefix}.new_source_files",
    )

    if observation is None:
        pool_paths: tuple[str, ...] = ()
        selected_paths: tuple[str, ...] = ()
    else:
        pool_paths = _paths(
            observation.get("candidate_pool_paths"),
            field_name="candidate_pool_paths",
        )
        selected_paths = _paths(
            observation.get("selected_paths"),
            field_name="selected_paths",
        )

    score_traces = _score_traces(
        observation,
        pool_paths,
    )
    rerank_traces = _rerank_traces(
        observation,
        pool_paths,
        selected_paths,
    )
    traces_by_path = {trace.path: trace for trace in score_traces}
    rerank_by_path = {trace.path: trace for trace in rerank_traces}

    pool_cutoff = pool_paths[:pool_k]
    selection_cutoff = selected_paths[:file_k]
    truth_set = set(truth)
    pool_set = set(pool_cutoff)
    selected_set = set(selection_cutoff)
    rankings: list[TruthPathRanking] = []

    for path in truth:
        score_trace = traces_by_path.get(path)
        rerank_trace = rerank_by_path.get(path)
        pool_rank = _rank(path, pool_paths)
        within_pool_cutoff = path in pool_set
        selection_rank = _rank(
            path,
            selection_cutoff,
        )

        outcome: TruthSelectionOutcome

        if selection_rank is not None:
            outcome = "selected"
        elif pool_rank is None:
            outcome = "not_in_pool"
        elif not within_pool_cutoff:
            outcome = "below_pool_cutoff"
        elif rerank_trace is not None and not rerank_trace.selected:
            outcome = "rerank_rejected"
        elif rerank_trace is not None:
            outcome = "below_selection_cutoff"
        else:
            outcome = "selection_untraced"

        rankings.append(
            TruthPathRanking(
                path=path,
                pool_rank=pool_rank,
                within_pool_cutoff=within_pool_cutoff,
                selection_rank=selection_rank,
                total_score=(score_trace.total_score if score_trace else None),
                signals=(score_trace.signals if score_trace else ()),
                outcome=outcome,
                rerank_rank=(rerank_trace.rerank_rank if rerank_trace else None),
                rerank_lane=(rerank_trace.lane if rerank_trace else None),
                rerank_selected=(rerank_trace.selected if rerank_trace else None),
                rank_delta=(
                    pool_rank - rerank_trace.rerank_rank
                    if (
                        rerank_trace is not None
                        and rerank_trace.rerank_rank is not None
                        and pool_rank is not None
                    )
                    else None
                ),
            )
        )

    missing_from_pool = tuple(path for path in truth if path not in pool_set)
    missed_after_ranking = tuple(
        path for path in truth if path in pool_set and path not in selected_set
    )
    false_positives = tuple(path for path in selection_cutoff if path not in truth_set)
    false_positive_score_traces = tuple(
        traces_by_path[path] for path in false_positives if path in traces_by_path
    )
    pool_hits = len(truth_set & pool_set)
    selection_hits = len(truth_set & selected_set)

    return SampleRankingDiagnostic(
        sample_id=_string(
            sample.get("sample_id"),
            field_name=f"{prefix}.sample_id",
        ),
        request_text=_string(
            sample.get("request_text"),
            field_name=f"{prefix}.request_text",
        ),
        analysis_commit=_string(
            sample.get("analysis_commit"),
            field_name=f"{prefix}.analysis_commit",
        ),
        answer_commit=_string(
            sample.get("answer_commit"),
            field_name=f"{prefix}.answer_commit",
        ),
        evaluable=bool(truth),
        existing_truth_count=len(truth),
        new_source_file_count=len(new_files),
        candidate_pool_size=len(pool_paths),
        selected_size=len(selection_cutoff),
        pool_recall=_ratio(
            pool_hits,
            len(truth),
        ),
        selection_recall=_ratio(
            selection_hits,
            len(truth),
        ),
        truth_rankings=tuple(rankings),
        missing_from_pool=missing_from_pool,
        missed_after_ranking=missed_after_ranking,
        false_positive_selections=false_positives,
        score_trace_available=bool(score_traces),
        rerank_trace_available=bool(rerank_traces),
        false_positive_score_traces=(false_positive_score_traces),
    )


def build_ranking_diagnostics(
    report_path: str | Path,
    *,
    pool_k: int | None = None,
    file_k: int | None = None,
) -> RankingDiagnosticsReport:
    """从评测报告生成候选池与最终选择的分阶段诊断。"""

    payload = load_ranking_source(report_path)
    dataset = _mapping(
        payload.get("dataset"),
        field_name="dataset",
    )
    metrics = _mapping(
        payload.get("metrics"),
        field_name="metrics",
    )
    resolved_pool_k = pool_k or _integer(
        metrics.get("pool_k"),
        field_name="metrics.pool_k",
    )
    resolved_file_k = file_k or _integer(
        metrics.get("file_k"),
        field_name="metrics.file_k",
    )

    if resolved_pool_k <= 0 or resolved_file_k <= 0:
        raise RankingDiagnosticsError("pool_k 和 file_k 必须大于 0。")

    observations = _index_observations(payload)
    diagnostics: list[SampleRankingDiagnostic] = []
    known_ids: set[str] = set()

    for index, raw_sample in enumerate(
        _sequence(
            dataset.get("samples"),
            field_name="dataset.samples",
        )
    ):
        sample = _mapping(
            raw_sample,
            field_name=f"dataset.samples[{index}]",
        )
        sample_id = _string(
            sample.get("sample_id"),
            field_name=f"dataset.samples[{index}].sample_id",
        )

        if sample_id in known_ids:
            raise RankingDiagnosticsError(f"重复 sample_id：{sample_id}")

        known_ids.add(sample_id)
        diagnostics.append(
            _sample_diagnostic(
                sample,
                observations.get(sample_id),
                index=index,
                pool_k=resolved_pool_k,
                file_k=resolved_file_k,
            )
        )

    unknown = sorted(set(observations) - known_ids)

    if unknown:
        raise RankingDiagnosticsError(f"observation 引用了未知样本：{unknown}")

    evaluable = tuple(item for item in diagnostics if item.evaluable)
    existing_truth_count = sum(item.existing_truth_count for item in evaluable)
    missing_pool_count = sum(len(item.missing_from_pool) for item in evaluable)
    missed_ranking_count = sum(len(item.missed_after_ranking) for item in evaluable)
    pool_hit_count = existing_truth_count - missing_pool_count
    selected_hit_count = existing_truth_count - missing_pool_count - missed_ranking_count

    return RankingDiagnosticsReport(
        schema_version="1.1",
        experiment_name=_string(
            payload.get("experiment_name"),
            field_name="experiment_name",
        ),
        source_report=str(Path(report_path).expanduser().resolve()),
        pool_k=resolved_pool_k,
        file_k=resolved_file_k,
        sample_count=len(diagnostics),
        evaluable_sample_count=len(evaluable),
        score_trace_sample_count=sum(item.score_trace_available for item in diagnostics),
        rerank_trace_sample_count=sum(item.rerank_trace_available for item in diagnostics),
        perfect_selection_sample_count=sum(item.selection_recall == 1.0 for item in evaluable),
        existing_truth_count=existing_truth_count,
        pool_hit_count=pool_hit_count,
        selected_hit_count=selected_hit_count,
        missing_from_pool_count=missing_pool_count,
        missed_after_ranking_count=missed_ranking_count,
        false_positive_selected_count=sum(
            len(item.false_positive_selections) for item in diagnostics
        ),
        rerank_promoted_truth_count=sum(
            (truth.rank_delta or 0) > 0 for item in diagnostics for truth in item.truth_rankings
        ),
        rerank_demoted_truth_count=sum(
            (truth.rank_delta or 0) < 0 for item in diagnostics for truth in item.truth_rankings
        ),
        rerank_unchanged_truth_count=sum(
            truth.rank_delta == 0 for item in diagnostics for truth in item.truth_rankings
        ),
        rerank_rejected_truth_count=sum(
            truth.rerank_selected is False for item in diagnostics for truth in item.truth_rankings
        ),
        pool_recall_micro=_ratio(
            pool_hit_count,
            existing_truth_count,
        ),
        selection_recall_micro=_ratio(
            selected_hit_count,
            existing_truth_count,
        ),
        selection_recall_macro=round(
            sum(item.selection_recall for item in evaluable) / max(len(evaluable), 1),
            6,
        ),
        diagnostics=tuple(diagnostics),
    )


def write_ranking_diagnostics(
    report: RankingDiagnosticsReport,
    output_path: str | Path,
) -> Path:
    """使用稳定 UTF-8 JSON 写入诊断报告。"""

    resolved = Path(output_path).expanduser().resolve()

    if resolved.suffix.casefold() != ".json":
        raise ValueError("排序诊断报告必须使用 .json 扩展名。")

    if resolved == Path(report.source_report).resolve():
        raise ValueError("排序诊断输出不能覆盖原始评测报告。")

    resolved.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    rendered = json.dumps(
        asdict(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    resolved.write_text(
        rendered + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return resolved
