"""读取本地代码仓库并生成安全、有限的代码快照。"""

import os
from pathlib import Path

from app.schemas.repository import RepositoryFile, RepositorySnapshot

# 将常用源代码扩展名映射为编程语言名称。
# 后续智能体可以根据 language 字段选择不同的分析策略。
LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
}


# 这些目录通常包含依赖、缓存、编译结果或 IDE 配置，
# 它们不属于需要分析的业务源代码。
IGNORED_DIRECTORIES: set[str] = {
    ".git",
    ".idea",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}


# 这些文件可能包含密码、API Key 或本地环境变量，
# 扫描工具必须避免读取它们。
IGNORED_FILE_NAMES: set[str] = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
}


def scan_repository(
    repository_path: str | Path,
    *,
    max_files: int = 200,
    max_file_size_bytes: int = 50_000,
) -> RepositorySnapshot:
    """扫描本地代码仓库并返回源代码快照。

    max_files 用于限制一次最多读取多少个文件。
    max_file_size_bytes 用于跳过体积过大的单个文件。
    这些限制可以防止后续发送给大模型的上下文无限增长。
    """

    # 配置值必须是正整数，否则扫描行为没有实际意义。
    if max_files <= 0:
        raise ValueError("max_files 必须大于 0。")

    if max_file_size_bytes <= 0:
        raise ValueError("max_file_size_bytes 必须大于 0。")

    # 将用户输入转换为规范化绝对路径。
    resolved_root = Path(repository_path).expanduser().resolve()

    # 在扫描之前明确判断路径是否存在，提供容易理解的错误信息。
    if not resolved_root.exists():
        raise FileNotFoundError(f"代码仓库不存在：{resolved_root}")

    # 普通文件不能作为代码仓库根目录。
    if not resolved_root.is_dir():
        raise NotADirectoryError(f"代码仓库路径不是目录：{resolved_root}")

    scanned_files: list[RepositoryFile] = []
    truncated = False

    # os.walk 默认不会跟随目录符号链接。
    # followlinks=False 可以降低扫描到仓库外部文件的风险。
    for current_directory, directory_names, file_names in os.walk(
        resolved_root,
        followlinks=False,
    ):
        # 直接修改 directory_names 可以阻止 os.walk 进入被忽略目录。
        # 排序可以保证每次扫描文件的顺序保持一致。
        directory_names[:] = sorted(
            directory_name
            for directory_name in directory_names
            if directory_name not in IGNORED_DIRECTORIES and not directory_name.startswith(".")
        )

        for file_name in sorted(file_names):
            # 隐藏文件和环境变量文件默认不进入扫描结果。
            if file_name.startswith(".") or file_name in IGNORED_FILE_NAMES:
                continue

            file_path = Path(current_directory) / file_name
            file_suffix = file_path.suffix.lower()

            # 没有出现在语言映射中的文件不属于当前支持范围。
            if file_suffix not in LANGUAGE_BY_SUFFIX:
                continue

            # 解析文件真实路径，并确保它仍然位于仓库目录内部。
            # 这可以阻止符号链接把扫描范围引向仓库外部。
            resolved_file = file_path.resolve()

            if not resolved_file.is_relative_to(resolved_root):
                continue

            if not resolved_file.is_file():
                continue

            try:
                file_size = resolved_file.stat().st_size
            except OSError:
                # 文件可能在扫描过程中被删除或失去访问权限。
                continue

            # 大文件可能是生成文件或压缩后的代码，暂时跳过。
            if file_size > max_file_size_bytes:
                continue

            # 只有在发现第 max_files + 1 个有效文件时，
            # 才认为扫描结果确实被截断。
            if len(scanned_files) >= max_files:
                truncated = True
                break

            try:
                # errors="replace" 可以替换少量无法解码的字符，
                # 避免单个文件影响整个仓库扫描。
                content = resolved_file.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                continue

            scanned_files.append(
                RepositoryFile(
                    relative_path=resolved_file.relative_to(resolved_root).as_posix(),
                    language=LANGUAGE_BY_SUFFIX[file_suffix],
                    size_bytes=file_size,
                    content=content,
                )
            )

        if truncated:
            break

    return RepositorySnapshot(
        repository_name=resolved_root.name,
        files=scanned_files,
        scanned_file_count=len(scanned_files),
        truncated=truncated,
    )
