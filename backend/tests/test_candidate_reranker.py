"""CandidatePool 二阶段确定性重排测试。"""

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
        _pool(
            (
                *tests,
                *sources,
            )
        ),
        budget=Budget(
            max_files=12,
        ),
    )

    assert result.selected_source_count == 10
    assert result.selected_test_count == 2
    assert len(result.selected_paths) == 12

    assert sum(path.startswith("app/") for path in result.selected_paths) == 10

    assert sum(path.startswith("tests/") for path in result.selected_paths) == 2


def test_reranker_preserves_score_order_inside_each_lane() -> None:
    """源码和测试各自内部必须继续服从第一阶段证据分数。"""

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
        budget=Budget(
            max_files=4,
        ),
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
        budget=Budget(
            max_files=4,
        ),
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
        budget=Budget(
            max_files=3,
        ),
    )

    second = rerank_candidate_pool(
        _pool(tuple(reversed(entries))),
        budget=Budget(
            max_files=3,
        ),
    )

    assert first == second
