"""本地代码仓库扫描工具的自动化测试。"""

from pathlib import Path

import pytest

from app.tools.repository_scanner import scan_repository


def test_scan_repository_reads_supported_source_files(
    tmp_path: Path,
) -> None:
    """扫描器应该读取源代码并忽略依赖目录和敏感文件。"""

    # 在 pytest 临时目录中创建模拟代码仓库。
    repository_path = tmp_path / "demo_repository"
    source_directory = repository_path / "src"
    dependency_directory = repository_path / "node_modules"

    source_directory.mkdir(parents=True)
    dependency_directory.mkdir()

    # 这些文件属于需要扫描的源代码和项目文档。
    (source_directory / "main.py").write_text(
        'def main() -> str:\n    return "hello"\n',
        encoding="utf-8",
    )
    (repository_path / "README.md").write_text(
        "# Demo Repository\n",
        encoding="utf-8",
    )

    # 这些文件应该被扫描器排除。
    (repository_path / ".env").write_text(
        "SECRET_KEY=do-not-read\n",
        encoding="utf-8",
    )
    (dependency_directory / "library.js").write_text(
        "console.log('dependency');\n",
        encoding="utf-8",
    )
    (repository_path / "image.png").write_bytes(b"not-a-real-image")

    snapshot = scan_repository(repository_path)

    scanned_paths = {repository_file.relative_path for repository_file in snapshot.files}

    assert snapshot.repository_name == "demo_repository"
    assert snapshot.scanned_file_count == 2
    assert snapshot.truncated is False
    assert scanned_paths == {
        "README.md",
        "src/main.py",
    }

    main_file = next(
        repository_file
        for repository_file in snapshot.files
        if repository_file.relative_path == "src/main.py"
    )

    assert main_file.language == "python"
    assert 'return "hello"' in main_file.content


def test_scan_repository_rejects_missing_directory(
    tmp_path: Path,
) -> None:
    """扫描器应该拒绝不存在的代码仓库路径。"""

    missing_repository = tmp_path / "missing_repository"

    with pytest.raises(
        FileNotFoundError,
        match="代码仓库不存在",
    ):
        scan_repository(missing_repository)
