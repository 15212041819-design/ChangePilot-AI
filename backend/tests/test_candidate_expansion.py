"""Semantic Mapping Agent 受控扩池预算测试。"""

from app.schemas.contracts import (
    CandidatePool,
    ExpansionRecord,
    PoolEntry,
    SignalScore,
)
from app.tools.candidate_expansion import (
    MAX_ADDITIONS_PER_ROUND,
    MAX_EXPANSION_ADDITIONS,
    MAX_EXPANSION_QUERIES,
    MAX_EXPANSION_ROUNDS,
    MAX_POOL_SIZE,
    ExpansionCandidate,
    ExpansionRequest,
    apply_candidate_expansion,
)

ZERO_HASH = "sha256:" + "0" * 64


def pool(
    *,
    entries: tuple[PoolEntry, ...] = (),
    records: tuple[ExpansionRecord, ...] = (),
    rounds_used: int = 0,
) -> CandidatePool:
    """创建测试使用的候选池。"""

    return CandidatePool(
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        entries=entries,
        expansion_rounds_used=rounds_used,
        expansion_records=records,
        pool_exhausted=False,
        pool_digest=ZERO_HASH,
    )


def baseline_entry(
    index: int,
) -> PoolEntry:
    """创建一个确定性检索产生的基础候选。"""

    return PoolEntry(
        path=f"package/baseline_{index:02d}.py",
        kind="source",
        signals=(
            SignalScore(
                name="keyword",
                value=1.0,
            ),
        ),
        pre_score=1.0,
        source="baseline",
    )


def expanded_entry(
    index: int,
    *,
    round_number: int,
) -> PoolEntry:
    """创建一个已经被扩池接受的候选。"""

    return PoolEntry(
        path=f"package/expanded_{index:02d}.py",
        kind="source",
        signals=(
            SignalScore(
                name="symbol",
                value=1.0,
            ),
        ),
        pre_score=1.0,
        source="llm_expansion",
        requested_by="semantic_mapping_agent",
        expansion_round=round_number,
    )


def candidate(
    index: int,
) -> ExpansionCandidate:
    """创建白名单查询工具返回的候选。"""

    return ExpansionCandidate(
        path=f"package/candidate_{index:02d}.py",
        kind="source",
        symbols=(f"Service{index}",),
        signals=(
            SignalScore(
                name="symbol",
                value=0.8,
            ),
        ),
    )


def request(
    *,
    round_number: int,
    candidates: tuple[
        ExpansionCandidate,
        ...,
    ] = (),
) -> ExpansionRequest:
    """创建一次受控扩池请求。"""

    return ExpansionRequest(
        round=round_number,
        tool="search_identifiers",
        args_digest=ZERO_HASH,
        candidates=candidates,
    )


def known_paths(
    count: int = 100,
) -> set[str]:
    """返回模拟 SymbolIndex 中真实存在的路径。"""

    return {
        f"package/candidate_{index:02d}.py"
        for index in range(
            1,
            count + 1,
        )
    }


def test_t24_fourth_round_is_rejected() -> None:
    """T-24：第 4 轮扩池必须被拒绝，且旧池保持不变。"""

    initial = pool(
        rounds_used=MAX_EXPANSION_ROUNDS,
    )

    result = apply_candidate_expansion(
        initial,
        request(
            round_number=(MAX_EXPANSION_ROUNDS + 1),
            candidates=(candidate(1),),
        ),
        known_paths=known_paths(),
    )

    assert result.applied is False
    assert result.pool == initial
    assert result.accepted_paths == ()

    assert result.rejected[0].startswith("ROUND_LIMIT_EXCEEDED")


def test_t24_thirteenth_query_is_rejected() -> None:
    """T-24：第 13 次查询必须被拒绝。"""

    records = tuple(
        ExpansionRecord(
            round=1,
            tool="search_identifiers",
            args_digest=ZERO_HASH,
        )
        for _ in range(MAX_EXPANSION_QUERIES)
    )

    initial = pool(
        records=records,
        rounds_used=1,
    )

    result = apply_candidate_expansion(
        initial,
        request(
            round_number=1,
            candidates=(candidate(1),),
        ),
        known_paths=known_paths(),
    )

    assert result.applied is False
    assert result.pool == initial

    assert result.rejected[0].startswith("QUERY_LIMIT_EXCEEDED")


def test_t24_single_round_accepts_only_ten_additions() -> None:
    """T-24：单轮累计最多接受 10 条扩池结果。"""

    existing = tuple(
        expanded_entry(
            index,
            round_number=1,
        )
        for index in range(
            1,
            MAX_ADDITIONS_PER_ROUND,
        )
    )

    initial = pool(
        entries=existing,
        rounds_used=1,
    )

    result = apply_candidate_expansion(
        initial,
        request(
            round_number=1,
            candidates=(
                candidate(20),
                candidate(21),
            ),
        ),
        known_paths=known_paths(),
    )

    assert result.applied is True

    assert result.accepted_paths == ("package/candidate_20.py",)

    assert any(reason.startswith("ROUND_ADDITION_LIMIT_EXCEEDED") for reason in result.rejected)


def test_t24_total_expansion_accepts_only_twenty_additions() -> None:
    """T-24：所有轮次累计最多接受 20 条扩池结果。"""

    existing = tuple(
        expanded_entry(
            index,
            round_number=(1 if index <= 10 else 2),
        )
        for index in range(
            1,
            MAX_EXPANSION_ADDITIONS + 1,
        )
    )

    initial = pool(
        entries=existing,
        rounds_used=2,
    )

    result = apply_candidate_expansion(
        initial,
        request(
            round_number=3,
            candidates=(candidate(30),),
        ),
        known_paths=known_paths(),
    )

    assert result.applied is True
    assert result.accepted_paths == ()

    assert result.rejected[0].startswith("TOTAL_ADDITION_LIMIT_EXCEEDED")


def test_t24_pool_capacity_never_exceeds_sixty() -> None:
    """T-24：CandidatePool 总容量不能超过 60。"""

    entries = tuple(
        baseline_entry(index)
        for index in range(
            1,
            MAX_POOL_SIZE,
        )
    )

    initial = pool(
        entries=entries,
    )

    result = apply_candidate_expansion(
        initial,
        request(
            round_number=1,
            candidates=(
                candidate(40),
                candidate(41),
            ),
        ),
        known_paths=known_paths(),
    )

    assert len(result.pool.entries) == MAX_POOL_SIZE

    assert result.accepted_paths == ("package/candidate_40.py",)

    assert any(reason.startswith("POOL_SIZE_LIMIT_EXCEEDED") for reason in result.rejected)


def test_unknown_and_duplicate_paths_are_rejected() -> None:
    """只有索引中的真实新路径可以进入 CandidatePool。"""

    initial = pool(
        entries=(baseline_entry(1),),
    )

    result = apply_candidate_expansion(
        initial,
        request(
            round_number=1,
            candidates=(
                ExpansionCandidate(
                    path=("package/baseline_01.py"),
                    kind="source",
                ),
                ExpansionCandidate(
                    path=("package/not_in_index.py"),
                    kind="source",
                ),
            ),
        ),
        known_paths={
            "package/baseline_01.py",
        },
    )

    assert result.accepted_paths == ()
    assert len(result.rejected) == 2

    assert any(item.startswith("DUPLICATE_PATH") for item in result.rejected)

    assert any(item.startswith("UNKNOWN_PATH") for item in result.rejected)
