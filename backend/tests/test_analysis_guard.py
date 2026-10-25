"""ChangePoint Guard 的 normalize、repair 和 fail 行为测试。"""

from app.guards.analysis_guard import guard_change_points
from app.schemas.contracts import (
    Budget,
    CandidateSet,
    ChangePoint,
    EvidencePointer,
    FileRef,
    SymbolRef,
)
from app.schemas.guard import (
    GuardContext,
    RepositoryFile,
    SymbolLocation,
)

ZERO_HASH = "sha256:" + "0" * 64


def candidate_set() -> CandidateSet:
    """创建 Guard 使用的冻结候选集。"""

    return CandidateSet(
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
                symbols=("OrderService.cancel_order",),
                line=10,
                reason="命中订单服务",
                score=1.0,
                confidence=0.9,
                selected_by="semantic_map",
            ),
        ),
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )


def context() -> GuardContext:
    """创建包含真实文件、符号范围和 active AC 的仓库事实。"""

    return GuardContext(
        repository_files=(
            RepositoryFile(
                path="order_app/service.py",
                line_count=80,
            ),
            RepositoryFile(
                path="order_app/models.py",
                line_count=40,
            ),
        ),
        symbols=(
            SymbolLocation(
                qualified_name=("order_app.service.OrderService.cancel_order"),
                path="order_app/service.py",
                kind="method",
                start_line=10,
                end_line=25,
            ),
            SymbolLocation(
                qualified_name=("order_app.service.OrderService"),
                path="order_app/service.py",
                kind="class",
                start_line=1,
                end_line=70,
            ),
        ),
        active_ac_ids=frozenset(
            {
                "AC-001",
            }
        ),
    )


def change_point(
    *,
    cp_id: str = "CP-0001",
    path: str = "order_app/service.py",
    symbol: str = "cancel_order",
    evidence_line: int = 10,
    confidence: float = 0.8,
) -> ChangePoint:
    """创建指向已有 cancel_order 方法的变更点。"""

    return ChangePoint(
        cp_id=cp_id,
        path=path,
        target=SymbolRef(
            name=symbol,
            parent_symbol=("order_app.service.OrderService"),
            expect="existing",
            kind="method",
        ),
        change_type="semantic",
        ac_ids=("AC-001",),
        evidence=(
            EvidencePointer(
                path=path,
                line=evidence_line,
                kind="def",
            ),
        ),
        confidence=confidence,
        source="impact",
    )


def numbered_context(
    count: int,
) -> GuardContext:
    """创建包含指定数量独立方法的仓库事实。"""

    symbols = tuple(
        SymbolLocation(
            qualified_name=(f"order_app.service.OrderService.cancel_order_{index}"),
            path="order_app/service.py",
            kind="method",
            start_line=10,
            end_line=25,
        )
        for index in range(
            1,
            count + 1,
        )
    )

    return context().model_copy(
        update={
            "symbols": symbols,
        }
    )


def numbered_change_points(
    count: int,
    *,
    broken_count: int,
) -> tuple[ChangePoint, ...]:
    """创建互不重复的变更点，并让前若干条具有可修复行号错误。"""

    return tuple(
        change_point(
            cp_id=f"CP-{index:04d}",
            symbol=f"cancel_order_{index}",
            evidence_line=(30 if index <= broken_count else 10),
        )
        for index in range(
            1,
            count + 1,
        )
    )


def test_t08_unknown_existing_symbol_fails() -> None:
    """T-08：声称已有但索引中不存在的符号必须 fail。"""

    result = guard_change_points(
        (
            change_point(
                symbol="missing_method",
            ),
        ),
        candidate_set=candidate_set(),
        context=context(),
        branch="impact",
        branch_kind="agent",
    )

    assert result.report.verdict == "fail"

    assert result.report.invalid[0].error_type == "unknown_existing_symbol"


def test_t17_windows_path_is_normalized_without_invalid_item() -> None:
    """T-17：反斜杠路径只做 normalize，不计入 invalid。"""

    result = guard_change_points(
        (
            change_point(
                path="order_app\\service.py",
            ),
        ),
        candidate_set=candidate_set(),
        context=context(),
        branch="impact",
        branch_kind="agent",
    )

    assert result.report.verdict == "pass"
    assert result.report.invalid == ()

    assert result.change_points[0].path == "order_app/service.py"

    assert any(action.kind == "path_separator" for action in (result.report.normalize_actions))


def test_t18_path_case_is_corrected_without_invalid_item() -> None:
    """T-18：路径大小写可以按仓库真实路径机械校正。"""

    result = guard_change_points(
        (
            change_point(
                path="ORDER_APP/SERVICE.PY",
            ),
        ),
        candidate_set=candidate_set(),
        context=context(),
        branch="impact",
        branch_kind="agent",
    )

    assert result.report.verdict == "pass"

    assert result.change_points[0].path == "order_app/service.py"

    assert any(action.kind == "path_case" for action in (result.report.normalize_actions))


def test_t19_duplicate_finding_keeps_highest_confidence() -> None:
    """T-19：重复变更点去重并保留最高 confidence。"""

    result = guard_change_points(
        (
            change_point(
                cp_id="CP-0001",
                confidence=0.3,
            ),
            change_point(
                cp_id="CP-0002",
                confidence=0.9,
            ),
        ),
        candidate_set=candidate_set(),
        context=context(),
        branch="impact",
        branch_kind="agent",
    )

    assert result.report.verdict == "pass"
    assert len(result.change_points) == 1

    assert result.change_points[0].cp_id == "CP-0002"

    assert any(action.kind == "duplicate_finding" for action in (result.report.normalize_actions))


def test_t20_line_error_repairs_agent_but_fails_engine() -> None:
    """T-20：相同的符号行号错误只允许 Agent repair。"""

    # 一条错误配四条合法结论，
    # invalid_ratio 正好为 0.20。
    broken = numbered_change_points(
        5,
        broken_count=1,
    )

    repository_context = numbered_context(5)

    agent_result = guard_change_points(
        broken,
        candidate_set=candidate_set(),
        context=repository_context,
        branch="impact",
        branch_kind="agent",
    )

    engine_result = guard_change_points(
        broken,
        candidate_set=candidate_set(),
        context=repository_context,
        branch="impact",
        branch_kind="engine",
    )

    assert agent_result.report.verdict == "repair"

    assert agent_result.report.repair_hints

    assert engine_result.report.verdict == "fail"

    assert "ENGINE_OUTPUT_INVALID" in engine_result.report.blocking_reasons


def test_t21_second_failed_attempt_does_not_repair_again() -> None:
    """T-21：一次 repair 后仍不合法必须 fail。"""

    result = guard_change_points(
        numbered_change_points(
            5,
            broken_count=1,
        ),
        candidate_set=candidate_set(),
        context=numbered_context(5),
        branch="impact",
        branch_kind="agent",
        repair_attempt=1,
    )

    assert result.report.verdict == "fail"

    assert result.report.blocking_reasons == ("REPAIR_ATTEMPT_EXHAUSTED",)


def test_t22_invalid_ratio_boundary_is_strictly_greater_than_point_two() -> None:
    """T-22：0.20 可以 repair，0.21 必须 fail。"""

    twenty_percent_items = numbered_change_points(
        5,
        broken_count=1,
    )

    twenty_one_percent_items = numbered_change_points(
        100,
        broken_count=21,
    )

    repair_result = guard_change_points(
        twenty_percent_items,
        candidate_set=candidate_set(),
        context=numbered_context(5),
        branch="impact",
        branch_kind="agent",
    )

    fail_result = guard_change_points(
        twenty_one_percent_items,
        candidate_set=candidate_set(),
        context=numbered_context(100),
        branch="impact",
        branch_kind="agent",
    )

    assert repair_result.report.invalid_ratio == 0.2

    assert repair_result.report.verdict == "repair"

    assert fail_result.report.invalid_ratio == 0.21

    assert fail_result.report.verdict == "fail"

    assert "INVALID_RATIO_EXCEEDED" in fail_result.report.blocking_reasons


def test_t23_candidate_set_write_attempt_fails() -> None:
    """T-23：分支尝试修改 CandidateSet 时必须 fail。"""

    result = guard_change_points(
        (change_point(),),
        candidate_set=candidate_set(),
        context=context(),
        branch="impact",
        branch_kind="agent",
        candidate_set_write_attempted=True,
    )

    assert result.report.verdict == "fail"

    assert result.report.invalid[-1].error_type == "candidate_set_write_attempt"

    assert "CANDIDATE_SET_WRITE_ATTEMPT" in result.report.blocking_reasons
