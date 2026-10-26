"""在不重新调用 LLM 的前提下重新计算历史评测报告。

旧报告已经保存了 CandidatePool、冻结选择和最终报告路径。指标口径变化时，
只需要在相同 Git ref 上重建数据集事实，再复用这些 observation 计算指标。
本模块会严格校验样本身份与答案字段，防止拿错仓库或 ref 后静默计分。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from app.schemas.evaluation import EvaluationDataset, EvaluationSample
from app.schemas.evaluation_metrics import (
    EvaluationObservation,
    EvaluationRunStatus,
)
from app.tools.evaluation_dataset import build_evaluation_dataset
from app.tools.evaluation_metrics import calculate_evaluation_metrics

SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0", "1.1"})


class EvaluationRescoreError(ValueError):
    """历史报告不完整、与仓库不匹配或无法安全重新计分。"""


def _mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    """读取 JSON 对象字段，并在类型错误时给出明确位置。"""

    if not isinstance(value, Mapping):
        raise EvaluationRescoreError(f"{field_name} 必须是 JSON 对象。")

    return value


def _sequence(value: object, *, field_name: str) -> Sequence[object]:
    """读取 JSON 数组字段，字符串不能冒充序列。"""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise EvaluationRescoreError(f"{field_name} 必须是 JSON 数组。")

    return value


def _string(value: object, *, field_name: str) -> str:
    """读取必需的非空字符串。"""

    if not isinstance(value, str) or not value.strip():
        raise EvaluationRescoreError(f"{field_name} 必须是非空字符串。")

    return value.strip()


def _integer(value: object, *, field_name: str) -> int:
    """读取整数，拒绝 bool 混入计数。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationRescoreError(f"{field_name} 必须是整数。")

    return value


def _path_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    """把 JSON 路径数组转换成不可变元组。"""

    items = _sequence(value, field_name=field_name)
    paths: list[str] = []

    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise EvaluationRescoreError(f"{field_name}[{index}] 必须是非空字符串。")

        paths.append(item)

    return tuple(paths)


def load_evaluation_report(report_path: str | Path) -> dict[str, Any]:
    """读取并执行报告顶层结构校验。"""

    resolved = Path(report_path).expanduser().resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"评测报告不存在：{resolved}")

    if not resolved.is_file():
        raise IsADirectoryError(f"评测报告路径不是文件：{resolved}")

    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvaluationRescoreError(f"评测报告不是合法 JSON：{exc}") from exc

    payload = dict(_mapping(raw, field_name="report"))
    schema_version = _string(
        payload.get("schema_version"),
        field_name="schema_version",
    )

    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise EvaluationRescoreError(
            "不支持的评测报告版本："
            f"{schema_version}；"
            f"支持版本为 {sorted(SUPPORTED_SCHEMA_VERSIONS)}。"
        )

    _mapping(payload.get("dataset"), field_name="dataset")
    _sequence(payload.get("observations"), field_name="observations")
    _mapping(payload.get("metrics"), field_name="metrics")
    return payload


def _observation_from_payload(
    raw_observation: object,
    *,
    index: int,
) -> EvaluationObservation:
    """恢复一条旧报告中的原始 observation。"""

    item = _mapping(
        raw_observation,
        field_name=f"observations[{index}]",
    )
    prefix = f"observations[{index}]"
    error = item.get("error")

    if error is not None and not isinstance(error, str):
        raise EvaluationRescoreError(f"{prefix}.error 必须是字符串或 null。")

    status = _string(
        item.get("status"),
        field_name=f"{prefix}.status",
    )

    if status not in {"success", "degraded", "failed"}:
        raise EvaluationRescoreError(f"{prefix}.status 非法：{status}")

    latency = item.get("latency_ms")

    if isinstance(latency, bool) or not isinstance(
        latency,
        (int, float),
    ):
        raise EvaluationRescoreError(f"{prefix}.latency_ms 必须是数字。")

    return EvaluationObservation(
        sample_id=_string(
            item.get("sample_id"),
            field_name=f"{prefix}.sample_id",
        ),
        status=cast(EvaluationRunStatus, status),
        candidate_pool_paths=_path_tuple(
            item.get("candidate_pool_paths"),
            field_name=f"{prefix}.candidate_pool_paths",
        ),
        selected_paths=_path_tuple(
            item.get("selected_paths"),
            field_name=f"{prefix}.selected_paths",
        ),
        reported_paths=_path_tuple(
            item.get("reported_paths"),
            field_name=f"{prefix}.reported_paths",
        ),
        invalid_reported_paths=_path_tuple(
            item.get("invalid_reported_paths"),
            field_name=f"{prefix}.invalid_reported_paths",
        ),
        analyzed_item_count=_integer(
            item.get("analyzed_item_count"),
            field_name=f"{prefix}.analyzed_item_count",
        ),
        assigned_item_count=_integer(
            item.get("assigned_item_count"),
            field_name=f"{prefix}.assigned_item_count",
        ),
        latency_ms=float(latency),
        token_count=_integer(
            item.get("token_count", 0),
            field_name=f"{prefix}.token_count",
        ),
        error=error,
    )


def _old_sample_by_id(
    dataset_payload: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """索引旧报告样本并拒绝重复 ID。"""

    raw_samples = _sequence(
        dataset_payload.get("samples"),
        field_name="dataset.samples",
    )
    indexed: dict[str, Mapping[str, Any]] = {}

    for index, raw_sample in enumerate(raw_samples):
        sample = _mapping(
            raw_sample,
            field_name=f"dataset.samples[{index}]",
        )
        sample_id = _string(
            sample.get("sample_id"),
            field_name=f"dataset.samples[{index}].sample_id",
        )

        if sample_id in indexed:
            raise EvaluationRescoreError(f"旧报告包含重复 sample_id：{sample_id}")

        indexed[sample_id] = sample

    return indexed


def _validate_sample_identity(
    rebuilt: EvaluationSample,
    old: Mapping[str, Any],
) -> None:
    """确保重建样本和旧实验使用的是同一条历史事实。"""

    scalar_fields = (
        "repository_relative_path",
        "request_text",
        "analysis_commit",
        "answer_commit",
    )

    for field_name in scalar_fields:
        old_value = old.get(field_name)
        new_value = getattr(rebuilt, field_name)

        if old_value != new_value:
            raise EvaluationRescoreError(
                f"样本 {rebuilt.sample_id} 的 {field_name} 与旧报告不一致。"
            )

    for field_name in (
        "changed_files",
        "source_files",
        "test_files",
    ):
        old_paths = _path_tuple(
            old.get(field_name),
            field_name=(f"dataset.samples[{rebuilt.sample_id}].{field_name}"),
        )
        new_paths = getattr(rebuilt, field_name)

        if old_paths != new_paths:
            raise EvaluationRescoreError(
                f"样本 {rebuilt.sample_id} 的 {field_name} 与旧报告不一致。"
            )


def rebuild_report_dataset(
    repository_path: str | Path,
    report_payload: Mapping[str, Any],
) -> EvaluationDataset:
    """在旧报告固定 ref 上重建带 existing/new 分类的数据集。"""

    dataset_payload = _mapping(
        report_payload.get("dataset"),
        field_name="dataset",
    )
    resolved_ref = _string(
        dataset_payload.get("resolved_ref"),
        field_name="dataset.resolved_ref",
    )
    old_samples = _old_sample_by_id(dataset_payload)
    sample_count = len(old_samples)

    if sample_count == 0:
        raise EvaluationRescoreError("旧报告的数据集没有样本。")

    scanned_commit_count = _integer(
        dataset_payload.get("scanned_commit_count"),
        field_name="dataset.scanned_commit_count",
    )
    rebuilt = build_evaluation_dataset(
        repository_path,
        requested_ref=resolved_ref,
        sample_limit=sample_count,
        scan_limit=max(
            scanned_commit_count,
            sample_count,
        ),
    )

    rebuilt_ids = tuple(sample.sample_id for sample in rebuilt.samples)
    old_ids = tuple(old_samples)

    if rebuilt_ids != old_ids:
        raise EvaluationRescoreError(
            "重建数据集的样本顺序或 ID 与旧报告不一致；请确认仓库路径、Git 历史和筛选规则没有变化。"
        )

    for sample in rebuilt.samples:
        _validate_sample_identity(
            sample,
            old_samples[sample.sample_id],
        )

    return rebuilt


def rescore_evaluation_report(
    report_path: str | Path,
    repository_path: str | Path,
    *,
    experiment_name: str | None = None,
    pool_k: int | None = None,
    file_k: int | None = None,
) -> dict[str, Any]:
    """复用历史 observation，以当前指标口径生成新版报告。"""

    payload = load_evaluation_report(report_path)
    dataset = rebuild_report_dataset(
        repository_path,
        payload,
    )
    raw_observations = _sequence(
        payload.get("observations"),
        field_name="observations",
    )
    observations = tuple(
        _observation_from_payload(
            item,
            index=index,
        )
        for index, item in enumerate(raw_observations)
    )
    old_metrics = _mapping(
        payload.get("metrics"),
        field_name="metrics",
    )
    resolved_pool_k = pool_k or _integer(
        old_metrics.get("pool_k"),
        field_name="metrics.pool_k",
    )
    resolved_file_k = file_k or _integer(
        old_metrics.get("file_k"),
        field_name="metrics.file_k",
    )
    source_experiment = _string(
        payload.get("experiment_name"),
        field_name="experiment_name",
    )
    resolved_experiment = (
        experiment_name.strip()
        if experiment_name is not None and experiment_name.strip()
        else f"{source_experiment}-rescored"
    )

    metrics = calculate_evaluation_metrics(
        dataset,
        observations,
        experiment_name=resolved_experiment,
        pool_k=resolved_pool_k,
        file_k=resolved_file_k,
    )

    rescored_payload = {
        "schema_version": "1.1",
        "report_kind": "rescored",
        "experiment_name": resolved_experiment,
        "source_report": {
            "schema_version": payload["schema_version"],
            "experiment_name": source_experiment,
            "resolved_ref": dataset.resolved_ref,
        },
        "dataset": asdict(dataset),
        "observations": [asdict(observation) for observation in observations],
        "cochange_leak_reports": list(payload.get("cochange_leak_reports", [])),
        "previous_metrics": dict(old_metrics),
        "metrics": asdict(metrics),
    }

    # dataclass 中的 tuple 在 JSON 中会变成 list。
    # 这里提前进行 JSON 规范化，保证 Python 返回值
    # 与最终写入磁盘的内容完全一致。
    return json.loads(
        json.dumps(
            rescored_payload,
            ensure_ascii=False,
        )
    )


def write_rescored_report(
    payload: Mapping[str, Any],
    output_path: str | Path,
    *,
    source_path: str | Path | None = None,
) -> Path:
    """以稳定 JSON 格式写入新版报告，并防止覆盖原报告。"""

    resolved_output = Path(output_path).expanduser().resolve()

    if resolved_output.suffix.casefold() != ".json":
        raise ValueError("重新计分报告必须使用 .json 扩展名。")

    if source_path is not None:
        resolved_source = Path(source_path).expanduser().resolve()

        if resolved_output == resolved_source:
            raise ValueError("输出路径不能覆盖原始评测报告。")

    if resolved_output.exists() and resolved_output.is_dir():
        raise IsADirectoryError(f"重新计分报告路径是目录：{resolved_output}")

    resolved_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    rendered = json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    resolved_output.write_text(
        rendered + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return resolved_output
