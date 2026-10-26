"""从 Git 历史提取无答案泄漏的离线评测样本。

每条样本严格使用目标提交的第一父提交作为分析快照，目标提交本身
只负责提供 diff 答案。模块只读取 Git 对象，不执行 checkout，也不改写
用户工作区。样本筛选规则是产品评测口径的一部分，不应在实验之间漂移。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from app.schemas.evaluation import EvaluationDataset, EvaluationSample

DEFAULT_SAMPLE_LIMIT = 15
DEFAULT_SCAN_LIMIT = 200
DEFAULT_MIN_CHANGED_FILES = 2
DEFAULT_MAX_CHANGED_FILES = 8
DEFAULT_MIN_MESSAGE_LENGTH = 16


class EvaluationDatasetError(RuntimeError):
    """Git 历史无法被安全读取或无法形成评测数据集。"""


def _run_git(
    working_directory: Path,
    arguments: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """不经过 shell 执行 Git，避免 ref 或路径被解释为命令。"""

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
        raise EvaluationDatasetError("当前环境无法执行 Git。") from exc

    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise EvaluationDatasetError(detail or "Git 命令执行失败。")

    return completed


def _normalize_path(path: str) -> str:
    """把 Git 路径规范为相对 POSIX 路径。"""

    normalized = PurePosixPath(path.replace("\\", "/")).as_posix().strip("/")
    return "" if normalized in {"", "."} else normalized


def _find_git_root(repository_path: Path) -> Path:
    """查找目标目录所属的 Git 根目录。"""

    completed = _run_git(
        repository_path,
        ("rev-parse", "--show-toplevel"),
        check=False,
    )

    if completed.returncode != 0:
        raise EvaluationDatasetError(f"仓库不在 Git worktree 中：{repository_path}")

    root_text = completed.stdout.strip()

    if not root_text:
        raise EvaluationDatasetError("Git 返回了空仓库根目录。")

    return Path(root_text).resolve()


def _resolve_commit(git_root: Path, requested_ref: str) -> str:
    """把分支、tag 或 SHA 解析为精确 commit。"""

    normalized_ref = requested_ref.strip()

    if not normalized_ref:
        raise EvaluationDatasetError("requested_ref 不能为空。")

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
        raise EvaluationDatasetError(f"Git ref 不存在：{normalized_ref}")

    resolved = completed.stdout.strip().lower()

    if len(resolved) != 40 or any(character not in "0123456789abcdef" for character in resolved):
        raise EvaluationDatasetError("Git 返回了非法 commit SHA。")

    return resolved


def _repository_scope(
    repository_path: Path,
    git_root: Path,
) -> tuple[str, tuple[str, ...]]:
    """生成 monorepo 子目录对应的 Git pathspec。"""

    if not repository_path.is_relative_to(git_root):
        raise EvaluationDatasetError("仓库路径位于 Git 根目录之外。")

    relative = _normalize_path(repository_path.relative_to(git_root).as_posix())
    return relative, (() if not relative else ("--", relative))


def _commit_rows(
    git_root: Path,
    resolved_ref: str,
    pathspec: tuple[str, ...],
    *,
    scan_limit: int,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """按从新到旧顺序返回提交及其父提交。"""

    arguments: list[str] = [
        "rev-list",
        "--parents",
        "--max-count",
        str(scan_limit),
        resolved_ref,
    ]
    arguments.extend(pathspec)
    output = _run_git(git_root, tuple(arguments)).stdout
    rows: list[tuple[str, tuple[str, ...]]] = []

    for raw_line in output.splitlines():
        parts = tuple(part.lower() for part in raw_line.split() if part)

        if parts:
            rows.append((parts[0], parts[1:]))

    return tuple(rows)


def _commit_subject(git_root: Path, commit: str) -> str:
    """读取提交标题，并折叠多余空白。"""

    output = _run_git(
        git_root,
        ("show", "-s", "--format=%s", commit),
    ).stdout
    return " ".join(output.split())


def _changed_paths(
    git_root: Path,
    parent: str,
    commit: str,
    repository_relative_path: str,
) -> tuple[str, ...]:
    """读取目标提交相对第一父提交的真实答案路径。"""

    arguments: list[str] = [
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        parent,
        commit,
    ]

    if repository_relative_path:
        arguments.extend(("--", repository_relative_path))

    output = _run_git(git_root, tuple(arguments)).stdout
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


def is_test_path(path: str) -> bool:
    """使用保守规则判断路径是否属于自动化测试。"""

    normalized = _normalize_path(path)
    parts = tuple(part.lower() for part in PurePosixPath(normalized).parts)
    filename = parts[-1] if parts else ""
    stem = PurePosixPath(filename).stem

    return (
        "tests" in parts
        or "test" in parts[:-1]
        or filename.startswith("test_")
        or stem.endswith("_test")
        or filename.endswith(".spec.ts")
        or filename.endswith(".spec.tsx")
        or filename.endswith(".test.ts")
        or filename.endswith(".test.tsx")
        or filename.endswith(".spec.js")
        or filename.endswith(".test.js")
    )


def build_evaluation_dataset(
    repository_path: str | Path,
    requested_ref: str = "HEAD",
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
    min_changed_files: int = DEFAULT_MIN_CHANGED_FILES,
    max_changed_files: int = DEFAULT_MAX_CHANGED_FILES,
    min_message_length: int = DEFAULT_MIN_MESSAGE_LENGTH,
) -> EvaluationDataset:
    """按冻结规则从 Git 历史中提取评测样本。

    筛选顺序也是契约的一部分：根提交、合并提交、短消息、文件数、
    纯测试提交依次过滤。一条提交只会进入一个拒绝计数器。
    """

    if sample_limit <= 0:
        raise ValueError("sample_limit 必须大于 0。")

    if scan_limit <= 0:
        raise ValueError("scan_limit 必须大于 0。")

    if min_changed_files <= 0:
        raise ValueError("min_changed_files 必须大于 0。")

    if max_changed_files < min_changed_files:
        raise ValueError("max_changed_files 不能小于 min_changed_files。")

    if min_message_length <= 0:
        raise ValueError("min_message_length 必须大于 0。")

    resolved_repository = Path(repository_path).expanduser().resolve()

    if not resolved_repository.exists():
        raise FileNotFoundError(f"仓库不存在：{resolved_repository}")

    if not resolved_repository.is_dir():
        raise NotADirectoryError(f"仓库路径不是目录：{resolved_repository}")

    git_root = _find_git_root(resolved_repository)
    resolved_ref = _resolve_commit(git_root, requested_ref)
    repository_relative_path, pathspec = _repository_scope(
        resolved_repository,
        git_root,
    )
    rows = _commit_rows(
        git_root,
        resolved_ref,
        pathspec,
        scan_limit=scan_limit,
    )

    samples: list[EvaluationSample] = []
    rejected_root_count = 0
    rejected_merge_count = 0
    rejected_short_message_count = 0
    rejected_file_count_count = 0
    rejected_pure_test_count = 0
    scanned_commit_count = 0

    for commit, parents in rows:
        # 数据集达到上限后停止，避免把未真正筛选的历史算入 scanned。
        if len(samples) >= sample_limit:
            break

        scanned_commit_count += 1

        if not parents:
            rejected_root_count += 1
            continue

        if len(parents) > 1:
            rejected_merge_count += 1
            continue

        request_text = _commit_subject(git_root, commit)

        if len(request_text) < min_message_length:
            rejected_short_message_count += 1
            continue

        analysis_commit = parents[0]
        changed_files = _changed_paths(
            git_root,
            analysis_commit,
            commit,
            repository_relative_path,
        )

        if not min_changed_files <= len(changed_files) <= max_changed_files:
            rejected_file_count_count += 1
            continue

        test_files = tuple(path for path in changed_files if is_test_path(path))
        test_file_set = set(test_files)
        source_files = tuple(path for path in changed_files if path not in test_file_set)

        if not source_files:
            rejected_pure_test_count += 1
            continue

        samples.append(
            EvaluationSample(
                sample_id=f"git-{commit[:12]}",
                repository_relative_path=repository_relative_path,
                request_text=request_text,
                analysis_commit=analysis_commit,
                answer_commit=commit,
                changed_files=changed_files,
                source_files=source_files,
                test_files=test_files,
            )
        )

    return EvaluationDataset(
        repository_relative_path=repository_relative_path,
        requested_ref=requested_ref.strip(),
        resolved_ref=resolved_ref,
        scanned_commit_count=scanned_commit_count,
        samples=tuple(samples),
        rejected_root_count=rejected_root_count,
        rejected_merge_count=rejected_merge_count,
        rejected_short_message_count=rejected_short_message_count,
        rejected_file_count_count=rejected_file_count_count,
        rejected_pure_test_count=rejected_pure_test_count,
    )
