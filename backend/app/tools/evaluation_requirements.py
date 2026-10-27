"""离线评测的 RequirementSpec 冻结与回放工件。

同一条历史样本如果在不同实验中重新调用 Requirement Agent，
实验之间就同时改变了需求解析和上下文策略，无法归因。本模块把
首次生成的 RequirementSpec 写成与数据集强绑定的 JSON 工件，
后续实验只能回放这份工件，不得静默重新调用模型。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.contracts import RequirementSpec
from app.schemas.evaluation import EvaluationDataset, EvaluationSample

ARTIFACT_SCHEMA_VERSION = "1.0"
SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class RequirementArtifactError(RuntimeError):
    """需求冻结工件不完整、被篡改或与数据集不匹配。"""


class FrozenRequirementEntry(BaseModel):
    """一条评测样本对应的已审批需求契约。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    sample_id: str = Field(min_length=1)
    analysis_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    answer_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    request_digest: str = Field(pattern=SHA256_PATTERN)
    requirement_spec: RequirementSpec

    @model_validator(mode="after")
    def validate_source_text_digest(self) -> FrozenRequirementEntry:
        """确保契约保留的原始需求与样本需求摘要一致。"""

        actual = compute_request_digest(
            self.requirement_spec.source_text,
        )

        if actual != self.request_digest:
            raise ValueError("RequirementSpec.source_text 与 request_digest 不一致。")

        return self


class FrozenRequirementArtifact(BaseModel):
    """可稳定序列化的需求评测工件。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    schema_version: Literal["1.0"] = ARTIFACT_SCHEMA_VERSION
    dataset_digest: str = Field(pattern=SHA256_PATTERN)
    entries: tuple[FrozenRequirementEntry, ...] = Field(min_length=1)
    artifact_digest: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_stable_order_and_digest(self) -> FrozenRequirementArtifact:
        """防止重复样本、非稳定排序和 JSON 被篡改。"""

        sample_ids = tuple(entry.sample_id for entry in self.entries)

        if sample_ids != tuple(sorted(sample_ids)):
            raise ValueError("entries 必须按 sample_id 稳定排序。")

        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("entries 不能包含重复 sample_id。")

        actual = compute_artifact_digest(
            self.dataset_digest,
            self.entries,
        )

        if actual != self.artifact_digest:
            raise ValueError("artifact_digest 校验失败，工件可能被篡改。")

        return self


def normalize_request_text(text: str) -> str:
    """对需求文本执行与 Requirement Agent 相同的空白归一化。"""

    return " ".join(text.split())


def _sha256_text(value: str) -> str:
    """返回带算法前缀的 UTF-8 SHA-256。"""

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_request_digest(request_text: str) -> str:
    """计算规范化需求文本摘要。"""

    normalized = normalize_request_text(request_text)

    if not normalized:
        raise ValueError("request_text 不能为空。")

    return _sha256_text(normalized)


def _canonical_json(value: object) -> str:
    """生成不受平台换行和字典顺序影响的 JSON。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sample_identity(sample: EvaluationSample) -> dict[str, str]:
    """提取不含答案文件列表的样本身份。"""

    return {
        "sample_id": sample.sample_id,
        "analysis_commit": sample.analysis_commit,
        "answer_commit": sample.answer_commit,
        "request_digest": compute_request_digest(sample.request_text),
    }


def compute_dataset_requirement_digest(
    dataset: EvaluationDataset,
) -> str:
    """将冻结工件绑定到确切的样本集合。"""

    identities = sorted(
        (_sample_identity(sample) for sample in dataset.samples),
        key=lambda item: item["sample_id"],
    )

    if not identities:
        raise ValueError("不能为空数据集生成需求工件。")

    return _sha256_text(
        _canonical_json(identities),
    )


def compute_artifact_digest(
    dataset_digest: str,
    entries: tuple[FrozenRequirementEntry, ...],
) -> str:
    """计算排除 artifact_digest 自身的工件摘要。"""

    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "dataset_digest": dataset_digest,
        "entries": [
            entry.model_dump(
                mode="json",
                round_trip=True,
            )
            for entry in entries
        ],
    }

    return _sha256_text(
        _canonical_json(payload),
    )


class EvaluationRequirementStore:
    """在记录或回放模式下为适配器提供 RequirementSpec。"""

    def __init__(
        self,
        dataset: EvaluationDataset,
        *,
        mode: Literal["record", "replay"],
        entries: tuple[FrozenRequirementEntry, ...] = (),
    ) -> None:
        if mode not in {"record", "replay"}:
            raise ValueError("mode 必须是 record 或 replay。")

        self._dataset = dataset
        self._mode = mode
        self._dataset_digest = compute_dataset_requirement_digest(dataset)
        self._samples = {sample.sample_id: sample for sample in dataset.samples}
        self._entries = {entry.sample_id: entry for entry in entries}

        if len(self._entries) != len(entries):
            raise RequirementArtifactError("需求工件含有重复 sample_id。")

        if mode == "replay":
            self._validate_complete_entries()

    @property
    def mode(self) -> Literal["record", "replay"]:
        """返回当前存储模式。"""

        return self._mode

    @property
    def dataset_digest(self) -> str:
        """返回当前数据集的需求身份摘要。"""

        return self._dataset_digest

    def _validate_entry(
        self,
        sample: EvaluationSample,
        entry: FrozenRequirementEntry,
    ) -> None:
        """校验一条工件记录与当前样本完全一致。"""

        expected = _sample_identity(sample)
        actual = {
            "sample_id": entry.sample_id,
            "analysis_commit": entry.analysis_commit,
            "answer_commit": entry.answer_commit,
            "request_digest": entry.request_digest,
        }

        if actual != expected:
            raise RequirementArtifactError(
                f"需求工件与评测样本不匹配：sample_id={sample.sample_id}。"
            )

        if normalize_request_text(entry.requirement_spec.source_text) != normalize_request_text(
            sample.request_text
        ):
            raise RequirementArtifactError(
                f"RequirementSpec.source_text 与样本需求不一致：sample_id={sample.sample_id}。"
            )

    def _validate_complete_entries(self) -> None:
        """回放工件必须不多不少地覆盖整个数据集。"""

        expected_ids = set(self._samples)
        actual_ids = set(self._entries)

        missing = tuple(sorted(expected_ids - actual_ids))
        extra = tuple(sorted(actual_ids - expected_ids))

        if missing or extra:
            raise RequirementArtifactError(
                f"需求工件与数据集样本集合不一致：missing={missing}，extra={extra}。"
            )

        for sample_id, sample in self._samples.items():
            self._validate_entry(
                sample,
                self._entries[sample_id],
            )

    def resolve(
        self,
        sample: EvaluationSample,
        *,
        generate: Callable[[], RequirementSpec],
    ) -> RequirementSpec:
        """记录模式生成一次，回放模式绝不调用 generate。"""

        expected_sample = self._samples.get(sample.sample_id)

        if expected_sample is None:
            raise RequirementArtifactError(f"样本不属于当前数据集：{sample.sample_id}。")

        if _sample_identity(expected_sample) != _sample_identity(sample):
            raise RequirementArtifactError(f"运行时样本身份发生变化：{sample.sample_id}。")

        if self._mode == "replay":
            entry = self._entries[sample.sample_id]
            self._validate_entry(sample, entry)
            return entry.requirement_spec

        requirement = generate()

        entry = FrozenRequirementEntry(
            **_sample_identity(sample),
            requirement_spec=requirement,
        )

        self._validate_entry(sample, entry)

        existing = self._entries.get(sample.sample_id)

        if existing is not None and existing != entry:
            raise RequirementArtifactError(f"同一样本不能记录两份不同需求：{sample.sample_id}。")

        self._entries[sample.sample_id] = entry
        return requirement

    def build_artifact(self) -> FrozenRequirementArtifact:
        """只有所有样本都已记录才允许生成工件。"""

        self._validate_complete_entries()

        entries = tuple(
            sorted(
                self._entries.values(),
                key=lambda entry: entry.sample_id,
            )
        )

        return FrozenRequirementArtifact(
            dataset_digest=self._dataset_digest,
            entries=entries,
            artifact_digest=compute_artifact_digest(
                self._dataset_digest,
                entries,
            ),
        )


def create_recording_requirement_store(
    dataset: EvaluationDataset,
) -> EvaluationRequirementStore:
    """创建会记录每条样本需求的存储器。"""

    return EvaluationRequirementStore(
        dataset,
        mode="record",
    )


def load_requirement_artifact(
    path: str | Path,
    dataset: EvaluationDataset,
) -> EvaluationRequirementStore:
    """读取、验证并创建只回放存储器。"""

    resolved = Path(path).expanduser().resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"需求工件不存在：{resolved}")

    if not resolved.is_file():
        raise RequirementArtifactError(f"需求工件路径不是文件：{resolved}")

    try:
        payload = json.loads(
            resolved.read_text(encoding="utf-8"),
        )
        artifact = FrozenRequirementArtifact.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RequirementArtifactError(f"无法读取有效需求工件：{resolved}。") from exc

    expected_digest = compute_dataset_requirement_digest(dataset)

    if artifact.dataset_digest != expected_digest:
        raise RequirementArtifactError("需求工件 dataset_digest 与当前数据集不一致。")

    return EvaluationRequirementStore(
        dataset,
        mode="replay",
        entries=artifact.entries,
    )


def write_requirement_artifact(
    store: EvaluationRequirementStore,
    path: str | Path,
) -> Path:
    """以 UTF-8、LF 和稳定键顺序写入需求工件。"""

    if store.mode != "record":
        raise RequirementArtifactError("只有 record 模式存储器可以写入工件。")

    resolved = Path(path).expanduser().resolve()

    if resolved.suffix.casefold() != ".json":
        raise ValueError("需求工件必须使用 .json 扩展名。")

    if resolved.exists() and resolved.is_dir():
        raise IsADirectoryError(f"需求工件路径是目录：{resolved}")

    artifact = store.build_artifact()

    resolved.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    rendered = json.dumps(
        artifact.model_dump(
            mode="json",
            round_trip=True,
        ),
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
