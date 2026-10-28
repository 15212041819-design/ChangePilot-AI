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
from app.schemas.evidence_analysis_api import EvidenceAnalysisRequest
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
        repository = SQLAlchemyAnalysisContextRepository(session)
        lifecycle = EvidenceAnalysisLifecycleService(
            executor=SuccessfulExecutor(),
            repository=repository,
        )

        execution = lifecycle.execute(_request())
        context = lifecycle.get_context(execution.thread_id)

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
        repository = SQLAlchemyAnalysisContextRepository(session)
        lifecycle = EvidenceAnalysisLifecycleService(
            executor=FailedExecutor(),
            repository=repository,
        )

        with pytest.raises(
            ValueError,
            match="digest 不一致",
        ):
            lifecycle.execute(_request())

        thread_id = session.scalar(select(AnalysisThread.thread_id))

        assert thread_id is not None

        context = lifecycle.get_context(thread_id)

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
        repository = SQLAlchemyAnalysisContextRepository(session)
        lifecycle = EvidenceAnalysisLifecycleService(
            executor=ReviewRequiredExecutor(),
            repository=repository,
        )

        with pytest.raises(PersistedEvidenceAnalysisReviewRequiredError) as error:
            lifecycle.execute(_request())

        thread_id = error.value.thread_id

        context = lifecycle.get_context(thread_id)

        assert context is not None
        assert context.thread.workflow_status == "awaiting_requirement_review"
        assert context.runs[0].status == "paused"
        assert context.events[-1].event_type == "analysis.review_required"
