"""Semantic Mapping Agent 的受控候选池扩展工具。

本模块不负责执行标识符搜索或调用方查询。它接收工具已经返回的候选项，
验证路径真实性和扩池预算，然后构造一个新的 CandidatePool 快照。

CandidatePool 是冻结模型，因此扩池不会原地修改旧对象。
"""

from pydantic import Field

from app.schemas.contracts import (
    HASH_PATTERN,
    CandidatePool,
    ExpansionRecord,
    FileKind,
    FrozenContractModel,
    PoolEntry,
    SignalScore,
)
from app.tools.contract_digest import (
    compute_pool_digest,
    normalize_digest_path,
    stable_signal_sum,
)

MAX_POOL_SIZE = 60
MAX_EXPANSION_ROUNDS = 3
MAX_EXPANSION_QUERIES = 12
MAX_EXPANSION_ADDITIONS = 20
MAX_ADDITIONS_PER_ROUND = 10


class ExpansionCandidate(FrozenContractModel):
    """白名单查询工具返回的一个真实候选文件。"""

    path: str = Field(
        min_length=1,
    )

    kind: FileKind

    symbols: tuple[str, ...] = Field(
        default=(),
        max_length=10,
    )

    signals: tuple[SignalScore, ...] = ()


class ExpansionRequest(FrozenContractModel):
    """Semantic Mapping Agent 发起的一次扩池请求。"""

    round: int = Field(
        ge=1,
    )

    tool: str = Field(
        pattern=(
            r"^(search_identifiers|expand_callers|"
            r"read_signature|list_module_symbols)$"
        ),
    )

    args_digest: str = Field(
        pattern=HASH_PATTERN,
    )

    candidates: tuple[ExpansionCandidate, ...] = ()


class ExpansionOutcome(FrozenContractModel):
    """一次扩池尝试的不可变结果。"""

    pool: CandidatePool

    accepted_paths: tuple[str, ...] = ()

    rejected: tuple[str, ...] = ()

    applied: bool


def _rejection(
    code: str,
    detail: str,
) -> str:
    """生成便于测试和报告解析的稳定拒绝原因。"""

    return f"{code}: {detail}"


def _expanded_entry_count(
    pool: CandidatePool,
) -> int:
    """统计当前候选池中由 LLM 受控扩池加入的条目数。"""

    return sum(entry.source == "llm_expansion" for entry in pool.entries)


def _round_entry_count(
    pool: CandidatePool,
    round_number: int,
) -> int:
    """统计指定轮次已经加入的扩池条目数。"""

    return sum(
        entry.source == "llm_expansion" and entry.expansion_round == round_number
        for entry in pool.entries
    )


def _with_computed_digest(
    pool: CandidatePool,
) -> CandidatePool:
    """为新候选池快照计算内容摘要。"""

    return pool.model_copy(
        update={
            "pool_digest": compute_pool_digest(pool),
        }
    )


def _reject_entire_request(
    pool: CandidatePool,
    *,
    reason: str,
) -> ExpansionOutcome:
    """拒绝整个请求并保持原候选池不变。"""

    return ExpansionOutcome(
        pool=pool,
        rejected=(reason,),
        applied=False,
    )


def apply_candidate_expansion(
    pool: CandidatePool,
    request: ExpansionRequest,
    *,
    known_paths: set[str],
    requested_by: str = "semantic_mapping_agent",
) -> ExpansionOutcome:
    """在真实性和预算约束内生成新的 CandidatePool。

    处理顺序固定为：

    1. 校验轮次和查询次数；
    2. 归一化候选路径；
    3. 拒绝未知路径和重复路径；
    4. 按路径稳定排序；
    5. 依次应用池容量、累计新增和单轮新增预算；
    6. 记录 ExpansionRecord 并重新计算 pool digest。

    第 4 轮或第 13 次查询属于整个请求越权，因此不会写入旧候选池，
    但拒绝原因会通过 ExpansionOutcome.rejected 返回给编排层记录。
    """

    if not requested_by.strip():
        raise ValueError("requested_by 不能为空。")

    if request.round > MAX_EXPANSION_ROUNDS:
        return _reject_entire_request(
            pool,
            reason=_rejection(
                "ROUND_LIMIT_EXCEEDED",
                (f"requested={request.round}, maximum={MAX_EXPANSION_ROUNDS}"),
            ),
        )

    if len(pool.expansion_records) >= MAX_EXPANSION_QUERIES:
        return _reject_entire_request(
            pool,
            reason=_rejection(
                "QUERY_LIMIT_EXCEEDED",
                (f"requested={len(pool.expansion_records) + 1}, maximum={MAX_EXPANSION_QUERIES}"),
            ),
        )

    normalized_known_paths = {normalize_digest_path(path) for path in known_paths}

    existing_paths = {normalize_digest_path(entry.path) for entry in pool.entries}

    candidates_by_path: dict[
        str,
        ExpansionCandidate,
    ] = {}

    rejected: list[str] = []

    for candidate in request.candidates:
        path = normalize_digest_path(candidate.path)

        if path not in normalized_known_paths:
            rejected.append(
                _rejection(
                    "UNKNOWN_PATH",
                    path,
                )
            )

            continue

        if path in existing_paths or path in candidates_by_path:
            rejected.append(
                _rejection(
                    "DUPLICATE_PATH",
                    path,
                )
            )

            continue

        candidates_by_path[path] = candidate.model_copy(
            update={
                "path": path,
            }
        )

    additions_used = _expanded_entry_count(pool)

    round_additions_used = _round_entry_count(
        pool,
        request.round,
    )

    accepted_entries: list[PoolEntry] = []

    for path, candidate in sorted(candidates_by_path.items()):
        if len(pool.entries) + len(accepted_entries) >= MAX_POOL_SIZE:
            rejected.append(
                _rejection(
                    "POOL_SIZE_LIMIT_EXCEEDED",
                    (f"path={path}, maximum={MAX_POOL_SIZE}"),
                )
            )

            continue

        if additions_used + len(accepted_entries) >= MAX_EXPANSION_ADDITIONS:
            rejected.append(
                _rejection(
                    "TOTAL_ADDITION_LIMIT_EXCEEDED",
                    (f"path={path}, maximum={MAX_EXPANSION_ADDITIONS}"),
                )
            )

            continue

        if round_additions_used + len(accepted_entries) >= MAX_ADDITIONS_PER_ROUND:
            rejected.append(
                _rejection(
                    "ROUND_ADDITION_LIMIT_EXCEEDED",
                    (f"path={path}, round={request.round}, maximum={MAX_ADDITIONS_PER_ROUND}"),
                )
            )

            continue

        accepted_entries.append(
            PoolEntry(
                path=path,
                kind=candidate.kind,
                signals=candidate.signals,
                pre_score=stable_signal_sum(signal.value for signal in candidate.signals),
                symbols=tuple(sorted(candidate.symbols)),
                source="llm_expansion",
                requested_by=requested_by,
                expansion_round=request.round,
            )
        )

    accepted_paths = tuple(entry.path for entry in accepted_entries)

    record = ExpansionRecord(
        round=request.round,
        tool=request.tool,
        args_digest=request.args_digest,
        added_paths=accepted_paths,
        added_symbols=tuple(
            sorted({symbol for entry in accepted_entries for symbol in entry.symbols})
        ),
        rejected=tuple(rejected),
    )

    new_entries = tuple(
        sorted(
            (
                *pool.entries,
                *accepted_entries,
            ),
            key=lambda entry: entry.path,
        )
    )

    updated_pool = pool.model_copy(
        update={
            "entries": new_entries,
            "expansion_rounds_used": max(
                pool.expansion_rounds_used,
                request.round,
            ),
            "expansion_records": (
                *pool.expansion_records,
                record,
            ),
        }
    )

    updated_pool = _with_computed_digest(updated_pool)

    return ExpansionOutcome(
        pool=updated_pool,
        accepted_paths=accepted_paths,
        rejected=tuple(rejected),
        applied=True,
    )
