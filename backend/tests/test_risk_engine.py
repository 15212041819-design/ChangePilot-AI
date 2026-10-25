"""确定性 Risk Engine v1 测试。"""

from app.engines.risk_engine import (
    MISSING_CALLER_RULE,
    SCHEMA_CHANGE_RULE,
    analyze_risks,
)
from app.schemas.contracts import (
    Budget,
    CandidateSet,
    ChangePoint,
    EvidencePointer,
    FileRef,
    SymbolRef,
)
from app.schemas.dependency_graph import (
    ModuleDependency,
    PythonModuleNode,
    RepositoryDependencyGraph,
)
from app.schemas.guard import GuardDigests
from app.schemas.risk import (
    SchemaField,
    SchemaModel,
)

ZERO_HASH = "sha256:" + "0" * 64


def digests() -> GuardDigests:
    """创建统一上游摘要。"""

    return GuardDigests(
        set_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        index_digest=ZERO_HASH,
    )


def candidate_set(
    *,
    include_api: bool = False,
) -> CandidateSet:
    """创建风险分析使用的候选集。"""

    files = [
        FileRef(
            path="order_app/models.py",
            content_sha256=ZERO_HASH,
            kind="source",
            reason="命中订单模型",
            score=1.0,
            confidence=0.9,
            selected_by="semantic_map",
        ),
        FileRef(
            path="order_app/service.py",
            content_sha256=ZERO_HASH,
            kind="source",
            reason="命中订单服务",
            score=1.0,
            confidence=0.9,
            selected_by="semantic_map",
        ),
    ]

    if include_api:
        files.append(
            FileRef(
                path="order_app/api.py",
                content_sha256=ZERO_HASH,
                kind="source",
                reason="依赖订单服务",
                score=0.8,
                confidence=0.8,
                selected_by="semantic_map",
            )
        )

    return CandidateSet(
        set_id="CS-0001",
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        files=tuple(files),
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )


def dependency_graph(
    *,
    parse_error_path: str | None = None,
) -> RepositoryDependencyGraph:
    """创建包含 api -> service -> models 的依赖图。"""

    modules = [
        PythonModuleNode(
            module_name="order_app.models",
            file_path="order_app/models.py",
            has_parse_error=(parse_error_path == "order_app/models.py"),
        ),
        PythonModuleNode(
            module_name="order_app.service",
            file_path="order_app/service.py",
            has_parse_error=(parse_error_path == "order_app/service.py"),
        ),
        PythonModuleNode(
            module_name="order_app.api",
            file_path="order_app/api.py",
            has_parse_error=(parse_error_path == "order_app/api.py"),
        ),
    ]

    dependencies = [
        ModuleDependency(
            source_module="order_app.service",
            source_file="order_app/service.py",
            target_module="order_app.models",
            target_file="order_app/models.py",
            imported_name="Order",
            kind="internal",
            line_number=3,
        ),
        ModuleDependency(
            source_module="order_app.api",
            source_file="order_app/api.py",
            target_module="order_app.service",
            target_file="order_app/service.py",
            imported_name="OrderService",
            kind="internal",
            line_number=5,
        ),
    ]

    return RepositoryDependencyGraph(
        repository_name="order_service",
        modules=modules,
        dependencies=dependencies,
        internal_dependency_count=2,
        external_dependency_count=0,
        unresolved_internal_dependency_count=0,
    )


def service_change_point() -> ChangePoint:
    """创建订单服务语义变更点。"""

    return ChangePoint(
        cp_id="CP-0001",
        path="order_app/service.py",
        target=SymbolRef(
            name="cancel_order",
            parent_symbol="order_app.service.OrderService",
            expect="existing",
            kind="method",
        ),
        change_type="semantic",
        ac_ids=("AC-001",),
        evidence=(
            EvidencePointer(
                path="order_app/service.py",
                line=20,
                kind="def",
            ),
        ),
        confidence=0.9,
        source="impact",
    )


def schema_change_point() -> ChangePoint:
    """创建删除 Pydantic 模型字段的变更点。"""

    return ChangePoint(
        cp_id="CP-0002",
        path="order_app/models.py",
        target=SymbolRef(
            name="legacy_code",
            parent_symbol="order_app.models.Order",
            expect="existing",
            kind="attribute",
        ),
        change_type="delete",
        ac_ids=("AC-002",),
        evidence=(
            EvidencePointer(
                path="order_app/models.py",
                line=12,
                kind="schema",
            ),
        ),
        confidence=0.95,
        source="impact",
    )


def schema_models() -> tuple[SchemaModel, ...]:
    """创建订单 Pydantic 模型索引事实。"""

    return (
        SchemaModel(
            qualified_name="order_app.models.Order",
            path="order_app/models.py",
            kind="pydantic",
            line=6,
            fields=(
                SchemaField(
                    name="legacy_code",
                    line=12,
                    type_annotation="str | None",
                    required=False,
                ),
            ),
        ),
    )


def resources(
    *,
    changes: tuple[ChangePoint, ...],
    include_schema: bool = True,
    parse_error_path: str | None = None,
) -> dict[str, object]:
    """创建 Risk Engine 运行期资源容器。"""

    return {
        "dependency_graph": dependency_graph(parse_error_path=parse_error_path),
        "validated_change_points": changes,
        "schema_models": (schema_models() if include_schema else ()),
    }


def test_missing_caller_outside_candidate_set_is_reported() -> None:
    """依赖图存在但候选集遗漏的调用方必须成为 major finding。"""

    result = analyze_risks(
        candidate_set(),
        digests(),
        resources(
            changes=(service_change_point(),),
            include_schema=False,
        ),
    )

    assert result.status == "success"
    assert len(result.findings) == 1

    risk = result.findings[0]

    assert risk.priority == "major"
    assert risk.path == "order_app/api.py"
    assert risk.rule_ids == (MISSING_CALLER_RULE,)
    assert risk.ac_ids == ("AC-001",)


def test_caller_already_in_candidate_set_is_not_reported() -> None:
    """已经进入候选集的调用方不能重复报告为 missing caller。"""

    result = analyze_risks(
        candidate_set(include_api=True),
        digests(),
        resources(
            changes=(service_change_point(),),
            include_schema=False,
        ),
    )

    assert result.status == "success"
    assert result.findings == ()


def test_schema_field_delete_is_reported_as_major() -> None:
    """删除真实 schema 字段必须提示数据兼容风险。"""

    result = analyze_risks(
        candidate_set(),
        digests(),
        resources(
            changes=(schema_change_point(),),
        ),
    )

    schema_risks = tuple(
        finding for finding in result.findings if SCHEMA_CHANGE_RULE in finding.rule_ids
    )

    assert len(schema_risks) == 1
    assert schema_risks[0].priority == "major"
    assert schema_risks[0].path == "order_app/models.py"
    assert schema_risks[0].symbol == "legacy_code"
    assert schema_risks[0].ac_ids == ("AC-002",)


def test_schema_field_add_is_minor_not_blocker() -> None:
    """新增 schema 字段提示风险，但不能自动升级为 blocker。"""

    change = schema_change_point().model_copy(
        update={
            "cp_id": "CP-0003",
            "target": SymbolRef(
                name="cancel_reason",
                parent_symbol="order_app.models.Order",
                expect="new",
                kind="attribute",
            ),
            "change_type": "add",
        }
    )

    result = analyze_risks(
        candidate_set(),
        digests(),
        resources(
            changes=(change,),
        ),
    )

    schema_risk = next(
        finding for finding in result.findings if SCHEMA_CHANGE_RULE in finding.rule_ids
    )

    assert schema_risk.priority == "minor"
    assert schema_risk.priority != "blocker"


def test_parse_error_degrades_coverage_instead_of_claiming_no_risk() -> None:
    """解析失败必须公开降低 coverage，不能静默显示无风险。"""

    result = analyze_risks(
        candidate_set(),
        digests(),
        resources(
            changes=(service_change_point(),),
            include_schema=False,
            parse_error_path="order_app/models.py",
        ),
    )

    assert result.status == "degraded"
    assert result.coverage.assigned == 2
    assert result.coverage.analyzed == 1
    assert result.coverage.ratio == 0.5
    assert result.coverage.missing[0].path == ("order_app/models.py")
    assert result.degraded_reasons


def test_risk_engine_preserves_consumed_digests() -> None:
    """Risk Engine 必须原样回传并行阶段提供的 digest。"""

    expected = digests()

    result = analyze_risks(
        candidate_set(),
        expected,
        resources(
            changes=(),
            include_schema=False,
        ),
    )

    assert result.consumed_digests == expected
