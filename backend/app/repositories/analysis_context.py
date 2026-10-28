"""基于 SQLAlchemy 的分析上下文仓储。"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    AnalysisCheckpoint,
    AnalysisEvent,
    AnalysisReportRecord,
    AnalysisRun,
    AnalysisThread,
    ReviewDecision,
    new_id,
    utc_now,
)
from app.schemas.analysis_context import (
    AnalysisCheckpointSnapshot,
    AnalysisEventSnapshot,
    AnalysisRunSnapshot,
    AnalysisThreadSnapshot,
    ReviewAction,
    ReviewDecisionSnapshot,
    StoredAnalysisContext,
)
from app.schemas.analysis_report import AnalysisReport


class AnalysisContextNotFoundError(LookupError):
    """指定 thread_id 或 run_id 不存在。"""


class AnalysisContextConflictError(RuntimeError):
    """上下文状态或唯一约束不允许当前写入。"""


class AnalysisContextPersistenceError(RuntimeError):
    """数据库写入失败且事务已经回滚。"""


class SQLAlchemyAnalysisContextRepository:
    """为分析生命周期提供原子化、可测试的数据库操作。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_thread(
        self,
        *,
        thread_id: str,
        repository_name: str,
        commit_ref: str,
        request_text: str,
        api_version: str = "1.0",
    ) -> AnalysisThreadSnapshot:
        """创建 queued 会话，并写入第一条时间线事件。"""

        if self._session.get(AnalysisThread, thread_id) is not None:
            raise AnalysisContextConflictError(f"分析会话已存在：{thread_id}")

        thread = AnalysisThread(
            thread_id=thread_id,
            repository_name=repository_name,
            commit_ref=commit_ref,
            request_text=request_text,
            workflow_status="queued",
            api_version=api_version,
        )

        event = AnalysisEvent(
            thread_id=thread_id,
            sequence=1,
            event_type="analysis.created",
            payload={
                "repository_name": repository_name,
                "commit_ref": commit_ref,
            },
        )

        self._session.add_all([thread, event])
        self._commit("无法创建分析会话。")

        return AnalysisThreadSnapshot.model_validate(thread)

    def start_run(
        self,
        *,
        thread_id: str,
        model_name: str | None,
    ) -> AnalysisRunSnapshot:
        """创建 running 执行记录，并将会话推进到 running。"""

        thread = self._require_thread(thread_id)

        if thread.workflow_status == "running":
            raise AnalysisContextConflictError("分析会话已经处于 running 状态。")

        if thread.workflow_status == "completed":
            raise AnalysisContextConflictError("已经完成的分析会话不能再次启动。")

        run = AnalysisRun(
            run_id=new_id(),
            thread_id=thread_id,
            status="running",
            model_name=model_name,
        )

        thread.workflow_status = "running"
        thread.updated_at = utc_now()

        self._session.add(run)
        self._append_event(
            thread_id,
            "analysis.started",
            {"run_id": run.run_id},
        )
        self._commit("无法启动分析运行。")

        return AnalysisRunSnapshot.model_validate(run)

    def append_checkpoint(
        self,
        *,
        thread_id: str,
        run_id: str,
        stage: str,
        payload: Mapping[str, Any],
    ) -> AnalysisCheckpointSnapshot:
        """追加一个顺序稳定的工作流检查点。"""

        self._require_thread(thread_id)
        run = self._require_run(run_id)

        if run.thread_id != thread_id:
            raise AnalysisContextConflictError("run_id 不属于指定的分析会话。")

        checkpoint = AnalysisCheckpoint(
            checkpoint_id=new_id(),
            thread_id=thread_id,
            run_id=run_id,
            sequence=self._next_checkpoint_sequence(thread_id),
            stage=stage,
            payload=dict(payload),
        )

        self._session.add(checkpoint)
        self._append_event(
            thread_id,
            "checkpoint.saved",
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "stage": stage,
            },
        )
        self._commit("无法保存分析检查点。")

        return AnalysisCheckpointSnapshot.model_validate(checkpoint)

    def mark_review_required(
        self,
        *,
        thread_id: str,
        run_id: str,
        reason: str,
        payload: Mapping[str, Any] | None = None,
    ) -> AnalysisThreadSnapshot:
        """暂停当前运行，并把会话置为等待人工审核。"""

        thread = self._require_thread(thread_id)
        run = self._require_run(run_id)

        if run.thread_id != thread_id:
            raise AnalysisContextConflictError("run_id 不属于指定的分析会话。")

        run.status = "paused"
        run.finished_at = utc_now()
        thread.workflow_status = "awaiting_requirement_review"
        thread.updated_at = utc_now()

        self._append_event(
            thread_id,
            "analysis.review_required",
            {
                "run_id": run_id,
                "reason": reason,
                **dict(payload or {}),
            },
        )
        self._commit("无法保存人工审核等待状态。")

        return AnalysisThreadSnapshot.model_validate(thread)

    def record_review_decision(
        self,
        *,
        thread_id: str,
        action: ReviewAction,
        feedback: str,
        actor_id: str | None = None,
    ) -> ReviewDecisionSnapshot:
        """保存人工决定，不在仓储层擅自解释业务语义。"""

        self._require_thread(thread_id)

        decision = ReviewDecision(
            decision_id=new_id(),
            thread_id=thread_id,
            action=action,
            feedback=feedback,
            actor_id=actor_id,
        )

        self._session.add(decision)
        self._append_event(
            thread_id,
            "review.recorded",
            {
                "decision_id": decision.decision_id,
                "action": action,
            },
        )
        self._commit("无法保存人工审核决定。")

        return ReviewDecisionSnapshot.model_validate(decision)

    def complete_run(
        self,
        *,
        thread_id: str,
        run_id: str,
        report: AnalysisReport,
        token_count: int = 0,
    ) -> AnalysisThreadSnapshot:
        """以单个事务完成运行、会话、报告和完成事件。"""

        thread = self._require_thread(thread_id)
        run = self._require_run(run_id)

        if run.thread_id != thread_id:
            raise AnalysisContextConflictError("run_id 不属于指定的分析会话。")

        if thread.report is not None:
            raise AnalysisContextConflictError("分析会话已经存在最终报告。")

        now = utc_now()

        run.status = "completed"
        run.token_count = token_count
        run.finished_at = now

        thread.workflow_status = "completed"
        thread.review_status = report.overview.status
        thread.updated_at = now

        record = AnalysisReportRecord(
            report_id=report.report_id,
            thread_id=thread_id,
            report_version=report.report_version,
            status=report.overview.status,
            report_digest=report.report_digest,
            payload=report.model_dump(mode="json"),
        )

        self._session.add(record)
        self._append_event(
            thread_id,
            "analysis.completed",
            {
                "run_id": run_id,
                "report_id": report.report_id,
                "status": report.overview.status,
            },
        )
        self._commit("无法保存最终分析报告。")

        return AnalysisThreadSnapshot.model_validate(thread)

    def fail_run(
        self,
        *,
        thread_id: str,
        run_id: str,
        error_code: str,
        error_message: str,
    ) -> AnalysisThreadSnapshot:
        """保存失败状态和可供前端展示的错误信息。"""

        thread = self._require_thread(thread_id)
        run = self._require_run(run_id)

        if run.thread_id != thread_id:
            raise AnalysisContextConflictError("run_id 不属于指定的分析会话。")

        now = utc_now()

        run.status = "failed"
        run.error_code = error_code
        run.error_message = error_message
        run.finished_at = now

        thread.workflow_status = "failed"
        thread.updated_at = now

        self._append_event(
            thread_id,
            "analysis.failed",
            {
                "run_id": run_id,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        self._commit("无法保存分析失败状态。")

        return AnalysisThreadSnapshot.model_validate(thread)

    def get_context(
        self,
        thread_id: str,
    ) -> StoredAnalysisContext | None:
        """一次性加载前端恢复页面所需的完整上下文。"""

        statement = (
            select(AnalysisThread)
            .where(AnalysisThread.thread_id == thread_id)
            .options(
                selectinload(AnalysisThread.runs),
                selectinload(AnalysisThread.checkpoints),
                selectinload(AnalysisThread.review_decisions),
                selectinload(AnalysisThread.events),
                selectinload(AnalysisThread.report),
            )
        )

        thread = self._session.scalar(statement)

        if thread is None:
            return None

        report = (
            AnalysisReport.model_validate(thread.report.payload)
            if thread.report is not None
            else None
        )

        return StoredAnalysisContext(
            thread=AnalysisThreadSnapshot.model_validate(thread),
            runs=tuple(
                AnalysisRunSnapshot.model_validate(item)
                for item in sorted(
                    thread.runs,
                    key=lambda item: item.started_at,
                )
            ),
            checkpoints=tuple(
                AnalysisCheckpointSnapshot.model_validate(item)
                for item in sorted(
                    thread.checkpoints,
                    key=lambda item: item.sequence,
                )
            ),
            review_decisions=tuple(
                ReviewDecisionSnapshot.model_validate(item)
                for item in sorted(
                    thread.review_decisions,
                    key=lambda item: item.created_at,
                )
            ),
            events=tuple(
                AnalysisEventSnapshot.model_validate(item)
                for item in sorted(
                    thread.events,
                    key=lambda item: item.sequence,
                )
            ),
            report=report,
        )

    def _require_thread(
        self,
        thread_id: str,
    ) -> AnalysisThread:
        # 所有写操作都先锁住会话主记录，防止并发请求同时计算出相同的
        # checkpoint/event sequence。SQLite 测试会忽略 FOR UPDATE，
        # PostgreSQL 则会真正执行行级锁。
        thread = self._session.scalar(
            select(AnalysisThread).where(AnalysisThread.thread_id == thread_id).with_for_update()
        )

        if thread is None:
            raise AnalysisContextNotFoundError(f"分析会话不存在：{thread_id}")

        return thread

    def _require_run(
        self,
        run_id: str,
    ) -> AnalysisRun:
        run = self._session.get(AnalysisRun, run_id)

        if run is None:
            raise AnalysisContextNotFoundError(f"分析运行不存在：{run_id}")

        return run

    def _next_checkpoint_sequence(
        self,
        thread_id: str,
    ) -> int:
        current = self._session.scalar(
            select(func.max(AnalysisCheckpoint.sequence)).where(
                AnalysisCheckpoint.thread_id == thread_id,
            )
        )

        return int(current or 0) + 1

    def _next_event_sequence(
        self,
        thread_id: str,
    ) -> int:
        current = self._session.scalar(
            select(func.max(AnalysisEvent.sequence)).where(
                AnalysisEvent.thread_id == thread_id,
            )
        )

        return int(current or 0) + 1

    def _append_event(
        self,
        thread_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> AnalysisEvent:
        event = AnalysisEvent(
            event_id=new_id(),
            thread_id=thread_id,
            sequence=self._next_event_sequence(thread_id),
            event_type=event_type,
            payload=dict(payload),
        )

        self._session.add(event)
        return event

    def _commit(
        self,
        message: str,
    ) -> None:
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise AnalysisContextConflictError(message) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise AnalysisContextPersistenceError(message) from exc
