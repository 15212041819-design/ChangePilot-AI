"""确定性 Test Strategy Engine 测试。"""

import pytest

from app.engines.test_strategy_engine import (
    EXISTING_TEST_RULE,
    MISSING_TEST_RULE,
    analyze_test_strategy,
)
from app.engines.test_strategy_engine import (
    TestStrategyEngineValidationError as EngineValidationError,
)
from app.schemas.contracts import (
    AcceptanceCriterion,
    Budget,
    CandidateSet,
    ChangePoint,
    EntityHint,
    EvidencePointer,
    FileRef,
    RequirementSpec,
    ScopeHints,
    SymbolRef,
)
from app.schemas.guard import GuardDigests
from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.tools.python_symbol_indexer import (
    index_python_symbols,
)

ZERO_HASH = "sha256:" + "0" * 64
ONE_HASH = "sha256:" + "1" * 64


def _repository(
    *,
    broken_source: bool = False,
    include_test: bool = True,
) -> RepositorySnapshot:
    """创建包含订单服务和可选测试文件的仓库。"""

    source = "def cancel_order(order_id: str) -> None:\n    pass\n"

    if broken_source:
        source = "def cancel_order(:\n"

    files = [
        RepositoryFile(
            relative_path="order_app/service.py",
            language="python",
            size_bytes=len(source.encode("utf-8")),
            content=source,
        )
    ]

    if include_test:
        test_content = (
            "from order_app.service "
            "import cancel_order\n\n"
            "def test_cancel_order() -> None:\n"
            "    cancel_order('1')\n"
        )

        files.append(
            RepositoryFile(
                relative_path=("tests/test_service.py"),
                language="python",
                size_bytes=len(test_content.encode("utf-8")),
                content=test_content,
            )
        )

    return RepositorySnapshot(
        repository_name="order_service",
        files=files,
        scanned_file_count=len(files),
        truncated=False,
    )


def _requirement() -> RequirementSpec:
    """创建带一条 P0 验收标准的需求契约。"""

    return RequirementSpec(
        spec_id="RS-0001",
        spec_revision=1,
        summary=("订单所属用户可以取消待支付订单。"),
        change_type="feature",
        acceptance_criteria=(
            AcceptanceCriterion(
                id="AC-001",
                description=("订单所属用户可以取消待支付订单。"),
                verification="test",
                priority="P0",
            ),
        ),
        entity_hints=(
            EntityHint(
                concept_id="EH-001",
                concept="取消订单",
                lexical_variants=("cancel order",),
                symbol_candidates=("cancel_order",),
                role="action_target",
                in_repo_vocab=True,
            ),
        ),
        scope_hints=ScopeHints(),
        source_text=("为订单系统增加取消订单功能。"),
        spec_digest=ZERO_HASH,
    )


def _change_point(
    *,
    path: str = "order_app/service.py",
    ac_id: str = "AC-001",
) -> ChangePoint:
    """创建已经由 Guard 通过的现有函数变更点。"""

    return ChangePoint(
        cp_id="CP-0001",
        path=path,
        target=SymbolRef(
            name="cancel_order",
            expect="existing",
            kind="function",
        ),
        change_type="semantic",
        ac_ids=(ac_id,),
        evidence=(
            EvidencePointer(
                path=path,
                line=1,
                kind="def",
            ),
        ),
        confidence=1.0,
        source="impact",
        rule_ids=("IMPACT-EXISTING-SYMBOL",),
    )


def _candidate_set(
    *,
    include_test: bool = True,
) -> CandidateSet:
    """创建冻结候选集；测试文件是否入池由参数控制。"""

    refs = [
        FileRef(
            path="order_app/service.py",
            content_sha256=ZERO_HASH,
            kind="source",
            reason="需求命中订单服务",
            score=1.0,
            confidence=0.9,
            selected_by="semantic_map",
        )
    ]

    if include_test:
        refs.append(
            FileRef(
                path="tests/test_service.py",
                content_sha256=ZERO_HASH,
                kind="test",
                reason="测试映射命中",
                score=0.8,
                confidence=0.9,
                selected_by="baseline",
            )
        )

    return CandidateSet(
        set_id="CS-0001",
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        files=tuple(refs),
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )


def _digests(
    *,
    set_digest: str = ZERO_HASH,
) -> GuardDigests:
    """创建分支声称消费的上游摘要。"""

    return GuardDigests(
        set_digest=set_digest,
        spec_digest=ZERO_HASH,
        index_digest=ZERO_HASH,
    )


def _resources(
    repository: RepositorySnapshot,
    *,
    change_points: (tuple[ChangePoint, ...] | None) = None,
) -> dict[str, object]:
    """创建 state 外共享资源，不提供 Impact BranchResult。"""

    return {
        "requirement_spec": _requirement(),
        "symbol_index": (index_python_symbols(repository)),
        "validated_change_points": ((_change_point(),) if change_points is None else change_points),
    }


def test_existing_candidate_test_is_mapped_by_real_import() -> None:
    """候选测试真实导入变更模块时应生成可追踪测试建议。"""

    result = analyze_test_strategy(
        _candidate_set(),
        _digests(),
        _resources(_repository()),
    )

    assert result.branch == "test"
    assert result.status == "success"

    assert result.coverage.assigned == 1
    assert result.coverage.analyzed == 1

    assert len(result.findings) == 1

    finding = result.findings[0]

    assert finding.finding_id == "TEST-0001"

    assert finding.path == "tests/test_service.py"

    assert finding.symbol == "cancel_order"

    assert finding.priority == "major"

    assert finding.ac_ids == ("AC-001",)

    assert finding.rule_ids == (EXISTING_TEST_RULE,)

    assert "IMPORT:tests/test_service.py:1" in finding.evidence_ids


def test_missing_test_is_reported_on_real_source_path() -> None:
    """没有候选测试时不得虚构测试路径。"""

    result = analyze_test_strategy(
        _candidate_set(include_test=False),
        _digests(),
        _resources(_repository(include_test=False)),
    )

    assert result.status == "success"

    assert len(result.findings) == 1

    finding = result.findings[0]

    assert finding.path == "order_app/service.py"

    assert finding.rule_ids == (MISSING_TEST_RULE,)

    assert "人工确认新增测试文件位置" in finding.summary

    assert not finding.path.startswith("tests/")


def test_test_file_outside_candidate_set_cannot_be_used() -> None:
    """全仓索引中的测试文件不能绕过 CandidateSet 冻结边界。"""

    result = analyze_test_strategy(
        _candidate_set(include_test=False),
        _digests(),
        _resources(_repository(include_test=True)),
    )

    assert result.findings[0].path == "order_app/service.py"

    assert result.findings[0].rule_ids == (MISSING_TEST_RULE,)


def test_engine_does_not_require_impact_branch_result() -> None:
    """Test 引擎只共享证据，不读取 Impact 分支结论。"""

    resources = _resources(_repository())

    assert "impact_result" not in resources

    result = analyze_test_strategy(
        _candidate_set(),
        _digests(),
        resources,
    )

    assert result.status == "success"

    assert result.findings[0].rule_ids == (EXISTING_TEST_RULE,)


def test_source_parse_error_degrades_visible_coverage() -> None:
    """源码解析失败必须降低覆盖率并公开原因。"""

    result = analyze_test_strategy(
        _candidate_set(),
        _digests(),
        _resources(_repository(broken_source=True)),
    )

    assert result.status == "failed"

    assert result.coverage.assigned == 1

    assert result.coverage.analyzed == 0

    assert result.coverage.ratio == 0.0

    assert result.coverage.missing[0].path == "order_app/service.py"

    assert "语法解析错误" in result.coverage.missing[0].reason


def test_digest_mismatch_is_rejected_before_analysis() -> None:
    """分支消费错误 CandidateSet 摘要时必须立即失败。"""

    with pytest.raises(
        EngineValidationError,
        match="set_digest",
    ):
        analyze_test_strategy(
            _candidate_set(),
            _digests(set_digest=ONE_HASH),
            _resources(_repository()),
        )


def test_change_point_outside_candidate_set_is_rejected() -> None:
    """已有文件变更点不能绕过冻结候选集进入测试分支。"""

    with pytest.raises(
        EngineValidationError,
        match="未进入 CandidateSet",
    ):
        analyze_test_strategy(
            _candidate_set(),
            _digests(),
            _resources(
                _repository(),
                change_points=(_change_point(path=("order_app/hidden.py")),),
            ),
        )


def test_change_point_cannot_reference_inactive_or_unknown_ac() -> None:
    """测试 finding 不能通过 ChangePoint 引用不存在的 AC。"""

    with pytest.raises(
        EngineValidationError,
        match="AC-999",
    ):
        analyze_test_strategy(
            _candidate_set(),
            _digests(),
            _resources(
                _repository(),
                change_points=(_change_point(ac_id="AC-999"),),
            ),
        )
