"""Evidence-grounded 架构的三路并行分析骨架。

本模块暂时不替换旧的 ``change_analysis.py``。它先固定并验证以下编排约束：

1. Impact、Test、Risk 从同一个 CandidateSet 并行启动；
2. 每个分支只写自己的 state key；
3. 单路异常被转换成结构化 BranchResult，不让整个 superstep 崩溃；
4. fan-in 始终按 Impact、Test、Risk 顺序合并；
5. fan-in 前校验三个分支消费的 digest 是否完全一致；
6. state 只保存 JSON 数据，索引等资源通过闭包中的资源容器传入。
"""

import json
from collections.abc import Callable, Mapping
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.engines.branch_result import (
    build_failed_branch_result,
    source_file_assignments,
)
from app.schemas.branch_result import (
    AnalysisBranch,
    BranchResult,
)
from app.schemas.contracts import CandidateSet
from app.schemas.guard import (
    GuardDigests,
    GuardReport,
    InvalidFinding,
)

BranchRunner = Callable[
    [
        CandidateSet,
        GuardDigests,
        Mapping[str, object],
    ],
    BranchResult,
]

BRANCH_ORDER: tuple[
    AnalysisBranch,
    ...,
] = (
    "impact",
    "test",
    "risk",
)

BRANCH_STATE_KEYS: dict[
    AnalysisBranch,
    str,
] = {
    "impact": "impact_result",
    "test": "test_result",
    "risk": "risk_result",
}


class ParallelAnalysisState(TypedDict):
    """只包含可 JSON 序列化数据的并行工作流状态。"""

    candidate_set: dict[str, Any]

    digests: dict[str, Any]

    resource_id: str

    impact_result: (
        dict[
            str,
            Any,
        ]
        | None
    )

    test_result: (
        dict[
            str,
            Any,
        ]
        | None
    )

    risk_result: (
        dict[
            str,
            Any,
        ]
        | None
    )

    ordered_results: list[dict[str, Any]]

    digest_guard: (
        dict[
            str,
            Any,
        ]
        | None
    )

    status: str


def create_parallel_analysis_state(
    candidate_set: CandidateSet,
    *,
    resource_id: str,
) -> ParallelAnalysisState:
    """把冻结候选集转换为可检查点保存的初始状态。"""

    if not resource_id.strip():
        raise ValueError("resource_id 不能为空。")

    digests = GuardDigests(
        set_digest=(candidate_set.set_digest),
        spec_digest=(candidate_set.spec_digest),
        index_digest=(candidate_set.index_digest),
    )

    return ParallelAnalysisState(
        candidate_set=(candidate_set.model_dump(mode="json")),
        digests=digests.model_dump(mode="json"),
        resource_id=resource_id,
        impact_result=None,
        test_result=None,
        risk_result=None,
        ordered_results=[],
        digest_guard=None,
        status="parallel_ready",
    )


def assert_state_json_serializable(
    state: Mapping[str, object],
) -> str:
    """序列化 state；出现运行期资源对象时立即抛出 TypeError。"""

    return json.dumps(
        state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    )


def _load_inputs(
    state: ParallelAnalysisState,
) -> tuple[
    CandidateSet,
    GuardDigests,
]:
    """从 JSON state 恢复冻结候选集和上游摘要。"""

    return (
        CandidateSet.model_validate(state["candidate_set"]),
        GuardDigests.model_validate(state["digests"]),
    )


def _create_branch_node(
    branch: AnalysisBranch,
    runner: BranchRunner,
    *,
    resources: Mapping[
        str,
        object,
    ],
) -> Callable[
    [ParallelAnalysisState],
    dict[str, object],
]:
    """创建一个具有异常降级能力的并行分支节点。"""

    state_key = BRANCH_STATE_KEYS[branch]

    def branch_node(
        state: ParallelAnalysisState,
    ) -> dict[str, object]:
        """执行分支，并确保异常不会逃出并行 superstep。"""

        (
            candidate_set,
            digests,
        ) = _load_inputs(state)

        resource_id = state["resource_id"]

        try:
            branch_resources = resources[resource_id]

            if not isinstance(
                branch_resources,
                Mapping,
            ):
                raise TypeError("资源容器中的 resource_id 必须映射到 Mapping。")

            result = runner(
                candidate_set,
                digests,
                branch_resources,
            )

            if result.branch != branch:
                raise ValueError(f"{branch} 节点返回了 {result.branch} 分支结果。")

        except Exception as error:  # noqa: BLE001
            result = build_failed_branch_result(
                branch=branch,
                assigned_items=(source_file_assignments(candidate_set)),
                unit="file",
                consumed_digests=(digests),
                error=error,
            )

        return {
            state_key: result.model_dump(mode="json"),
        }

    return branch_node


def _ordered_branch_results(
    state: ParallelAnalysisState,
) -> tuple[
    BranchResult,
    ...,
]:
    """按固定分支顺序读取结果，禁止依赖节点完成顺序。"""

    results: list[BranchResult] = []

    for branch in BRANCH_ORDER:
        state_key = BRANCH_STATE_KEYS[branch]

        raw_result = state[state_key]  # type: ignore[literal-required]

        if raw_result is None:
            raise RuntimeError(f"fan-in 缺少 {branch} 分支结果。")

        result = BranchResult.model_validate(raw_result)

        if result.branch != branch:
            raise RuntimeError(f"state key {state_key} 保存了错误分支结果。")

        results.append(result)

    return tuple(results)


def validate_parallel_digests(
    results: tuple[
        BranchResult,
        ...,
    ],
    *,
    expected: GuardDigests,
) -> GuardReport:
    """对三路 BranchResult 执行 digest 硬校验。"""

    invalid = tuple(
        InvalidFinding(
            finding_id=(f"DIGEST-{result.branch.upper()}"),
            error_type=("digest_mismatch"),
            detail=(f"{result.branch} 分支消费的 digest 与冻结 CandidateSet 不一致。"),
            repairable=False,
        )
        for result in results
        if (result.consumed_digests != expected)
    )

    if invalid:
        verdict: Literal[
            "pass",
            "fail",
        ] = "fail"

        blocking_reasons = ("PARALLEL_DIGEST_MISMATCH",)

    else:
        verdict = "pass"
        blocking_reasons = ()

    return GuardReport(
        branch="review",
        branch_kind="engine",
        checked_count=len(results),
        invalid=invalid,
        invalid_ratio=round(
            len(invalid)
            / max(
                len(results),
                1,
            ),
            3,
        ),
        repair_attempt=0,
        verdict=verdict,
        blocking_reasons=(blocking_reasons),
        digests_echo=expected,
        guard_version="1.0.0",
    )


def fan_in_parallel_results(
    state: ParallelAnalysisState,
) -> dict[str, object]:
    """稳定合并三路结果，并计算整个并行阶段的状态。"""

    results = _ordered_branch_results(state)

    expected = GuardDigests.model_validate(state["digests"])

    digest_guard = validate_parallel_digests(
        results,
        expected=expected,
    )

    if digest_guard.verdict == "fail":
        status = "parallel_failed"

    elif any(result.status != "success" for result in results):
        status = "parallel_degraded"

    else:
        status = "parallel_complete"

    return {
        "ordered_results": [result.model_dump(mode="json") for result in results],
        "digest_guard": (digest_guard.model_dump(mode="json")),
        "status": status,
    }


def build_parallel_analysis_graph(
    *,
    impact_runner: BranchRunner,
    test_runner: BranchRunner,
    risk_runner: BranchRunner,
    resources: Mapping[
        str,
        object,
    ],
):
    """构建并编译 Impact、Test、Risk 三路并行工作流。"""

    builder = StateGraph(ParallelAnalysisState)

    builder.add_node(
        "impact_branch",
        _create_branch_node(
            "impact",
            impact_runner,
            resources=resources,
        ),
    )

    builder.add_node(
        "test_branch",
        _create_branch_node(
            "test",
            test_runner,
            resources=resources,
        ),
    )

    builder.add_node(
        "risk_branch",
        _create_branch_node(
            "risk",
            risk_runner,
            resources=resources,
        ),
    )

    builder.add_node(
        "fan_in",
        fan_in_parallel_results,
    )

    builder.add_edge(
        START,
        "impact_branch",
    )

    builder.add_edge(
        START,
        "test_branch",
    )

    builder.add_edge(
        START,
        "risk_branch",
    )

    builder.add_edge(
        [
            "impact_branch",
            "test_branch",
            "risk_branch",
        ],
        "fan_in",
    )

    builder.add_edge(
        "fan_in",
        END,
    )

    return builder.compile()
