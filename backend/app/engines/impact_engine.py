"""基于冻结候选集、符号索引和需求契约的确定性 Impact Engine。

v1 只生成能够由当前仓库事实直接证明的变更点：

1. RequirementSpec.entity_hints 中命中的已有类、函数或方法；
2. RequirementSpec.symbol_hints 中明确声明的已有符号；
3. 父符号真实存在且目标文件已进入 CandidateSet 的新增成员。

本模块不调用 LLM，不猜测新文件路径，也不把依赖文件自动当成修改文件。
所有 ChangePoint 都必须经过 Analysis Guard 后才能进入 Risk、Review 和 Plan。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.engines.branch_result import (
    build_candidate_set_branch_result,
)
from app.guards.analysis_guard import guard_change_points
from app.schemas.branch_result import (
    BranchFinding,
    BranchResult,
)
from app.schemas.code_index import (
    CodeSymbol,
    RepositorySymbolIndex,
)
from app.schemas.contracts import (
    CandidateSet,
    ChangePoint,
    EvidencePointer,
    RequirementSpec,
    SymbolKind,
    SymbolRef,
)
from app.schemas.guard import (
    GuardContext,
    GuardDigests,
    GuardReport,
    RepositoryFile,
    SymbolLocation,
)
from app.schemas.repository import RepositorySnapshot
from app.tools.contract_digest import normalize_digest_path

EXISTING_SYMBOL_RULE = "IMPACT-EXISTING-SYMBOL"
NEW_SYMBOL_RULE = "IMPACT-NEW-SYMBOL"


class ImpactEngineValidationError(ValueError):
    """Impact Engine 输入或 Guard 结果无法安全进入下游。"""


@dataclass(frozen=True)
class ImpactEngineResult:
    """确定性影响分析的完整输出。"""

    branch_result: BranchResult

    change_points: tuple[ChangePoint, ...]

    guard_report: GuardReport


@dataclass(frozen=True)
class _PlannedChange:
    """分配 CP ID 前的内部稳定变更描述。"""

    path: str

    target: SymbolRef

    change_type: str

    ac_ids: tuple[str, ...]

    evidence: tuple[EvidencePointer, ...]

    confidence: float

    rule_id: str


def _load_requirement(
    resources: Mapping[str, object],
) -> RequirementSpec:
    """读取全链路唯一 RequirementSpec。"""

    raw_requirement = resources.get("requirement_spec")

    if raw_requirement is None:
        raise KeyError("Impact Engine 缺少 requirement_spec 资源。")

    return RequirementSpec.model_validate(raw_requirement)


def _load_repository(
    resources: Mapping[str, object],
) -> RepositorySnapshot:
    """读取冻结候选集对应的仓库快照。"""

    raw_repository = resources.get("repository_snapshot")

    if raw_repository is None:
        raise KeyError("Impact Engine 缺少 repository_snapshot 资源。")

    return RepositorySnapshot.model_validate(raw_repository)


def _load_symbol_index(
    resources: Mapping[str, object],
) -> RepositorySymbolIndex:
    """读取与 CandidateSet index_digest 对应的符号索引。"""

    raw_index = resources.get("symbol_index")

    if raw_index is None:
        raise KeyError("Impact Engine 缺少 symbol_index 资源。")

    return RepositorySymbolIndex.model_validate(raw_index)


def _active_ac_ids(
    requirement: RequirementSpec,
) -> tuple[str, ...]:
    """返回稳定排序的 active 验收标准 ID。"""

    return tuple(
        sorted(
            criterion.id
            for criterion in requirement.acceptance_criteria
            if criterion.status == "active"
        )
    )


def _contract_symbol_kind(
    symbol: CodeSymbol,
) -> SymbolKind:
    """把 Python 索引符号类型转换为全链路契约类型。"""

    if symbol.kind == "class":
        return "class"

    if symbol.kind in {
        "method",
        "async_method",
    }:
        return "method"

    return "function"


def _parent_symbol(
    symbol: CodeSymbol,
) -> str | None:
    """方法使用类限定名作为父符号，函数和类不设置父符号。"""

    if symbol.kind not in {
        "method",
        "async_method",
    }:
        return None

    return symbol.qualified_name.rsplit(
        ".",
        maxsplit=1,
    )[0]


def _candidate_source_paths(
    candidate_set: CandidateSet,
) -> frozenset[str]:
    """返回 Impact Engine 获准读取的源码文件。"""

    return frozenset(
        normalize_digest_path(file_ref.path)
        for file_ref in candidate_set.files
        if file_ref.kind == "source"
    )


def _candidate_symbols(
    candidate_set: CandidateSet,
    symbol_index: RepositorySymbolIndex,
) -> tuple[CodeSymbol, ...]:
    """只返回冻结 CandidateSet 源码文件中的真实符号。"""

    allowed_paths = _candidate_source_paths(candidate_set)

    return tuple(
        sorted(
            (
                symbol
                for indexed_file in symbol_index.files
                if (normalize_digest_path(indexed_file.file_path) in allowed_paths)
                and indexed_file.parse_error is None
                for symbol in indexed_file.symbols
            ),
            key=lambda item: (
                normalize_digest_path(item.file_path),
                item.line_start,
                item.qualified_name,
            ),
        )
    )


def _all_index_symbols(
    symbol_index: RepositorySymbolIndex,
) -> tuple[CodeSymbol, ...]:
    """返回完整索引中的真实符号，用于识别候选集漏召回。"""

    return tuple(
        symbol
        for indexed_file in symbol_index.files
        if indexed_file.parse_error is None
        for symbol in indexed_file.symbols
    )


def _symbol_matches_name(
    symbol: CodeSymbol,
    name: str,
) -> bool:
    """使用简称或完整限定名精确匹配符号。"""

    requested = name.strip().casefold()

    return requested in {
        symbol.name.casefold(),
        symbol.qualified_name.casefold(),
    }


def _ac_ids_for_entity_hint(
    requirement: RequirementSpec,
    *,
    concept: str,
    lexical_variants: tuple[str, ...],
    symbol_candidates: tuple[str, ...],
) -> tuple[str, ...]:
    """用需求词汇把符号命中关联到 AC，无法细分时保守关联全部 AC。"""

    terms = tuple(
        term.strip().casefold()
        for term in (
            concept,
            *lexical_variants,
            *symbol_candidates,
        )
        if term.strip()
    )

    matched = tuple(
        sorted(
            criterion.id
            for criterion in requirement.acceptance_criteria
            if criterion.status == "active"
            and any(term in criterion.description.casefold() for term in terms)
        )
    )

    return matched or _active_ac_ids(requirement)


def _change_type_for_existing_symbol(
    requirement: RequirementSpec,
) -> str:
    """只有明确 remove 需求可确定为 delete，其他已有符号按 semantic 表达。"""

    if requirement.change_type == "remove":
        return "delete"

    return "semantic"


def _planned_existing_changes(
    requirement: RequirementSpec,
    *,
    candidate_symbols: tuple[CodeSymbol, ...],
    all_symbols: tuple[CodeSymbol, ...],
    candidate_paths: frozenset[str],
) -> tuple[_PlannedChange, ...]:
    """生成已有符号的确定性变更描述。"""

    planned: dict[
        tuple[str, str, str, str],
        _PlannedChange,
    ] = {}

    existing_hints = tuple(
        hint
        for hint in requirement.symbol_hints
        if hint.expect == "existing" and hint.name is not None
    )

    for hint in existing_hints:
        assert hint.name is not None

        repository_matches = tuple(
            symbol
            for symbol in all_symbols
            if _symbol_matches_name(
                symbol,
                hint.name,
            )
        )

        if not repository_matches:
            raise ImpactEngineValidationError(f"需求声明的已有符号 {hint.name} 不存在于仓库索引。")

        if not any(
            normalize_digest_path(symbol.file_path) in candidate_paths
            for symbol in repository_matches
        ):
            raise ImpactEngineValidationError(
                f"需求明确引用的已有符号 {hint.name} 未进入 CandidateSet。"
            )

        for symbol in candidate_symbols:
            if not _symbol_matches_name(
                symbol,
                hint.name,
            ):
                continue

            target = SymbolRef(
                name=symbol.name,
                parent_symbol=_parent_symbol(symbol),
                expect="existing",
                kind=_contract_symbol_kind(symbol),
            )

            change_type = _change_type_for_existing_symbol(requirement)

            key = (
                normalize_digest_path(symbol.file_path),
                target.parent_symbol or "",
                target.name or "",
                change_type,
            )

            planned[key] = _PlannedChange(
                path=key[0],
                target=target,
                change_type=change_type,
                ac_ids=_active_ac_ids(requirement),
                evidence=(
                    EvidencePointer(
                        path=key[0],
                        line=symbol.line_start,
                        end_line=symbol.line_end,
                        kind="def",
                    ),
                ),
                confidence=0.95,
                rule_id=EXISTING_SYMBOL_RULE,
            )

    for entity_hint in requirement.entity_hints:
        ac_ids = _ac_ids_for_entity_hint(
            requirement,
            concept=entity_hint.concept,
            lexical_variants=(entity_hint.lexical_variants),
            symbol_candidates=(entity_hint.symbol_candidates),
        )

        for symbol in candidate_symbols:
            if not any(
                _symbol_matches_name(
                    symbol,
                    candidate,
                )
                for candidate in entity_hint.symbol_candidates
            ):
                continue

            target = SymbolRef(
                name=symbol.name,
                parent_symbol=_parent_symbol(symbol),
                expect="existing",
                kind=_contract_symbol_kind(symbol),
            )

            change_type = _change_type_for_existing_symbol(requirement)

            key = (
                normalize_digest_path(symbol.file_path),
                target.parent_symbol or "",
                target.name or "",
                change_type,
            )

            existing = planned.get(key)

            combined_ac_ids = tuple(
                sorted(
                    {
                        *ac_ids,
                        *(existing.ac_ids if existing is not None else ()),
                    }
                )
            )

            planned[key] = _PlannedChange(
                path=key[0],
                target=target,
                change_type=change_type,
                ac_ids=combined_ac_ids,
                evidence=(
                    EvidencePointer(
                        path=key[0],
                        line=symbol.line_start,
                        end_line=symbol.line_end,
                        kind="def",
                    ),
                ),
                confidence=max(
                    0.85,
                    (existing.confidence if existing is not None else 0.0),
                ),
                rule_id=EXISTING_SYMBOL_RULE,
            )

    return tuple(planned[key] for key in sorted(planned))


def _planned_new_changes(
    requirement: RequirementSpec,
    *,
    candidate_symbols: tuple[CodeSymbol, ...],
    all_symbols: tuple[CodeSymbol, ...],
    candidate_paths: frozenset[str],
) -> tuple[_PlannedChange, ...]:
    """为具有真实父符号的新成员生成 add 变更点。"""

    planned: list[_PlannedChange] = []

    all_qualified_names = {symbol.qualified_name.casefold() for symbol in all_symbols}

    candidate_by_qualified_name = {
        symbol.qualified_name.casefold(): symbol for symbol in candidate_symbols
    }

    for hint in requirement.symbol_hints:
        if hint.expect != "new":
            continue

        if hint.name is None or hint.kind == "module":
            raise ImpactEngineValidationError(
                "确定性 Impact Engine 无法为新增模块猜测文件路径；请先由人工明确目标路径。"
            )

        parent = hint.parent_symbol

        if parent is None:
            raise ImpactEngineValidationError(
                f"新增符号 {hint.name} 缺少 parent_symbol，无法确定目标文件。"
            )

        complete_name = f"{parent}.{hint.name}".casefold()

        if complete_name in all_qualified_names:
            raise ImpactEngineValidationError(
                f"新增符号 {parent}.{hint.name} 已经存在于仓库索引中。"
            )

        parent_symbol = candidate_by_qualified_name.get(parent.casefold())

        if parent_symbol is None:
            repository_parent = next(
                (
                    symbol
                    for symbol in all_symbols
                    if (symbol.qualified_name.casefold() == parent.casefold())
                ),
                None,
            )

            if repository_parent is not None:
                parent_path = normalize_digest_path(repository_parent.file_path)

                if parent_path not in candidate_paths:
                    raise ImpactEngineValidationError(
                        f"新增符号 {hint.name} 的父符号 {parent} 未进入 CandidateSet。"
                    )

            raise ImpactEngineValidationError(f"新增符号 {hint.name} 的父符号 {parent} 不存在。")

        path = normalize_digest_path(parent_symbol.file_path)

        planned.append(
            _PlannedChange(
                path=path,
                target=hint,
                change_type="add",
                ac_ids=_active_ac_ids(requirement),
                evidence=(
                    EvidencePointer(
                        path=path,
                        line=(parent_symbol.line_start),
                        end_line=(parent_symbol.line_end),
                        kind="def",
                    ),
                ),
                confidence=0.95,
                rule_id=NEW_SYMBOL_RULE,
            )
        )

    return tuple(
        sorted(
            planned,
            key=lambda item: (
                item.path,
                item.target.parent_symbol or "",
                item.target.name or "",
            ),
        )
    )


def _build_change_points(
    requirement: RequirementSpec,
    candidate_set: CandidateSet,
    symbol_index: RepositorySymbolIndex,
) -> tuple[ChangePoint, ...]:
    """生成具有稳定 CP ID 的候选变更点。"""

    candidate_paths = _candidate_source_paths(candidate_set)

    candidate_symbols = _candidate_symbols(
        candidate_set,
        symbol_index,
    )

    all_symbols = _all_index_symbols(symbol_index)

    planned = (
        *_planned_existing_changes(
            requirement,
            candidate_symbols=candidate_symbols,
            all_symbols=all_symbols,
            candidate_paths=candidate_paths,
        ),
        *_planned_new_changes(
            requirement,
            candidate_symbols=candidate_symbols,
            all_symbols=all_symbols,
            candidate_paths=candidate_paths,
        ),
    )

    stable = sorted(
        planned,
        key=lambda item: (
            item.path,
            item.target.parent_symbol or "",
            item.target.name or "",
            item.change_type,
            item.rule_id,
        ),
    )

    return tuple(
        ChangePoint(
            cp_id=f"CP-{index:04d}",
            path=item.path,
            target=item.target,
            change_type=item.change_type,
            declares_new_file=False,
            callers=(),
            callers_verified_empty=False,
            ac_ids=item.ac_ids,
            evidence=item.evidence,
            confidence=item.confidence,
            source="impact",
            rule_ids=(item.rule_id,),
        )
        for index, item in enumerate(
            stable,
            start=1,
        )
    )


def _guard_context(
    requirement: RequirementSpec,
    repository: RepositorySnapshot,
    symbol_index: RepositorySymbolIndex,
) -> GuardContext:
    """把仓库快照和符号索引转换为 Guard 事实上下文。"""

    repository_files = tuple(
        RepositoryFile(
            path=normalize_digest_path(item.relative_path),
            line_count=len(item.content.splitlines()),
        )
        for item in sorted(
            repository.files,
            key=lambda item: normalize_digest_path(item.relative_path),
        )
    )

    symbols = tuple(
        SymbolLocation(
            qualified_name=(symbol.qualified_name),
            path=normalize_digest_path(symbol.file_path),
            kind=_contract_symbol_kind(symbol),
            start_line=symbol.line_start,
            end_line=symbol.line_end,
        )
        for symbol in sorted(
            _all_index_symbols(symbol_index),
            key=lambda item: (
                item.qualified_name,
                item.file_path,
                item.line_start,
            ),
        )
    )

    return GuardContext(
        repository_files=repository_files,
        symbols=symbols,
        active_ac_ids=frozenset(_active_ac_ids(requirement)),
    )


def _analyzed_paths(
    candidate_set: CandidateSet,
    symbol_index: RepositorySymbolIndex,
) -> tuple[
    tuple[str, ...],
    dict[str, str],
]:
    """计算 Impact 分支真实完成 AST 分析的文件和漏报原因。"""

    source_paths = _candidate_source_paths(candidate_set)

    index_by_path = {
        normalize_digest_path(indexed_file.file_path): indexed_file
        for indexed_file in symbol_index.files
    }

    analyzed: list[str] = []
    missing_reasons: dict[str, str] = {}

    for path in sorted(source_paths):
        indexed_file = index_by_path.get(path)

        if indexed_file is None:
            missing_reasons[path] = (
                "文件未进入 Python SymbolIndex，Impact Engine 无法完成确定性符号分析。"
            )
            continue

        if indexed_file.parse_error is not None:
            missing_reasons[path] = (
                "文件存在 Python 语法解析错误，Impact Engine 无法完成确定性符号分析。"
            )
            continue

        analyzed.append(path)

    return (
        tuple(analyzed),
        missing_reasons,
    )


def _finding_priority(
    change_point: ChangePoint,
    requirement: RequirementSpec,
) -> str:
    """删除变更和 P0 验收标准对应 major，其余为 minor。"""

    priority_by_ac = {
        criterion.id: criterion.priority for criterion in requirement.acceptance_criteria
    }

    if change_point.change_type == "delete" or any(
        priority_by_ac.get(ac_id) == "P0" for ac_id in change_point.ac_ids
    ):
        return "major"

    return "minor"


def _build_findings(
    change_points: Sequence[ChangePoint],
    requirement: RequirementSpec,
) -> tuple[BranchFinding, ...]:
    """把 Guard 通过的变更点转换为统一 Impact findings。"""

    action_labels = {
        "add": "新增",
        "signature": "调整签名",
        "semantic": "修改语义",
        "delete": "删除",
    }

    return tuple(
        BranchFinding(
            finding_id=f"IMP-{index:04d}",
            priority=_finding_priority(
                change_point,
                requirement,
            ),
            path=change_point.path,
            symbol=(change_point.target.name),
            summary=(
                f"建议在 {change_point.path} 中"
                f"{action_labels[change_point.change_type]}"
                f"符号 "
                f"{change_point.target.name or '<module>'}。"
            ),
            ac_ids=tuple(sorted(change_point.ac_ids)),
            evidence_ids=tuple(
                f"{evidence.kind.upper()}:{evidence.path}:{evidence.line}"
                for evidence in change_point.evidence
            ),
            rule_ids=(change_point.rule_ids),
        )
        for index, change_point in enumerate(
            change_points,
            start=1,
        )
    )


def analyze_impact_evidence(
    candidate_set: CandidateSet,
    digests: GuardDigests,
    resources: Mapping[str, object],
) -> ImpactEngineResult:
    """生成、校验并汇总确定性 Impact 证据。"""

    requirement = _load_requirement(resources)

    repository = _load_repository(resources)

    symbol_index = _load_symbol_index(resources)

    if requirement.spec_digest != candidate_set.spec_digest:
        raise ImpactEngineValidationError("RequirementSpec 与 CandidateSet 的 spec_digest 不一致。")

    if repository.repository_name != symbol_index.repository_name:
        raise ImpactEngineValidationError("RepositorySnapshot 与 SymbolIndex 不属于同一仓库。")

    change_points = _build_change_points(
        requirement,
        candidate_set,
        symbol_index,
    )

    guard_evaluation = guard_change_points(
        change_points,
        candidate_set=candidate_set,
        context=_guard_context(
            requirement,
            repository,
            symbol_index,
        ),
        branch="impact",
        branch_kind="engine",
        consumed_digests=digests,
    )

    if guard_evaluation.report.verdict != "pass":
        reasons = "、".join(guard_evaluation.report.blocking_reasons)

        raise ImpactEngineValidationError(
            "Impact ChangePoint 未通过 "
            "Analysis Guard：" + (reasons or guard_evaluation.report.verdict) + "。"
        )

    validated_change_points = guard_evaluation.change_points

    (
        analyzed_paths,
        missing_reasons,
    ) = _analyzed_paths(
        candidate_set,
        symbol_index,
    )

    branch_result = build_candidate_set_branch_result(
        branch="impact",
        candidate_set=candidate_set,
        analyzed_paths=analyzed_paths,
        findings=_build_findings(
            validated_change_points,
            requirement,
        ),
        consumed_digests=digests,
        missing_reasons=missing_reasons,
    )

    return ImpactEngineResult(
        branch_result=branch_result,
        change_points=(validated_change_points),
        guard_report=(guard_evaluation.report),
    )


def analyze_impact(
    candidate_set: CandidateSet,
    digests: GuardDigests,
    resources: Mapping[str, object],
) -> BranchResult:
    """提供符合 ParallelAnalysis BranchRunner 的 Impact 入口。"""

    return analyze_impact_evidence(
        candidate_set,
        digests,
        resources,
    ).branch_result
