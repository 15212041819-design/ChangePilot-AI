"""服务存活与数据库就绪接口测试。"""

from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_database_session
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
    """存活检查不应依赖 PostgreSQL。"""

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ChangePilot AI",
        "version": "0.1.0",
    }


def test_readiness_endpoint_returns_database_status() -> None:
    """数据库可查询时，就绪接口应返回 ready。"""

    engine = create_engine("sqlite+pysqlite:///:memory:")

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_database_session] = override_session

    try:
        response = client.get("/api/v1/health/ready")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "service": "ChangePilot AI",
            "version": "0.1.0",
            "database": "ok",
        }
    finally:
        app.dependency_overrides.pop(get_database_session, None)
        engine.dispose()


def test_readiness_endpoint_returns_503_when_database_is_unavailable() -> None:
    """数据库异常不能被伪装成服务已经就绪。"""

    class BrokenSession:
        """模拟数据库连接已经中断。"""

        def execute(self, statement: object) -> None:
            raise SQLAlchemyError("database offline")

    app.dependency_overrides[get_database_session] = lambda: BrokenSession()

    try:
        response = client.get("/api/v1/health/ready")

        assert response.status_code == 503
        assert response.json()["detail"] == "PostgreSQL 暂不可用。"
    finally:
        app.dependency_overrides.pop(get_database_session, None)
