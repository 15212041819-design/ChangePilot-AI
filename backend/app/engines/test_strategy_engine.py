"""基于冻结候选集和已验证变更点的确定性测试策略引擎。

本模块不调用 LLM，也不读取 Impact ``BranchResult``。它只消费三类共享证据：

1. ``RequirementSpec``：提供 active AC 及其优先级；
2. ``CandidateSet``：限定允许分析的源码文件与测试文件；
3. 已通过 Guard 的 ``ChangePoint``：提供需要被测试覆盖的变更目标。

测试文件只会从 CandidateSet 中选择。找不到真实测试文件时，引擎把缺口挂在
真实源码路径上，要求人工确定新测试文件位置，绝不猜测 ``test_xxx.py`` 路径。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.engines.branch_result import (
    build_candidate_set_branch_result,
    source_file_assignments,
)
from app.schemas.branch_result import BranchFinding, BranchResult
from app.schemas.code_index import PythonFileIndex, RepositorySymbolIndex
from app.schemas.contracts import CandidateSet, ChangePoint, RequirementSpec
from app.schemas.guard import GuardDigests

EXISTING_TEST_RULE = "TEST-EXISTING-FILE"
MISSING_TEST_RULE = "TEST-MISSING-FILE"


class TestStrategyEngineValidationError(ValueError):
    """测试策略输入违反冻结上下文或引用约束。"""


@dataclass(frozen=True, slots=True)
class _FindingDraft:
    """分配稳定 finding_id 前使用的内部不可变结构。"""

    priority: str
    path: str
    symbol: str | None
    summary: str
    ac_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    rule_id: str


def _load_requirement(
    resources: Mapping[str, object],
) -> RequirementSpec:
    """从 state 外资源容器读取需求契约。"""

    raw_spec = resources.get("requirement_spec")

    if raw_spec is None:
        raise KeyError("Test Strategy Engine 缺少 requirement_spec 资源。")

    return RequirementSpec.model_validate(raw_spec)


def _load_symbol_index(
    resources: Mapping[str, object],
) -> RepositorySymbolIndex:
    """从 state 外资源容器读取 Python 符号索引。"""

    raw_index = resources.get("symbol_index")

    if raw_index is None:
        raise KeyError("Test Strategy Engine 缺少 symbol_index 资源。")

    return RepositorySymbolIndex.model_validate(raw_index)


def _load_change_points(
    resources: Mapping[str, object],
) -> tuple[ChangePoint, ...]:
    """读取已通过 Analysis Guard 的变更点。"""

    raw_items = resources.get("validated_change_points")

    if raw_items is None:
        raise KeyError("Test Strategy Engine 缺少 validated_change_points 资源。")

    if not isinstance(raw_items, Sequence) or isinstance(
        raw_items,
        (str, bytes),
    ):
        raise TypeError("validated_change_points 必须是列表或元组。")

    return tuple(ChangePoint.model_validate(item) for item in raw_items)


def _validate_digests(
    *,
    candidate_set: CandidateSet,
    requirement: RequirementSpec,
    digests: GuardDigests,
) -> None:
    """确认 Test 分支消费的是同一份冻结上下文。"""

    mismatches: list[str] = []

    if digests.set_digest != candidate_set.set_digest:
        mismatches.append("set_digest")

    if digests.spec_digest != candidate_set.spec_digest:
        mismatches.append("spec_digest")

    if digests.index_digest != candidate_set.index_digest:
        mismatches.append("index_digest")

    if requirement.spec_digest != candidate_set.spec_digest:
        mismatches.append("requirement_spec.spec_digest")

    if mismatches:
        raise TestStrategyEngineValidationError(
            "Test Strategy Engine digest 不一致：" + ", ".join(sorted(mismatches)) + "。"
        )


def _active_ac_priorities(
    requirement: RequirementSpec,
) -> dict[str, str]:
    """返回 active AC 的稳定优先级映射。"""

    return {
        criterion.id: criterion.priority
        for criterion in requirement.acceptance_criteria
        if criterion.status == "active"
    }


def _validate_change_points(
    *,
    candidate_set: CandidateSet,
    change_points: tuple[ChangePoint, ...],
    active_ac_ids: set[str],
) -> None:
    """拒绝绕过 CandidateSet 或引用非 active AC 的变更点。"""

    candidate_source_paths = set(source_file_assignments(candidate_set))

    unknown_ac_ids = sorted(
        {
            ac_id
            for change_point in change_points
            for ac_id in change_point.ac_ids
            if ac_id not in active_ac_ids
        }
    )

    if unknown_ac_ids:
        raise TestStrategyEngineValidationError(
            "ChangePoint 引用了不存在或非 active 的 AC：" + ", ".join(unknown_ac_ids) + "。"
        )

    invalid_paths = sorted(
        {
            change_point.path
            for change_point in change_points
            if not change_point.declares_new_file
            and change_point.path not in candidate_source_paths
        }
    )

    if invalid_paths:
        raise TestStrategyEngineValidationError(
            "已有文件 ChangePoint 未进入 CandidateSet：" + ", ".join(invalid_paths) + "。"
        )


def _indexed_files_by_path(
    symbol_index: RepositorySymbolIndex,
) -> dict[str, PythonFileIndex]:
    """按规范化路径建立索引查询表。"""

    return {
        indexed_file.file_path.replace("\\", "/"): indexed_file
        for indexed_file in symbol_index.files
    }


def _candidate_test_paths(
    candidate_set: CandidateSet,
) -> tuple[str, ...]:
    """只返回冻结候选集中已经真实存在的测试文件。"""

    return tuple(
        sorted(
            {
                file_ref.path.replace("\\", "/")
                for file_ref in candidate_set.files
                if file_ref.kind == "test"
            }
        )
    )


def _path_stem(path: str) -> str:
    """提取文件名并去掉 Python 扩展名。"""

    filename = path.replace("\\", "/").rsplit("/", maxsplit=1)[-1]

    if filename.endswith(".py"):
        return filename[:-3]

    return filename


def _normalized_test_stem(path: str) -> str:
    """把 test_service 或 service_test 归一化为 service。"""

    stem = _path_stem(path)

    if stem.startswith("test_"):
        stem = stem[5:]

    if stem.endswith("_test"):
        stem = stem[:-5]

    return stem


def _import_candidates(
    indexed_test: PythonFileIndex,
) -> tuple[tuple[str, int], ...]:
    """展开测试文件导入中可以与源码模块比较的限定名。"""

    candidates: set[tuple[str, int]] = set()

    for imported in indexed_test.imports:
        module = imported.module.strip(".")

        if module:
            candidates.add(
                (
                    module,
                    imported.line_number,
                )
            )

        if module and imported.imported_name:
            candidates.add(
                (
                    f"{module}.{imported.imported_name}",
                    imported.line_number,
                )
            )

    return tuple(sorted(candidates))


def _matching_import_lines(
    indexed_test: PythonFileIndex,
    source_module: str,
) -> tuple[int, ...]:
    """返回测试文件直接导入源码模块的真实行号。"""

    return tuple(
        sorted(
            {
                line_number
                for imported_name, line_number in _import_candidates(indexed_test)
                if imported_name == source_module or imported_name.startswith(f"{source_module}.")
            }
        )
    )


def _mapped_tests_for_source(
    *,
    source_index: PythonFileIndex,
    candidate_test_paths: tuple[str, ...],
    indexed_by_path: Mapping[str, PythonFileIndex],
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """用真实 import 证据和稳定文件名规则映射源码到测试文件。"""

    source_stem = _path_stem(source_index.file_path)

    matches: list[
        tuple[
            str,
            tuple[int, ...],
        ]
    ] = []

    for test_path in candidate_test_paths:
        indexed_test = indexed_by_path.get(test_path)

        if indexed_test is None or indexed_test.parse_error is not None:
            continue

        import_lines = _matching_import_lines(
            indexed_test,
            source_index.module_name,
        )

        stem_matches = _normalized_test_stem(test_path) == source_stem

        if import_lines or stem_matches:
            matches.append(
                (
                    test_path,
                    import_lines,
                )
            )

    return tuple(sorted(matches))


def _change_points_by_path(
    change_points: tuple[ChangePoint, ...],
) -> dict[str, tuple[ChangePoint, ...]]:
    """按路径稳定分组已验证变更点。"""

    grouped: dict[
        str,
        list[ChangePoint],
    ] = {}

    for change_point in sorted(
        change_points,
        key=lambda item: item.cp_id,
    ):
        grouped.setdefault(
            change_point.path,
            [],
        ).append(change_point)

    return {path: tuple(items) for path, items in grouped.items()}


def _priority_for_ac_ids(
    ac_ids: tuple[str, ...],
    priorities: Mapping[str, str],
) -> str:
    """把 AC 优先级稳定映射为统一 finding 优先级。"""

    values = {priorities[ac_id] for ac_id in ac_ids}

    if "P0" in values:
        return "major"

    if "P1" in values:
        return "minor"

    return "info"


def _cp_evidence_ids(
    change_points: tuple[ChangePoint, ...],
) -> tuple[str, ...]:
    """把变更点和源码证据转换为报告可追踪 ID。"""

    evidence_ids = {f"CHANGE_POINT:{change_point.cp_id}" for change_point in change_points}

    evidence_ids.update(
        (f"{evidence.kind.upper()}:{evidence.path}:{evidence.line}")
        for change_point in change_points
        for evidence in change_point.evidence
    )

    return tuple(sorted(evidence_ids))


def _target_symbol(
    change_points: tuple[ChangePoint, ...],
) -> str | None:
    """当一组变更点只有一个目标符号时返回该符号。"""

    names = {
        change_point.target.name
        for change_point in change_points
        if change_point.target.name is not None
    }

    if len(names) == 1:
        return next(iter(names))

    return None


def _drafts_for_existing_source(
    *,
    source_path: str,
    source_index: PythonFileIndex,
    change_points: tuple[ChangePoint, ...],
    candidate_test_paths: tuple[str, ...],
    indexed_by_path: Mapping[str, PythonFileIndex],
    ac_priorities: Mapping[str, str],
) -> tuple[_FindingDraft, ...]:
    """为一个已有源码文件生成现有测试映射或明确测试缺口。"""

    if not change_points:
        return ()

    ac_ids = tuple(
        sorted({ac_id for change_point in change_points for ac_id in change_point.ac_ids})
    )

    priority = _priority_for_ac_ids(
        ac_ids,
        ac_priorities,
    )

    symbol = _target_symbol(change_points)

    cp_evidence = _cp_evidence_ids(change_points)

    mapped_tests = _mapped_tests_for_source(
        source_index=source_index,
        candidate_test_paths=candidate_test_paths,
        indexed_by_path=indexed_by_path,
    )

    if not mapped_tests:
        return (
            _FindingDraft(
                priority=priority,
                path=source_path,
                symbol=symbol,
                summary=(
                    "冻结候选集中没有找到可核验的测试文件覆盖 "
                    f"{source_path}；"
                    "需要人工确认新增测试文件位置并补充对应验收场景。"
                ),
                ac_ids=ac_ids,
                evidence_ids=cp_evidence,
                rule_id=MISSING_TEST_RULE,
            ),
        )

    drafts: list[_FindingDraft] = []

    for test_path, import_lines in mapped_tests:
        import_evidence = tuple(f"IMPORT:{test_path}:{line}" for line in import_lines)

        drafts.append(
            _FindingDraft(
                priority=priority,
                path=test_path,
                symbol=symbol,
                summary=(
                    f"扩展现有测试文件 {test_path}，"
                    f"覆盖 {source_path} "
                    "关联的验收标准 "
                    f"{', '.join(ac_ids)}。"
                ),
                ac_ids=ac_ids,
                evidence_ids=tuple(
                    sorted(
                        {
                            *cp_evidence,
                            *import_evidence,
                        }
                    )
                ),
                rule_id=EXISTING_TEST_RULE,
            )
        )

    return tuple(drafts)


def _drafts_for_declared_new_files(
    *,
    change_points: tuple[ChangePoint, ...],
    existing_source_paths: set[str],
    ac_priorities: Mapping[str, str],
) -> tuple[_FindingDraft, ...]:
    """为 Guard 已验证的新文件目标生成不虚构测试路径的测试缺口。"""

    grouped = _change_points_by_path(
        tuple(
            change_point
            for change_point in change_points
            if change_point.declares_new_file and change_point.path not in existing_source_paths
        )
    )

    drafts: list[_FindingDraft] = []

    for path in sorted(grouped):
        items = grouped[path]

        ac_ids = tuple(sorted({ac_id for item in items for ac_id in item.ac_ids}))

        drafts.append(
            _FindingDraft(
                priority=_priority_for_ac_ids(
                    ac_ids,
                    ac_priorities,
                ),
                path=path,
                symbol=_target_symbol(items),
                summary=(
                    f"{path} 是已通过 Guard 的计划新增源码文件；"
                    "当前不存在可核验测试路径，"
                    "需要在实施阶段明确测试文件位置。"
                ),
                ac_ids=ac_ids,
                evidence_ids=_cp_evidence_ids(items),
                rule_id=MISSING_TEST_RULE,
            )
        )

    return tuple(drafts)


def _materialize_findings(
    drafts: Sequence[_FindingDraft],
) -> tuple[BranchFinding, ...]:
    """稳定排序草稿后分配可复现的 TEST finding ID。"""

    ordered = sorted(
        drafts,
        key=lambda item: (
            item.rule_id,
            item.path,
            item.symbol or "",
            item.ac_ids,
            item.summary,
        ),
    )

    return tuple(
        BranchFinding(
            finding_id=f"TEST-{index:04d}",
            priority=draft.priority,
            path=draft.path,
            symbol=draft.symbol,
            summary=draft.summary,
            ac_ids=draft.ac_ids,
            evidence_ids=draft.evidence_ids,
            rule_ids=(draft.rule_id,),
        )
        for index, draft in enumerate(
            ordered,
            start=1,
        )
    )


def analyze_test_strategy(
    candidate_set: CandidateSet,
    digests: GuardDigests,
    resources: Mapping[str, object],
) -> BranchResult:
    """执行确定性测试映射并返回统一 BranchResult。"""

    requirement = _load_requirement(resources)

    symbol_index = _load_symbol_index(resources)

    change_points = _load_change_points(resources)

    _validate_digests(
        candidate_set=candidate_set,
        requirement=requirement,
        digests=digests,
    )

    ac_priorities = _active_ac_priorities(requirement)

    _validate_change_points(
        candidate_set=candidate_set,
        change_points=change_points,
        active_ac_ids=set(ac_priorities),
    )

    indexed_by_path = _indexed_files_by_path(symbol_index)

    source_paths = source_file_assignments(candidate_set)

    candidate_test_paths = _candidate_test_paths(candidate_set)

    grouped_change_points = _change_points_by_path(change_points)

    analyzed_paths: list[str] = []

    missing_reasons: dict[str, str] = {}

    drafts: list[_FindingDraft] = []

    for source_path in source_paths:
        source_index = indexed_by_path.get(source_path)

        if source_index is None:
            missing_reasons[source_path] = (
                "源码文件未进入 Python SymbolIndex，Test Strategy Engine 无法完成测试映射。"
            )
            continue

        if source_index.parse_error is not None:
            missing_reasons[source_path] = (
                "源码文件存在 Python 语法解析错误，Test Strategy Engine 无法完成测试映射。"
            )
            continue

        analyzed_paths.append(source_path)

        drafts.extend(
            _drafts_for_existing_source(
                source_path=source_path,
                source_index=source_index,
                change_points=(
                    grouped_change_points.get(
                        source_path,
                        (),
                    )
                ),
                candidate_test_paths=(candidate_test_paths),
                indexed_by_path=indexed_by_path,
                ac_priorities=ac_priorities,
            )
        )

    drafts.extend(
        _drafts_for_declared_new_files(
            change_points=change_points,
            existing_source_paths=set(source_paths),
            ac_priorities=ac_priorities,
        )
    )

    return build_candidate_set_branch_result(
        branch="test",
        candidate_set=candidate_set,
        analyzed_paths=analyzed_paths,
        findings=_materialize_findings(drafts),
        consumed_digests=digests,
        missing_reasons=missing_reasons,
    )
