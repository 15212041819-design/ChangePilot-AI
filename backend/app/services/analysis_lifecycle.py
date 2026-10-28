"""正式证据分析流程的持久化生命周期服务。"""

from dataclasses import dataclass
from uuid import uuid4

from app.repositories.analysis_context import (
    AnalysisContextConflictError,
    SQLAlchemyAnalysisContextRepository,
)
from app.schemas.analysis_context import StoredAnalysisContext
from app.schemas.analysis_report import AnalysisReport
from app.schemas.evidence_analysis_api import (
    EvidenceAnalysisRequest,
    EvidenceAnalysisReviewRequest,
)
from app.services.evidence_analysis import (
    EvidenceAnalysisExecutor,
    EvidenceAnalysisReviewRequiredError,
)


@dataclass(frozen=True, slots=True)
class CompletedEvidenceAnalysis:
    """一次已经完成并成功持久化的分析。"""

    thread_id: str
    report: AnalysisReport


class PersistedEvidenceAnalysisReviewRequiredError(EvidenceAnalysisReviewRequiredError):
    """已经保存到数据库、可由前端继续的人工审核暂停。"""

    def __init__(
        self,
        *,
        thread_id: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.thread_id = thread_id


class EvidenceAnalysisLifecycleService:
    """协调分析执行器与 PostgreSQL 上下文仓储。"""

    def __init__(
        self,
        *,
        executor: EvidenceAnalysisExecutor,
        repository: SQLAlchemyAnalysisContextRepository,
    ) -> None:
        self._executor = executor
        self._repository = repository

    def execute(
        self,
        request: EvidenceAnalysisRequest,
    ) -> CompletedEvidenceAnalysis:
        """执行分析，并把成功、审核等待或失败状态写入数据库。"""

        thread_id = uuid4().hex

        self._repository.create_thread(
            thread_id=thread_id,
            repository_name=request.repository_name,
            commit_ref=request.commit,
            request_text=request.request_text,
        )

        run = self._repository.start_run(
            thread_id=thread_id,
            model_name=type(self._executor).__name__,
        )

        try:
            report = self._executor.analyze(
                request,
            )
        except EvidenceAnalysisReviewRequiredError as exc:
            self._repository.mark_review_required(
                thread_id=thread_id,
                run_id=run.run_id,
                reason=str(exc),
            )

            raise PersistedEvidenceAnalysisReviewRequiredError(
                thread_id=thread_id,
                message=str(exc),
            ) from exc
        except Exception as exc:
            self._repository.fail_run(
                thread_id=thread_id,
                run_id=run.run_id,
                error_code=type(exc).__name__,
                error_message=str(exc) or type(exc).__name__,
            )

            raise

        self._repository.complete_run(
            thread_id=thread_id,
            run_id=run.run_id,
            report=report,
        )

        return CompletedEvidenceAnalysis(
            thread_id=thread_id,
            report=report,
        )

    def get_context(
        self,
        thread_id: str,
    ) -> StoredAnalysisContext | None:
        """读取前端恢复分析页面所需的完整上下文。"""

        return self._repository.get_context(
            thread_id,
        )

    def submit_review(
        self,
        *,
        thread_id: str,
        decision: EvidenceAnalysisReviewRequest,
    ) -> StoredAnalysisContext:
        """保存人工决定，并按决定终止或恢复原分析会话。"""

        context = self._repository.get_context(
            thread_id,
        )

        if context is None:
            raise LookupError(f"分析会话不存在：{thread_id}")

        if context.thread.workflow_status != "awaiting_requirement_review":
            raise AnalysisContextConflictError("分析会话当前不需要人工审核。")

        # 所有审核决定都必须先写入数据库，确保后续失败仍有审计记录。
        self._repository.record_review_decision(
            thread_id=thread_id,
            action=decision.action,
            feedback=decision.feedback,
            actor_id=decision.actor_id,
        )

        # reject 只终止当前会话，不启动新的分析 run。
        if decision.action == "reject":
            self._repository.reject_review(
                thread_id=thread_id,
                feedback=decision.feedback,
            )

            return self._require_context(
                thread_id,
            )

        # approve 和 revise 都使用数据库保存的原始输入恢复分析，
        # 不信任前端重新提交 repository 或 commit。
        request = EvidenceAnalysisRequest(
            request_text=context.thread.request_text,
            repository_name=context.thread.repository_name,
            commit=context.thread.commit_ref,
        )

        run = self._repository.start_run(
            thread_id=thread_id,
            model_name=type(self._executor).__name__,
        )

        try:
            report = self._executor.analyze(
                request,
                review=decision,
            )
        except EvidenceAnalysisReviewRequiredError as exc:
            # revise 后仍然存在未确认假设时，允许再次暂停。
            self._repository.mark_review_required(
                thread_id=thread_id,
                run_id=run.run_id,
                reason=str(exc),
            )

            raise PersistedEvidenceAnalysisReviewRequiredError(
                thread_id=thread_id,
                message=str(exc),
            ) from exc
        except Exception as exc:
            self._repository.fail_run(
                thread_id=thread_id,
                run_id=run.run_id,
                error_code=type(exc).__name__,
                error_message=str(exc) or type(exc).__name__,
            )

            raise

        self._repository.complete_run(
            thread_id=thread_id,
            run_id=run.run_id,
            report=report,
        )

        return self._require_context(
            thread_id,
        )

    def _require_context(
        self,
        thread_id: str,
    ) -> StoredAnalysisContext:
        """读取刚完成写入的上下文；缺失表示持久化层状态异常。"""

        context = self._repository.get_context(
            thread_id,
        )

        if context is None:
            raise RuntimeError(f"分析上下文在写入后消失：{thread_id}")

        return context
