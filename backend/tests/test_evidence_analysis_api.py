"""Evidence-grounded HTTP API 边界测试。"""

from fastapi.testclient import TestClient

from app.engines.branch_result import build_branch_result
from app.engines.report_builder import build_analysis_report
from app.main import app
from app.schemas.analysis_report import AnalysisReport
from app.schemas.branch_result import BranchFinding
from app.schemas.contracts import (
    AcceptanceCriterion,
    Budget,
    CandidateSet,
    EntityHint,
    FileRef,
    RequirementSpec,
    ScopeHints,
)
from app.schemas.evidence_analysis_api import EvidenceAnalysisRequest
from app.schemas.guard import GuardDigests
from app.schemas.review import (
    AcceptanceCoverage,
    BranchReviewSummary,
    ReviewReport,
)
from app.services.evidence_analysis import (
    EvidenceAnalysisReviewRequiredError,
    get_evidence_analysis_executor,
)

ZERO_HASH = "sha256:" + "0" * 64


def _build_ready_report() -> AnalysisReport:
    """构造一份通过全部契约校验的最小 ready 报告。"""

    digests = GuardDigests(
        set_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        index_digest=ZERO_HASH,
    )

    requirement = RequirementSpec(
        spec_id="RS-0001",
        spec_revision=1,
        summary="订单所属用户可以取消待支付订单。",
        change_type="feature",
        acceptance_criteria=(
            AcceptanceCriterion(
                id="AC-001",
                description="订单所属用户可以取消待支付订单。",
                verification="test",
                priority="P0",
            ),
        ),
        entity_hints=(
            EntityHint(
                concept_id="EH-001",
                concept="取消订单",
                lexical_variants=("cancel order",),
                symbol_candidates=("cancel_order",),
                role="action_target",
                in_repo_vocab=True,
            ),
        ),
        scope_hints=ScopeHints(),
        source_text="为订单系统增加取消订单功能。",
        spec_digest=ZERO_HASH,
    )

    candidates = CandidateSet(
        set_id="CS-0001",
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        files=(
            FileRef(
                path="order_app/service.py",
                content_sha256=ZERO_HASH,
                kind="source",
                reason="命中订单服务",
                score=1.0,
                confidence=0.9,
                selected_by="semantic_map",
            ),
            FileRef(
                path="tests/test_service.py",
                content_sha256=ZERO_HASH,
                kind="test",
                reason="命中服务测试",
                score=0.8,
                confidence=0.8,
                selected_by="semantic_map",
            ),
        ),
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )

    impact = build_branch_result(
        branch="impact",
        assigned_items=("order_app/service.py",),
        analyzed_items=("order_app/service.py",),
        unit="file",
        findings=(
            BranchFinding(
                finding_id="IMP-001",
                priority="major",
                path="order_app/service.py",
                summary="需要增加 cancel_order 业务逻辑。",
                ac_ids=("AC-001",),
            ),
        ),
        consumed_digests=digests,
    )

    test = build_branch_result(
        branch="test",
        assigned_items=("tests/test_service.py",),
        analyzed_items=("tests/test_service.py",),
        unit="file",
        findings=(
            BranchFinding(
                finding_id="TEST-001",
                priority="major",
                path="tests/test_service.py",
                summary="需要补充待支付订单取消测试。",
                ac_ids=("AC-001",),
            ),
        ),
        consumed_digests=digests,
    )

    risk = build_branch_result(
        branch="risk",
        assigned_items=("order_app/service.py",),
        analyzed_items=("order_app/service.py",),
        unit="file",
        findings=(),
        consumed_digests=digests,
    )

    branch_results = (
        impact,
        test,
        risk,
    )

    review = ReviewReport(
        status="ready",
        issues=(),
        acceptance_coverage=(
            AcceptanceCoverage(
                ac_id="AC-001",
                change_point_ids=("CP-0001",),
                impact_finding_ids=("IMP-001",),
                test_finding_ids=("TEST-001",),
                implementation_covered=True,
                test_covered=True,
            ),
        ),
        branches=tuple(
            BranchReviewSummary(
                branch=result.branch,
                status=result.status,
                finding_count=len(result.findings),
                blocker_count=sum(finding.priority == "blocker" for finding in result.findings),
                coverage_assigned=result.coverage.assigned,
                coverage_analyzed=result.coverage.analyzed,
                coverage_ratio=result.coverage.ratio,
            )
            for result in branch_results
        ),
        reviewed_digests=digests,
    )

    return build_analysis_report(
        requirement_spec=requirement,
        candidate_set=candidates,
        review_report=review,
        branch_results=branch_results,
    )


class FakeEvidenceAnalysisExecutor:
    """记录 API 传入值并返回可信报告的测试执行器。"""

    def __init__(self) -> None:
        self.requests: list[EvidenceAnalysisRequest] = []

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
    ) -> AnalysisReport:
        self.requests.append(request)
        return _build_ready_report()


def test_evidence_analysis_endpoint_returns_unified_report(
    api_client: TestClient,
) -> None:
    """新接口应返回前端可直接消费的统一报告。"""

    executor = FakeEvidenceAnalysisExecutor()

    app.dependency_overrides[get_evidence_analysis_executor] = lambda: executor

    try:
        response = api_client.post(
            "/api/v1/changes/evidence/analyze",
            json={
                "request_text": "  为订单系统增加取消订单功能  ",
                "repository_name": "  order_service  ",
                "commit": "  HEAD  ",
            },
        )

        assert response.status_code == 200

        body = response.json()

        assert len(body["thread_id"]) == 32
        assert body["status"] == "ready"
        assert body["report"]["overview"]["can_implement"] is True
        assert body["report"]["requirement"]["spec_id"] == "RS-0001"
        assert executor.requests[0].request_text == "为订单系统增加取消订单功能"
        assert executor.requests[0].repository_name == "order_service"
        assert executor.requests[0].commit == "HEAD"

        stored_response = api_client.get(f"/api/v1/analyses/{body['thread_id']}")

        assert stored_response.status_code == 200

        stored = stored_response.json()

        assert stored["thread"]["workflow_status"] == "completed"
        assert stored["report"]["report_id"] == body["report"]["report_id"]
        assert [event["event_type"] for event in stored["events"]] == [
            "analysis.created",
            "analysis.started",
            "analysis.completed",
        ]

    finally:
        app.dependency_overrides.pop(
            get_evidence_analysis_executor,
            None,
        )


def test_evidence_analysis_endpoint_rejects_blank_text(
    api_client: TestClient,
) -> None:
    """只包含空格的需求不得进入执行层。"""

    app.dependency_overrides[get_evidence_analysis_executor] = FakeEvidenceAnalysisExecutor

    try:
        response = api_client.post(
            "/api/v1/changes/evidence/analyze",
            json={
                "request_text": "   ",
                "repository_name": "order_service",
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "变更需求不能为空。"

    finally:
        app.dependency_overrides.pop(
            get_evidence_analysis_executor,
            None,
        )


def test_evidence_analysis_endpoint_returns_404_for_unknown_repository(
    api_client: TestClient,
) -> None:
    """生产执行器已接线，未知仓库应返回 404 而不是旧的 503。"""

    response = api_client.post(
        "/api/v1/changes/evidence/analyze",
        json={
            "request_text": "为订单系统增加取消订单功能",
            "repository_name": "repository_that_does_not_exist",
        },
    )

    assert response.status_code == 404
    assert "不存在" in response.json()["detail"]


def test_evidence_analysis_endpoint_converts_contract_error_to_502(
    api_client: TestClient,
) -> None:
    """执行层返回契约错误时应该标记为上游无效响应。"""

    class InvalidExecutor:
        """模拟 digest 或上游结果不一致。"""

        def analyze(
            self,
            request: EvidenceAnalysisRequest,
        ) -> AnalysisReport:
            raise ValueError("CandidateSet digest 不一致。")

    app.dependency_overrides[get_evidence_analysis_executor] = InvalidExecutor

    try:
        response = api_client.post(
            "/api/v1/changes/evidence/analyze",
            json={
                "request_text": "为订单系统增加取消订单功能",
                "repository_name": "order_service",
            },
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "CandidateSet digest 不一致。"

    finally:
        app.dependency_overrides.pop(
            get_evidence_analysis_executor,
            None,
        )


def test_review_required_response_contains_persisted_thread_id(
    api_client: TestClient,
) -> None:
    """409 必须返回可供前端恢复审核流程的真实 thread_id。"""

    class ReviewRequiredExecutor:
        """模拟需求需要人工确认。"""

        def analyze(
            self,
            request: EvidenceAnalysisRequest,
        ) -> AnalysisReport:
            raise EvidenceAnalysisReviewRequiredError("需求存在未确认假设。")

    app.dependency_overrides[get_evidence_analysis_executor] = ReviewRequiredExecutor

    try:
        response = api_client.post(
            "/api/v1/analyses",
            json={
                "request_text": "为订单系统增加取消订单功能",
                "repository_name": "order_service",
            },
        )

        assert response.status_code == 409

        detail = response.json()["detail"]

        assert len(detail["thread_id"]) == 32
        assert detail["workflow_status"] == "awaiting_requirement_review"

        stored = api_client.get(f"/api/v1/analyses/{detail['thread_id']}")

        assert stored.status_code == 200
        assert stored.json()["thread"]["workflow_status"] == "awaiting_requirement_review"
    finally:
        app.dependency_overrides.pop(
            get_evidence_analysis_executor,
            None,
        )
