"""评测 CLI 的 CandidatePool 截断口径回归测试。

这个文件专门锁定 ``--pool-limit`` 的双重语义：

1. 它控制检索阶段最多保留多少个候选文件；
2. 它也必须控制 ``pool_recall@k`` 中的 k。

如果只完成第 1 点，执行 ``--pool-limit 60`` 后仍会显示
``pool_recall@50``，报告口径便会与实际候选池大小不一致。
"""

from pathlib import Path
from types import SimpleNamespace

import app.cli.evaluate as evaluate_module
from app.cli.evaluate import run_evaluation_cli


def test_cli_passes_pool_limit_to_evaluation_metric_cutoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """CLI 应把 pool-limit 原样传给评测运行器的 pool_k。"""

    repository = tmp_path / "repository"
    repository.mkdir()

    output_path = tmp_path / "result.json"
    dataset = SimpleNamespace(
        samples=(object(),),
    )
    expected_result = object()
    captured_arguments: dict[str, object] = {}

    monkeypatch.setattr(
        evaluate_module,
        "build_evaluation_dataset",
        lambda *args, **kwargs: dataset,
    )

    monkeypatch.setattr(
        evaluate_module,
        "create_evidence_impact_adapter",
        lambda **kwargs: object(),
    )

    def fake_run_evaluation_experiment(
        repository_path,
        received_dataset,
        adapter,
        **kwargs,
    ):
        captured_arguments.update(kwargs)
        return expected_result

    monkeypatch.setattr(
        evaluate_module,
        "run_evaluation_experiment",
        fake_run_evaluation_experiment,
    )

    monkeypatch.setattr(
        evaluate_module,
        "write_evaluation_result",
        lambda result, path: Path(path).resolve(),
    )

    result, resolved_output = run_evaluation_cli(
        (
            str(repository),
            "--samples",
            "1",
            "--pool-limit",
            "60",
            "--output",
            str(output_path),
        )
    )

    assert result is expected_result
    assert resolved_output == output_path.resolve()
    assert captured_arguments["pool_k"] == 60
