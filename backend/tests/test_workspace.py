"""安全工作区路径解析器的自动化测试。"""

from pathlib import Path

import pytest

from app.tools.workspace import (
    WorkspacePathError,
    resolve_repository_directory,
)


def test_resolve_repository_inside_workspace(
    tmp_path: Path,
) -> None:
    """应该正确解析工作区内部的代码仓库。"""

    workspace_root = tmp_path / "workspace"
    repository_path = workspace_root / "demo_repository"
    repository_path.mkdir(parents=True)

    result = resolve_repository_directory(
        "demo_repository",
        workspace_root=workspace_root,
    )

    assert result == repository_path.resolve()


def test_reject_repository_outside_workspace(
    tmp_path: Path,
) -> None:
    """应该拒绝使用路径穿越访问工作区外部目录。"""

    workspace_root = tmp_path / "workspace"
    outside_repository = tmp_path / "outside_repository"

    workspace_root.mkdir()
    outside_repository.mkdir()

    with pytest.raises(
        WorkspacePathError,
        match="超出允许的工作区范围",
    ):
        resolve_repository_directory(
            "../outside_repository",
            workspace_root=workspace_root,
        )


def test_reject_missing_repository(
    tmp_path: Path,
) -> None:
    """应该拒绝工作区中不存在的仓库。"""

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(
        FileNotFoundError,
        match="代码仓库不存在",
    ):
        resolve_repository_directory(
            "missing_repository",
            workspace_root=workspace_root,
        )
