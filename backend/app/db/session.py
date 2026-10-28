"""SQLAlchemy Engine、SessionFactory 与 FastAPI 依赖。"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings


def create_database_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool_size: int = 5,
    max_overflow: int = 10,
) -> Engine:
    """根据数据库 URL 创建可复用的同步 Engine。

    生产环境使用 PostgreSQL + psycopg；测试允许使用内存 SQLite，
    但两者共享完全相同的 ORM 模型和仓储接口。
    """

    common_options: dict[str, object] = {
        "echo": echo,
        "pool_pre_ping": True,
    }

    if database_url.startswith("sqlite"):
        # StaticPool 让不同 Session 共享同一个内存数据库连接。
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            **common_options,
        )

    return create_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        **common_options,
    )


def create_session_factory(
    engine: Engine,
) -> sessionmaker[Session]:
    """创建事务边界清晰、提交后对象仍可读取的 SessionFactory。"""

    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


@lru_cache
def get_database_engine() -> Engine:
    """根据 Settings 创建并缓存生产 Engine。"""

    settings = get_settings()

    return create_database_engine(
        settings.database_url.get_secret_value(),
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """返回进程内共享的 SessionFactory。"""

    return create_session_factory(get_database_engine())


def get_database_session() -> Generator[Session, None, None]:
    """为一次 HTTP 请求提供独立 Session，并确保连接被归还。"""

    session = get_session_factory()()

    try:
        yield session
    finally:
        session.close()
