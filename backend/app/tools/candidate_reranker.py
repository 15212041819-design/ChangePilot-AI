"""对 CandidatePool 执行稳定、可解释的第二阶段候选选择。

第一阶段检索负责尽可能把相关文件放进宽候选池；第二阶段需要在有限的
CandidateSet 预算内平衡源码与测试。基础分数容易让大量测试文件占满前 12 名，
从而使真正需要分析的业务源码无法进入 Impact/Risk 分支。

本模块不读取评测答案、不调用 LLM，也不修改第一阶段分数。它只依据契约中的
文件类型和冻结的证据规则执行类型感知的重排，并在某一类候选不足时自动用
剩余候选补满预算。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.contracts import (
    Budget,
    CandidatePool,
    PoolEntry,
)

# 测试文件对测试策略有价值，但不能占满供三个分析分支共享的上下文。
# 每 5 个文件预算预留 1 个测试位，v1 最多预留 2 个。
TEST_SLOT_DIVISOR = 5
MAX_RESERVED_TEST_FILES = 2

# 第一阶段 symbol 信号会随同义词、限定名和签名中的重复命中不断累加。
# 第二阶段只需要确认“存在强符号证据”，不能让重复命中无限放大。
RERANK_SIGNAL_CAPS = {
    "symbol": 3.0,
}

# dependency closure 是当前快照中的直接结构事实，应高于重复词面命中；
# co-change 是时间切分后的历史结构证据，用来补足纯关键词/符号排序的遗漏。
# 两项权重均在开发样本与留出样本上同向改善后冻结。
RERANK_SIGNAL_WEIGHTS = {
    "cochange": 2.0,
    "dep_closure": 4.0,
}


@dataclass(frozen=True)
class CandidateRerankItem:
    """一个候选在第二阶段选择中的可解释轨迹。"""

    path: str
    original_rank: int
    rerank_rank: int | None
    pre_score: float
    lane: Literal["source", "test"]
    selected: bool


@dataclass(frozen=True)
class CandidateRerankResult:
    """二阶段选择结果及其稳定轨迹。"""

    selected_paths: tuple[str, ...]
    traces: tuple[CandidateRerankItem, ...]
    selected_source_count: int
    selected_test_count: int


def _stable_pool_order(
    entries: tuple[PoolEntry, ...],
) -> tuple[PoolEntry, ...]:
    """使用第一阶段分数与路径形成跨平台稳定顺序。"""

    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                -round(
                    entry.pre_score,
                    3,
                ),
                entry.path,
            ),
        )
    )


def _reserved_test_slots(
    *,
    max_files: int,
    test_count: int,
) -> int:
    """计算测试文件预留位，小预算优先保障业务源码。"""

    return min(
        MAX_RESERVED_TEST_FILES,
        max_files // TEST_SLOT_DIVISOR,
        test_count,
    )


def _rerank_evidence_score(
    entry: PoolEntry,
) -> float:
    """应用冻结的封顶与权重规则，返回稳定的二阶段证据分数。

    处理过程：

    1. 对 symbol 等配置过上限的信号执行封顶；
    2. 对依赖闭包和历史共变等信号进行加权；
    3. 对分值排序后求和，避免浮点累加顺序受输入顺序影响；
    4. 统一保留三位小数，保证跨平台排序稳定。
    """

    values = sorted(
        min(
            signal.value,
            RERANK_SIGNAL_CAPS.get(
                signal.name,
                signal.value,
            ),
        )
        * RERANK_SIGNAL_WEIGHTS.get(
            signal.name,
            1.0,
        )
        for signal in entry.signals
    )

    return round(
        sum(values),
        3,
    )


def _stable_source_order(
    entries: tuple[PoolEntry, ...],
) -> tuple[PoolEntry, ...]:
    """按二阶段证据分数、原始分数和路径稳定排列源码。"""

    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                -_rerank_evidence_score(entry),
                -round(
                    entry.pre_score,
                    3,
                ),
                entry.path,
            ),
        )
    )


def rerank_candidate_pool(
    pool: CandidatePool,
    *,
    budget: Budget,
) -> CandidateRerankResult:
    """按源码优先、测试受控的策略选择 CandidateSet 路径。

    规则如下：

    1. 源码内部按冻结的证据封顶和权重规则重排；
    2. 在 12 文件默认预算下，最多为测试文件预留 2 个位置；
    3. 其余位置优先分给 source/config；
    4. 任一类型数量不足时，从全局原始排名中补满剩余预算；
    5. 返回顺序和轨迹完全稳定，不依赖 set/dict 遍历顺序。
    """

    ordered = _stable_pool_order(pool.entries)

    limit = min(
        budget.max_files,
        len(ordered),
    )

    if limit == 0:
        return CandidateRerankResult(
            selected_paths=(),
            traces=(),
            selected_source_count=0,
            selected_test_count=0,
        )

    # 源码使用二阶段证据规则排序。
    source_like = _stable_source_order(tuple(entry for entry in ordered if entry.kind != "test"))

    # 测试文件继续沿用第一阶段顺序，
    # 避免测试映射结果被再次扰动。
    tests = tuple(entry for entry in ordered if entry.kind == "test")

    reserved_test_slots = _reserved_test_slots(
        max_files=limit,
        test_count=len(tests),
    )

    source_slots = min(
        limit - reserved_test_slots,
        len(source_like),
    )

    selected: list[PoolEntry] = [
        *source_like[:source_slots],
        *tests[:reserved_test_slots],
    ]

    selected_paths = {entry.path for entry in selected}

    # 某一类数量不足时，按原始全局证据排名填满剩余位置。
    for entry in ordered:
        if len(selected) >= limit:
            break

        if entry.path in selected_paths:
            continue

        selected.append(entry)
        selected_paths.add(entry.path)

    original_rank = {
        entry.path: rank
        for rank, entry in enumerate(
            ordered,
            start=1,
        )
    }

    rerank_rank = {
        entry.path: rank
        for rank, entry in enumerate(
            selected,
            start=1,
        )
    }

    traces = tuple(
        CandidateRerankItem(
            path=entry.path,
            original_rank=(original_rank[entry.path]),
            rerank_rank=(rerank_rank.get(entry.path)),
            pre_score=entry.pre_score,
            lane=("test" if entry.kind == "test" else "source"),
            selected=(entry.path in selected_paths),
        )
        for entry in ordered
    )

    return CandidateRerankResult(
        selected_paths=tuple(entry.path for entry in selected),
        traces=traces,
        selected_source_count=sum(entry.kind != "test" for entry in selected),
        selected_test_count=sum(entry.kind == "test" for entry in selected),
    )
