"""使用隔离的 Git worktree 创建不可变仓库快照。

分析器不得切换用户当前分支，也不得从当前工作区冒充读取历史提交。
本模块把请求的 ref 解析为精确 commit SHA，创建临时脱离式 worktree，
并在分析结束后将它清理。
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


class GitSnapshotError(RuntimeError):
    """无法安全创建仓库快照。"""


class GitReferenceNotFoundError(FileNotFoundError):
    """请求的 commit、分支或 tag 不存在。"""


@dataclass(frozen=True)
class GitSnapshotCheckout:
    """一次脱离式、约定只读的快照签出信息。"""

    repository_path: Path
    git_root: Path
    repository_relative_path: str
    requested_ref: str
    resolved_commit: str


def _run_git(
    working_directory: Path,
    arguments: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """不经过 shell 执行 Git，防止不可信 ref 变成命令。"""

    try:
        completed = subprocess.run(
            ["git", "-C", str(working_directory), *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise GitSnapshotError("当前环境无法执行 Git。") from exc

    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()

        raise GitSnapshotError(detail or "Git 命令执行失败。")

    return completed


def _find_git_root(
    repository_path: Path,
) -> Path:
    """查找所属 Git 根目录，同时支持 monorepo 子目录。"""

    completed = _run_git(
        repository_path,
        (
            "rev-parse",
            "--show-toplevel",
        ),
        check=False,
    )

    if completed.returncode != 0:
        raise GitSnapshotError(f"仓库不在 Git worktree 中：{repository_path}")

    root_text = completed.stdout.strip()

    if not root_text:
        raise GitSnapshotError("Git 返回了空仓库根目录。")

    return Path(root_text).resolve()


def _resolve_commit(
    git_root: Path,
    requested_ref: str,
) -> str:
    """把 ref 解析为精确 commit SHA，并阻止选项注入。"""

    normalized_ref = requested_ref.strip()

    if not normalized_ref:
        raise GitReferenceNotFoundError("Git commit、分支或 tag 不能为空。")

    completed = _run_git(
        git_root,
        (
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{normalized_ref}^{{commit}}",
        ),
        check=False,
    )

    if completed.returncode != 0:
        raise GitReferenceNotFoundError(f"Git ref 不存在：{normalized_ref}")

    resolved_commit = completed.stdout.strip().lower()

    if len(resolved_commit) != 40 or any(
        character not in "0123456789abcdef" for character in resolved_commit
    ):
        raise GitSnapshotError("Git 返回了非法 commit SHA。")

    return resolved_commit


@contextmanager
def checkout_repository_snapshot(
    repository_path: str | Path,
    requested_ref: str,
) -> Iterator[GitSnapshotCheckout]:
    """从临时脱离式 worktree 中返回请求的仓库目录。

    传入目录可以是 Git 根目录，也可以是 monorepo 中的子目录。
    如果是后者，会在临时快照中继续选择相同的相对子目录。
    """

    resolved_repository = Path(repository_path).expanduser().resolve()

    if not resolved_repository.exists():
        raise FileNotFoundError(f"仓库不存在：{resolved_repository}")

    if not resolved_repository.is_dir():
        raise NotADirectoryError(f"仓库路径不是目录：{resolved_repository}")

    git_root = _find_git_root(resolved_repository)

    if not resolved_repository.is_relative_to(git_root):
        raise GitSnapshotError("仓库路径位于解析出的 Git 根目录之外。")

    repository_relative = resolved_repository.relative_to(git_root)
    repository_relative_path = repository_relative.as_posix()

    resolved_commit = _resolve_commit(
        git_root,
        requested_ref,
    )

    with TemporaryDirectory(prefix="changepilot-git-snapshot-") as temporary_directory:
        # 保留 Git 根目录名称。当分析对象就是根目录时，
        # 仓库扫描器仍然可以返回它的真实名称。
        checkout_root = Path(temporary_directory) / git_root.name

        _run_git(
            git_root,
            (
                "worktree",
                "add",
                "--detach",
                str(checkout_root),
                resolved_commit,
            ),
        )

        snapshot_repository = (checkout_root / repository_relative).resolve()

        try:
            if not snapshot_repository.exists():
                raise GitReferenceNotFoundError(
                    f"仓库子目录在 commit "
                    f"{resolved_commit} 中不存在："
                    f"{repository_relative_path or '.'}"
                )

            if not snapshot_repository.is_dir():
                raise NotADirectoryError(
                    f"仓库路径在 commit "
                    f"{resolved_commit} 中不是目录："
                    f"{repository_relative_path or '.'}"
                )

            yield GitSnapshotCheckout(
                repository_path=snapshot_repository,
                git_root=git_root,
                repository_relative_path=(repository_relative_path),
                requested_ref=requested_ref.strip(),
                resolved_commit=resolved_commit,
            )

        finally:
            # 清理错误不应覆盖分析器已经抛出的异常。
            # Git prune 只会移除失效的管理记录，
            # 不会删除用户仓库文件。
            _run_git(
                git_root,
                (
                    "worktree",
                    "remove",
                    "--force",
                    str(checkout_root),
                ),
                check=False,
            )

            _run_git(
                git_root,
                (
                    "worktree",
                    "prune",
                ),
                check=False,
            )
