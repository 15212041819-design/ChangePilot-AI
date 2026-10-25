"""对 ChangePoint 执行路径、符号、证据、AC 与 digest 真实性校验。

Guard 不负责产生新的业务结论。它只做三件事：

1. normalize：执行路径分隔符、大小写、去重和排序等机械修正；
2. repair：把需要 LLM 重新判断的问题退回原 Agent，最多一次；
3. fail：阻止虚构事实、越权写入或不一致摘要继续进入下游。
"""

from collections.abc import Iterable
from pathlib import PurePosixPath

from app.schemas.contracts import (
    CallerRef,
    CandidateSet,
    ChangePoint,
    EvidencePointer,
)
from app.schemas.guard import (
    BranchKind,
    GuardBranch,
    GuardContext,
    GuardDigests,
    GuardEvaluation,
    GuardReport,
    InvalidFinding,
    NormalizeAction,
    SymbolLocation,
)

GUARD_VERSION = "1.0.0"


def normalize_repository_path(
    path: str,
) -> str:
    """把输入路径转换为仓库内部统一使用的 POSIX 形式。"""

    normalized = path.strip().replace("\\", "/")

    while "//" in normalized:
        normalized = normalized.replace("//", "/")

    while normalized.startswith("./"):
        normalized = normalized[2:]

    return PurePosixPath(normalized).as_posix()


def _path_is_outside_repository(
    path: str,
) -> bool:
    """识别绝对路径和包含父目录穿越的仓库外路径。"""

    candidate = path.replace("\\", "/")
    parts = PurePosixPath(candidate).parts

    return (
        candidate.startswith("/") or (len(candidate) >= 2 and candidate[1] == ":") or ".." in parts
    )


def _repository_file_maps(
    context: GuardContext,
) -> tuple[dict[str, int], dict[str, str]]:
    """构造真实路径和大小写无关路径映射。"""

    files = {
        normalize_repository_path(item.path): item.line_count for item in context.repository_files
    }

    casefolded = {path.casefold(): path for path in files}

    return files, casefolded


def _normalize_path_with_repository_case(
    path: str,
    *,
    casefolded_files: dict[str, str],
) -> tuple[str, tuple[NormalizeAction, ...]]:
    """归一化路径并在可以唯一确定时校正真实大小写。"""

    actions: list[NormalizeAction] = []
    original = path
    normalized = normalize_repository_path(path)

    if "\\" in original:
        actions.append(
            NormalizeAction(
                kind="path_separator",
                path=normalized,
                detail=f"路径分隔符已归一化：{original} -> {normalized}",
            )
        )

    cleaned_original = original.replace("\\", "/")

    if cleaned_original != normalized and "\\" not in original:
        actions.append(
            NormalizeAction(
                kind="path_cleanup",
                path=normalized,
                detail=f"路径前缀或重复分隔符已清理：{original} -> {normalized}",
            )
        )

    real_path = casefolded_files.get(normalized.casefold())

    if real_path is not None and real_path != normalized:
        actions.append(
            NormalizeAction(
                kind="path_case",
                path=real_path,
                detail=f"路径大小写已校正：{normalized} -> {real_path}",
            )
        )

        normalized = real_path

    return normalized, tuple(actions)


def _normalize_evidence(
    evidence: EvidencePointer,
    *,
    casefolded_files: dict[str, str],
) -> tuple[EvidencePointer, tuple[NormalizeAction, ...]]:
    """归一化证据路径，保留行号和证据类型。"""

    path, actions = _normalize_path_with_repository_case(
        evidence.path,
        casefolded_files=casefolded_files,
    )

    return evidence.model_copy(
        update={
            "path": path,
        }
    ), actions


def _normalize_caller(
    caller: CallerRef,
    *,
    casefolded_files: dict[str, str],
) -> tuple[CallerRef, tuple[NormalizeAction, ...]]:
    """归一化调用点路径。"""

    path, actions = _normalize_path_with_repository_case(
        caller.path,
        casefolded_files=casefolded_files,
    )

    return caller.model_copy(
        update={
            "path": path,
        }
    ), actions


def _normalize_change_point(
    change_point: ChangePoint,
    *,
    casefolded_files: dict[str, str],
) -> tuple[ChangePoint, tuple[NormalizeAction, ...]]:
    """对一个变更点及其嵌套路径执行机械归一化。"""

    path, path_actions = _normalize_path_with_repository_case(
        change_point.path,
        casefolded_files=casefolded_files,
    )

    evidence_items: list[EvidencePointer] = []
    caller_items: list[CallerRef] = []
    actions = list(path_actions)

    for item in change_point.evidence:
        normalized, nested_actions = _normalize_evidence(
            item,
            casefolded_files=casefolded_files,
        )

        evidence_items.append(normalized)
        actions.extend(nested_actions)

    for item in change_point.callers:
        normalized, nested_actions = _normalize_caller(
            item,
            casefolded_files=casefolded_files,
        )

        caller_items.append(normalized)
        actions.extend(nested_actions)

    normalized_change_point = change_point.model_copy(
        update={
            "path": path,
            "evidence": tuple(evidence_items),
            "callers": tuple(caller_items),
        }
    )

    return normalized_change_point, tuple(actions)


def _finding_identity(
    change_point: ChangePoint,
) -> tuple[str, str, str, str, str]:
    """返回 finding 去重使用的稳定身份键。"""

    return (
        change_point.path,
        change_point.target.parent_symbol or "",
        change_point.target.name or "",
        change_point.change_type,
        change_point.source,
    )


def _deduplicate_change_points(
    change_points: Iterable[ChangePoint],
) -> tuple[
    tuple[ChangePoint, ...],
    tuple[NormalizeAction, ...],
]:
    """删除重复 finding，并保留置信度最高的版本。"""

    selected: dict[
        tuple[str, str, str, str, str],
        ChangePoint,
    ] = {}

    duplicate_actions: list[NormalizeAction] = []

    for item in change_points:
        key = _finding_identity(item)
        existing = selected.get(key)

        if existing is None:
            selected[key] = item
            continue

        if item.confidence > existing.confidence:
            selected[key] = item

        duplicate_actions.append(
            NormalizeAction(
                kind="duplicate_finding",
                path=item.path,
                detail=(f"重复变更点 {item.cp_id} 已合并，保留相同目标中 confidence 最高的条目。"),
            )
        )

    stable = tuple(
        sorted(
            selected.values(),
            key=lambda item: (
                item.path,
                item.target.parent_symbol or "",
                item.target.name or "",
                item.change_type,
                item.cp_id,
            ),
        )
    )

    original_order = tuple(item.cp_id for item in selected.values())

    stable_order = tuple(item.cp_id for item in stable)

    if original_order != stable_order:
        duplicate_actions.append(
            NormalizeAction(
                kind="stable_sort",
                detail=("变更点已按 path、symbol、change_type、cp_id 稳定排序。"),
            )
        )

    return stable, tuple(duplicate_actions)


def _symbol_candidates(
    change_point: ChangePoint,
) -> tuple[str, ...]:
    """构造目标符号可能使用的限定名。"""

    target = change_point.target

    if target.name is None:
        return ()

    candidates = [
        target.name,
    ]

    if target.parent_symbol is not None:
        candidates.insert(
            0,
            f"{target.parent_symbol}.{target.name}",
        )

    return tuple(dict.fromkeys(candidates))


def _find_symbol(
    change_point: ChangePoint,
    symbols: dict[str, SymbolLocation],
) -> SymbolLocation | None:
    """使用限定名优先策略查找目标符号。"""

    for candidate in _symbol_candidates(change_point):
        found = symbols.get(candidate)

        if found is not None:
            return found

    return None


def _append_invalid(
    invalid: list[InvalidFinding],
    *,
    change_point: ChangePoint,
    error_type: str,
    detail: str,
    repairable: bool,
    path: str | None = None,
) -> None:
    """为当前变更点追加一条结构统一的错误记录。"""

    invalid.append(
        InvalidFinding(
            finding_id=change_point.cp_id,
            error_type=error_type,
            path=path or change_point.path,
            symbol=change_point.target.name,
            detail=detail,
            repairable=repairable,
        )
    )


def _validate_change_point(
    change_point: ChangePoint,
    *,
    files: dict[str, int],
    symbols: dict[str, SymbolLocation],
    active_ac_ids: frozenset[str],
) -> tuple[InvalidFinding, ...]:
    """校验一个变更点引用的路径、符号、AC 和代码证据。"""

    invalid: list[InvalidFinding] = []

    if _path_is_outside_repository(change_point.path):
        _append_invalid(
            invalid,
            change_point=change_point,
            error_type="path_outside_repository",
            detail="变更点路径是绝对路径或包含父目录穿越。",
            repairable=False,
        )

        return tuple(invalid)

    path_exists = change_point.path in files

    if change_point.declares_new_file:
        if path_exists:
            _append_invalid(
                invalid,
                change_point=change_point,
                error_type="invalid_new_symbol_target",
                detail="变更点声明新文件，但目标路径已经存在。",
                repairable=False,
            )

    elif not path_exists:
        _append_invalid(
            invalid,
            change_point=change_point,
            error_type="unknown_file",
            detail="变更点引用的目标文件不存在。",
            repairable=False,
        )

    target_symbol = _find_symbol(
        change_point,
        symbols,
    )

    if change_point.target.expect == "existing" and target_symbol is None:
        _append_invalid(
            invalid,
            change_point=change_point,
            error_type="unknown_existing_symbol",
            detail="expect=existing 的目标符号不在 SymbolIndex 中。",
            repairable=False,
        )

    if change_point.target.expect == "new":
        parent = change_point.target.parent_symbol

        parent_exists = parent is not None and parent in symbols

        if not change_point.declares_new_file and not path_exists and not parent_exists:
            _append_invalid(
                invalid,
                change_point=change_point,
                error_type="invalid_new_symbol_target",
                detail=("新增符号缺少真实目标文件、真实父符号或新文件声明。"),
                repairable=False,
            )

        if parent is not None and not parent_exists:
            _append_invalid(
                invalid,
                change_point=change_point,
                error_type="invalid_new_symbol_target",
                detail=("新增符号声明的 parent_symbol 不在 SymbolIndex 中。"),
                repairable=False,
            )

    for ac_id in change_point.ac_ids:
        if ac_id not in active_ac_ids:
            _append_invalid(
                invalid,
                change_point=change_point,
                error_type="inactive_acceptance_criterion",
                detail=(f"验收标准 {ac_id} 不存在或不是 active 状态。"),
                repairable=False,
            )

    for evidence in change_point.evidence:
        if _path_is_outside_repository(evidence.path):
            _append_invalid(
                invalid,
                change_point=change_point,
                error_type="path_outside_repository",
                detail="证据路径越出仓库根目录。",
                repairable=False,
                path=evidence.path,
            )

            continue

        line_count = files.get(evidence.path)

        if line_count is None:
            _append_invalid(
                invalid,
                change_point=change_point,
                error_type="invalid_evidence_path",
                detail="证据引用的文件不存在。",
                repairable=False,
                path=evidence.path,
            )

            continue

        if evidence.line > line_count or (
            evidence.end_line is not None and evidence.end_line > line_count
        ):
            _append_invalid(
                invalid,
                change_point=change_point,
                error_type="evidence_line_out_of_range",
                detail="证据行号超过文件实际行数。",
                repairable=False,
                path=evidence.path,
            )

            continue

        if (
            evidence.kind == "def"
            and target_symbol is not None
            and not (target_symbol.start_line <= evidence.line <= target_symbol.end_line)
        ):
            _append_invalid(
                invalid,
                change_point=change_point,
                error_type="definition_line_outside_symbol",
                detail="def 证据行号没有落在目标符号范围内。",
                repairable=True,
                path=evidence.path,
            )

    for caller in change_point.callers:
        if caller.path not in files:
            _append_invalid(
                invalid,
                change_point=change_point,
                error_type="unknown_caller_file",
                detail="调用点引用的文件不存在。",
                repairable=False,
                path=caller.path,
            )

    return tuple(invalid)


def _expected_digests(
    candidate_set: CandidateSet,
) -> GuardDigests:
    """从冻结候选集读取权威上游摘要。"""

    return GuardDigests(
        set_digest=candidate_set.set_digest,
        spec_digest=candidate_set.spec_digest,
        index_digest=candidate_set.index_digest,
    )


def _unique_strings(
    values: Iterable[str],
) -> tuple[str, ...]:
    """返回保持首次出现顺序的去重字符串。"""

    return tuple(dict.fromkeys(values))


def guard_change_points(
    change_points: Iterable[ChangePoint],
    *,
    candidate_set: CandidateSet,
    context: GuardContext,
    branch: GuardBranch,
    branch_kind: BranchKind,
    consumed_digests: GuardDigests | None = None,
    repair_attempt: int = 0,
    candidate_set_write_attempted: bool = False,
) -> GuardEvaluation:
    """校验并归一化一个分支产生的 ChangePoint。

    返回值中的 change_points 可以继续进入下游。

    只有 report.verdict 为 pass 时，才表示全部事实已经通过校验。
    repair 必须退回原 Agent。
    fail 必须阻断当前分支。
    """

    if repair_attempt not in {
        0,
        1,
    }:
        raise ValueError("repair_attempt 只允许为 0 或 1。")

    files, casefolded_files = _repository_file_maps(context)

    symbol_map = {item.qualified_name: item for item in context.symbols}

    normalize_actions: list[NormalizeAction] = []
    normalized_items: list[ChangePoint] = []

    for item in change_points:
        normalized, actions = _normalize_change_point(
            item,
            casefolded_files=casefolded_files,
        )

        normalized_items.append(normalized)
        normalize_actions.extend(actions)

    (
        deduplicated,
        deduplicate_actions,
    ) = _deduplicate_change_points(normalized_items)

    normalize_actions.extend(deduplicate_actions)

    invalid: list[InvalidFinding] = []

    for item in deduplicated:
        invalid.extend(
            _validate_change_point(
                item,
                files=files,
                symbols=symbol_map,
                active_ac_ids=context.active_ac_ids,
            )
        )

    expected = _expected_digests(candidate_set)

    consumed = consumed_digests or expected

    hard_failure_codes: list[str] = []

    if consumed != expected:
        invalid.append(
            InvalidFinding(
                finding_id="DIGESTS",
                error_type="digest_mismatch",
                detail=("分支消费的 set/spec/index digest 与冻结候选集不一致。"),
                repairable=False,
            )
        )

        hard_failure_codes.append("DIGEST_MISMATCH")

    if candidate_set_write_attempted:
        invalid.append(
            InvalidFinding(
                finding_id="CANDIDATE_SET",
                error_type=("candidate_set_write_attempt"),
                detail=("分支尝试修改已经冻结的 CandidateSet。"),
                repairable=False,
            )
        )

        hard_failure_codes.append("CANDIDATE_SET_WRITE_ATTEMPT")

    checked_count = len(deduplicated)

    invalid_ratio = round(
        len(invalid)
        / max(
            checked_count,
            1,
        ),
        3,
    )

    hard_failure_codes.extend(
        f"INVALID_{item.error_type.upper()}" for item in invalid if not item.repairable
    )

    repair_hints = tuple(item.detail for item in invalid if item.repairable)

    if not invalid:
        verdict = "pass"

        blocking_reasons: tuple[str, ...] = ()

    elif hard_failure_codes:
        verdict = "fail"

        blocking_reasons = _unique_strings(hard_failure_codes)

    elif invalid_ratio > 0.20:
        verdict = "fail"

        blocking_reasons = ("INVALID_RATIO_EXCEEDED",)

    elif repair_attempt >= 1:
        verdict = "fail"

        blocking_reasons = ("REPAIR_ATTEMPT_EXHAUSTED",)

    elif branch_kind == "engine":
        verdict = "fail"

        blocking_reasons = ("ENGINE_OUTPUT_INVALID",)

    else:
        verdict = "repair"
        blocking_reasons = ()

    report = GuardReport(
        branch=branch,
        branch_kind=branch_kind,
        checked_count=checked_count,
        normalize_actions=tuple(normalize_actions),
        invalid=tuple(invalid),
        invalid_ratio=invalid_ratio,
        repair_attempt=repair_attempt,
        verdict=verdict,
        blocking_reasons=blocking_reasons,
        repair_hints=(repair_hints if verdict == "repair" else ()),
        digests_echo=consumed,
        guard_version=GUARD_VERSION,
    )

    return GuardEvaluation(
        change_points=deduplicated,
        report=report,
    )
