"""从历史评测 JSON 生成候选排序诊断，不调用 LLM。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.schemas.ranking_diagnostics import (
    RankingDiagnosticsReport,
    SampleRankingDiagnostic,
    TruthPathRanking,
)


class RankingDiagnosticsError(ValueError):
    """评测报告缺少生成可信排序诊断所需的事实。"""


def _mapping(
    value: object,
    *,
    field_name: str,
) -> Mapping[str, Any]:
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

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RankingDiagnosticsError(f"{field_name} 必须是 JSON 数组。")

    return value


def _string(
    value: object,
    *,
    field_name: str,
) -> str:
    """读取必需的非空字符串。"""

    if not isinstance(value, str) or not value.strip():
        raise RankingDiagnosticsError(f"{field_name} 必须是非空字符串。")

    return value.strip()


def _integer(
    value: object,
    *,
    field_name: str,
) -> int:
    """读取整数并拒绝 bool。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise RankingDiagnosticsError(f"{field_name} 必须是整数。")

    return value


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


def _ratio(
    numerator: int,
    denominator: int,
) -> float:
    """返回稳定的六位小数比例。"""

    if denominator == 0:
        return 0.0

    return round(
        numerator / denominator,
        6,
    )


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
        payload = json.loads(resolved.read_text(encoding="utf-8"))
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

    indexed: dict[
        str,
        Mapping[str, Any],
    ] = {}

    raw_observations = _sequence(
        payload.get("observations"),
        field_name="observations",
    )

    for index, raw in enumerate(raw_observations):
        item = _mapping(
            raw,
            field_name=(f"observations[{index}]"),
        )
        sample_id = _string(
            item.get("sample_id"),
            field_name=(f"observations[{index}].sample_id"),
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
        field_name=(f"dataset.samples[{index}]"),
    )
    prefix = f"dataset.samples[{index}]"

    if "existing_source_files" not in sample or "new_source_files" not in sample:
        raise RankingDiagnosticsError(
            "报告没有 existing_source_files/new_source_files；请先使用 app.cli.rescore 升级报告。"
        )

    truth = _paths(
        sample.get("existing_source_files"),
        field_name=(f"{prefix}.existing_source_files"),
    )
    new_files = _paths(
        sample.get("new_source_files"),
        field_name=(f"{prefix}.new_source_files"),
    )

    if observation is None:
        pool_paths: tuple[str, ...] = ()
        selected_paths: tuple[str, ...] = ()
    else:
        pool_paths = _paths(
            observation.get("candidate_pool_paths"),
            field_name=("candidate_pool_paths"),
        )
        selected_paths = _paths(
            observation.get("selected_paths"),
            field_name="selected_paths",
        )

    pool_cutoff = pool_paths[:pool_k]
    selection_cutoff = selected_paths[:file_k]

    truth_set = set(truth)
    pool_set = set(pool_cutoff)
    selected_set = set(selection_cutoff)

    rankings = tuple(
        TruthPathRanking(
            path=path,
            pool_rank=_rank(
                path,
                pool_paths,
            ),
            within_pool_cutoff=(path in pool_set),
            selection_rank=_rank(
                path,
                selection_cutoff,
            ),
        )
        for path in truth
    )

    missing_from_pool = tuple(path for path in truth if path not in pool_set)
    missed_after_ranking = tuple(
        path for path in truth if (path in pool_set and path not in selected_set)
    )
    false_positives = tuple(path for path in selection_cutoff if path not in truth_set)

    pool_hits = len(truth_set & pool_set)
    selection_hits = len(truth_set & selected_set)

    return SampleRankingDiagnostic(
        sample_id=_string(
            sample.get("sample_id"),
            field_name=(f"{prefix}.sample_id"),
        ),
        request_text=_string(
            sample.get("request_text"),
            field_name=(f"{prefix}.request_text"),
        ),
        analysis_commit=_string(
            sample.get("analysis_commit"),
            field_name=(f"{prefix}.analysis_commit"),
        ),
        answer_commit=_string(
            sample.get("answer_commit"),
            field_name=(f"{prefix}.answer_commit"),
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
        truth_rankings=rankings,
        missing_from_pool=(missing_from_pool),
        missed_after_ranking=(missed_after_ranking),
        false_positive_selections=(false_positives),
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

    raw_samples = _sequence(
        dataset.get("samples"),
        field_name="dataset.samples",
    )

    for index, raw_sample in enumerate(raw_samples):
        sample = _mapping(
            raw_sample,
            field_name=(f"dataset.samples[{index}]"),
        )
        sample_id = _string(
            sample.get("sample_id"),
            field_name=(f"dataset.samples[{index}].sample_id"),
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
        schema_version="1.0",
        experiment_name=_string(
            payload.get("experiment_name"),
            field_name="experiment_name",
        ),
        source_report=str(Path(report_path).expanduser().resolve()),
        pool_k=resolved_pool_k,
        file_k=resolved_file_k,
        sample_count=len(diagnostics),
        evaluable_sample_count=len(evaluable),
        perfect_selection_sample_count=sum(item.selection_recall == 1.0 for item in evaluable),
        existing_truth_count=(existing_truth_count),
        pool_hit_count=pool_hit_count,
        selected_hit_count=(selected_hit_count),
        missing_from_pool_count=(missing_pool_count),
        missed_after_ranking_count=(missed_ranking_count),
        false_positive_selected_count=sum(
            len(item.false_positive_selections) for item in diagnostics
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
