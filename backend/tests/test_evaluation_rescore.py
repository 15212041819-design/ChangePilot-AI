"""历史评测报告重新计分工具的端到端测试。"""

import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from app.cli.rescore import main, run_rescore_cli
from app.schemas.evaluation_metrics import EvaluationObservation
from app.tools.evaluation_dataset import build_evaluation_dataset
from app.tools.evaluation_rescore import (
    EvaluationRescoreError,
    rescore_evaluation_report,
    write_rescored_report,
)


def _git(repository: Path, *arguments: str) -> str:
    """在临时仓库中执行 Git。"""

    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _commit(
    repository: Path,
    message: str,
    files: dict[str, str],
) -> str:
    """写入文件并创建提交。"""

    for relative_path, content in files.items():
        target = repository / relative_path
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        target.write_text(
            content,
            encoding="utf-8",
        )

    _git(repository, "add", ".")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _repository(tmp_path: Path) -> Path:
    """创建同时包含既有文件修改和新增文件的历史样本。"""

    repository = tmp_path / "repository"
    repository.mkdir()

    _git(repository, "init")
    _git(
        repository,
        "config",
        "user.email",
        "tests@example.com",
    )
    _git(
        repository,
        "config",
        "user.name",
        "ChangePilot Tests",
    )

    _commit(
        repository,
        "initialize existing order service module",
        {
            "app/service.py": "VALUE = 1\n",
        },
    )
    _commit(
        repository,
        "extend service and add audit module coverage",
        {
            "app/service.py": "VALUE = 2\n",
            "app/audit.py": "AUDIT = True\n",
            "tests/test_service.py": ("def test_service():\n    assert True\n"),
        },
    )
    return repository


def _old_report(
    repository: Path,
    report_path: Path,
) -> None:
    """生成模拟 1.0 报告，其中尚无 existing/new 分类。"""

    dataset = build_evaluation_dataset(
        repository,
        sample_limit=1,
    )
    sample = dataset.samples[0]
    dataset_payload = asdict(dataset)

    for item in dataset_payload["samples"]:
        item.pop("existing_source_files")
        item.pop("new_source_files")

    observation = EvaluationObservation(
        sample_id=sample.sample_id,
        status="success",
        candidate_pool_paths=("app/service.py",),
        selected_paths=("app/service.py",),
        reported_paths=(),
        invalid_reported_paths=(),
        analyzed_item_count=1,
        assigned_item_count=1,
        latency_ms=12.5,
    )
    payload = {
        "schema_version": "1.0",
        "experiment_name": "original-experiment",
        "dataset": dataset_payload,
        "observations": [asdict(observation)],
        "cochange_leak_reports": [],
        "metrics": {
            "pool_k": 50,
            "file_k": 12,
            "pool_recall_at_k": 0.5,
            "file_recall_at_k": 0.5,
        },
    }
    report_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_rescore_reuses_observations_and_corrects_new_file_denominator(
    tmp_path: Path,
) -> None:
    """重新计分不得运行模型，并应排除新增文件的检索惩罚。"""

    repository = _repository(tmp_path)
    report_path = tmp_path / "old.json"
    _old_report(repository, report_path)

    payload = rescore_evaluation_report(
        report_path,
        repository,
    )

    assert payload["schema_version"] == "1.1"
    assert payload["report_kind"] == "rescored"
    assert payload["previous_metrics"]["pool_recall_at_k"] == 0.5
    assert payload["metrics"]["pool_recall_at_k"] == 1.0
    assert payload["metrics"]["file_recall_at_k"] == 1.0
    assert payload["metrics"]["existing_source_file_count"] == 1
    assert payload["metrics"]["new_source_file_count"] == 1
    assert payload["metrics"]["hallucination_evaluable"] is False
    assert payload["observations"][0]["latency_ms"] == 12.5


def test_rescore_cli_writes_new_file_without_overwriting_source(
    tmp_path: Path,
) -> None:
    """CLI 应写入独立报告并保留原始 JSON。"""

    repository = _repository(tmp_path)
    report_path = tmp_path / "old.json"
    output_path = tmp_path / "new.json"
    _old_report(repository, report_path)
    original = report_path.read_text(encoding="utf-8")

    payload, written = run_rescore_cli(
        (
            str(report_path),
            "--repository",
            str(repository),
            "--output",
            str(output_path),
        )
    )

    assert written == output_path.resolve()
    assert report_path.read_text(encoding="utf-8") == original
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload


def test_rescore_rejects_mismatched_sample_identity(
    tmp_path: Path,
) -> None:
    """报告样本与仓库历史不一致时必须停止。"""

    repository = _repository(tmp_path)
    report_path = tmp_path / "old.json"
    _old_report(repository, report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["dataset"]["samples"][0]["request_text"] = "tampered request"
    report_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(
        EvaluationRescoreError,
        match="request_text",
    ):
        rescore_evaluation_report(
            report_path,
            repository,
        )


def test_rescore_preserves_candidate_score_traces(
    tmp_path: Path,
) -> None:
    """新版报告的候选评分证据必须穿过离线重算。"""

    repository = _repository(tmp_path)
    report_path = tmp_path / "old.json"
    _old_report(repository, report_path)

    source = json.loads(report_path.read_text(encoding="utf-8"))
    source["observations"][0]["candidate_score_traces"] = [
        {
            "path": "app/service.py",
            "pool_rank": 1,
            "total_score": 3.5,
            "signals": [
                {
                    "name": "keyword",
                    "value": 1.5,
                },
                {
                    "name": "symbol",
                    "value": 2.0,
                },
            ],
        }
    ]
    report_path.write_text(
        json.dumps(source),
        encoding="utf-8",
    )

    payload = rescore_evaluation_report(
        report_path,
        repository,
    )
    traces = payload["observations"][0]["candidate_score_traces"]

    assert traces == source["observations"][0]["candidate_score_traces"]


def test_writer_and_main_reject_overwriting_original_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """重新计分不能破坏唯一的原始实验记录。"""

    repository = _repository(tmp_path)
    report_path = tmp_path / "old.json"
    _old_report(repository, report_path)

    payload = rescore_evaluation_report(
        report_path,
        repository,
    )

    with pytest.raises(
        ValueError,
        match="不能覆盖",
    ):
        write_rescored_report(
            payload,
            report_path,
            source_path=report_path,
        )

    exit_code = main(
        (
            str(report_path),
            "--repository",
            str(repository),
            "--output",
            str(report_path),
        )
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "rescore failed" in captured.err
