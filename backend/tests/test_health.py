from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_endpoint() -> None:
    """根接口应当返回项目入口信息。"""

    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Welcome to ChangePilot AI",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


def test_health_endpoint() -> None:
    """健康检查接口应当返回服务状态。"""

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ChangePilot AI",
        "version": "0.1.0",
    }
