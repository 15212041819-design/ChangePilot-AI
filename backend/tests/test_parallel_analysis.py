"""三路并行分析骨架、稳定 fan-in 和 digest 守卫测试。"""

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
)
from app.schemas.contracts import (
    Budget,
    CandidateSet,
    FileRef,
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


def finding(
    branch: AnalysisBranch,
    suffix: str,
    *,
    priority: str,
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
        resources: Mapping[
            str,
            object,
        ],
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
                    path=("app/service.py"),
                ),
                finding(
                    branch,
                    "A",
                    priority="major",
                    path=("app/models.py"),
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
):
    """创建使用不可序列化运行期资源的测试图。"""

    return build_parallel_analysis_graph(
        impact_runner=(impact_runner or successful_runner("impact")),
        test_runner=(test_runner or successful_runner("test")),
        risk_runner=(risk_runner or successful_runner("risk")),
        resources={
            "RESOURCE-001": {
                # object() 故意不可 JSON 序列化，
                # 用于证明资源没有进入 state。
                "symbol_index": object(),
            }
        },
    )


def initial_state() -> dict[
    str,
    object,
]:
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
            (f"{result['branch'].upper()}-A"),
            (f"{result['branch'].upper()}-B"),
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
    assert json.loads(serialized_result)["status"] == "parallel_complete"


def test_t29_mismatched_branch_digest_fails_fan_in() -> None:
    """T-29：任一分支消费不同 digest 时必须阻断 fan-in。"""

    def mismatched_risk_runner(
        frozen_set: CandidateSet,
        digests: GuardDigests,
        resources: Mapping[
            str,
            object,
        ],
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
            consumed_digests=(mismatched),
        )

    result = graph(
        risk_runner=(mismatched_risk_runner),
    ).invoke(initial_state())

    assert result["status"] == "parallel_failed"

    assert result["digest_guard"]["verdict"] == "fail"

    assert result["digest_guard"]["blocking_reasons"] == [
        "PARALLEL_DIGEST_MISMATCH",
    ]

    assert result["digest_guard"]["invalid"][0]["finding_id"] == "DIGEST-RISK"


def test_single_branch_exception_degrades_without_losing_other_results() -> None:
    """一个分支异常时，另外两个分支和 fan-in 仍然完成。"""

    def failing_test_runner(
        frozen_set: CandidateSet,
        digests: GuardDigests,
        resources: Mapping[
            str,
            object,
        ],
    ) -> BranchResult:
        del (
            frozen_set,
            digests,
            resources,
        )

        raise RuntimeError("测试分支故障")

    result = graph(
        test_runner=failing_test_runner,
    ).invoke(initial_state())

    assert result["status"] == "parallel_degraded"

    assert result["impact_result"]["status"] == "success"

    assert result["test_result"]["status"] == "failed"

    assert result["risk_result"]["status"] == "success"

    assert result["test_result"]["errors"][0]["message"] == "测试分支故障"

    assert result["digest_guard"]["verdict"] == "pass"
