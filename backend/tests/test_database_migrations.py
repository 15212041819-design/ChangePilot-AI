"""Alembic 初始迁移的升级与回滚测试。"""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.core.config import BACKEND_DIR, get_settings


def test_initial_migration_upgrade_and_downgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """初始迁移必须可创建并完整回滚六张上下文表。"""

    database_path = tmp_path / "migration-test.db"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"

    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    config = Config(str(BACKEND_DIR / "alembic.ini"))

    try:
        command.upgrade(config, "head")

        engine = create_engine(database_url)

        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "analysis_checkpoints",
            "analysis_events",
            "analysis_reports",
            "analysis_runs",
            "analysis_threads",
            "review_decisions",
        }

        engine.dispose()

        command.downgrade(config, "base")

        engine = create_engine(database_url)

        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
        }

        engine.dispose()
    finally:
        get_settings.cache_clear()
