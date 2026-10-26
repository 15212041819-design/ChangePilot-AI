"""Semantic Mapping 上下文流水线测试。"""

import json

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.schemas.contracts import (
    AcceptanceCriterion,
    Assumption,
    EntityHint,
    RequirementSpec,
    ScopeHints,
)
from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.services.semantic_context import (
    SemanticContextValidationError,
    build_semantic_context,
)

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


def _repository(
    *,
    truncated: bool = False,
) -> RepositorySnapshot:
    """创建业务模块、调用方、测试和隐藏候选组成的仓库。"""

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
            "tests/test_service.py",
            (
                "from order_app.service import OrderService\n\n"
                "def test_cancel_order() -> None:\n"
                "    assert OrderService()\n"
            ),
        ),
        _file(
            "support/ledger.py",
            ("class EventLedger:\n    def append(self) -> None:\n        pass\n"),
        ),
    ]

    return RepositorySnapshot(
        repository_name="order_service",
        files=files,
        scanned_file_count=len(files),
        truncated=truncated,
    )


def _requirement(
    *,
    assumptions: tuple[Assumption, ...] = (),
) -> RequirementSpec:
    """创建已经分配 AC ID 的需求契约。"""

    return RequirementSpec(
        spec_id="RS-0001",
        spec_revision=1,
        summary="订单所属用户可以取消待支付订单。",
        change_type="feature",
        acceptance_criteria=(
            AcceptanceCriterion(
                id="AC-001",
                description="订单所属用户可以取消待支付订单。",
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
        assumptions=assumptions,
        scope_hints=ScopeHints(),
        source_text="为订单系统增加取消订单功能。",
        spec_digest=ZERO_HASH,
    )


def _decision(
    *,
    needs_more_context: bool,
    selected_paths: tuple[str, ...] = (),
    queries: tuple[dict[str, object], ...] = (),
) -> str:
    """构造 Semantic Mapping 的结构化模型响应。"""

    return json.dumps(
        {
            "needs_more_context": needs_more_context,
            "selected_paths": list(selected_paths),
            "queries": list(queries),
            "explanation": ("根据仓库符号和依赖证据选择文件。"),
        },
        ensure_ascii=False,
    )


def test_pipeline_retrieves_maps_and_freezes_one_context() -> None:
    """检索、语义选择和冻结必须消费同一份索引与需求摘要。"""

    result = build_semantic_context(
        _requirement(),
        _repository(),
        commit="abc123",
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

    assert result.index_coverage == 1.0
    assert result.warnings == ()
    assert result.query_count == 0
    assert result.candidate_set.commit == "abc123"

    assert result.candidate_set.index_digest == result.index_digest

    assert result.candidate_set.spec_digest == result.requirement_spec.spec_digest

    assert tuple(item.path for item in result.candidate_set.files) == (
        "order_app/service.py",
        "tests/test_service.py",
    )


def test_pipeline_allows_only_controlled_expansion_before_freeze() -> None:
    """基线未命中的文件必须先通过白名单工具加入候选池。"""

    result = build_semantic_context(
        _requirement(),
        _repository(),
        commit="abc123",
        model=FakeListChatModel(
            responses=[
                _decision(
                    needs_more_context=True,
                    queries=(
                        {
                            "tool": "list_module_symbols",
                            "terms": [],
                            "symbol": None,
                            "module": "support.ledger",
                            "reason": ("需要确认事件台账模块。"),
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
        ),
    )

    assert result.query_count == 1

    assert result.tool_traces[0].accepted_paths == ("support/ledger.py",)

    assert "support/ledger.py" in {entry.path for entry in result.candidate_pool.entries}

    assert "support/ledger.py" in {item.path for item in result.candidate_set.files}


def test_pipeline_rejects_unreviewed_assumption_before_model_call() -> None:
    """未审核需求不得提前进入代码语义映射。"""

    requirement = _requirement(
        assumptions=(
            Assumption(
                id="AS-001",
                text="取消订单是否恢复库存尚未确认。",
                status="unconfirmed",
            ),
        )
    )

    with pytest.raises(
        SemanticContextValidationError,
        match="AS-001",
    ):
        build_semantic_context(
            requirement,
            _repository(),
            commit="abc123",
            model=FakeListChatModel(responses=[]),
        )


def test_pipeline_reports_low_index_coverage() -> None:
    """AST 解析覆盖不足必须显式形成警告，不能静默漏报。"""

    repository = RepositorySnapshot(
        repository_name="broken_service",
        files=[
            _file(
                "order_app/service.py",
                ("class OrderService:\n    def cancel_order(self) -> None:\n        pass\n"),
            ),
            _file(
                "order_app/broken_a.py",
                "def broken(:\n",
            ),
            _file(
                "order_app/broken_b.py",
                "class Broken(\n",
            ),
        ],
        scanned_file_count=3,
        truncated=False,
    )

    result = build_semantic_context(
        _requirement(),
        repository,
        commit="abc123",
        model=FakeListChatModel(
            responses=[
                _decision(
                    needs_more_context=False,
                    selected_paths=("order_app/service.py",),
                )
            ]
        ),
    )

    assert result.index_coverage == 0.333

    assert any("低于 70%" in warning for warning in result.warnings)


def test_pipeline_reports_truncated_repository_snapshot() -> None:
    """扫描截断必须传播为后续报告可见的警告。"""

    result = build_semantic_context(
        _requirement(),
        _repository(truncated=True),
        commit="abc123",
        model=FakeListChatModel(
            responses=[
                _decision(
                    needs_more_context=False,
                    selected_paths=("order_app/service.py",),
                )
            ]
        ),
    )

    assert any("扫描结果已截断" in warning for warning in result.warnings)


def test_pipeline_rejects_blank_commit_before_indexing() -> None:
    """空 commit 不能生成无法追踪的 CandidateSet。"""

    with pytest.raises(
        SemanticContextValidationError,
        match="commit 不能为空",
    ):
        build_semantic_context(
            _requirement(),
            _repository(),
            commit="   ",
            model=FakeListChatModel(responses=[]),
        )
