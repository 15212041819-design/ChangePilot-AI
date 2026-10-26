"""三路并行分析、稳定 fan-in 和结果审核工作流测试。"""

import json
from collections.abc import Mapping

from app.engines.branch_result import (
    build_candidate_set_branch_result,
)
from app.graph.parallel_analysis import (
    assert_state_json_serializable,
    build_parallel_analysis_graph,
    create_parallel_analysis_state,
)
from app.schemas.branch_result import (
    AnalysisBranch,
    BranchFinding,
    BranchResult,
    FindingPriority,
)
from app.schemas.contracts import (
    AcceptanceCriterion,
    Budget,
    CandidateSet,
    ChangePoint,
    EntityHint,
    EvidencePointer,
    FileRef,
    RequirementSpec,
    ScopeHints,
    SymbolRef,
)
from app.schemas.guard import GuardDigests

ZERO_HASH = "sha256:" + "0" * 64
ONE_HASH = "sha256:" + "1" * 64


def candidate_set() -> CandidateSet:
    """创建并行分支共享的冻结候选集。"""

    return CandidateSet(
        set_id="CS-0001",
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        files=(
            FileRef(
                path="app/models.py",
                content_sha256=ZERO_HASH,
                kind="source",
                reason="命中模型",
                score=1.0,
                confidence=0.9,
                selected_by="semantic_map",
            ),
            FileRef(
                path="app/service.py",
                content_sha256=ZERO_HASH,
                kind="source",
                reason="命中服务",
                score=1.0,
                confidence=0.9,
                selected_by="semantic_map",
            ),
        ),
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )


def requirement_spec() -> RequirementSpec:
    """创建工作流审核节点使用的唯一需求词汇表。"""

    return RequirementSpec(
        spec_id="RS-0001",
        spec_revision=1,
        summary="修改服务并补充相应测试。",
        change_type="feature",
        acceptance_criteria=(
            AcceptanceCriterion(
                id="AC-001",
                description=("服务变更拥有对应测试。"),
                verification="test",
                priority="P0",
            ),
        ),
        entity_hints=(
            EntityHint(
                concept_id="EH-001",
                concept="服务",
                lexical_variants=("service",),
                symbol_candidates=("Service",),
                role="action_target",
                in_repo_vocab=True,
            ),
        ),
        scope_hints=ScopeHints(),
        source_text=("修改服务并补充相应测试。"),
        spec_digest=ZERO_HASH,
    )


def change_points() -> tuple[
    ChangePoint,
    ...,
]:
    """创建已经通过 Guard 的 AC-001 实施变更点。"""

    return (
        ChangePoint(
            cp_id="CP-0001",
            path="app/service.py",
            target=SymbolRef(
                name="run",
                parent_symbol=("app.service.Service"),
                expect="existing",
                kind="method",
            ),
            change_type="semantic",
            ac_ids=("AC-001",),
            evidence=(
                EvidencePointer(
                    path="app/service.py",
                    line=10,
                    kind="def",
                ),
            ),
            confidence=0.9,
            source="impact",
        ),
    )


def finding(
    branch: AnalysisBranch,
    suffix: str,
    *,
    priority: FindingPriority,
    path: str,
) -> BranchFinding:
    """创建分支测试 finding。"""

    return BranchFinding(
        finding_id=(f"{branch.upper()}-{suffix}"),
        priority=priority,
        path=path,
        summary=(f"{branch} 分支结论 {suffix}"),
        ac_ids=("AC-001",),
        rule_ids=(f"{branch.upper()}-RULE",),
    )


def successful_runner(
    branch: AnalysisBranch,
):
    """创建返回完整覆盖结果的假分支执行器。"""

    def runner(
        frozen_set: CandidateSet,
        digests: GuardDigests,
        resources: Mapping[str, object],
    ) -> BranchResult:
        """验证资源通过闭包传入，而不是写进 state。"""

        assert resources["symbol_index"] is not None

        return build_candidate_set_branch_result(
            branch=branch,
            candidate_set=frozen_set,
            analyzed_paths=(
                "app/models.py",
                "app/service.py",
            ),
            findings=(
                finding(
                    branch,
                    "B",
                    priority="minor",
                    path="app/service.py",
                ),
                finding(
                    branch,
                    "A",
                    priority="major",
                    path="app/models.py",
                ),
            ),
            consumed_digests=digests,
        )

    return runner


def graph(
    *,
    impact_runner=None,
    test_runner=None,
    risk_runner=None,
    validated_change_points=None,
    include_review_resources: bool = True,
):
    """创建使用不可序列化运行期资源的测试图。"""

    branch_resources: dict[
        str,
        object,
    ] = {
        # object() 故意不可 JSON 序列化，
        # 用于证明资源没有进入 state。
        "symbol_index": object(),
    }

    if include_review_resources:
        branch_resources.update(
            {
                "requirement_spec": (requirement_spec()),
                "validated_change_points": (
                    change_points() if validated_change_points is None else validated_change_points
                ),
            }
        )

    return build_parallel_analysis_graph(
        impact_runner=(impact_runner or successful_runner("impact")),
        test_runner=(test_runner or successful_runner("test")),
        risk_runner=(risk_runner or successful_runner("risk")),
        resources={
            "RESOURCE-001": (branch_resources),
        },
    )


def initial_state() -> dict[str, object]:
    """创建可直接传入 LangGraph 的 JSON state。"""

    return create_parallel_analysis_state(
        candidate_set(),
        resource_id="RESOURCE-001",
    )


def test_t27_parallel_fan_in_has_stable_branch_and_finding_order() -> None:
    """T-27：并行完成顺序不能改变最终结果顺序。"""

    first = graph().invoke(initial_state())

    second = graph().invoke(initial_state())

    first_json = assert_state_json_serializable(first)

    second_json = assert_state_json_serializable(second)

    assert first_json == second_json

    assert [item["branch"] for item in first["ordered_results"]] == [
        "impact",
        "test",
        "risk",
    ]

    for result in first["ordered_results"]:
        assert [item["finding_id"] for item in result["findings"]] == [
            f"{result['branch'].upper()}-A",
            f"{result['branch'].upper()}-B",
        ]


def test_t28_state_is_json_serializable_and_excludes_resources() -> None:
    """T-28：state 只保存 resource_id，不保存索引对象。"""

    state = initial_state()

    serialized_initial = assert_state_json_serializable(state)

    result = graph().invoke(state)

    serialized_result = assert_state_json_serializable(result)

    assert "RESOURCE-001" in serialized_initial

    assert "RESOURCE-001" in serialized_result

    assert "symbol_index" not in serialized_initial

    assert "symbol_index" not in serialized_result

    # 再使用标准库解析一次，
    # 证明不是自定义字符串伪装。
    parsed = json.loads(serialized_result)

    assert parsed["parallel_status"] == "parallel_complete"

    assert parsed["status"] == "analysis_ready"

    assert parsed["review_report"]["status"] == "ready"


def test_t29_mismatched_branch_digest_fails_fan_in() -> None:
    """T-29：任一分支消费不同 digest 时必须阻断 fan-in。"""

    def mismatched_risk_runner(
        frozen_set: CandidateSet,
        digests: GuardDigests,
        resources: Mapping[str, object],
    ) -> BranchResult:
        assert resources["symbol_index"] is not None

        mismatched = digests.model_copy(
            update={
                "set_digest": ONE_HASH,
            }
        )

        return build_candidate_set_branch_result(
            branch="risk",
            candidate_set=frozen_set,
            analyzed_paths=(
                "app/models.py",
                "app/service.py",
            ),
            findings=(),
            consumed_digests=mismatched,
        )

    result = graph(
        risk_runner=mismatched_risk_runner,
    ).invoke(initial_state())

    assert result["parallel_status"] == "parallel_failed"

    assert result["status"] == "analysis_blocked"

    assert result["digest_guard"]["verdict"] == "fail"

    assert result["digest_guard"]["blocking_reasons"] == ["PARALLEL_DIGEST_MISMATCH"]

    assert result["digest_guard"]["invalid"][0]["finding_id"] == "DIGEST-RISK"

    assert result["review_report"]["status"] == "blocked"


def test_single_branch_exception_degrades_without_losing_other_results() -> None:
    """一个分支异常时，另外两个分支和 fan-in 仍然完成。"""

    def failing_test_runner(
        frozen_set: CandidateSet,
        digests: GuardDigests,
        resources: Mapping[str, object],
    ) -> BranchResult:
        del frozen_set
        del digests
        del resources

        raise RuntimeError("测试分支故障")

    result = graph(
        test_runner=failing_test_runner,
    ).invoke(initial_state())

    assert result["parallel_status"] == "parallel_degraded"

    assert result["status"] == "analysis_blocked"

    assert result["impact_result"]["status"] == "success"

    assert result["test_result"]["status"] == "failed"

    assert result["risk_result"]["status"] == "success"

    assert result["test_result"]["errors"][0]["message"] == "测试分支故障"

    assert result["digest_guard"]["verdict"] == "pass"

    assert result["review_report"]["status"] == "blocked"


def test_result_review_turns_degraded_risk_branch_into_gate() -> None:
    """三路可用但 Risk 覆盖不完整时，最终状态必须是 gated。"""

    def degraded_risk_runner(
        frozen_set: CandidateSet,
        digests: GuardDigests,
        resources: Mapping[str, object],
    ) -> BranchResult:
        assert resources["symbol_index"] is not None

        return build_candidate_set_branch_result(
            branch="risk",
            candidate_set=frozen_set,
            analyzed_paths=("app/models.py",),
            findings=(),
            consumed_digests=digests,
        )

    result = graph(
        risk_runner=degraded_risk_runner,
    ).invoke(initial_state())

    assert result["parallel_status"] == "parallel_degraded"

    assert result["status"] == "analysis_gated"

    assert result["review_report"]["status"] == "gated"

    assert result["review_report"]["issues"][0]["code"] == "REVIEW-BRANCH-DEGRADED"


def test_missing_validated_change_point_blocks_final_workflow() -> None:
    """AC 没有已验证 ChangePoint 时，审核节点必须阻断后续实施。"""

    result = graph(
        validated_change_points=(),
    ).invoke(initial_state())

    assert result["parallel_status"] == "parallel_complete"

    assert result["status"] == "analysis_blocked"

    assert result["review_report"]["status"] == "blocked"

    assert result["review_report"]["issues"][0]["code"] == "REVIEW-IMPLEMENTATION-GAP"


def test_review_resource_error_is_structured_and_blocks_workflow() -> None:
    """审核资源缺失不能让图崩溃，必须留下结构化错误。"""

    result = graph(
        include_review_resources=False,
    ).invoke(initial_state())

    assert result["parallel_status"] == "parallel_complete"

    assert result["status"] == "analysis_blocked"

    assert result["review_report"] is None

    assert result["review_error"]["code"] == "KeyError"

    assert "requirement_spec" in result["review_error"]["message"]
