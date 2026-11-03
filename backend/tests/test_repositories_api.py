"""仓库选择接口只列出工作区直属目录。"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes.repositories import get_workspace_root
from app.main import app


def test_repository_list_contains_only_directories(tmp_path: Path) -> None:
    """前端只能选到实际存在的直属仓库，不能看到文件或隐藏目录。"""

    (tmp_path / "order_service").mkdir()
    (tmp_path / "billing_service").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "README.md").write_text("demo", encoding="utf-8")
    app.dependency_overrides[get_workspace_root] = lambda: str(tmp_path)
    try:
        response = TestClient(app).get("/api/v1/repositories")
    finally:
        app.dependency_overrides.pop(get_workspace_root, None)

    assert response.status_code == 200
    assert response.json() == {
        "repositories": ["billing_service", "order_service"],
    }


def test_repository_list_explains_missing_workspace(tmp_path: Path) -> None:
    """工作区不存在时应返回明确错误，不能让前端出现空白选项。"""

    app.dependency_overrides[get_workspace_root] = lambda: str(tmp_path / "missing")
    try:
        response = TestClient(app).get("/api/v1/repositories")
    finally:
        app.dependency_overrides.pop(get_workspace_root, None)

    assert response.status_code == 503
    assert "sample_repos" in response.json()["detail"]
