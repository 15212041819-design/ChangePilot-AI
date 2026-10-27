"""RequirementSpec 冻结与回放工件测试。"""

import json
from pathlib import Path

import pytest

from app.schemas.contracts import (
    AcceptanceCriterion,
    EntityHint,
    RequirementSpec,
    ScopeHints,
)
from app.schemas.evaluation import EvaluationDataset, EvaluationSample
from app.tools.evaluation_requirements import (
    RequirementArtifactError,
    compute_dataset_requirement_digest,
    create_recording_requirement_store,
    load_requirement_artifact,
    write_requirement_artifact,
)

ZERO_SHA256 = "sha256:" + "0" * 64


def _sample(
    *,
    sample_id: str = "git-bbbbbbbbbbbb",
    request_text: str = "改进订单取消行为和测试覆盖",
    analysis_commit: str = "a" * 40,
    answer_commit: str = "b" * 40,
) -> EvaluationSample:
    """构造一条有时间边界的评测样本。"""

    return EvaluationSample(
        sample_id=sample_id,
        repository_relative_path="",
        request_text=request_text,
        analysis_commit=analysis_commit,
        answer_commit=answer_commit,
        changed_files=(
            "order_app/service.py",
            "tests/test_service.py",
        ),
        source_files=("order_app/service.py",),
        test_files=("tests/test_service.py",),
    )


def _dataset(
    *samples: EvaluationSample,
) -> EvaluationDataset:
    """构造统计量自洽的评测数据集。"""

    return EvaluationDataset(
        repository_relative_path="",
        requested_ref="HEAD",
        resolved_ref="c" * 40,
        scanned_commit_count=len(samples),
        samples=tuple(samples),
        rejected_root_count=0,
        rejected_merge_count=0,
        rejected_short_message_count=0,
        rejected_file_count_count=0,
        rejected_pure_test_count=0,
    )


def _requirement(
    request_text: str,
) -> RequirementSpec:
    """构造已通过离线审批的稳定需求契约。"""

    return RequirementSpec(
        spec_id="RS-0001",
        spec_revision=1,
        summary="改进订单取消行为和测试覆盖。",
        change_type="feature",
        acceptance_criteria=(
            AcceptanceCriterion(
                id="AC-001",
                description="订单取消返回明确结果。",
                verification="test",
                priority="P0",
            ),
        ),
        entity_hints=(
            EntityHint(
                concept_id="EH-001",
                concept="订单取消",
                lexical_variants=("order cancellation",),
                symbol_candidates=("cancel_order",),
                role="action_target",
                in_repo_vocab=True,
            ),
        ),
        scope_hints=ScopeHints(),
        source_text=" ".join(request_text.split()),
        spec_digest=ZERO_SHA256,
    )


def test_artifact_round_trip_replays_exact_requirement_without_generation(
    tmp_path: Path,
) -> None:
    """回放模式必须返回冻结契约，不得重新生成。"""

    sample = _sample()
    dataset = _dataset(sample)
    requirement = _requirement(sample.request_text)
    store = create_recording_requirement_store(dataset)

    assert store.resolve(sample, generate=lambda: requirement) == requirement

    artifact_path = write_requirement_artifact(
        store,
        tmp_path / "requirements.json",
    )

    replay = load_requirement_artifact(
        artifact_path,
        dataset,
    )

    called = False

    def forbidden_generate() -> RequirementSpec:
        nonlocal called
        called = True
        raise AssertionError("回放模式不应调用 Requirement Agent。")

    assert replay.resolve(sample, generate=forbidden_generate) == requirement
    assert called is False


def test_artifact_json_is_stable_and_contains_dataset_binding(
    tmp_path: Path,
) -> None:
    """工件必须包含可审计的数据集和自身摘要。"""

    sample = _sample()
    dataset = _dataset(sample)
    store = create_recording_requirement_store(dataset)
    store.resolve(sample, generate=lambda: _requirement(sample.request_text))

    path = write_requirement_artifact(
        store,
        tmp_path / "requirements.json",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.0"
    assert payload["dataset_digest"] == compute_dataset_requirement_digest(dataset)
    assert payload["artifact_digest"].startswith("sha256:")
    assert payload["entries"][0]["sample_id"] == sample.sample_id
    assert payload["entries"][0]["requirement_spec"]["source_text"] == (sample.request_text)


def test_loader_rejects_tampered_requirement_content(
    tmp_path: Path,
) -> None:
    """修改需求内容但不更新摘要时必须拒绝回放。"""

    sample = _sample()
    dataset = _dataset(sample)
    store = create_recording_requirement_store(dataset)
    store.resolve(sample, generate=lambda: _requirement(sample.request_text))

    path = write_requirement_artifact(
        store,
        tmp_path / "requirements.json",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"][0]["requirement_spec"]["summary"] = "被篡改的需求。"
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        RequirementArtifactError,
        match="有效需求工件",
    ):
        load_requirement_artifact(path, dataset)


def test_loader_rejects_artifact_from_different_dataset(
    tmp_path: Path,
) -> None:
    """即使 sample_id 相同，分析提交改变也不能复用旧工件。"""

    sample = _sample()
    dataset = _dataset(sample)
    store = create_recording_requirement_store(dataset)
    store.resolve(sample, generate=lambda: _requirement(sample.request_text))

    path = write_requirement_artifact(
        store,
        tmp_path / "requirements.json",
    )

    changed_dataset = _dataset(
        _sample(
            analysis_commit="d" * 40,
        )
    )

    with pytest.raises(
        RequirementArtifactError,
        match="dataset_digest",
    ):
        load_requirement_artifact(path, changed_dataset)


def test_recording_store_refuses_partial_artifact(
    tmp_path: Path,
) -> None:
    """任意样本没有需求时，不得写出看似完整的工件。"""

    first = _sample()
    second = _sample(
        sample_id="git-dddddddddddd",
        request_text="增加订单支付状态检查",
        analysis_commit="c" * 40,
        answer_commit="d" * 40,
    )
    dataset = _dataset(first, second)
    store = create_recording_requirement_store(dataset)
    store.resolve(first, generate=lambda: _requirement(first.request_text))

    with pytest.raises(
        RequirementArtifactError,
        match="missing",
    ):
        write_requirement_artifact(
            store,
            tmp_path / "partial.json",
        )

    assert not (tmp_path / "partial.json").exists()


def test_recording_store_rejects_requirement_for_different_request() -> None:
    """模型结果不得伪装成另一条样本的需求。"""

    sample = _sample()
    store = create_recording_requirement_store(
        _dataset(sample),
    )

    with pytest.raises(
        ValueError,
        match="request_digest",
    ):
        store.resolve(
            sample,
            generate=lambda: _requirement("完全不同的需求"),
        )
