"""跨测试模块复用的基础设施夹具。"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_database_session
from app.main import app


@pytest.fixture
def api_database_engine() -> Iterator[Engine]:
    """为 API 持久化测试创建独立的内存数据库。"""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    yield engine

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def api_session_factory(
    api_database_engine: Engine,
) -> sessionmaker[Session]:
    """创建与生产配置一致的测试 SessionFactory。"""

    return sessionmaker(
        bind=api_database_engine,
        class_=Session,
        expire_on_commit=False,
    )


@pytest.fixture
def api_client(
    api_session_factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    """让 API 测试使用内存数据库，不连接开发机 PostgreSQL。"""

    def override_database_session() -> Iterator[Session]:
        with api_session_factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = override_database_session

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(
            get_database_session,
            None,
        )
