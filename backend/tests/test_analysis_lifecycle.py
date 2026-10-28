"""正式分析生命周期服务的持久化行为测试。"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_evidence_analysis_api import _build_ready_report

from app.db.models import AnalysisThread
from app.repositories.analysis_context import (
    SQLAlchemyAnalysisContextRepository,
)
from app.schemas.analysis_report import AnalysisReport
from app.schemas.evidence_analysis_api import (
    EvidenceAnalysisRequest,
    EvidenceAnalysisReviewRequest,
)
from app.services.analysis_lifecycle import (
    EvidenceAnalysisLifecycleService,
    PersistedEvidenceAnalysisReviewRequiredError,
)
from app.services.evidence_analysis import (
    EvidenceAnalysisReviewRequiredError,
)


class SuccessfulExecutor:
    """返回可信报告的测试执行器。"""

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
    ) -> AnalysisReport:
        assert request.repository_name == "order-service"

        return _build_ready_report()


class FailedExecutor:
    """模拟分析阶段契约失败。"""

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
    ) -> AnalysisReport:
        raise ValueError("CandidateSet digest 不一致。")


class ReviewRequiredExecutor:
    """模拟需求需要人工确认。"""

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
    ) -> AnalysisReport:
        raise EvidenceAnalysisReviewRequiredError("需求存在未确认假设。")


class ResumableExecutor:
    """首次暂停，收到批准后返回可信报告。"""

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
        *,
        review: EvidenceAnalysisReviewRequest | None = None,
    ) -> AnalysisReport:
        if review is None:
            raise EvidenceAnalysisReviewRequiredError("需求存在未确认假设。")

        assert review.action == "approve"
        assert review.feedback == "确认现有假设"

        return _build_ready_report()


class RevisableExecutor:
    """修订后仍产生新假设，用于验证再次暂停。"""

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
        *,
        review: EvidenceAnalysisReviewRequest | None = None,
    ) -> AnalysisReport:
        message = "修订后仍存在未确认假设。" if review is not None else "需求存在未确认假设。"

        raise EvidenceAnalysisReviewRequiredError(
            message,
        )


def _request() -> EvidenceAnalysisRequest:
    """构造已经通过 HTTP 输入清理的请求。"""

    return EvidenceAnalysisRequest(
        request_text="为订单系统增加取消订单功能。",
        repository_name="order-service",
        commit="HEAD",
    )


def test_lifecycle_persists_completed_report(
    api_session_factory: sessionmaker[Session],
) -> None:
    """成功执行应保存真实 thread_id、运行记录和统一报告。"""

    with api_session_factory() as session:
        repository = SQLAlchemyAnalysisContextRepository(
            session,
        )
        lifecycle = EvidenceAnalysisLifecycleService(
            executor=SuccessfulExecutor(),
            repository=repository,
        )

        execution = lifecycle.execute(
            _request(),
        )
        context = lifecycle.get_context(
            execution.thread_id,
        )

        assert context is not None
        assert context.thread.workflow_status == "completed"
        assert context.thread.repository_name == "order-service"
        assert context.thread.commit_ref == "HEAD"
        assert context.runs[0].status == "completed"
        assert context.report == execution.report
        assert [event.event_type for event in context.events] == [
            "analysis.created",
            "analysis.started",
            "analysis.completed",
        ]


def test_lifecycle_persists_executor_failure(
    api_session_factory: sessionmaker[Session],
) -> None:
    """执行器异常必须留下 failed 上下文，而不是丢失现场。"""

    with api_session_factory() as session:
        repository = SQLAlchemyAnalysisContextRepository(
            session,
        )
        lifecycle = EvidenceAnalysisLifecycleService(
            executor=FailedExecutor(),
            repository=repository,
        )

        with pytest.raises(
            ValueError,
            match="digest 不一致",
        ):
            lifecycle.execute(
                _request(),
            )

        thread_id = session.scalar(
            select(
                AnalysisThread.thread_id,
            )
        )

        assert thread_id is not None

        context = lifecycle.get_context(
            thread_id,
        )

        assert context is not None
        assert context.thread.workflow_status == "failed"
        assert context.runs[0].status == "failed"
        assert context.runs[0].error_code == "ValueError"
        assert context.events[-1].event_type == "analysis.failed"


def test_lifecycle_persists_review_required_state(
    api_session_factory: sessionmaker[Session],
) -> None:
    """人工审核异常必须保存 paused 运行和等待审核状态。"""

    with api_session_factory() as session:
        repository = SQLAlchemyAnalysisContextRepository(
            session,
        )
        lifecycle = EvidenceAnalysisLifecycleService(
            executor=ReviewRequiredExecutor(),
            repository=repository,
        )

        with pytest.raises(
            PersistedEvidenceAnalysisReviewRequiredError,
        ) as error:
            lifecycle.execute(
                _request(),
            )

        thread_id = error.value.thread_id

        context = lifecycle.get_context(
            thread_id,
        )

        assert context is not None
        assert context.thread.workflow_status == "awaiting_requirement_review"
        assert context.runs[0].status == "paused"
        assert context.events[-1].event_type == "analysis.review_required"


def test_lifecycle_approval_resumes_same_thread(
    api_session_factory: sessionmaker[Session],
) -> None:
    """批准应复用原 thread_id、创建新 run 并保存最终报告。"""

    with api_session_factory() as session:
        lifecycle = EvidenceAnalysisLifecycleService(
            executor=ResumableExecutor(),
            repository=SQLAlchemyAnalysisContextRepository(
                session,
            ),
        )

        with pytest.raises(
            PersistedEvidenceAnalysisReviewRequiredError,
        ) as error:
            lifecycle.execute(
                _request(),
            )

        context = lifecycle.submit_review(
            thread_id=error.value.thread_id,
            decision=EvidenceAnalysisReviewRequest(
                action="approve",
                feedback="  确认现有假设  ",
                actor_id="reviewer-1",
            ),
        )

        assert context.thread.thread_id == error.value.thread_id
        assert context.thread.workflow_status == "completed"
        assert [run.status for run in context.runs] == [
            "paused",
            "completed",
        ]
        assert context.review_decisions[0].action == "approve"
        assert context.review_decisions[0].actor_id == "reviewer-1"
        assert context.report is not None
        assert [event.event_type for event in context.events] == [
            "analysis.created",
            "analysis.started",
            "analysis.review_required",
            "review.recorded",
            "analysis.started",
            "analysis.completed",
        ]


def test_lifecycle_revision_can_pause_again(
    api_session_factory: sessionmaker[Session],
) -> None:
    """修订后若仍有假设，应保留决定并再次进入等待状态。"""

    with api_session_factory() as session:
        lifecycle = EvidenceAnalysisLifecycleService(
            executor=RevisableExecutor(),
            repository=SQLAlchemyAnalysisContextRepository(
                session,
            ),
        )

        with pytest.raises(
            PersistedEvidenceAnalysisReviewRequiredError,
        ) as first:
            lifecycle.execute(
                _request(),
            )

        with pytest.raises(
            PersistedEvidenceAnalysisReviewRequiredError,
        ) as second:
            lifecycle.submit_review(
                thread_id=first.value.thread_id,
                decision=EvidenceAnalysisReviewRequest(
                    action="revise",
                    feedback="补充管理员权限边界。",
                ),
            )

        assert second.value.thread_id == first.value.thread_id

        context = lifecycle.get_context(
            first.value.thread_id,
        )

        assert context is not None
        assert context.thread.workflow_status == "awaiting_requirement_review"
        assert [run.status for run in context.runs] == [
            "paused",
            "paused",
        ]
        assert context.review_decisions[0].action == "revise"


def test_lifecycle_rejection_terminates_without_new_run(
    api_session_factory: sessionmaker[Session],
) -> None:
    """拒绝只终止会话，不应启动一次没有意义的新分析。"""

    with api_session_factory() as session:
        lifecycle = EvidenceAnalysisLifecycleService(
            executor=ReviewRequiredExecutor(),
            repository=SQLAlchemyAnalysisContextRepository(
                session,
            ),
        )

        with pytest.raises(
            PersistedEvidenceAnalysisReviewRequiredError,
        ) as error:
            lifecycle.execute(
                _request(),
            )

        context = lifecycle.submit_review(
            thread_id=error.value.thread_id,
            decision=EvidenceAnalysisReviewRequest(
                action="reject",
                feedback="本次需求暂不实施。",
            ),
        )

        assert context.thread.workflow_status == "rejected"
        assert len(context.runs) == 1
        assert context.runs[0].status == "paused"
        assert context.review_decisions[0].action == "reject"
        assert context.events[-1].event_type == "analysis.rejected"
