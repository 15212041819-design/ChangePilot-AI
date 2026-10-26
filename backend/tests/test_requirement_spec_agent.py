"""Requirement Agent 与 RequirementSpec 确定性组装测试。"""

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.schemas.contracts import (
    ACRegistryState,
)
from app.services.requirement_spec_agent import (
    analyze_requirement_spec,
)


def _model_response(
    *,
    criterion: str = ("订单所属用户可以取消待支付订单。"),
) -> str:
    """构造无 ID、无 digest 的 LLM 需求草稿。"""

    return f"""
    {{
        "summary": "订单所属用户可以取消待支付订单。",
        "change_type": "feature",
        "acceptance_criteria": [
            {{
                "description": "{criterion}",
                "verification": "test",
                "priority": "P0"
            }}
        ],
        "entity_hints": [
            {{
                "concept": "取消订单",
                "lexical_variants": ["cancel order"],
                "symbol_candidates": [
                    "cancel_order",
                    "cancel_purchase"
                ],
                "role": "action_target"
            }}
        ],
        "constraints": [
            "仅允许取消待支付订单"
        ],
        "assumptions": [
            {{
                "text": "取消后是否恢复库存尚未确认"
            }}
        ]
    }}
    """


def test_agent_builds_spec_with_code_allocated_ids_and_digest() -> None:
    """LLM 只产生语义草稿，ID 和 digest 必须由代码生成。"""

    result = analyze_requirement_spec(
        ("  为订单系统增加   取消订单功能  "),
        repository_vocabulary={
            "cancel_order",
            "OrderService",
        },
        model=FakeListChatModel(responses=[_model_response()]),
    )

    assert result.spec.spec_id == ("RS-0001")

    assert result.spec.spec_revision == 1

    assert result.spec.source_text == ("为订单系统增加 取消订单功能")

    assert result.spec.acceptance_criteria[0].id == "AC-001"

    assert result.spec.assumptions[0].id == "AS-001"

    assert result.spec.spec_digest.startswith("sha256:")

    assert result.spec.spec_digest != ("sha256:" + "0" * 64)

    assert result.registry.version == 1


def test_entity_hint_marks_only_repository_backed_symbols_as_verified() -> None:
    """仓库词表未命中的符号候选必须显式标记为 unverified。"""

    result = analyze_requirement_spec(
        "为订单系统增加取消订单功能",
        repository_vocabulary={"CANCEL_ORDER"},
        model=FakeListChatModel(responses=[_model_response()]),
    )

    hint = result.spec.entity_hints[0]

    assert hint.concept_id == "EH-001"
    assert hint.in_repo_vocab is True

    assert hint.unverified == ("cancel_purchase",)


def test_revision_preserves_matching_acceptance_id() -> None:
    """需求修订时，相似验收标准应继承旧 AC ID。"""

    first = analyze_requirement_spec(
        "为订单系统增加取消订单功能",
        repository_vocabulary={"cancel_order"},
        model=FakeListChatModel(responses=[_model_response()]),
    )

    revised = analyze_requirement_spec(
        "为订单系统增加取消订单功能",
        repository_vocabulary={"cancel_order"},
        registry=first.registry,
        revision=2,
        model=FakeListChatModel(
            responses=[_model_response(criterion=("订单所属用户本人可以取消待支付状态的订单。"))]
        ),
    )

    assert revised.spec.acceptance_criteria[0].id == "AC-001"

    assert revised.spec.spec_revision == 2
    assert revised.registry.version == 2

    assert revised.spec.spec_digest != first.spec.spec_digest


def test_agent_does_not_trust_unknown_symbol_candidates() -> None:
    """没有仓库证据时，Agent 推测的符号不能被当成已存在符号。"""

    result = analyze_requirement_spec(
        "为订单系统增加取消订单功能",
        repository_vocabulary=set(),
        model=FakeListChatModel(responses=[_model_response()]),
    )

    hint = result.spec.entity_hints[0]

    assert hint.in_repo_vocab is False

    assert hint.unverified == (
        "cancel_order",
        "cancel_purchase",
    )

    assert result.spec.symbol_hints == ()


def test_agent_rejects_blank_request_before_calling_model() -> None:
    """空需求应在 LLM 调用前被拒绝。"""

    with pytest.raises(
        ValueError,
        match="变更需求不能为空",
    ):
        analyze_requirement_spec(
            "   ",
            repository_vocabulary=set(),
            model=FakeListChatModel(responses=["{}"]),
        )


def test_agent_accepts_existing_registry_object_without_mutating_it() -> None:
    """传入的 registry 必须保持不变。"""

    initial = ACRegistryState()

    result = analyze_requirement_spec(
        "为订单系统增加取消订单功能",
        repository_vocabulary={"cancel_order"},
        registry=initial,
        model=FakeListChatModel(responses=[_model_response()]),
    )

    assert initial.version == 0
    assert initial.criteria == {}

    assert result.registry.version == 1
