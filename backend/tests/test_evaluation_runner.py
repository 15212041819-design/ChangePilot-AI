"""隔离评测运行器的单元测试。"""

import subprocess
from pathlib import Path

import pytest

from app.schemas.evaluation_metrics import (
    EvaluationObservation,
)
from app.tools.evaluation_dataset import (
    build_evaluation_dataset,
)
from app.tools.evaluation_runner import (
    EvaluationCaseContext,
    EvaluationRunInvalidError,
    run_evaluation_experiment,
)
from app.tools.git_cochange import (
    CoChangeLeakReport,
)


def _git(
    repository: Path,
    *arguments: str,
) -> str:
    """在临时仓库中执行 Git 命令。"""

    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    return completed.stdout.strip()


def _commit_files(
    repository: Path,
    message: str,
    files: dict[str, str],
) -> str:
    """写入文件并创建测试提交。"""

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

    _git(
        repository,
        "add",
        ".",
    )
    _git(
        repository,
        "commit",
        "-m",
        message,
    )

    return _git(
        repository,
        "rev-parse",
        "HEAD",
    )


def _repository_with_evaluation_sample(
    tmp_path: Path,
) -> tuple[Path, str, str]:
    """创建一条满足样本筛选规则的两提交仓库。"""

    repository = tmp_path / "sample"
    repository.mkdir()

    _git(
        repository,
        "init",
    )
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

    parent = _commit_files(
        repository,
        "initialize order service repository",
        {
            "app/service.py": ("VERSION = 'before'\n"),
            "tests/test_service.py": ("def test_before():\n    assert True\n"),
        },
    )

    answer = _commit_files(
        repository,
        "implement order cancellation behavior",
        {
            "app/service.py": ("VERSION = 'after'\n"),
            "tests/test_service.py": ("def test_after():\n    assert True\n"),
        },
    )

    return repository, parent, answer


def _success_observation(
    context: EvaluationCaseContext,
) -> EvaluationObservation:
    """读取隔离快照并返回一条成功记录。"""

    content = (context.snapshot_path / "app" / "service.py").read_text(
        encoding="utf-8",
    )

    assert content == "VERSION = 'before'\n"

    return EvaluationObservation(
        sample_id=context.sample.sample_id,
        status="success",
        candidate_pool_paths=("app/service.py",),
        selected_paths=("app/service.py",),
        reported_paths=("app/service.py",),
        invalid_reported_paths=(),
        analyzed_item_count=1,
        assigned_item_count=1,
        latency_ms=10.0,
    )


def test_runner_uses_analysis_commit_snapshot_and_cleans_worktree(
    tmp_path: Path,
) -> None:
    """实验只能看到改前内容，且结束后必须清理临时 worktree。"""

    repository, parent, answer = _repository_with_evaluation_sample(tmp_path)

    dataset = build_evaluation_dataset(
        repository,
        sample_limit=1,
    )

    result = run_evaluation_experiment(
        repository,
        dataset,
        _success_observation,
        experiment_name="snapshot-test",
    )

    assert dataset.samples[0].analysis_commit == parent
    assert dataset.samples[0].answer_commit == answer
    assert result.metrics.file_recall_at_k == 1.0
    assert result.metrics.failure_rate == 0.0
    assert result.cochange_leak_reports[0].passed is True
    assert (
        _git(
            repository,
            "rev-parse",
            "HEAD",
        )
        == answer
    )
    assert (
        _git(
            repository,
            "worktree",
            "list",
            "--porcelain",
        ).count("worktree ")
        == 1
    )


def test_runner_converts_adapter_exception_to_failed_observation(
    tmp_path: Path,
) -> None:
    """单个实验适配器崩溃时应记录失败，而不是丢掉样本。"""

    repository, _, _ = _repository_with_evaluation_sample(tmp_path)

    dataset = build_evaluation_dataset(
        repository,
        sample_limit=1,
    )

    def broken_adapter(
        context: EvaluationCaseContext,
    ) -> EvaluationObservation:
        raise RuntimeError(f"cannot analyze {context.sample.sample_id}")

    result = run_evaluation_experiment(
        repository,
        dataset,
        broken_adapter,
        experiment_name="failure-test",
    )

    assert len(result.observations) == 1
    assert result.observations[0].status == "failed"
    assert "RuntimeError" in (result.observations[0].error or "")
    assert result.metrics.failed_count == 1
    assert result.metrics.failure_rate == 1.0


def test_runner_converts_wrong_sample_id_to_failure(
    tmp_path: Path,
) -> None:
    """适配器错误归属样本时必须显式失败。"""

    repository, _, _ = _repository_with_evaluation_sample(tmp_path)

    dataset = build_evaluation_dataset(
        repository,
        sample_limit=1,
    )

    def wrong_adapter(
        context: EvaluationCaseContext,
    ) -> EvaluationObservation:
        return EvaluationObservation(
            sample_id="wrong-sample",
            status="success",
            candidate_pool_paths=(),
            selected_paths=(),
            reported_paths=(),
            invalid_reported_paths=(),
            analyzed_item_count=0,
            assigned_item_count=0,
            latency_ms=1.0,
        )

    result = run_evaluation_experiment(
        repository,
        dataset,
        wrong_adapter,
        experiment_name="wrong-id-test",
    )

    assert result.observations[0].status == "failed"
    assert "错误 sample_id" in (result.observations[0].error or "")


def test_runner_invalidates_entire_run_when_cochange_leak_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共变时间泄漏属于红线，不能降级成普通失败。"""

    repository, _, _ = _repository_with_evaluation_sample(tmp_path)

    dataset = build_evaluation_dataset(
        repository,
        sample_limit=1,
    )
    adapter_called = False

    def failing_leak_check(
        *args: object,
        **kwargs: object,
    ) -> CoChangeLeakReport:
        return CoChangeLeakReport(
            passed=False,
            target_commit_matches=True,
            target_commit_excluded=False,
            checked_commit_count=1,
            unexpected_commits=(dataset.samples[0].answer_commit,),
        )

    def adapter(
        context: EvaluationCaseContext,
    ) -> EvaluationObservation:
        nonlocal adapter_called
        adapter_called = True

        return _success_observation(context)

    monkeypatch.setattr(
        ("app.tools.evaluation_runner.check_cochange_leak"),
        failing_leak_check,
    )

    with pytest.raises(
        EvaluationRunInvalidError,
        match="结果作废",
    ):
        run_evaluation_experiment(
            repository,
            dataset,
            adapter,
            experiment_name="leak-test",
        )

    assert adapter_called is False
