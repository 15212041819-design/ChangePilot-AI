"""Git 历史评测样本提取器的单元测试。"""

import subprocess
from pathlib import Path

import pytest

from app.schemas.evaluation import EvaluationSample
from app.tools.evaluation_dataset import (
    EvaluationDatasetError,
    build_evaluation_dataset,
    is_test_path,
)


def _git(repository: Path, *arguments: str) -> str:
    """在临时仓库中执行 Git 命令。"""

    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
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
    """写入一组文件并创建提交。"""

    for relative_path, content in files.items():
        target = repository / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    _git(repository, "add", ".")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _new_repository(tmp_path: Path) -> Path:
    """创建带测试身份配置的临时仓库。"""

    repository = tmp_path / "sample"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "tests@example.com")
    _git(repository, "config", "user.name", "ChangePilot Tests")
    return repository


def test_dataset_uses_parent_as_input_and_commit_diff_as_answer(
    tmp_path: Path,
) -> None:
    """分析输入必须是 commit^，答案必须来自目标 commit 的 diff。"""

    repository = _new_repository(tmp_path)
    parent = _commit_files(
        repository,
        "initialize sample repository",
        {"app/service.py": "VALUE = 1\n"},
    )
    answer = _commit_files(
        repository,
        "add cancellation behavior and regression coverage",
        {
            "app/service.py": "VALUE = 2\n",
            "tests/test_service.py": "def test_value():\n    assert True\n",
        },
    )

    dataset = build_evaluation_dataset(repository, sample_limit=1)

    assert dataset.accepted_sample_count == 1
    sample = dataset.samples[0]
    assert sample.analysis_commit == parent
    assert sample.answer_commit == answer
    assert sample.changed_files == (
        "app/service.py",
        "tests/test_service.py",
    )
    assert sample.source_files == ("app/service.py",)
    assert sample.test_files == ("tests/test_service.py",)
    assert sample.request_text == "add cancellation behavior and regression coverage"


def test_dataset_filters_short_pure_test_and_large_commits(
    tmp_path: Path,
) -> None:
    """冻结筛选规则，防止实验时临时挑选有利样本。"""

    repository = _new_repository(tmp_path)
    _commit_files(
        repository,
        "initialize repository files",
        {"app/base.py": "BASE = 1\n"},
    )
    _commit_files(
        repository,
        "short",
        {
            "app/one.py": "ONE = 1\n",
            "app/two.py": "TWO = 2\n",
        },
    )
    _commit_files(
        repository,
        "expand only automated regression test coverage",
        {
            "tests/test_one.py": "def test_one():\n    assert True\n",
            "tests/test_two.py": "def test_two():\n    assert True\n",
        },
    )
    _commit_files(
        repository,
        "perform broad generated source update",
        {f"app/generated_{index}.py": f"VALUE = {index}\n" for index in range(9)},
    )
    eligible = _commit_files(
        repository,
        "implement final eligible behavior change",
        {
            "app/final.py": "FINAL = True\n",
            "tests/test_final.py": "def test_final():\n    assert True\n",
        },
    )

    dataset = build_evaluation_dataset(repository, sample_limit=10)

    assert tuple(sample.answer_commit for sample in dataset.samples) == (eligible,)
    assert dataset.rejected_short_message_count == 1
    assert dataset.rejected_pure_test_count == 1
    assert dataset.rejected_file_count_count == 1
    assert dataset.rejected_root_count == 1
    assert dataset.scanned_commit_count == 5


def test_dataset_rejects_merge_commits(tmp_path: Path) -> None:
    """合并提交不能成为样本，避免把整条分支误当成一次需求。"""

    repository = _new_repository(tmp_path)
    _commit_files(
        repository,
        "initialize repository state",
        {"base.py": "BASE = 1\n"},
    )
    main_branch = _git(repository, "branch", "--show-current")

    _git(repository, "checkout", "-b", "feature")
    _commit_files(
        repository,
        "implement isolated feature branch change",
        {
            "feature.py": "FEATURE = 1\n",
            "feature_test.py": "VALUE = 1\n",
        },
    )

    _git(repository, "checkout", main_branch)
    _commit_files(
        repository,
        "implement independent main branch change",
        {
            "main.py": "MAIN = 1\n",
            "main_test.py": "VALUE = 1\n",
        },
    )
    _git(
        repository,
        "merge",
        "--no-ff",
        "feature",
        "-m",
        "merge completed feature branch",
    )

    dataset = build_evaluation_dataset(repository, sample_limit=10)

    assert dataset.rejected_merge_count == 1
    assert all(len(sample.answer_commit) == 40 for sample in dataset.samples)


def test_dataset_preserves_monorepo_subdirectory_boundary(
    tmp_path: Path,
) -> None:
    """评测目标是子项目时，答案不得混入 monorepo 其他目录。"""

    root = _new_repository(tmp_path)
    service = root / "services" / "order"
    _commit_files(
        root,
        "initialize monorepo service layout",
        {
            "services/order/base.py": "BASE = 1\n",
            "services/other/base.py": "BASE = 1\n",
        },
    )
    _commit_files(
        root,
        "add order cancellation and unrelated change",
        {
            "services/order/service.py": "VALUE = 1\n",
            "services/order/tests/test_service.py": ("def test_it():\n    assert True\n"),
            "services/other/unrelated.py": "OTHER = 1\n",
        },
    )

    dataset = build_evaluation_dataset(
        service,
        sample_limit=1,
    )

    assert dataset.repository_relative_path == "services/order"
    assert dataset.samples[0].changed_files == (
        "service.py",
        "tests/test_service.py",
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_service.py", True),
        ("src/service_test.py", True),
        ("web/order.spec.ts", True),
        ("web/order.test.tsx", True),
        ("app/service.py", False),
    ],
)
def test_test_path_classification(
    path: str,
    expected: bool,
) -> None:
    """测试文件识别同时覆盖 Python 和常见前端命名。"""

    assert is_test_path(path) is expected


def test_evaluation_sample_rejects_leaked_same_commit() -> None:
    """输入 SHA 和答案 SHA 相同时必须立即拒绝。"""

    commit = "a" * 40

    with pytest.raises(ValueError, match="不能等于"):
        EvaluationSample(
            sample_id="git-aaaaaaaaaaaa",
            repository_relative_path="",
            request_text="implement a meaningful behavior change",
            analysis_commit=commit,
            answer_commit=commit,
            changed_files=("app/service.py",),
            source_files=("app/service.py",),
            test_files=(),
        )


def test_dataset_rejects_unknown_reference(tmp_path: Path) -> None:
    """不存在的评测 ref 必须返回明确错误。"""

    repository = _new_repository(tmp_path)
    _commit_files(
        repository,
        "initialize repository state",
        {"base.py": "BASE = 1\n"},
    )

    with pytest.raises(
        EvaluationDatasetError,
        match="不存在",
    ):
        build_evaluation_dataset(
            repository,
            "missing-ref",
        )
