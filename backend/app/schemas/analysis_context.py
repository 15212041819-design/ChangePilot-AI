"""分析上下文仓储对服务层暴露的稳定数据契约。"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.analysis_report import AnalysisReport
from app.schemas.review import ReviewStatus

AnalysisWorkflowStatus = Literal[
    "queued",
    "running",
    "awaiting_requirement_review",
    "completed",
    "failed",
    "rejected",
]

AnalysisRunStatus = Literal[
    "running",
    "paused",
    "completed",
    "failed",
]

ReviewAction = Literal[
    "approve",
    "revise",
    "reject",
]


class ContextSnapshotModel(BaseModel):
    """所有仓储快照的只读 Pydantic 基类。"""

    model_config = ConfigDict(
        frozen=True,
        from_attributes=True,
        extra="forbid",
    )

    @field_validator("*", mode="before")
    @classmethod
    def normalize_naive_datetime(cls, value: object) -> object:
        """兼容 SQLite，将无时区数据库时间统一解释为 UTC。"""

        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value


class AnalysisThreadSnapshot(ContextSnapshotModel):
    """前端和服务层可以安全读取的分析会话摘要。"""

    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    repository_name: str
    commit_ref: str
    request_text: str
    workflow_status: AnalysisWorkflowStatus
    review_status: ReviewStatus | None = None
    api_version: str
    created_at: datetime
    updated_at: datetime


class AnalysisRunSnapshot(ContextSnapshotModel):
    """一次分析执行的持久化快照。"""

    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: AnalysisRunStatus
    model_name: str | None = None
    token_count: int = Field(ge=0)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class AnalysisCheckpointSnapshot(ContextSnapshotModel):
    """一个可恢复工作流阶段的快照。"""

    checkpoint_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    run_id: str | None = None
    sequence: int = Field(ge=1)
    stage: str
    payload: dict[str, object]
    created_at: datetime


class ReviewDecisionSnapshot(ContextSnapshotModel):
    """一次人工审核决定。"""

    decision_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    action: ReviewAction
    feedback: str
    actor_id: str | None = None
    created_at: datetime


class AnalysisEventSnapshot(ContextSnapshotModel):
    """分析时间线中的一条只追加事件。"""

    event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sequence: int = Field(ge=1)
    event_type: str
    payload: dict[str, object]
    created_at: datetime


class StoredAnalysisContext(ContextSnapshotModel):
    """查询接口返回的一次完整持久化上下文。"""

    thread: AnalysisThreadSnapshot
    runs: tuple[AnalysisRunSnapshot, ...]
    checkpoints: tuple[AnalysisCheckpointSnapshot, ...]
    review_decisions: tuple[ReviewDecisionSnapshot, ...]
    events: tuple[AnalysisEventSnapshot, ...]
    report: AnalysisReport | None = None
