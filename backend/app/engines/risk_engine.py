"""基于真实依赖图和 schema 索引的确定性风险引擎。

v1 只输出两类可靠风险：

1. missing_caller：变更文件存在真实上游依赖方，但候选集遗漏该调用方；
2. schema_change：已验证变更点触及 dataclass、Pydantic、TypedDict 或 attrs 字段。

本模块禁止调用 LLM，也不输出并发、事务等无法由当前索引证明的推测性风险。
"""

from collections.abc import Mapping, Sequence

from app.engines.branch_result import (
    build_candidate_set_branch_result,
    source_file_assignments,
)
from app.schemas.branch_result import (
    BranchFinding,
    BranchResult,
)
from app.schemas.contracts import (
    CandidateSet,
    ChangePoint,
)
from app.schemas.dependency_graph import (
    RepositoryDependencyGraph,
)
from app.schemas.guard import GuardDigests
from app.schemas.risk import SchemaModel

MISSING_CALLER_RULE = "RISK-MISSING-CALLER"
SCHEMA_CHANGE_RULE = "RISK-SCHEMA-CHANGE"


def _load_dependency_graph(
    resources: Mapping[str, object],
) -> RepositoryDependencyGraph:
    """从资源容器读取并校验依赖图。"""

    raw_graph = resources.get("dependency_graph")

    if raw_graph is None:
        raise KeyError("Risk Engine 缺少 dependency_graph 资源。")

    return RepositoryDependencyGraph.model_validate(raw_graph)


def _load_change_points(
    resources: Mapping[str, object],
) -> tuple[ChangePoint, ...]:
    """读取已经通过 Guard 的 ChangePoint。"""

    raw_items = resources.get("validated_change_points")

    if raw_items is None:
        raise KeyError("Risk Engine 缺少 validated_change_points 资源。")

    if not isinstance(
        raw_items,
        Sequence,
    ) or isinstance(
        raw_items,
        (str, bytes),
    ):
        raise TypeError("validated_change_points 必须是序列。")

    return tuple(ChangePoint.model_validate(item) for item in raw_items)


def _load_schema_models(
    resources: Mapping[str, object],
) -> tuple[SchemaModel, ...]:
    """读取确定性 schema 索引；没有 schema 时允许为空。"""

    raw_items = resources.get(
        "schema_models",
        (),
    )

    if not isinstance(
        raw_items,
        Sequence,
    ) or isinstance(
        raw_items,
        (str, bytes),
    ):
        raise TypeError("schema_models 必须是序列。")

    return tuple(SchemaModel.model_validate(item) for item in raw_items)


def _ac_ids_for_path(
    change_points: tuple[ChangePoint, ...],
    path: str,
) -> tuple[str, ...]:
    """汇总一个变更文件关联的 active AC ID。"""

    return tuple(
        sorted(
            {
                ac_id
                for change_point in change_points
                if change_point.path == path
                for ac_id in change_point.ac_ids
            }
        )
    )


def _missing_caller_findings(
    *,
    candidate_set: CandidateSet,
    graph: RepositoryDependencyGraph,
    change_points: tuple[ChangePoint, ...],
) -> tuple[BranchFinding, ...]:
    """查找真实依赖图中被候选集遗漏的上游调用方。"""

    candidate_paths = set(source_file_assignments(candidate_set))

    changed_paths = {change_point.path for change_point in change_points}

    # 这里只使用依赖图中已经确认的内部依赖关系。
    # 不根据文件名或关键词猜测调用关系。
    missing_edges = sorted(
        {
            (
                dependency.source_file,
                dependency.target_file,
                dependency.imported_name,
                dependency.line_number,
            )
            for dependency in graph.dependencies
            if dependency.kind == "internal"
            and dependency.target_file in changed_paths
            and dependency.source_file not in candidate_paths
        },
        key=lambda item: (
            item[0],
            item[1] or "",
            item[2] or "",
            item[3],
        ),
    )

    findings: list[BranchFinding] = []

    for index, (
        source_file,
        target_file,
        imported_name,
        line_number,
    ) in enumerate(
        missing_edges,
        start=1,
    ):
        if target_file is None:
            continue

        findings.append(
            BranchFinding(
                finding_id=f"RISK-MC-{index:04d}",
                priority="major",
                path=source_file,
                symbol=imported_name,
                summary=(
                    f"{source_file} 在第 {line_number} 行依赖"
                    f"变更文件 {target_file}，但未进入冻结候选集。"
                ),
                ac_ids=_ac_ids_for_path(
                    change_points,
                    target_file,
                ),
                evidence_ids=(f"IMPORT:{source_file}:{line_number}",),
                rule_ids=(MISSING_CALLER_RULE,),
            )
        )

    return tuple(findings)


def _schema_matches_change_point(
    schema: SchemaModel,
    change_point: ChangePoint,
) -> bool:
    """判断 ChangePoint 是否触及指定 schema 类或字段。"""

    if change_point.path != schema.path:
        return False

    target = change_point.target

    # 整个模型类发生变更。
    if target.kind == "class":
        return target.name in {
            schema.qualified_name,
            schema.qualified_name.rsplit(
                ".",
                maxsplit=1,
            )[-1],
        }

    # schema 字段以 attribute 形式表达。
    if target.kind != "attribute":
        return False

    return target.parent_symbol == schema.qualified_name


def _schema_change_findings(
    *,
    change_points: tuple[ChangePoint, ...],
    schema_models: tuple[SchemaModel, ...],
) -> tuple[BranchFinding, ...]:
    """根据已验证 ChangePoint 识别数据模型结构变化。"""

    matches = sorted(
        (
            (schema, change_point)
            for schema in schema_models
            for change_point in change_points
            if _schema_matches_change_point(
                schema,
                change_point,
            )
        ),
        key=lambda item: (
            item[0].path,
            item[0].qualified_name,
            item[1].target.name or "",
            item[1].cp_id,
        ),
    )

    findings: list[BranchFinding] = []

    for index, (
        schema,
        change_point,
    ) in enumerate(
        matches,
        start=1,
    ):
        target_name = change_point.target.name or schema.qualified_name

        # 新增字段通常不会直接破坏旧数据读取，因此先标记 minor。
        # 删除、重命名和语义修改可能破坏现有调用，标记为 major。
        priority = "minor" if change_point.change_type == "add" else "major"

        findings.append(
            BranchFinding(
                finding_id=f"RISK-SC-{index:04d}",
                priority=priority,
                path=schema.path,
                symbol=target_name,
                summary=(
                    f"{schema.kind} 数据模型 "
                    f"{schema.qualified_name} 的 {target_name} "
                    f"将发生 {change_point.change_type} 变更；"
                    "需要检查序列化、反序列化和旧数据兼容性。"
                ),
                ac_ids=tuple(sorted(change_point.ac_ids)),
                evidence_ids=tuple(
                    f"{evidence.kind.upper()}:{evidence.path}:{evidence.line}"
                    for evidence in change_point.evidence
                ),
                rule_ids=(SCHEMA_CHANGE_RULE,),
            )
        )

    return tuple(findings)


def analyze_risks(
    candidate_set: CandidateSet,
    digests: GuardDigests,
    resources: Mapping[str, object],
) -> BranchResult:
    """执行 v1 确定性风险分析并返回统一 BranchResult。"""

    dependency_graph = _load_dependency_graph(resources)
    change_points = _load_change_points(resources)
    schema_models = _load_schema_models(resources)

    source_paths = source_file_assignments(candidate_set)

    # 有语法错误的文件不能被视为已经完成分析。
    parse_error_paths = {
        module.file_path for module in dependency_graph.modules if module.has_parse_error
    }

    analyzed_paths = tuple(path for path in source_paths if path not in parse_error_paths)

    missing_reasons = {
        path: "文件存在语法解析错误，Risk Engine 无法完成确定性扫描。"
        for path in source_paths
        if path in parse_error_paths
    }

    findings = (
        *_missing_caller_findings(
            candidate_set=candidate_set,
            graph=dependency_graph,
            change_points=change_points,
        ),
        *_schema_change_findings(
            change_points=change_points,
            schema_models=schema_models,
        ),
    )

    # 使用统一工厂生成 BranchResult，
    # 保证 coverage、digest 和稳定排序规则与其他并行分支一致。
    return build_candidate_set_branch_result(
        branch="risk",
        candidate_set=candidate_set,
        analyzed_paths=analyzed_paths,
        findings=findings,
        consumed_digests=digests,
        missing_reasons=missing_reasons,
    )
