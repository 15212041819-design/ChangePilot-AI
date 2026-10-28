"""PostgreSQL 上下文表结构与 Session 基础行为测试。"""

from sqlalchemy import inspect, select

from app.db.models import (
    AnalysisCheckpoint,
    AnalysisEvent,
    AnalysisReportRecord,
    AnalysisRun,
    AnalysisThread,
    Base,
    ReviewDecision,
)
from app.db.session import create_database_engine, create_session_factory


def _database():
    """创建共享连接的内存数据库，模拟 PostgreSQL 表结构。"""

    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    return engine, create_session_factory(engine)


def test_context_database_creates_six_tables() -> None:
    """上下文持久化第一版必须包含六张核心表。"""

    engine, _ = _database()

    assert set(inspect(engine).get_table_names()) == {
        "analysis_checkpoints",
        "analysis_events",
        "analysis_reports",
        "analysis_runs",
        "analysis_threads",
        "review_decisions",
    }


def test_database_persists_complete_analysis_context() -> None:
    """会话、运行、检查点、审核、报告和事件应能一起提交。"""

    _, session_factory = _database()

    with session_factory.begin() as session:
        thread = AnalysisThread(
            thread_id="a" * 32,
            repository_name="order_service",
            commit_ref="HEAD",
            request_text="为订单系统增加取消订单功能",
            workflow_status="completed",
            review_status="ready",
        )

        run = AnalysisRun(
            run_id="b" * 32,
            thread_id=thread.thread_id,
            status="completed",
            model_name="deepseek-v4-pro",
            token_count=128,
        )

        session.add_all(
            [
                thread,
                run,
                AnalysisCheckpoint(
                    thread_id=thread.thread_id,
                    run_id=run.run_id,
                    sequence=1,
                    stage="context_frozen",
                    payload={"set_digest": "sha256:test"},
                ),
                ReviewDecision(
                    thread_id=thread.thread_id,
                    action="approve",
                    feedback="批准继续分析",
                    actor_id="developer-1",
                ),
                AnalysisReportRecord(
                    report_id="AR-000000000001",
                    thread_id=thread.thread_id,
                    report_version="1.0.0",
                    status="ready",
                    report_digest="sha256:test",
                    payload={"overview": {"status": "ready"}},
                ),
                AnalysisEvent(
                    thread_id=thread.thread_id,
                    sequence=1,
                    event_type="analysis.completed",
                    payload={"status": "ready"},
                ),
            ]
        )

    with session_factory() as session:
        stored = session.scalar(
            select(AnalysisThread).where(
                AnalysisThread.thread_id == "a" * 32,
            )
        )

        assert stored is not None
        assert stored.repository_name == "order_service"
        assert stored.workflow_status == "completed"
        assert stored.review_status == "ready"
        assert stored.runs[0].token_count == 128
        assert stored.checkpoints[0].stage == "context_frozen"
        assert stored.review_decisions[0].action == "approve"
        assert stored.report is not None
        assert stored.report.payload["overview"]["status"] == "ready"
        assert stored.events[0].event_type == "analysis.completed"


def test_deleting_thread_cascades_context_records() -> None:
    """删除会话时 ORM 级联应清理其运行记录，避免孤儿上下文。"""

    _, session_factory = _database()

    with session_factory.begin() as session:
        thread = AnalysisThread(
            thread_id="c" * 32,
            repository_name="order_service",
            commit_ref="HEAD",
            request_text="测试级联删除",
            workflow_status="running",
        )

        thread.runs.append(
            AnalysisRun(
                run_id="d" * 32,
                status="running",
            )
        )

        session.add(thread)

    with session_factory.begin() as session:
        stored = session.get(
            AnalysisThread,
            "c" * 32,
        )

        assert stored is not None

        session.delete(stored)

    with session_factory() as session:
        assert session.get(AnalysisThread, "c" * 32) is None
        assert session.get(AnalysisRun, "d" * 32) is None
