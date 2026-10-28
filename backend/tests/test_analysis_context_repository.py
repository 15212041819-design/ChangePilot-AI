"""PostgreSQL 分析上下文仓储的事务与生命周期测试。"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from test_evidence_analysis_api import _build_ready_report

from app.db.models import Base
from app.repositories.analysis_context import (
    AnalysisContextConflictError,
    AnalysisContextNotFoundError,
    SQLAlchemyAnalysisContextRepository,
)

THREAD_ID = "a" * 32
SECOND_THREAD_ID = "b" * 32


@pytest.fixture
def session() -> Iterator[Session]:
    """使用单连接内存 SQLite 验证仓储逻辑，不依赖本机 PostgreSQL。"""

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    factory = sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
    )

    with factory() as database_session:
        yield database_session

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def repository(
    session: Session,
) -> SQLAlchemyAnalysisContextRepository:
    """为每个用例提供隔离的仓储实例。"""

    return SQLAlchemyAnalysisContextRepository(session)


def _create_thread(
    repository: SQLAlchemyAnalysisContextRepository,
    *,
    thread_id: str = THREAD_ID,
) -> None:
    """创建测试会话，减少用例中的重复样板。"""

    repository.create_thread(
        thread_id=thread_id,
        repository_name="order-service",
        commit_ref="abc123",
        request_text="为订单系统增加取消订单功能。",
    )


def test_repository_persists_complete_analysis_lifecycle(
    repository: SQLAlchemyAnalysisContextRepository,
) -> None:
    """完整分析应原子保存运行、检查点、报告和有序事件。"""

    created = repository.create_thread(
        thread_id=THREAD_ID,
        repository_name="order-service",
        commit_ref="abc123",
        request_text="为订单系统增加取消订单功能。",
    )
    run = repository.start_run(
        thread_id=THREAD_ID,
        model_name="gpt-test",
    )

    first_checkpoint = repository.append_checkpoint(
        thread_id=THREAD_ID,
        run_id=run.run_id,
        stage="requirement_analyzed",
        payload={"spec_id": "RS-0001"},
    )
    second_checkpoint = repository.append_checkpoint(
        thread_id=THREAD_ID,
        run_id=run.run_id,
        stage="context_frozen",
        payload={"set_id": "CS-0001"},
    )

    report = _build_ready_report()
    completed = repository.complete_run(
        thread_id=THREAD_ID,
        run_id=run.run_id,
        report=report,
        token_count=321,
    )
    context = repository.get_context(THREAD_ID)

    assert created.workflow_status == "queued"
    assert first_checkpoint.sequence == 1
    assert second_checkpoint.sequence == 2
    assert completed.workflow_status == "completed"
    assert completed.review_status == "ready"

    assert context is not None
    assert context.thread.workflow_status == "completed"
    assert len(context.runs) == 1
    assert context.runs[0].status == "completed"
    assert context.runs[0].token_count == 321
    assert [item.sequence for item in context.checkpoints] == [1, 2]

    assert context.report is not None
    assert context.report.report_id == report.report_id
    assert context.report.report_digest == report.report_digest

    assert [item.sequence for item in context.events] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert [item.event_type for item in context.events] == [
        "analysis.created",
        "analysis.started",
        "checkpoint.saved",
        "checkpoint.saved",
        "analysis.completed",
    ]


def test_repository_persists_review_pause_and_decision(
    repository: SQLAlchemyAnalysisContextRepository,
) -> None:
    """人工审核暂停点和审核决定应能在服务重启后恢复。"""

    _create_thread(repository)
    run = repository.start_run(
        thread_id=THREAD_ID,
        model_name=None,
    )

    paused = repository.mark_review_required(
        thread_id=THREAD_ID,
        run_id=run.run_id,
        reason="需求存在未确认假设",
        payload={"question_count": 2},
    )
    decision = repository.record_review_decision(
        thread_id=THREAD_ID,
        action="approve",
        feedback="批准当前假设并继续分析。",
        actor_id="user-001",
    )
    context = repository.get_context(THREAD_ID)

    assert paused.workflow_status == "awaiting_requirement_review"
    assert decision.action == "approve"
    assert decision.actor_id == "user-001"

    assert context is not None
    assert context.runs[0].status == "paused"
    assert len(context.review_decisions) == 1
    assert context.review_decisions[0].decision_id == decision.decision_id
    assert context.review_decisions[0].feedback == decision.feedback
    assert context.review_decisions[0].created_at.tzinfo is not None
    assert context.events[-2].event_type == "analysis.review_required"
    assert context.events[-2].payload["question_count"] == 2
    assert context.events[-1].event_type == "review.recorded"


def test_repository_persists_failed_run(
    repository: SQLAlchemyAnalysisContextRepository,
) -> None:
    """执行失败时应保存稳定错误码和可展示消息。"""

    _create_thread(repository)
    run = repository.start_run(
        thread_id=THREAD_ID,
        model_name="gpt-test",
    )

    failed = repository.fail_run(
        thread_id=THREAD_ID,
        run_id=run.run_id,
        error_code="SEMANTIC_MAPPING_FAILED",
        error_message="语义映射服务暂时不可用。",
    )
    context = repository.get_context(THREAD_ID)

    assert failed.workflow_status == "failed"

    assert context is not None
    assert context.runs[0].status == "failed"
    assert context.runs[0].error_code == "SEMANTIC_MAPPING_FAILED"
    assert context.runs[0].error_message == "语义映射服务暂时不可用。"
    assert context.events[-1].event_type == "analysis.failed"


def test_repository_rejects_duplicate_thread(
    repository: SQLAlchemyAnalysisContextRepository,
) -> None:
    """重复 thread_id 应在写库前返回明确冲突。"""

    _create_thread(repository)

    with pytest.raises(
        AnalysisContextConflictError,
        match="已存在",
    ):
        _create_thread(repository)

    context = repository.get_context(THREAD_ID)

    assert context is not None
    assert len(context.events) == 1


def test_repository_rejects_unknown_thread(
    repository: SQLAlchemyAnalysisContextRepository,
) -> None:
    """未知会话不能启动运行。"""

    with pytest.raises(
        AnalysisContextNotFoundError,
        match="不存在",
    ):
        repository.start_run(
            thread_id=THREAD_ID,
            model_name=None,
        )

    assert repository.get_context(THREAD_ID) is None


def test_repository_rejects_run_from_another_thread(
    repository: SQLAlchemyAnalysisContextRepository,
) -> None:
    """run_id 不得跨会话写入检查点。"""

    _create_thread(
        repository,
        thread_id=THREAD_ID,
    )
    _create_thread(
        repository,
        thread_id=SECOND_THREAD_ID,
    )
    run = repository.start_run(
        thread_id=THREAD_ID,
        model_name=None,
    )

    with pytest.raises(
        AnalysisContextConflictError,
        match="不属于",
    ):
        repository.append_checkpoint(
            thread_id=SECOND_THREAD_ID,
            run_id=run.run_id,
            stage="invalid_cross_thread_write",
            payload={},
        )

    first_context = repository.get_context(THREAD_ID)
    second_context = repository.get_context(SECOND_THREAD_ID)

    assert first_context is not None
    assert second_context is not None
    assert first_context.checkpoints == ()
    assert second_context.checkpoints == ()


def test_repository_rejects_second_completion(
    repository: SQLAlchemyAnalysisContextRepository,
) -> None:
    """最终报告一旦写入，重复完成不得覆盖原报告。"""

    _create_thread(repository)
    run = repository.start_run(
        thread_id=THREAD_ID,
        model_name=None,
    )
    report = _build_ready_report()

    repository.complete_run(
        thread_id=THREAD_ID,
        run_id=run.run_id,
        report=report,
    )

    with pytest.raises(
        AnalysisContextConflictError,
        match="最终报告",
    ):
        repository.complete_run(
            thread_id=THREAD_ID,
            run_id=run.run_id,
            report=report,
        )

    context = repository.get_context(THREAD_ID)

    assert context is not None
    assert context.report is not None
    assert context.report.report_id == report.report_id
    assert [event.event_type for event in context.events].count("analysis.completed") == 1
