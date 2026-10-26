"""Git 共变索引和时间泄漏自检测试。"""

import subprocess
from dataclasses import replace
from pathlib import Path

from app.tools.git_cochange import (
    build_cochange_index,
    check_cochange_leak,
    find_cochange_neighbors,
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


def _write(
    repository: Path,
    path: str,
    content: str,
) -> None:
    """写入一个测试文件并自动创建父目录。"""

    target = repository / path

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    target.write_text(
        content,
        encoding="utf-8",
    )


def _commit(
    repository: Path,
    message: str,
    changes: dict[str, str],
) -> str:
    """写入一组文件并创建提交。"""

    for path, content in changes.items():
        _write(
            repository,
            path,
            content,
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


def _history_repository(
    tmp_path: Path,
) -> tuple[
    Path,
    str,
    str,
    str,
    str,
]:
    """创建包含目标提交和未来提交的历史。"""

    repository = tmp_path / "repository"
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

    first = _commit(
        repository,
        "change a and b",
        {
            "app/a.py": "A = 1\n",
            "app/b.py": "B = 1\n",
        },
    )

    second = _commit(
        repository,
        "change a and c",
        {
            "app/a.py": "A = 2\n",
            "app/c.py": "C = 1\n",
        },
    )

    target = _commit(
        repository,
        "target change",
        {
            "app/target.py": ("TARGET = True\n"),
            "app/a.py": "A = 3\n",
        },
    )

    future = _commit(
        repository,
        "future change b and c",
        {
            "app/b.py": "B = 2\n",
            "app/c.py": "C = 2\n",
        },
    )

    return (
        repository,
        first,
        second,
        target,
        future,
    )


def test_index_uses_only_history_before_target_commit(
    tmp_path: Path,
) -> None:
    """目标 commit 和它之后的提交不得进入共变特征。"""

    (
        repository,
        first,
        second,
        target,
        future,
    ) = _history_repository(tmp_path)

    index = build_cochange_index(
        repository,
        target,
    )

    assert index.target_commit == target
    assert index.history_commits == (
        second,
        first,
    )
    assert target not in index.history_commits
    assert future not in index.history_commits

    assert (
        check_cochange_leak(
            repository,
            index,
        ).passed
        is True
    )


def test_index_builds_normalized_pair_scores_and_neighbors(
    tmp_path: Path,
) -> None:
    """文件计数、共变计数和邻居排序必须稳定。"""

    (
        repository,
        _,
        _,
        target,
        _,
    ) = _history_repository(tmp_path)

    index = build_cochange_index(
        repository,
        target,
    )

    pairs = {
        (
            pair.first_path,
            pair.second_path,
        ): pair
        for pair in index.pairs
    }

    pair_ab = pairs[
        (
            "app/a.py",
            "app/b.py",
        )
    ]

    pair_ac = pairs[
        (
            "app/a.py",
            "app/c.py",
        )
    ]

    assert pair_ab.shared_commit_count == 1
    assert pair_ab.first_commit_count == 2
    assert pair_ab.second_commit_count == 1
    assert pair_ab.score == pair_ac.score

    neighbors = find_cochange_neighbors(
        index,
        "app/a.py",
    )

    assert tuple(path for path, _ in neighbors) == (
        "app/b.py",
        "app/c.py",
    )


def test_index_respects_monorepo_subdirectory_scope(
    tmp_path: Path,
) -> None:
    """对 monorepo 子仓库建索引时不得混入外部文件。"""

    root = tmp_path / "monorepo"

    service = root / "services" / "orders"
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

    _commit(
        root,
        "initial",
        {
            "services/orders/a.py": ("A = 1\n"),
            "services/orders/b.py": ("B = 1\n"),
            "services/payments/external.py": ("EXTERNAL = 1\n"),
        },
    )

    target = _commit(
        root,
        "target",
        {
            "services/orders/target.py": ("TARGET = True\n"),
            "services/payments/future.py": ("FUTURE = True\n"),
        },
    )

    index = build_cochange_index(
        service,
        target,
    )

    assert index.repository_relative_path == "services/orders"

    assert tuple(path for path, _ in index.file_commit_counts) == (
        "a.py",
        "b.py",
    )

    assert all("payments" not in pair.first_path for pair in index.pairs)

    assert all("payments" not in pair.second_path for pair in index.pairs)


def test_large_commits_are_excluded_from_pair_statistics(
    tmp_path: Path,
) -> None:
    """超过文件上限的大提交不能制造大量虚假共变。"""

    repository = tmp_path / "large"
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

    _commit(
        repository,
        "large history",
        {
            "a.py": "A = 1\n",
            "b.py": "B = 1\n",
            "c.py": "C = 1\n",
        },
    )

    target = _commit(
        repository,
        "target",
        {"target.py": ("TARGET = True\n")},
    )

    index = build_cochange_index(
        repository,
        target,
        max_files_per_commit=2,
    )

    assert index.skipped_large_commit_count == 1
    assert index.analyzed_commit_count == 0
    assert index.pairs == ()


def test_leak_check_rejects_injected_future_commit(
    tmp_path: Path,
) -> None:
    """自检必须拦截被篡改后混入的未来提交。"""

    (
        repository,
        _,
        _,
        target,
        future,
    ) = _history_repository(tmp_path)

    index = build_cochange_index(
        repository,
        target,
    )

    # 使用 dataclasses.replace 模拟一份
    # 被错误加入未来提交的共变索引。
    tampered = replace(
        index,
        history_commits=(
            *index.history_commits,
            future,
        ),
    )

    report = check_cochange_leak(
        repository,
        tampered,
    )

    assert report.passed is False
    assert report.unexpected_commits == (future,)


def test_root_commit_has_empty_but_valid_history(
    tmp_path: Path,
) -> None:
    """根提交没有父历史，应返回空索引而不是报错。"""

    repository = tmp_path / "root"
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

    root_commit = _commit(
        repository,
        "root",
        {
            "a.py": "A = 1\n",
            "b.py": "B = 1\n",
        },
    )

    index = build_cochange_index(
        repository,
        root_commit,
    )

    assert index.history_commits == ()
    assert index.available_commit_count == 0
    assert index.pairs == ()

    assert (
        check_cochange_leak(
            repository,
            index,
        ).passed
        is True
    )
