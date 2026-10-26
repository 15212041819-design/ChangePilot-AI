"""确定性 Impact Engine 测试。"""

import pytest

from app.engines.impact_engine import (
    EXISTING_SYMBOL_RULE,
    NEW_SYMBOL_RULE,
    ImpactEngineValidationError,
    analyze_impact,
    analyze_impact_evidence,
)
from app.schemas.contracts import (
    AcceptanceCriterion,
    Budget,
    CandidateSet,
    EntityHint,
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


def _file(
    path: str,
    content: str,
) -> RepositoryFile:
    """创建测试仓库文件。"""

    return RepositoryFile(
        relative_path=path,
        language="python",
        size_bytes=len(content.encode("utf-8")),
        content=content,
    )


def _repository(
    *,
    include_cancel_order: bool = True,
    include_broken_file: bool = False,
) -> RepositorySnapshot:
    """创建订单服务、API 调用方和可选语法错误文件。"""

    service_method = (
        "\n    def cancel_order(self, order_id: str) -> None:\n        pass\n"
        if include_cancel_order
        else ("\n    def create_order(self) -> None:\n        pass\n")
    )

    files = [
        _file(
            "order_app/service.py",
            ("class OrderService:\n" + service_method),
        ),
        _file(
            "order_app/api.py",
            (
                "from order_app.service "
                "import OrderService\n\n"
                "def cancel_endpoint() -> None:\n"
                "    OrderService()"
                ".cancel_order('1')\n"
            ),
        ),
    ]

    if include_broken_file:
        files.append(
            _file(
                "order_app/broken.py",
                "def broken(:\n",
            )
        )

    return RepositorySnapshot(
        repository_name="order_service",
        files=files,
        scanned_file_count=len(files),
        truncated=False,
    )


def _requirement(
    *,
    symbol_hints: tuple[
        SymbolRef,
        ...,
    ] = (),
    symbol_candidate: str = "cancel_order",
) -> RequirementSpec:
    """创建 Impact Engine 使用的需求契约。"""

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
                symbol_candidates=(symbol_candidate,),
                role="action_target",
                in_repo_vocab=True,
            ),
        ),
        symbol_hints=symbol_hints,
        scope_hints=ScopeHints(),
        source_text=("为订单系统增加取消订单功能。"),
        spec_digest=ZERO_HASH,
    )


def _candidate_set(
    *paths: str,
) -> CandidateSet:
    """创建只允许 Impact Engine 读取指定路径的候选集。"""

    return CandidateSet(
        set_id="CS-0001",
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        files=tuple(
            FileRef(
                path=path,
                content_sha256=ZERO_HASH,
                kind="source",
                reason="测试候选文件",
                score=1.0,
                confidence=0.9,
                selected_by="semantic_map",
            )
            for path in paths
        ),
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )


def _digests(
    *,
    set_digest: str = ZERO_HASH,
) -> GuardDigests:
    """创建分支消费的上游摘要。"""

    return GuardDigests(
        set_digest=set_digest,
        spec_digest=ZERO_HASH,
        index_digest=ZERO_HASH,
    )


def _resources(
    repository: RepositorySnapshot,
    requirement: RequirementSpec,
) -> dict[str, object]:
    """创建 Impact Engine 的 state 外资源容器。"""

    return {
        "requirement_spec": requirement,
        "repository_snapshot": repository,
        "symbol_index": (index_python_symbols(repository)),
    }


def test_existing_symbol_becomes_guarded_change_point_and_finding() -> None:
    """真实符号命中应生成通过 Guard 的变更点和 Impact finding。"""

    repository = _repository()

    result = analyze_impact_evidence(
        _candidate_set("order_app/service.py"),
        _digests(),
        _resources(
            repository,
            _requirement(),
        ),
    )

    assert result.guard_report.verdict == "pass"

    assert result.branch_result.status == "success"

    assert result.branch_result.coverage.ratio == 1.0

    assert len(result.change_points) == 1

    change_point = result.change_points[0]

    assert change_point.cp_id == "CP-0001"

    assert change_point.path == "order_app/service.py"

    assert change_point.target.name == "cancel_order"

    assert change_point.target.expect == "existing"

    assert change_point.change_type == "semantic"

    assert change_point.ac_ids == ("AC-001",)

    assert change_point.rule_ids == (EXISTING_SYMBOL_RULE,)

    finding = result.branch_result.findings[0]

    assert finding.finding_id == "IMP-0001"

    assert finding.priority == "major"

    assert finding.path == "order_app/service.py"


def test_explicit_new_member_uses_real_parent_symbol() -> None:
    """新增成员必须依附真实父类，不能猜测目标文件。"""

    repository = _repository(
        include_cancel_order=False,
    )

    requirement = _requirement(
        symbol_hints=(
            SymbolRef(
                name="cancel_order",
                parent_symbol=("order_app.service.OrderService"),
                expect="new",
                kind="method",
            ),
        ),
    )

    result = analyze_impact_evidence(
        _candidate_set("order_app/service.py"),
        _digests(),
        _resources(
            repository,
            requirement,
        ),
    )

    assert result.guard_report.verdict == "pass"

    assert len(result.change_points) == 1

    change_point = result.change_points[0]

    assert change_point.target.expect == "new"

    assert change_point.target.parent_symbol == "order_app.service.OrderService"

    assert change_point.change_type == "add"

    assert change_point.rule_ids == (NEW_SYMBOL_RULE,)


def test_explicit_existing_symbol_outside_candidate_set_is_rejected() -> None:
    """完整索引不能被用来绕过冻结 CandidateSet 的读取边界。"""

    repository = _repository()

    requirement = _requirement(
        symbol_hints=(
            SymbolRef(
                name="cancel_endpoint",
                parent_symbol=None,
                expect="existing",
                kind="function",
            ),
        ),
    )

    with pytest.raises(
        ImpactEngineValidationError,
        match="未进入 CandidateSet",
    ):
        analyze_impact_evidence(
            _candidate_set("order_app/service.py"),
            _digests(),
            _resources(
                repository,
                requirement,
            ),
        )


def test_explicit_unknown_existing_symbol_is_rejected() -> None:
    """expect=existing 的需求提示不能引用仓库中不存在的符号。"""

    repository = _repository()

    requirement = _requirement(
        symbol_hints=(
            SymbolRef(
                name="refund_order",
                parent_symbol=None,
                expect="existing",
                kind="function",
            ),
        ),
    )

    with pytest.raises(
        ImpactEngineValidationError,
        match="不存在于仓库索引",
    ):
        analyze_impact_evidence(
            _candidate_set("order_app/service.py"),
            _digests(),
            _resources(
                repository,
                requirement,
            ),
        )


def test_parse_error_degrades_impact_coverage() -> None:
    """语法错误文件必须降低 coverage，不能静默宣称已经分析。"""

    repository = _repository(
        include_broken_file=True,
    )

    result = analyze_impact_evidence(
        _candidate_set(
            "order_app/service.py",
            "order_app/broken.py",
        ),
        _digests(),
        _resources(
            repository,
            _requirement(),
        ),
    )

    assert result.branch_result.status == "degraded"

    assert result.branch_result.coverage.assigned == 2

    assert result.branch_result.coverage.analyzed == 1

    assert result.branch_result.coverage.ratio == 0.5

    assert result.branch_result.coverage.missing[0].path == "order_app/broken.py"


def test_digest_mismatch_is_blocked_by_guard() -> None:
    """分支消费错误摘要时不能输出可用 ChangePoint。"""

    repository = _repository()

    with pytest.raises(
        ImpactEngineValidationError,
        match="DIGEST_MISMATCH",
    ):
        analyze_impact_evidence(
            _candidate_set("order_app/service.py"),
            _digests(set_digest=ONE_HASH),
            _resources(
                repository,
                _requirement(),
            ),
        )


def test_parallel_runner_entry_returns_only_branch_result() -> None:
    """ParallelAnalysis 使用的入口必须符合统一 BranchRunner 签名。"""

    repository = _repository()

    branch_result = analyze_impact(
        _candidate_set("order_app/service.py"),
        _digests(),
        _resources(
            repository,
            _requirement(),
        ),
    )

    assert branch_result.branch == "impact"

    assert branch_result.status == "success"

    assert branch_result.findings[0].rule_ids == (EXISTING_SYMBOL_RULE,)
