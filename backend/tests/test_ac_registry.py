"""ACRegistry 稳定 ID 与修订追踪规则测试。"""

from app.schemas.contracts import (
    AcceptanceCriterionDraft,
    ACRegistryState,
)
from app.services.ac_registry import (
    active_acceptance_criteria,
    allocate_acceptance_criteria,
)


def draft(
    description: str,
    *,
    priority: str = "P1",
) -> AcceptanceCriterionDraft:
    """创建测试使用的验收标准草稿。"""

    return AcceptanceCriterionDraft(
        description=description,
        verification="test",
        priority=priority,
    )


def test_t01_first_allocation_is_stable_and_idempotent() -> None:
    """T-01：首次分配按顺序生成 ID，重复输入不改变 registry。"""

    initial = ACRegistryState()

    first = allocate_acceptance_criteria(
        initial,
        [
            draft("待支付订单可以取消"),
            draft("取消成功后记录审计日志"),
        ],
        revision=1,
    )

    second = allocate_acceptance_criteria(
        first,
        [
            draft("待支付订单可以取消"),
            draft("取消成功后记录审计日志"),
        ],
        revision=1,
    )

    assert [item.id for item in active_acceptance_criteria(first)] == [
        "AC-001",
        "AC-002",
    ]

    assert second == first
    assert second.version == 1


def test_t02_reworded_criterion_keeps_id_and_appends_history() -> None:
    """T-02：高相似度改写沿用旧 ID。"""

    first = allocate_acceptance_criteria(
        ACRegistryState(),
        [draft("用户可以取消待支付订单")],
        revision=1,
    )

    revised = allocate_acceptance_criteria(
        first,
        [draft("用户可以取消待支付状态的订单")],
        revision=2,
    )

    record = revised.criteria["AC-001"]

    assert record.criterion.status == "active"
    assert record.criterion.description == ("用户可以取消待支付状态的订单")
    assert record.history[-1].event == "rewritten"
    assert record.history[-1].revision == 2


def test_t03_split_keeps_parent_id_active_and_tracks_child() -> None:
    """T-03：拆分时最近条目继承旧 ID，其余条目追踪来源。"""

    first = allocate_acceptance_criteria(
        ACRegistryState(),
        [draft("系统应允许订单所属用户取消待支付订单")],
        revision=1,
    )

    revised = allocate_acceptance_criteria(
        first,
        [
            draft("系统应允许订单所属用户本人取消待支付订单"),
            draft("系统应通过接口允许订单所属用户取消待支付订单"),
        ],
        revision=2,
    )

    assert revised.criteria["AC-001"].criterion.status == "active"

    assert revised.criteria["AC-001"].history[-1].event == "split"

    assert revised.criteria["AC-002"].criterion.derived_from == "AC-001"


def test_t04_merge_uses_smallest_id_and_marks_other_records() -> None:
    """T-04：合并继承最小 ID，其他旧 ID 进入 merged 状态。"""

    first = allocate_acceptance_criteria(
        ACRegistryState(),
        [
            draft("系统允许用户取消待支付订单"),
            draft("系统允许订单所属用户取消未支付订单"),
        ],
        revision=1,
    )

    revised = allocate_acceptance_criteria(
        first,
        [draft("系统允许订单所属用户取消待支付订单")],
        revision=2,
    )

    assert revised.criteria["AC-001"].criterion.status == "active"

    assert revised.criteria["AC-001"].criterion.supersedes == ("AC-002",)

    assert revised.criteria["AC-002"].criterion.status == "merged"


def test_t05_dropped_id_is_never_reused() -> None:
    """T-05：被删除 AC 保留记录，新 AC 只能使用更大的 ID。"""

    first = allocate_acceptance_criteria(
        ACRegistryState(),
        [draft("待支付订单可以取消")],
        revision=1,
    )

    dropped = allocate_acceptance_criteria(
        first,
        [draft("管理员可以导出季度统计报表")],
        revision=2,
    )

    assert dropped.criteria["AC-001"].criterion.status == "dropped"

    assert dropped.criteria["AC-002"].criterion.status == "active"


def test_t06_duplicate_drafts_keep_higher_priority() -> None:
    """T-06：重复描述只分配一个 ID，并保留更高优先级。"""

    result = allocate_acceptance_criteria(
        ACRegistryState(),
        [
            draft(
                "待支付订单可以取消",
                priority="P2",
            ),
            draft(
                "待支付订单可以取消。",
                priority="P0",
            ),
        ],
        revision=1,
    )

    active = active_acceptance_criteria(result)

    assert len(active) == 1
    assert active[0].priority == "P0"
    assert result.warnings
