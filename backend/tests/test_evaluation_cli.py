"""历史提交评测 CLI 的端到端测试。"""

import json
import subprocess
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.cli.evaluate import (
    main,
    run_evaluation_cli,
)


def _git(repository: Path, *arguments: str) -> str:
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
    return _git(
        repository,
        "rev-parse",
        "HEAD",
    )


def _repository(tmp_path: Path) -> Path:
    """创建一条可被评测器选中的历史样本。"""

    repository = tmp_path / "order_service"
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

    _commit_files(
        repository,
        "initialize order cancellation service",
        {
            "order_app/service.py": (
                "class OrderService:\n"
                "    def cancel_order("
                "self, order_id: str"
                ") -> None:\n"
                "        pass\n"
            ),
            "tests/test_service.py": (
                "from order_app.service "
                "import OrderService\n\n"
                "def test_cancel_order() -> None:\n"
                "    OrderService().cancel_order('1')\n"
            ),
        },
    )

    _commit_files(
        repository,
        "improve order cancellation behavior and coverage",
        {
            "order_app/service.py": (
                "class OrderService:\n"
                "    def cancel_order("
                "self, order_id: str"
                ") -> bool:\n"
                "        return True\n"
            ),
            "tests/test_service.py": (
                "from order_app.service "
                "import OrderService\n\n"
                "def test_cancel_order() -> None:\n"
                "    assert "
                "OrderService().cancel_order('1') "
                "is True\n"
            ),
        },
    )

    return repository


def _model_response() -> str:
    """生成能命中快照真实符号的需求草稿。"""

    return """
    {
        "summary": "改进订单取消行为和测试覆盖。",
        "change_type": "feature",
        "acceptance_criteria": [
            {
                "description": "订单取消操作返回明确结果。",
                "verification": "test",
                "priority": "P0"
            }
        ],
        "entity_hints": [
            {
                "concept": "订单取消",
                "lexical_variants": [
                    "order cancellation"
                ],
                "symbol_candidates": [
                    "cancel_order"
                ],
                "role": "action_target"
            }
        ],
        "constraints": [],
        "assumptions": []
    }
    """


def test_cli_runs_real_adapter_and_writes_versioned_json(
    tmp_path: Path,
) -> None:
    """CLI 应完成真实链路并保存可审计 JSON。"""

    repository = _repository(tmp_path)
    output = tmp_path / "results" / "report.json"

    result, written_path = run_evaluation_cli(
        (
            str(repository),
            "--samples",
            "1",
            "--output",
            str(output),
            "--experiment-name",
            "integration-test",
        ),
        model=FakeListChatModel(responses=[_model_response()]),
    )

    assert written_path == output.resolve()
    assert result.metrics.sample_count == 1
    assert result.metrics.failure_rate == 0.0
    assert result.metrics.pool_recall_at_k == 1.0
    assert result.metrics.file_recall_at_k == 1.0
    assert result.metrics.hallucination_rate == 0.0

    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.0"
    assert payload["experiment_name"] == "integration-test"
    assert (
        payload["dataset"]["samples"][0]["analysis_commit"]
        != payload["dataset"]["samples"][0]["answer_commit"]
    )
    assert payload["cochange_leak_reports"][0]["passed"] is True
    assert payload["metrics"]["file_recall_at_k"] == 1.0


def test_cli_rejects_non_json_output_before_running_model(
    tmp_path: Path,
) -> None:
    """报告扩展名错误时不得调用模型或创建文件。"""

    repository = _repository(tmp_path)
    output = tmp_path / "report.txt"

    with pytest.raises(
        ValueError,
        match=".json",
    ):
        run_evaluation_cli(
            (
                str(repository),
                "--output",
                str(output),
            ),
            model=FakeListChatModel(responses=[]),
        )

    assert not output.exists()


def test_main_returns_failure_code_for_invalid_arguments(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """普通配置错误必须返回退出码 1，方便 CI 阻断。"""

    repository = _repository(tmp_path)

    exit_code = main(
        (
            str(repository),
            "--samples",
            "0",
        )
    )

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "evaluation failed" in captured.err


def test_cli_rejects_history_without_eligible_sample(
    tmp_path: Path,
) -> None:
    """没有合格样本时不能生成空报告冒充成功。"""

    repository = tmp_path / "empty_history"
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

    _commit_files(
        repository,
        "short",
        {
            "only.py": "VALUE = 1\n",
        },
    )

    with pytest.raises(
        ValueError,
        match="没有筛选到",
    ):
        run_evaluation_cli(
            (str(repository),),
            model=FakeListChatModel(responses=[]),
        )
