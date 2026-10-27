"""使用受控仓库工具扩展并重排 CandidatePool。"""

import json
from dataclasses import dataclass

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.schemas.code_index import RepositorySymbolIndex
from app.schemas.contracts import (
    Budget,
    CandidatePool,
    CandidateSet,
    RequirementSpec,
    SignalScore,
)
from app.schemas.dependency_graph import RepositoryDependencyGraph
from app.schemas.repository import RepositorySnapshot
from app.schemas.semantic_mapping import (
    SemanticMappingDecision,
    SemanticQuery,
    SemanticToolTrace,
)
from app.services.llm import get_chat_model
from app.tools.candidate_expansion import (
    MAX_EXPANSION_QUERIES,
    MAX_EXPANSION_ROUNDS,
    ExpansionCandidate,
    ExpansionRequest,
    apply_candidate_expansion,
)
from app.tools.context_builder import (
    classify_file_kind,
    freeze_candidate_set,
)
from app.tools.contract_digest import (
    canonical_digest,
    normalize_digest_path,
)

MAX_TOOL_RESULTS = 10
MAX_DECISION_REPAIR_ATTEMPTS = 1

SYSTEM_PROMPT = """
你是 ChangePilot AI 的 Semantic Mapping Agent。

你的唯一职责是在已知 CandidatePool 中选择与需求最相关的文件，
或者在候选池召回不足时请求受控仓库查询工具。

必须遵守：

1. 不得自行虚构文件路径、行号、符号或依赖。
2. 最终 selected_paths 只能来自当前 CandidatePool。
3. 候选不足时，只能使用 search_identifiers、expand_callers、
   read_signature、list_module_symbols 四个工具。
4. 工具返回的路径仍要经过服务端真实性和预算校验。
5. needs_more_context=true 时只输出 queries，不输出 selected_paths。
6. needs_more_context=false 时只输出 selected_paths，不再请求工具。
7. 优先选择有符号、依赖或测试证据的文件，不要为了填满预算而选择无关文件。
8. selected_paths 数量不得超过输入 selection_budget.max_selected_files。
9. search_identifiers 只能提供 terms；expand_callers 和 read_signature 只能提供
   symbol；list_module_symbols 只能提供 module。不要给工具附加无关参数。
10. repair_feedback 非空时，必须只修正其中指出的契约错误，不得忽略预算。
11. force_finalize=true 时扩池预算已经结束，必须返回 needs_more_context=false、
    空 queries 和不超过文件预算的 selected_paths，不得继续请求工具。

请严格按以下格式返回，不要输出额外说明：

{format_instructions}
""".strip()


class SemanticMappingValidationError(ValueError):
    """Semantic Mapping 输出越过候选池或工具预算。"""


@dataclass(frozen=True)
class SemanticMappingResult:
    """受控语义映射的完整结果。"""

    candidate_pool: CandidatePool

    candidate_set: CandidateSet

    decisions: tuple[
        SemanticMappingDecision,
        ...,
    ]

    tool_traces: tuple[
        SemanticToolTrace,
        ...,
    ]

    query_count: int


def _known_repository_paths(
    repository: RepositorySnapshot,
) -> set[str]:
    """返回快照中可被扩池工具返回的真实路径。"""

    return {
        normalize_digest_path(
            item.relative_path,
        )
        for item in repository.files
    }


def _candidate_from_indexed_file(
    *,
    path: str,
    symbols: tuple[str, ...],
    signal_name: str,
    signal_value: float,
) -> ExpansionCandidate:
    """把一个已索引真实文件转换为扩池候选。"""

    return ExpansionCandidate(
        path=normalize_digest_path(path),
        kind=classify_file_kind(path),
        symbols=tuple(
            sorted(
                set(symbols),
            )
        )[:10],
        signals=(
            SignalScore(
                name=signal_name,
                value=signal_value,
            ),
        ),
    )


def _search_identifiers(
    query: SemanticQuery,
    symbol_index: RepositorySymbolIndex,
) -> tuple[
    ExpansionCandidate,
    ...,
]:
    """在真实模块名、符号名和声明签名中查找关键词。"""

    terms = tuple(sorted({term.strip().casefold() for term in query.terms if term.strip()}))

    candidates: list[ExpansionCandidate] = []

    for indexed_file in sorted(
        symbol_index.files,
        key=lambda item: item.file_path,
    ):
        searchable = "\n".join(
            (
                indexed_file.file_path,
                indexed_file.module_name,
                *(
                    value
                    for symbol in (indexed_file.symbols)
                    for value in (
                        symbol.name,
                        symbol.qualified_name,
                        symbol.signature,
                    )
                ),
            )
        ).casefold()

        hits = sum(term in searchable for term in terms)

        if not hits:
            continue

        candidates.append(
            _candidate_from_indexed_file(
                path=indexed_file.file_path,
                symbols=tuple(
                    symbol.qualified_name
                    for symbol in (indexed_file.symbols)
                    if any(
                        term
                        in (
                            symbol.name + "\n" + symbol.qualified_name + "\n" + symbol.signature
                        ).casefold()
                        for term in terms
                    )
                ),
                signal_name="symbol",
                signal_value=round(
                    min(hits, 5) * 2.0,
                    3,
                ),
            )
        )

    return tuple(candidates[:MAX_TOOL_RESULTS])


def _matching_symbols(
    symbol_index: RepositorySymbolIndex,
    symbol_name: str,
):
    """按完整限定名或简称返回真实符号。"""

    requested = symbol_name.strip().casefold()

    return tuple(
        (
            indexed_file,
            symbol,
        )
        for indexed_file in sorted(
            symbol_index.files,
            key=lambda item: item.file_path,
        )
        for symbol in sorted(
            indexed_file.symbols,
            key=lambda item: item.qualified_name,
        )
        if requested
        in {
            symbol.name.casefold(),
            symbol.qualified_name.casefold(),
        }
    )


def _read_signature(
    query: SemanticQuery,
    symbol_index: RepositorySymbolIndex,
) -> tuple[
    ExpansionCandidate,
    ...,
]:
    """通过符号索引返回定义文件和真实签名证据。"""

    assert query.symbol is not None

    return tuple(
        _candidate_from_indexed_file(
            path=indexed_file.file_path,
            symbols=(symbol.qualified_name,),
            signal_name="symbol",
            signal_value=2.5,
        )
        for indexed_file, symbol in (
            _matching_symbols(
                symbol_index,
                query.symbol,
            )[:MAX_TOOL_RESULTS]
        )
    )


def _list_module_symbols(
    query: SemanticQuery,
    symbol_index: RepositorySymbolIndex,
) -> tuple[
    ExpansionCandidate,
    ...,
]:
    """列出一个真实 Python 模块定义的符号。"""

    assert query.module is not None

    requested = query.module.strip().casefold()

    return tuple(
        _candidate_from_indexed_file(
            path=indexed_file.file_path,
            symbols=tuple(symbol.qualified_name for symbol in (indexed_file.symbols)),
            signal_name="symbol",
            signal_value=2.0,
        )
        for indexed_file in sorted(
            symbol_index.files,
            key=lambda item: item.file_path,
        )
        if (indexed_file.module_name.casefold() == requested)
    )[:MAX_TOOL_RESULTS]


def _expand_callers(
    query: SemanticQuery,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
) -> tuple[
    ExpansionCandidate,
    ...,
]:
    """返回符号定义文件及直接导入该模块的仓库文件。"""

    assert query.symbol is not None

    matches = _matching_symbols(
        symbol_index,
        query.symbol,
    )

    definitions = {
        normalize_digest_path(
            indexed_file.file_path,
        ): tuple(
            symbol.qualified_name
            for file_item, symbol in matches
            if (file_item.file_path == indexed_file.file_path)
        )
        for indexed_file, _ in matches
    }

    paths: dict[
        str,
        tuple[str, ...],
    ] = dict(definitions)

    for dependency in dependency_graph.dependencies:
        if dependency.kind != "internal" or dependency.target_file is None:
            continue

        target_path = normalize_digest_path(
            dependency.target_file,
        )

        if target_path not in definitions:
            continue

        source_path = normalize_digest_path(
            dependency.source_file,
        )

        paths.setdefault(
            source_path,
            (),
        )

    return tuple(
        _candidate_from_indexed_file(
            path=path,
            symbols=symbols,
            signal_name=("symbol" if path in definitions else "dep_closure"),
            signal_value=(2.5 if path in definitions else 1.0),
        )
        for path, symbols in sorted(paths.items())
    )[:MAX_TOOL_RESULTS]


def execute_semantic_query(
    query: SemanticQuery,
    *,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
) -> tuple[
    ExpansionCandidate,
    ...,
]:
    """执行一次无副作用的白名单仓库查询。"""

    if query.tool == "search_identifiers":
        return _search_identifiers(
            query,
            symbol_index,
        )

    if query.tool == "read_signature":
        return _read_signature(
            query,
            symbol_index,
        )

    if query.tool == "list_module_symbols":
        return _list_module_symbols(
            query,
            symbol_index,
        )

    return _expand_callers(
        query,
        symbol_index,
        dependency_graph,
    )


def _prompt_payload(
    requirement: RequirementSpec,
    pool: CandidatePool,
    traces: tuple[
        SemanticToolTrace,
        ...,
    ],
    *,
    budget: Budget,
    repair_feedback: str | None,
    force_finalize: bool,
) -> str:
    """构造不包含仓库源码全文的最小模型上下文。"""

    payload = {
        "requirement": {
            "summary": (requirement.summary),
            "change_type": (requirement.change_type),
            "acceptance_criteria": [
                {
                    "id": item.id,
                    "description": (item.description),
                    "priority": (item.priority),
                }
                for item in (requirement.acceptance_criteria)
                if item.status == "active"
            ],
            "entity_hints": [
                item.model_dump(
                    mode="json",
                )
                for item in (requirement.entity_hints)
            ],
            "constraints": list(requirement.constraints),
        },
        "candidate_pool": [
            {
                "path": entry.path,
                "kind": entry.kind,
                "symbols": list(entry.symbols),
                "signals": [
                    signal.model_dump(
                        mode="json",
                    )
                    for signal in (entry.signals)
                ],
                "pre_score": (entry.pre_score),
                "source": entry.source,
            }
            for entry in pool.entries
        ],
        "tool_history": [
            trace.model_dump(
                mode="json",
            )
            for trace in traces
        ],
        "remaining_budget": {
            "rounds": (MAX_EXPANSION_ROUNDS - pool.expansion_rounds_used),
            "queries": (MAX_EXPANSION_QUERIES - len(pool.expansion_records)),
        },
        "selection_budget": {
            "max_selected_files": (budget.max_files),
        },
        "repair_feedback": (repair_feedback),
        "force_finalize": (force_finalize),
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )


def _invoke_mapping_model(
    *,
    requirement: RequirementSpec,
    pool: CandidatePool,
    traces: tuple[
        SemanticToolTrace,
        ...,
    ],
    budget: Budget,
    repair_feedback: str | None,
    force_finalize: bool,
    model: BaseChatModel,
) -> SemanticMappingDecision:
    """调用一轮结构化 Semantic Mapping 决策。"""

    parser = PydanticOutputParser(
        pydantic_object=(SemanticMappingDecision),
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                SYSTEM_PROMPT,
            ),
            (
                "human",
                ("请分析以下需求、候选池和工具历史：\n\n{payload}"),
            ),
        ]
    )

    chain = prompt | model | parser

    return chain.invoke(
        {
            "payload": _prompt_payload(
                requirement,
                pool,
                traces,
                budget=budget,
                repair_feedback=(repair_feedback),
                force_finalize=(force_finalize),
            ),
            "format_instructions": (parser.get_format_instructions()),
        }
    )


def _selection_budget_error(
    decision: SemanticMappingDecision,
    budget: Budget,
) -> str | None:
    """返回最终文件选择超过预算时的稳定错误信息。"""

    if decision.needs_more_context:
        return None

    selected_count = len(
        decision.selected_paths,
    )

    if selected_count <= budget.max_files:
        return None

    return (
        "selected_paths 超过 CandidateSet "
        "文件预算："
        f"selected={selected_count}, "
        f"maximum={budget.max_files}。"
    )


def _forced_finalization_error(
    decision: SemanticMappingDecision,
    *,
    force_finalize: bool,
) -> str | None:
    """强制收敛轮不允许模型继续申请仓库工具。"""

    if not force_finalize or not decision.needs_more_context:
        return None

    return (
        "扩池预算已经结束，强制收敛轮必须返回 "
        "needs_more_context=false、空 queries "
        "和最终 selected_paths。"
    )


def _invoke_mapping_model_with_repair(
    *,
    requirement: RequirementSpec,
    pool: CandidatePool,
    traces: tuple[
        SemanticToolTrace,
        ...,
    ],
    budget: Budget,
    model: BaseChatModel,
    force_finalize: bool = False,
) -> SemanticMappingDecision:
    """调用一轮决策，并允许 LLM 契约错误修正一次。

    修正只对 LLM 分支开放，且不会静默删除字段、截断路径或放宽预算。
    第一次错误会作为 ``repair_feedback`` 返回同一节点；
    第二次仍错误则正式失败。
    """

    repair_feedback: str | None = None

    for attempt in range(
        MAX_DECISION_REPAIR_ATTEMPTS + 1,
    ):
        try:
            decision = _invoke_mapping_model(
                requirement=requirement,
                pool=pool,
                traces=traces,
                budget=budget,
                repair_feedback=(repair_feedback),
                force_finalize=(force_finalize),
                model=model,
            )

        except OutputParserException as exc:
            if attempt >= MAX_DECISION_REPAIR_ATTEMPTS:
                raise SemanticMappingValidationError(
                    "Semantic Mapping 结构化输出修正一次后仍不合法。"
                ) from exc

            # 只返回契约错误，不自动修改模型输出。
            # 限制异常文本长度，避免把异常堆栈
            # 无限放大到下一轮提示。
            repair_feedback = (
                "上一轮输出未通过 "
                "SemanticMappingDecision 契约。"
                "请严格删除工具不允许的参数，"
                "并重新输出完整 JSON。"
                f"错误：{str(exc)[:2000]}"
            )

            continue

        decision_error = _forced_finalization_error(
            decision,
            force_finalize=(force_finalize),
        ) or _selection_budget_error(
            decision,
            budget,
        )

        if decision_error is None:
            return decision

        if attempt >= MAX_DECISION_REPAIR_ATTEMPTS:
            if force_finalize and decision.needs_more_context:
                raise SemanticMappingValidationError(decision_error + "模型修正一次后仍未收敛。")

            raise SemanticMappingValidationError(decision_error + "模型修正一次后仍超过预算。")

        repair_feedback = (
            decision_error + "请只保留证据最强的文件，"
            "重新输出完整 JSON；"
            "不得自行发明新路径或继续请求工具。"
        )

    raise SemanticMappingValidationError("Semantic Mapping 未生成有效决策。")


def run_semantic_mapping(
    requirement: RequirementSpec,
    pool: CandidatePool,
    repository: RepositorySnapshot,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
    *,
    budget: Budget,
    set_id: str = "CS-0001",
    model: BaseChatModel | None = None,
) -> SemanticMappingResult:
    """在有界工具循环内扩展候选池并冻结最终文件集。"""

    if requirement.spec_digest != pool.spec_digest:
        raise SemanticMappingValidationError(
            "RequirementSpec 与 CandidatePool 的 spec_digest 不一致。"
        )

    if repository.repository_name != symbol_index.repository_name:
        raise SemanticMappingValidationError("仓库快照与符号索引不匹配。")

    if repository.repository_name != dependency_graph.repository_name:
        raise SemanticMappingValidationError("仓库快照与依赖图不匹配。")

    selected_model = model or get_chat_model()

    current_pool = pool

    decisions: list[SemanticMappingDecision] = []

    traces: list[SemanticToolTrace] = []

    query_count = 0

    known_paths = _known_repository_paths(
        repository,
    )

    final_decision: SemanticMappingDecision | None = None

    # 最多允许三轮受控扩池。
    for _decision_attempt in range(
        1,
        MAX_EXPANSION_ROUNDS + 1,
    ):
        decision = _invoke_mapping_model_with_repair(
            requirement=requirement,
            pool=current_pool,
            traces=tuple(traces),
            budget=budget,
            model=selected_model,
        )

        decisions.append(decision)

        if not decision.needs_more_context:
            final_decision = decision
            break

        # 扩池轮次基于 CandidatePool 已消耗的轮数
        # 继续累计，防止重复调用时重新从第一轮开始。
        expansion_round = current_pool.expansion_rounds_used + 1

        if expansion_round > MAX_EXPANSION_ROUNDS:
            raise SemanticMappingValidationError(
                f"语义扩池轮次超过 {MAX_EXPANSION_ROUNDS} 轮限制。"
            )

        for query in decision.queries:
            query_count += 1

            # 已有查询记录和本次查询共同受到
            # 12 次白名单工具查询上限约束。
            if len(pool.expansion_records) + query_count > MAX_EXPANSION_QUERIES:
                raise SemanticMappingValidationError(
                    f"语义扩池查询超过 {MAX_EXPANSION_QUERIES} 次限制。"
                )

            candidates = execute_semantic_query(
                query,
                symbol_index=symbol_index,
                dependency_graph=(dependency_graph),
            )

            # reason 是解释性文本，
            # 不进入稳定的工具参数摘要。
            args_digest = canonical_digest(
                "semantic_query",
                query.model_dump(
                    mode="json",
                    exclude={
                        "reason",
                    },
                ),
            )

            outcome = apply_candidate_expansion(
                current_pool,
                ExpansionRequest(
                    round=expansion_round,
                    tool=query.tool,
                    args_digest=args_digest,
                    candidates=candidates,
                ),
                known_paths=known_paths,
            )

            current_pool = outcome.pool

            traces.append(
                SemanticToolTrace(
                    round=expansion_round,
                    tool=query.tool,
                    args_digest=args_digest,
                    accepted_paths=(outcome.accepted_paths),
                    rejected=(outcome.rejected),
                )
            )

    if final_decision is None:
        # 三轮扩池用尽后，不由代码擅自选择文件。
        # 给模型一次只允许最终选择的强制收敛轮。
        # 该调用不能执行新工具，也不会扩大查询预算。
        final_decision = _invoke_mapping_model_with_repair(
            requirement=requirement,
            pool=current_pool,
            traces=tuple(traces),
            budget=budget,
            model=selected_model,
            force_finalize=True,
        )

        decisions.append(
            final_decision,
        )

    normalized_selected = tuple(
        normalize_digest_path(path) for path in (final_decision.selected_paths)
    )

    if current_pool.entries and not normalized_selected:
        raise SemanticMappingValidationError(
            "CandidatePool 非空时，Semantic Mapping 必须选择至少一个文件。"
        )

    pool_paths = {
        normalize_digest_path(
            entry.path,
        )
        for entry in current_pool.entries
    }

    unknown_paths = sorted(set(normalized_selected) - pool_paths)

    if unknown_paths:
        raise SemanticMappingValidationError(
            "Semantic Mapping 返回了候选池外路径：" + "、".join(unknown_paths) + "。"
        )

    try:
        candidate_set = freeze_candidate_set(
            current_pool,
            repository,
            budget=budget,
            set_id=set_id,
            selected_paths=(normalized_selected),
            selected_by="semantic_map",
        )

    except ValueError as exc:
        raise SemanticMappingValidationError(str(exc)) from exc

    return SemanticMappingResult(
        candidate_pool=current_pool,
        candidate_set=candidate_set,
        decisions=tuple(decisions),
        tool_traces=tuple(traces),
        query_count=query_count,
    )
