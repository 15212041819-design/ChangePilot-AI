"""隔离 Git 快照工具的单元测试。"""

import subprocess
from pathlib import Path

import pytest

from app.tools.git_snapshot import (
    GitReferenceNotFoundError,
    checkout_repository_snapshot,
)


def _git(
    repository: Path,
    *arguments: str,
) -> str:
    """在临时测试仓库中执行 Git 命令。"""

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


def _repository_with_two_commits(
    tmp_path: Path,
) -> tuple[Path, str, str]:
    """创建一个前后两次提交内容明显不同的临时仓库。"""

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

    source = repository / "service.py"

    source.write_text(
        "VERSION = 'old'\n",
        encoding="utf-8",
    )

    _git(
        repository,
        "add",
        "service.py",
    )
    _git(
        repository,
        "commit",
        "-m",
        "old version",
    )

    old_commit = _git(
        repository,
        "rev-parse",
        "HEAD",
    )

    source.write_text(
        "VERSION = 'new'\n",
        encoding="utf-8",
    )

    _git(
        repository,
        "add",
        "service.py",
    )
    _git(
        repository,
        "commit",
        "-m",
        "new version",
    )

    new_commit = _git(
        repository,
        "rev-parse",
        "HEAD",
    )

    return (
        repository,
        old_commit,
        new_commit,
    )


def test_checkout_reads_historical_content_without_switching_current_tree(
    tmp_path: Path,
) -> None:
    """历史分析不得 checkout 或改写用户当前工作区。"""

    (
        repository,
        old_commit,
        new_commit,
    ) = _repository_with_two_commits(tmp_path)

    original_branch = _git(
        repository,
        "branch",
        "--show-current",
    )

    temporary_path: Path | None = None

    with checkout_repository_snapshot(
        repository,
        old_commit,
    ) as snapshot:
        temporary_path = snapshot.repository_path

        assert snapshot.resolved_commit == old_commit
        assert snapshot.repository_path != repository

        assert (snapshot.repository_path / "service.py").read_text(
            encoding="utf-8"
        ) == "VERSION = 'old'\n"

        # 主工作区仍停留在新提交和原分支上。
        assert (
            _git(
                repository,
                "rev-parse",
                "HEAD",
            )
            == new_commit
        )

        assert (
            _git(
                repository,
                "branch",
                "--show-current",
            )
            == original_branch
        )

        assert (repository / "service.py").read_text(encoding="utf-8") == "VERSION = 'new'\n"

    assert temporary_path is not None
    assert not temporary_path.exists()

    assert (
        _git(
            repository,
            "worktree",
            "list",
            "--porcelain",
        ).count("worktree ")
        == 1
    )


def test_checkout_preserves_repository_subdirectory_inside_monorepo(
    tmp_path: Path,
) -> None:
    """目标仓库是 monorepo 子目录时不得扩大扫描边界。"""

    root = tmp_path / "monorepo"

    service = root / "sample_repos" / "order_service"
    service.mkdir(parents=True)

    _git(
        root,
        "init",
    )
    _git(
        root,
        "config",
        "user.email",
        "tests@example.com",
    )
    _git(
        root,
        "config",
        "user.name",
        "ChangePilot Tests",
    )

    (service / "service.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    _git(
        root,
        "add",
        ".",
    )
    _git(
        root,
        "commit",
        "-m",
        "add service",
    )

    with checkout_repository_snapshot(
        service,
        "HEAD",
    ) as snapshot:
        assert snapshot.repository_relative_path == "sample_repos/order_service"

        assert snapshot.repository_path.name == "order_service"

        assert (snapshot.repository_path / "service.py").is_file()


def test_checkout_rejects_unknown_git_reference(
    tmp_path: Path,
) -> None:
    """不存在的 ref 必须在创建 worktree 前失败。"""

    (
        repository,
        _,
        _,
    ) = _repository_with_two_commits(tmp_path)

    with pytest.raises(
        GitReferenceNotFoundError,
        match="不存在",
    ):
        with checkout_repository_snapshot(
            repository,
            "missing-ref",
        ):
            pytest.fail("不存在的 Git ref 意外创建了快照。")
