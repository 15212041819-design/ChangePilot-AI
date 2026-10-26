"""Evidence-grounded 架构的三路并行分析、审核与报告工作流。

本模块暂时不替换旧的 ``change_analysis.py``。它固定并验证以下约束：

1. Impact、Test、Risk 从同一个 CandidateSet 并行启动；
2. 每个分支只写自己的 state key；
3. 单路异常被转换成结构化 BranchResult；
4. fan-in 始终按 Impact、Test、Risk 顺序合并；
5. fan-in 前校验三个分支消费的 digest；
6. fan-in 后执行 AC 覆盖和结论一致性审核；
7. 审核后构建供 API 和前端消费的统一报告；
8. state 只保存 JSON 数据，运行期资源保存在 state 外。
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
from app.schemas.contracts import (
    CandidateSet,
    ChangePoint,
    RequirementSpec,
)
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
    """只包含可 JSON 序列化数据的工作流状态。"""

    candidate_set: dict[str, Any]

    digests: dict[str, Any]

    resource_id: str

    impact_result: dict[str, Any] | None

    test_result: dict[str, Any] | None

    risk_result: dict[str, Any] | None

    ordered_results: list[dict[str, Any]]

    digest_guard: (
        dict[
            str,
            Any,
        ]
        | None
    )

    # 仅表示三个并行分析分支的运行结果。
    parallel_status: str

    # 结果审核引擎输出。
    review_report: (
        dict[
            str,
            Any,
        ]
        | None
    )

    # 审核节点自身的运行错误。
    review_error: (
        dict[
            str,
            str,
        ]
        | None
    )

    # 前端和 API 使用的统一报告。
    analysis_report: (
        dict[
            str,
            Any,
        ]
        | None
    )

    # 报告构建节点自身的运行错误。
    report_error: (
        dict[
            str,
            str,
        ]
        | None
    )

    # 整个工作流最终状态。
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
        parallel_status="parallel_ready",
        review_report=None,
        review_error=None,
        analysis_report=None,
        report_error=None,
        status="analysis_pending",
    )


def assert_state_json_serializable(
    state: Mapping[str, object],
) -> str:
    """序列化 state，运行期资源进入 state 时立即失败。"""

    return json.dumps(
        state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _load_inputs(
    state: ParallelAnalysisState,
) -> tuple[
    CandidateSet,
    GuardDigests,
]:
    """从 JSON state 恢复候选集和摘要。"""

    return (
        CandidateSet.model_validate(state["candidate_set"]),
        GuardDigests.model_validate(state["digests"]),
    )


def _create_branch_node(
    branch: AnalysisBranch,
    runner: BranchRunner,
    *,
    resources: Mapping[str, object],
) -> Callable[
    [ParallelAnalysisState],
    dict[str, object],
]:
    """创建具有异常降级能力的并行分支节点。"""

    state_key = BRANCH_STATE_KEYS[branch]

    def branch_node(
        state: ParallelAnalysisState,
    ) -> dict[str, object]:
        """执行单个分支，异常不得逃出并行阶段。"""

        candidate_set, digests = _load_inputs(state)

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
            # 单个分支失败时生成结构化失败结果，
            # 让其他分支和 fan-in 继续完成。
            result = build_failed_branch_result(
                branch=branch,
                assigned_items=(source_file_assignments(candidate_set)),
                unit="file",
                consumed_digests=digests,
                error=error,
            )

        return {
            state_key: result.model_dump(mode="json"),
        }

    return branch_node


def _ordered_branch_results(
    state: ParallelAnalysisState,
) -> tuple[BranchResult, ...]:
    """按固定顺序读取三路结果。"""

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
            error_type="digest_mismatch",
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
    """稳定合并三路结果并计算并行阶段状态。"""

    results = _ordered_branch_results(state)

    expected = GuardDigests.model_validate(state["digests"])

    digest_guard = validate_parallel_digests(
        results,
        expected=expected,
    )

    if digest_guard.verdict == "fail":
        parallel_status = "parallel_failed"

    elif any(result.status != "success" for result in results):
        parallel_status = "parallel_degraded"

    else:
        parallel_status = "parallel_complete"

    return {
        "ordered_results": [result.model_dump(mode="json") for result in results],
        "digest_guard": (digest_guard.model_dump(mode="json")),
        "parallel_status": (parallel_status),
    }


def _create_result_review_node(
    *,
    resources: Mapping[str, object],
) -> Callable[
    [ParallelAnalysisState],
    dict[str, object],
]:
    """创建结果审核节点。"""

    def result_review_node(
        state: ParallelAnalysisState,
    ) -> dict[str, object]:
        """审核 AC 覆盖、digest 和结论一致性。"""

        try:
            resource_id = state["resource_id"]

            branch_resources = resources[resource_id]

            if not isinstance(
                branch_resources,
                Mapping,
            ):
                raise TypeError("资源容器中的 resource_id 必须映射到 Mapping。")

            raw_spec = branch_resources.get("requirement_spec")

            raw_change_points = branch_resources.get("validated_change_points")

            if raw_spec is None:
                raise KeyError("结果审核缺少 requirement_spec 资源。")

            if raw_change_points is None:
                raise KeyError("结果审核缺少 validated_change_points 资源。")

            if not isinstance(
                raw_change_points,
                (list, tuple),
            ):
                raise TypeError("validated_change_points 必须是列表或元组。")

            requirement_spec = RequirementSpec.model_validate(raw_spec)

            change_points = tuple(ChangePoint.model_validate(item) for item in raw_change_points)

            branch_results = tuple(
                BranchResult.model_validate(item) for item in state["ordered_results"]
            )

            expected_digests = GuardDigests.model_validate(state["digests"])

            # 延迟导入避免与图模块形成
            # 初始化阶段循环导入。
            from app.engines.result_review import (
                review_parallel_results,
            )

            report = review_parallel_results(
                requirement_spec=(requirement_spec),
                change_points=change_points,
                branch_results=branch_results,
                expected_digests=(expected_digests),
            )

            final_status = {
                "ready": "analysis_ready",
                "gated": "analysis_gated",
                "blocked": ("analysis_blocked"),
            }[report.status]

            return {
                "review_report": (report.model_dump(mode="json")),
                "review_error": None,
                "status": final_status,
            }

        except Exception as error:  # noqa: BLE001
            return {
                "review_report": None,
                "review_error": {
                    "code": (type(error).__name__),
                    "message": (str(error) or type(error).__name__),
                },
                "status": ("analysis_blocked"),
            }

    return result_review_node


def _create_report_node(
    *,
    resources: Mapping[str, object],
) -> Callable[
    [ParallelAnalysisState],
    dict[str, object],
]:
    """创建统一报告节点。"""

    def build_report_node(
        state: ParallelAnalysisState,
    ) -> dict[str, object]:
        """使用审核后的数据生成最终 AnalysisReport。"""

        raw_review_report = state["review_report"]

        if raw_review_report is None:
            return {
                "analysis_report": None,
                "report_error": {
                    "code": ("ReviewUnavailable"),
                    "message": ("结果审核没有生成可用的 review_report。"),
                },
                "status": ("analysis_blocked"),
            }

        try:
            resource_id = state["resource_id"]

            branch_resources = resources[resource_id]

            if not isinstance(
                branch_resources,
                Mapping,
            ):
                raise TypeError("资源容器中的 resource_id 必须映射到 Mapping。")

            raw_spec = branch_resources.get("requirement_spec")

            if raw_spec is None:
                raise KeyError("统一报告缺少 requirement_spec 资源。")

            candidate_set = CandidateSet.model_validate(state["candidate_set"])

            requirement_spec = RequirementSpec.model_validate(raw_spec)

            branch_results = tuple(
                BranchResult.model_validate(item) for item in state["ordered_results"]
            )

            # 延迟导入避免 report_builder
            # 对 BRANCH_ORDER 的反向引用形成循环。
            from app.engines.report_builder import (
                build_analysis_report,
            )
            from app.schemas.review import (
                ReviewReport,
            )

            review_report = ReviewReport.model_validate(raw_review_report)

            report = build_analysis_report(
                requirement_spec=(requirement_spec),
                candidate_set=candidate_set,
                review_report=review_report,
                branch_results=branch_results,
            )

            return {
                "analysis_report": (report.model_dump(mode="json")),
                "report_error": None,
            }

        except Exception as error:  # noqa: BLE001
            return {
                "analysis_report": None,
                "report_error": {
                    "code": (type(error).__name__),
                    "message": (str(error) or type(error).__name__),
                },
                "status": ("analysis_blocked"),
            }

    return build_report_node


def build_parallel_analysis_graph(
    *,
    impact_runner: BranchRunner,
    test_runner: BranchRunner,
    risk_runner: BranchRunner,
    resources: Mapping[str, object],
):
    """构建并编译三路分析、审核与报告工作流。"""

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

    builder.add_node(
        "result_review",
        _create_result_review_node(
            resources=resources,
        ),
    )

    builder.add_node(
        "build_report",
        _create_report_node(
            resources=resources,
        ),
    )

    # 三个分支从 START 并行启动。
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

    # 等待三个分支全部结束后执行 fan-in。
    builder.add_edge(
        [
            "impact_branch",
            "test_branch",
            "risk_branch",
        ],
        "fan_in",
    )

    # fan-in 之后进行结论审核。
    builder.add_edge(
        "fan_in",
        "result_review",
    )

    # 审核之后构建统一报告。
    builder.add_edge(
        "result_review",
        "build_report",
    )

    builder.add_edge(
        "build_report",
        END,
    )

    return builder.compile()
