"""CandidatePool 二阶段确定性重排测试。

本文件验证：

1. 测试文件不会占满最终上下文；
2. 源码与测试配额能够稳定分配；
3. 某类文件不足时能够自动补满预算；
4. 输入顺序变化不会影响最终结果；
5. 重复 symbol 信号会被封顶；
6. co-change 信号会应用冻结的 2 倍权重；
7. 二阶段分数相同时使用原始分数稳定决胜。
"""

from app.schemas.contracts import (
    Budget,
    CandidatePool,
    PoolEntry,
    SignalScore,
)
from app.tools.candidate_reranker import rerank_candidate_pool

ZERO_HASH = "sha256:" + "0" * 64


def _entry(
    path: str,
    *,
    kind: str,
    score: float,
) -> PoolEntry:
    """创建只携带一个稳定关键词信号的候选。"""

    return PoolEntry(
        path=path,
        kind=kind,
        signals=(
            SignalScore(
                name="keyword",
                value=score,
            ),
        ),
        pre_score=score,
        source="baseline",
    )


def _pool(
    entries: tuple[PoolEntry, ...],
) -> CandidatePool:
    """创建供纯重排测试使用的候选池。"""

    return CandidatePool(
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        entries=entries,
        expansion_rounds_used=0,
        expansion_records=(),
        pool_exhausted=False,
        pool_digest=ZERO_HASH,
    )


def test_reranker_prevents_tests_from_filling_all_context_slots() -> None:
    """高分测试很多时，默认 12 文件预算仍应保留 10 个源码位。"""

    tests = tuple(
        _entry(
            f"tests/test_feature_{index}.py",
            kind="test",
            score=100.0 - index,
        )
        for index in range(12)
    )

    sources = tuple(
        _entry(
            f"app/service_{index}.py",
            kind="source",
            score=10.0 - index / 10,
        )
        for index in range(10)
    )

    result = rerank_candidate_pool(
        _pool((*tests, *sources)),
        budget=Budget(max_files=12),
    )

    assert result.selected_source_count == 10
    assert result.selected_test_count == 2
    assert len(result.selected_paths) == 12
    assert sum(path.startswith("app/") for path in result.selected_paths) == 10
    assert sum(path.startswith("tests/") for path in result.selected_paths) == 2


def test_reranker_preserves_score_order_inside_each_lane() -> None:
    """仅有关键词信号时，各类型内部应继续保持稳定的分数顺序。"""

    result = rerank_candidate_pool(
        _pool(
            (
                _entry(
                    "tests/test_low.py",
                    kind="test",
                    score=2.0,
                ),
                _entry(
                    "app/low.py",
                    kind="source",
                    score=1.0,
                ),
                _entry(
                    "tests/test_high.py",
                    kind="test",
                    score=8.0,
                ),
                _entry(
                    "app/high.py",
                    kind="source",
                    score=9.0,
                ),
            )
        ),
        budget=Budget(max_files=4),
    )

    assert result.selected_paths == (
        "app/high.py",
        "app/low.py",
        "tests/test_high.py",
        "tests/test_low.py",
    )


def test_reranker_fills_budget_when_source_candidates_are_insufficient() -> None:
    """源码不足时不能留下空位，应按全局原始排名补入测试。"""

    result = rerank_candidate_pool(
        _pool(
            (
                _entry(
                    "tests/test_a.py",
                    kind="test",
                    score=9.0,
                ),
                _entry(
                    "tests/test_b.py",
                    kind="test",
                    score=8.0,
                ),
                _entry(
                    "tests/test_c.py",
                    kind="test",
                    score=7.0,
                ),
                _entry(
                    "app/service.py",
                    kind="source",
                    score=1.0,
                ),
            )
        ),
        budget=Budget(max_files=4),
    )

    assert set(result.selected_paths) == {
        "app/service.py",
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_c.py",
    }
    assert len(result.selected_paths) == 4
    assert result.selected_source_count == 1
    assert result.selected_test_count == 3


def test_reranker_is_stable_when_pool_entry_order_changes() -> None:
    """候选池输入顺序变化不能改变二阶段结果。"""

    entries = (
        _entry(
            "app/b.py",
            kind="source",
            score=3.0,
        ),
        _entry(
            "tests/test_b.py",
            kind="test",
            score=5.0,
        ),
        _entry(
            "app/a.py",
            kind="source",
            score=3.0,
        ),
        _entry(
            "tests/test_a.py",
            kind="test",
            score=5.0,
        ),
    )

    first = rerank_candidate_pool(
        _pool(entries),
        budget=Budget(max_files=3),
    )

    second = rerank_candidate_pool(
        _pool(tuple(reversed(entries))),
        budget=Budget(max_files=3),
    )

    assert first == second


def test_reranker_caps_repeated_symbol_evidence() -> None:
    """重复符号命中不能压过同时拥有关键词和符号证据的源码。"""

    repeated_symbol = PoolEntry(
        path="app/engines/generic.py",
        kind="source",
        signals=(
            SignalScore(
                name="symbol",
                value=6.0,
            ),
        ),
        pre_score=6.0,
        source="baseline",
    )

    balanced_evidence = PoolEntry(
        path="app/services/order.py",
        kind="source",
        signals=(
            SignalScore(
                name="keyword",
                value=1.7,
            ),
            SignalScore(
                name="symbol",
                value=2.0,
            ),
        ),
        pre_score=3.7,
        source="baseline",
    )

    result = rerank_candidate_pool(
        _pool(
            (
                repeated_symbol,
                balanced_evidence,
            )
        ),
        budget=Budget(max_files=1),
    )

    assert result.selected_paths == ("app/services/order.py",)


def test_reranker_boosts_cochange_evidence() -> None:
    """历史共改证据应在第二阶段得到冻结的 2 倍权重。"""

    symbol_only = PoolEntry(
        path="app/engines/generic.py",
        kind="source",
        signals=(
            SignalScore(
                name="symbol",
                value=3.0,
            ),
        ),
        pre_score=3.0,
        source="baseline",
    )

    cochange_supported = PoolEntry(
        path="app/services/order.py",
        kind="source",
        signals=(
            SignalScore(
                name="keyword",
                value=0.2,
            ),
            SignalScore(
                name="cochange",
                value=1.5,
            ),
        ),
        pre_score=1.7,
        source="baseline",
    )

    result = rerank_candidate_pool(
        _pool(
            (
                symbol_only,
                cochange_supported,
            )
        ),
        budget=Budget(max_files=1),
    )

    assert result.selected_paths == ("app/services/order.py",)


def test_reranker_uses_original_score_after_signal_saturation_tie() -> None:
    """二阶段分数相同时继续使用原始分数和路径稳定决胜。"""

    entries = (
        _entry(
            "app/low.py",
            kind="source",
            score=4.0,
        ),
        _entry(
            "app/high.py",
            kind="source",
            score=5.0,
        ),
    )

    result = rerank_candidate_pool(
        _pool(entries),
        budget=Budget(max_files=1),
    )

    assert result.selected_paths == ("app/high.py",)
