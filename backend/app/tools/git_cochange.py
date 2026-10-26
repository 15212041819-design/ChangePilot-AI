"""基于目标 commit 之前的 Git 历史构建文件共变索引。

共变信号只用于补充候选召回，不代表两个文件在语义上必然相关。
为防止评测泄漏，本模块不读取目标 commit 本身，也不读取它之后的提交。
"""

from __future__ import annotations

import hashlib
import math
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path, PurePosixPath

DEFAULT_HISTORY_LIMIT = 200
DEFAULT_MAX_FILES_PER_COMMIT = 50
DEFAULT_MAX_PAIRS = 5_000


class CoChangeError(RuntimeError):
    """共变历史无法安全读取或解析。"""


@dataclass(frozen=True)
class CoChangePair:
    """两个文件在目标时点之前的共同变更证据。"""

    first_path: str
    second_path: str
    shared_commit_count: int
    first_commit_count: int
    second_commit_count: int
    score: float

    def other_path(
        self,
        path: str,
    ) -> str | None:
        """给定其中一个路径，返回另一个路径。"""

        normalized = _normalize_path(path)

        if normalized == self.first_path:
            return self.second_path

        if normalized == self.second_path:
            return self.first_path

        return None


@dataclass(frozen=True)
class CoChangeIndex:
    """一个目标 commit 对应的只读共变索引。"""

    repository_relative_path: str
    target_commit: str
    history_commits: tuple[str, ...]
    available_commit_count: int
    analyzed_commit_count: int
    skipped_merge_commit_count: int
    skipped_large_commit_count: int
    file_commit_counts: tuple[
        tuple[str, int],
        ...,
    ]
    pairs: tuple[
        CoChangePair,
        ...,
    ]
    history_truncated: bool
    pairs_truncated: bool
    history_digest: str


@dataclass(frozen=True)
class CoChangeLeakReport:
    """共变特征时间泄漏自检结果。"""

    passed: bool
    target_commit_matches: bool
    target_commit_excluded: bool
    checked_commit_count: int
    unexpected_commits: tuple[str, ...]


def _run_git(
    working_directory: Path,
    arguments: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """不经过 shell 执行 Git，并以 UTF-8 读取输出。"""

    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(working_directory),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise CoChangeError("当前环境无法执行 Git。") from exc

    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()

        raise CoChangeError(detail or "Git 命令执行失败。")

    return completed


def _normalize_path(
    path: str,
) -> str:
    """把 Git 路径统一为无前导斜杠的 POSIX 路径。"""

    normalized = PurePosixPath(path.replace("\\", "/")).as_posix().strip("/")

    if normalized in {
        "",
        ".",
    }:
        return ""

    return normalized


def _find_git_root(
    repository_path: Path,
) -> Path:
    """解析目标目录所属的 Git 根目录。"""

    completed = _run_git(
        repository_path,
        (
            "rev-parse",
            "--show-toplevel",
        ),
        check=False,
    )

    if completed.returncode != 0:
        raise CoChangeError(f"仓库不在 Git worktree 中：{repository_path}")

    root_text = completed.stdout.strip()

    if not root_text:
        raise CoChangeError("Git 返回了空仓库根目录。")

    return Path(root_text).resolve()


def _resolve_commit(
    git_root: Path,
    requested_ref: str,
) -> str:
    """把用户提供的 ref 解析为精确 commit SHA。"""

    normalized_ref = requested_ref.strip()

    if not normalized_ref:
        raise CoChangeError("Git commit、分支或 tag 不能为空。")

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
        raise CoChangeError(f"Git ref 不存在：{normalized_ref}")

    resolved = completed.stdout.strip().lower()

    if len(resolved) != 40 or any(character not in "0123456789abcdef" for character in resolved):
        raise CoChangeError("Git 返回了非法 commit SHA。")

    return resolved


def _target_parents(
    git_root: Path,
    target_commit: str,
) -> tuple[str, ...]:
    """返回目标 commit 的全部父提交，用它们界定时间边界。"""

    line = _run_git(
        git_root,
        (
            "rev-list",
            "--parents",
            "-n",
            "1",
            target_commit,
        ),
    ).stdout.strip()

    parts = tuple(part for part in line.split() if part)

    if not parts or parts[0].lower() != target_commit:
        raise CoChangeError("无法读取目标 commit 的父提交。")

    return tuple(part.lower() for part in parts[1:])


def _repository_scope(
    repository_path: Path,
    git_root: Path,
) -> tuple[
    str,
    tuple[str, ...],
]:
    """生成 monorepo 子目录对应的 Git pathspec。"""

    if not repository_path.is_relative_to(git_root):
        raise CoChangeError("仓库路径位于 Git 根目录之外。")

    relative = _normalize_path(repository_path.relative_to(git_root).as_posix())

    if not relative:
        return "", ()

    return (
        relative,
        (
            "--",
            relative,
        ),
    )


def _history_rows(
    git_root: Path,
    parents: tuple[str, ...],
    pathspec: tuple[str, ...],
    *,
    history_limit: int | None,
) -> tuple[
    tuple[str, tuple[str, ...]],
    ...,
]:
    """从父提交出发读取历史 SHA 及其父节点。"""

    if not parents:
        return ()

    arguments: list[str] = [
        "rev-list",
        "--parents",
    ]

    if history_limit is not None:
        arguments.extend(
            (
                "--max-count",
                str(history_limit),
            )
        )

    arguments.extend(parents)
    arguments.extend(pathspec)

    output = _run_git(
        git_root,
        tuple(arguments),
    ).stdout

    rows: list[
        tuple[
            str,
            tuple[str, ...],
        ]
    ] = []

    for raw_line in output.splitlines():
        parts = tuple(part.lower() for part in raw_line.split() if part)

        if not parts:
            continue

        rows.append(
            (
                parts[0],
                parts[1:],
            )
        )

    return tuple(rows)


def _changed_paths(
    git_root: Path,
    commit: str,
    repository_relative_path: str,
) -> tuple[str, ...]:
    """读取一次历史提交在目标仓库范围内修改的路径。"""

    arguments: list[str] = [
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "--no-renames",
        "-r",
        "-z",
        "--root",
        commit,
    ]

    if repository_relative_path:
        arguments.extend(
            (
                "--",
                repository_relative_path,
            )
        )

    output = _run_git(
        git_root,
        tuple(arguments),
    ).stdout

    prefix = f"{repository_relative_path}/" if repository_relative_path else ""

    paths: set[str] = set()

    for raw_path in output.split("\0"):
        normalized = _normalize_path(raw_path)

        if not normalized:
            continue

        if prefix:
            if not normalized.startswith(prefix):
                continue

            normalized = normalized[len(prefix) :]

        if normalized:
            paths.add(normalized)

    return tuple(sorted(paths))


def _history_digest(
    commits: tuple[str, ...],
) -> str:
    """对有序历史 SHA 生成稳定摘要。"""

    payload = "\n".join(commits).encode("ascii")

    return "sha256:" + hashlib.sha256(payload).hexdigest()


def build_cochange_index(
    repository_path: str | Path,
    target_ref: str,
    *,
    history_limit: int = (DEFAULT_HISTORY_LIMIT),
    max_files_per_commit: int = (DEFAULT_MAX_FILES_PER_COMMIT),
    max_pairs: int = (DEFAULT_MAX_PAIRS),
) -> CoChangeIndex:
    """使用目标 commit 之前的历史构建共变索引。"""

    if history_limit <= 0:
        raise ValueError("history_limit 必须大于 0。")

    if max_files_per_commit < 2:
        raise ValueError("max_files_per_commit 必须至少为 2。")

    if max_pairs <= 0:
        raise ValueError("max_pairs 必须大于 0。")

    resolved_repository = Path(repository_path).expanduser().resolve()

    if not resolved_repository.exists():
        raise FileNotFoundError(f"仓库不存在：{resolved_repository}")

    if not resolved_repository.is_dir():
        raise NotADirectoryError(f"仓库路径不是目录：{resolved_repository}")

    git_root = _find_git_root(resolved_repository)

    target_commit = _resolve_commit(
        git_root,
        target_ref,
    )

    parents = _target_parents(
        git_root,
        target_commit,
    )

    (
        repository_relative_path,
        pathspec,
    ) = _repository_scope(
        resolved_repository,
        git_root,
    )

    # 第一次读取完整历史只用于判断是否发生截断。
    # 真正参与统计的历史仍受 history_limit 限制。
    all_rows = _history_rows(
        git_root,
        parents,
        pathspec,
        history_limit=None,
    )

    selected_rows = all_rows[:history_limit]

    history_commits = tuple(row[0] for row in selected_rows)

    file_counts: Counter[str] = Counter()

    pair_counts: Counter[tuple[str, str]] = Counter()

    analyzed_commit_count = 0
    skipped_merge_commit_count = 0
    skipped_large_commit_count = 0

    for (
        commit,
        commit_parents,
    ) in selected_rows:
        # merge commit 会把整个分支的大量文件
        # 一次性带入。
        #
        # v1 直接排除，避免生成虚假强共变。
        if len(commit_parents) > 1:
            skipped_merge_commit_count += 1
            continue

        changed_paths = _changed_paths(
            git_root,
            commit,
            repository_relative_path,
        )

        # 只有一个文件的提交不能产生共变关系。
        if len(changed_paths) < 2:
            continue

        # 超大提交通常是格式化、批量重构或依赖升级，
        # 不应直接制造大量共变边。
        if len(changed_paths) > max_files_per_commit:
            skipped_large_commit_count += 1
            continue

        analyzed_commit_count += 1

        file_counts.update(changed_paths)

        pair_counts.update(
            combinations(
                changed_paths,
                2,
            )
        )

    pairs = [
        CoChangePair(
            first_path=first_path,
            second_path=second_path,
            shared_commit_count=(shared_count),
            first_commit_count=(file_counts[first_path]),
            second_commit_count=(file_counts[second_path]),
            # Ochiai 系数：
            # shared / sqrt(count_a * count_b)
            #
            # 避免频繁修改的公共文件仅凭出现次数
            # 获得过高分数。
            score=round(
                shared_count / math.sqrt(file_counts[first_path] * file_counts[second_path]),
                6,
            ),
        )
        for (
            first_path,
            second_path,
        ), shared_count in pair_counts.items()
    ]

    pairs.sort(
        key=lambda pair: (
            -pair.score,
            -pair.shared_commit_count,
            pair.first_path,
            pair.second_path,
        )
    )

    pairs_truncated = len(pairs) > max_pairs

    return CoChangeIndex(
        repository_relative_path=(repository_relative_path),
        target_commit=target_commit,
        history_commits=(history_commits),
        available_commit_count=(len(all_rows)),
        analyzed_commit_count=(analyzed_commit_count),
        skipped_merge_commit_count=(skipped_merge_commit_count),
        skipped_large_commit_count=(skipped_large_commit_count),
        file_commit_counts=tuple(sorted(file_counts.items())),
        pairs=tuple(pairs[:max_pairs]),
        history_truncated=(len(all_rows) > history_limit),
        pairs_truncated=(pairs_truncated),
        history_digest=(_history_digest(history_commits)),
    )


def find_cochange_neighbors(
    index: CoChangeIndex,
    path: str,
    *,
    min_shared_commits: int = 1,
    limit: int = 20,
) -> tuple[
    tuple[
        str,
        CoChangePair,
    ],
    ...,
]:
    """查找与某个文件共同变更过的稳定排序邻居。"""

    if min_shared_commits <= 0:
        raise ValueError("min_shared_commits 必须大于 0。")

    if limit <= 0:
        raise ValueError("limit 必须大于 0。")

    normalized_path = _normalize_path(path)

    neighbors: list[
        tuple[
            str,
            CoChangePair,
        ]
    ] = []

    for pair in index.pairs:
        if pair.shared_commit_count < min_shared_commits:
            continue

        other_path = pair.other_path(normalized_path)

        if other_path is not None:
            neighbors.append(
                (
                    other_path,
                    pair,
                )
            )

    neighbors.sort(
        key=lambda item: (
            -item[1].score,
            -item[1].shared_commit_count,
            item[0],
        )
    )

    return tuple(neighbors[:limit])


def check_cochange_leak(
    repository_path: str | Path,
    index: CoChangeIndex,
) -> CoChangeLeakReport:
    """重新查询 Git 边界，验证索引没有包含目标或未来提交。"""

    resolved_repository = Path(repository_path).expanduser().resolve()

    git_root = _find_git_root(resolved_repository)

    resolved_target = _resolve_commit(
        git_root,
        index.target_commit,
    )

    parents = _target_parents(
        git_root,
        resolved_target,
    )

    (
        repository_relative_path,
        pathspec,
    ) = _repository_scope(
        resolved_repository,
        git_root,
    )

    target_commit_matches = (
        resolved_target == index.target_commit
        and repository_relative_path == index.repository_relative_path
    )

    allowed_rows = _history_rows(
        git_root,
        parents,
        pathspec,
        history_limit=None,
    )

    allowed_commits = {row[0] for row in allowed_rows}

    target_commit_excluded = index.target_commit not in index.history_commits

    unexpected_commits = tuple(sorted(set(index.history_commits) - allowed_commits))

    return CoChangeLeakReport(
        passed=(target_commit_matches and target_commit_excluded and not unexpected_commits),
        target_commit_matches=(target_commit_matches),
        target_commit_excluded=(target_commit_excluded),
        checked_commit_count=(len(index.history_commits)),
        unexpected_commits=(unexpected_commits),
    )
