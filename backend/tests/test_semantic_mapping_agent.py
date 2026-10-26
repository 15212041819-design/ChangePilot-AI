"""受控 Semantic Mapping Agent 测试。"""

import json

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)
from pydantic import ValidationError

from app.schemas.contracts import (
    AcceptanceCriterion,
    Budget,
    EntityHint,
    RequirementSpec,
    ScopeHints,
)
from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.schemas.semantic_mapping import SemanticQuery
from app.services.semantic_mapping_agent import (
    SemanticMappingValidationError,
    run_semantic_mapping,
)
from app.tools.context_builder import build_repository_context

ZERO_HASH = "sha256:" + "0" * 64


def _file(
    path: str,
    content: str,
) -> RepositoryFile:
    """创建 Python 仓库文件。"""

    return RepositoryFile(
        relative_path=path,
        language="python",
        size_bytes=len(content.encode("utf-8")),
        content=content,
    )


def _repository() -> RepositorySnapshot:
    """创建包含一个未被基线检索命中模块的仓库。"""

    files = [
        _file(
            "order_app/service.py",
            ("class OrderService:\n    def cancel_order(self) -> None:\n        pass\n"),
        ),
        _file(
            "order_app/api.py",
            (
                "from order_app.service import OrderService\n\n"
                "def cancel_endpoint() -> None:\n"
                "    OrderService().cancel_order()\n"
            ),
        ),
        _file(
            "order_app/audit.py",
            ("class AuditLogger:\n    def record_event(self) -> None:\n        pass\n"),
        ),
        _file(
            "support/ledger.py",
            ("class EventLedger:\n    def append(self) -> None:\n        pass\n"),
        ),
        _file(
            "tests/test_service.py",
            (
                "from order_app.service import OrderService\n\n"
                "def test_cancel_order() -> None:\n"
                "    assert OrderService()\n"
            ),
        ),
    ]

    return RepositorySnapshot(
        repository_name="order_service",
        files=files,
        scanned_file_count=len(files),
        truncated=False,
    )


def _requirement() -> RequirementSpec:
    """创建用于语义映射的需求契约。"""

    return RequirementSpec(
        spec_id="RS-0001",
        spec_revision=1,
        summary="用户可以取消订单。",
        change_type="feature",
        acceptance_criteria=(
            AcceptanceCriterion(
                id="AC-001",
                description="用户可以取消订单。",
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
        source_text="为订单系统增加取消订单功能。",
        spec_digest=ZERO_HASH,
    )


def _context():
    """创建基线 CandidatePool 和仓库索引。"""

    return build_repository_context(
        _requirement(),
        _repository(),
        commit="abc123",
    )


def _decision(
    *,
    needs_more_context: bool,
    selected_paths: tuple[str, ...] = (),
    queries: tuple[dict[str, object], ...] = (),
) -> str:
    """构造模型的结构化决策。"""

    return json.dumps(
        {
            "needs_more_context": needs_more_context,
            "selected_paths": list(selected_paths),
            "queries": list(queries),
            "explanation": "根据候选符号和依赖证据选择。",
        },
        ensure_ascii=False,
    )


def test_agent_selects_only_existing_pool_paths() -> None:
    """Agent 可以在不扩池时直接冻结已知候选。"""

    context = _context()

    result = run_semantic_mapping(
        _requirement(),
        context.candidate_pool,
        _repository(),
        context.symbol_index,
        context.dependency_graph,
        budget=Budget(),
        model=FakeListChatModel(
            responses=[
                _decision(
                    needs_more_context=False,
                    selected_paths=(
                        "order_app/service.py",
                        "tests/test_service.py",
                    ),
                )
            ]
        ),
    )

    assert result.query_count == 0
    assert result.tool_traces == ()

    assert tuple(item.path for item in result.candidate_set.files) == (
        "order_app/service.py",
        "tests/test_service.py",
    )

    assert all(item.selected_by == "semantic_map" for item in result.candidate_set.files)


def test_agent_expands_pool_through_whitelisted_tool() -> None:
    """未命中文件只能通过白名单工具进入 CandidatePool。"""

    context = _context()

    assert "support/ledger.py" not in {entry.path for entry in context.candidate_pool.entries}

    model = FakeListChatModel(
        responses=[
            _decision(
                needs_more_context=True,
                queries=(
                    {
                        "tool": "list_module_symbols",
                        "terms": [],
                        "symbol": None,
                        "module": "support.ledger",
                        "reason": ("需要确认候选池外但索引内的事件台账模块。"),
                    },
                ),
            ),
            _decision(
                needs_more_context=False,
                selected_paths=(
                    "order_app/service.py",
                    "support/ledger.py",
                ),
            ),
        ]
    )

    result = run_semantic_mapping(
        _requirement(),
        context.candidate_pool,
        _repository(),
        context.symbol_index,
        context.dependency_graph,
        budget=Budget(),
        model=model,
    )

    assert result.query_count == 1
    assert len(result.decisions) == 2

    assert result.tool_traces[0].accepted_paths == ("support/ledger.py",)

    assert "support/ledger.py" in {entry.path for entry in result.candidate_pool.entries}

    assert "support/ledger.py" in {item.path for item in result.candidate_set.files}


def test_agent_rejects_fabricated_final_path() -> None:
    """即使 JSON 结构合法，候选池外路径仍必须被拒绝。"""

    context = _context()

    with pytest.raises(
        SemanticMappingValidationError,
        match="候选池外路径",
    ):
        run_semantic_mapping(
            _requirement(),
            context.candidate_pool,
            _repository(),
            context.symbol_index,
            context.dependency_graph,
            budget=Budget(),
            model=FakeListChatModel(
                responses=[
                    _decision(
                        needs_more_context=False,
                        selected_paths=("invented/path.py",),
                    )
                ]
            ),
        )


def test_unknown_identifier_query_cannot_add_fake_path() -> None:
    """查询不到真实符号时，扩池记录存在但不能新增路径。"""

    context = _context()

    original_paths = {entry.path for entry in context.candidate_pool.entries}

    result = run_semantic_mapping(
        _requirement(),
        context.candidate_pool,
        _repository(),
        context.symbol_index,
        context.dependency_graph,
        budget=Budget(),
        model=FakeListChatModel(
            responses=[
                _decision(
                    needs_more_context=True,
                    queries=(
                        {
                            "tool": "search_identifiers",
                            "terms": [
                                "imaginary_symbol",
                            ],
                            "symbol": None,
                            "module": None,
                            "reason": "查找潜在符号。",
                        },
                    ),
                ),
                _decision(
                    needs_more_context=False,
                    selected_paths=("order_app/service.py",),
                ),
            ]
        ),
    )

    assert {entry.path for entry in result.candidate_pool.entries} == original_paths

    assert result.tool_traces[0].accepted_paths == ()


def test_agent_fails_when_round_budget_ends_without_final_selection() -> None:
    """连续三轮扩池后仍未决策时必须失败，不能暗中自选。"""

    context = _context()

    expand = _decision(
        needs_more_context=True,
        queries=(
            {
                "tool": "search_identifiers",
                "terms": [
                    "missing_symbol",
                ],
                "symbol": None,
                "module": None,
                "reason": "继续查找语义对应符号。",
            },
        ),
    )

    with pytest.raises(
        SemanticMappingValidationError,
        match="3 轮预算",
    ):
        run_semantic_mapping(
            _requirement(),
            context.candidate_pool,
            _repository(),
            context.symbol_index,
            context.dependency_graph,
            budget=Budget(),
            model=FakeListChatModel(
                responses=[
                    expand,
                    expand,
                    expand,
                ]
            ),
        )


def test_query_contract_rejects_wrong_arguments() -> None:
    """工具参数必须在调用仓库工具前完成互斥校验。"""

    with pytest.raises(
        ValidationError,
        match="只允许提供 symbol",
    ):
        SemanticQuery(
            tool="read_signature",
            terms=("cancel",),
            symbol="cancel_order",
            reason="参数组合不合法。",
        )
