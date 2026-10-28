"""分析上下文持久化使用的 SQLAlchemy ORM 模型。"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """统一生成带 UTC 时区的时间，避免本地时区污染审计数据。"""

    return datetime.now(UTC)


def new_id() -> str:
    """生成与 API thread_id 风格一致的 32 位十六进制 ID。"""

    return uuid4().hex


# PostgreSQL 使用 JSONB 获得索引和查询能力；SQLite 测试使用普通 JSON。
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """所有 ChangePilot ORM 模型的声明式基类。"""


class AnalysisThread(Base):
    """一次可恢复的软件变更分析会话。"""

    __tablename__ = "analysis_threads"

    thread_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    repository_name: Mapped[str] = mapped_column(String(200), nullable=False)
    commit_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_status: Mapped[str] = mapped_column(String(50), nullable=False)
    review_status: Mapped[str | None] = mapped_column(String(20))
    api_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="1.0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    runs: Mapped[list["AnalysisRun"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )
    checkpoints: Mapped[list["AnalysisCheckpoint"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )
    review_decisions: Mapped[list["ReviewDecision"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )
    report: Mapped["AnalysisReportRecord | None"] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        uselist=False,
    )
    events: Mapped[list["AnalysisEvent"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_analysis_threads_status_updated",
            "workflow_status",
            "updated_at",
        ),
    )


class AnalysisRun(Base):
    """一次会话中的单次执行，可用于重试和耗时统计。"""

    __tablename__ = "analysis_runs"

    run_id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        default=new_id,
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(200))
    token_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    thread: Mapped[AnalysisThread] = relationship(
        back_populates="runs",
    )


class AnalysisCheckpoint(Base):
    """工作流阶段快照，用于服务重启后的恢复。"""

    __tablename__ = "analysis_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        default=new_id,
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.run_id", ondelete="SET NULL"),
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    thread: Mapped[AnalysisThread] = relationship(
        back_populates="checkpoints",
    )

    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "sequence",
            name="uq_analysis_checkpoint_sequence",
        ),
    )


class ReviewDecision(Base):
    """人工审核对需求假设或门禁作出的决定。"""

    __tablename__ = "review_decisions"

    decision_id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        default=new_id,
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    feedback: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    actor_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    thread: Mapped[AnalysisThread] = relationship(
        back_populates="review_decisions",
    )


class AnalysisReportRecord(Base):
    """一次分析会话最终产生的统一报告。"""

    __tablename__ = "analysis_reports"

    report_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    report_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    report_digest: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    thread: Mapped[AnalysisThread] = relationship(
        back_populates="report",
    )


class AnalysisEvent(Base):
    """面向审计和前端时间线的只追加事件。"""

    __tablename__ = "analysis_events"

    event_id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        default=new_id,
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    thread: Mapped[AnalysisThread] = relationship(
        back_populates="events",
    )

    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "sequence",
            name="uq_analysis_event_sequence",
        ),
    )
