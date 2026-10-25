"""符号、变更点和冻结候选集契约测试。"""

import pytest
from pydantic import ValidationError

from app.schemas.contracts import (
    Budget,
    CallerRef,
    CandidateSet,
    ChangePoint,
    ContractReferenceError,
    EvidencePointer,
    FileRef,
    SymbolRef,
    validate_change_point_references,
)

ZERO_HASH = "sha256:" + "0" * 64


def evidence() -> tuple[EvidencePointer, ...]:
    """创建测试使用的真实位置占位证据。"""

    return (
        EvidencePointer(
            path="order_app/service.py",
            line=10,
            kind="def",
        ),
    )


def change_point(
    *,
    target: SymbolRef,
    change_type: str = "add",
    path: str = "order_app/service.py",
    declares_new_file: bool = False,
) -> ChangePoint:
    """创建统一的测试变更点。"""

    return ChangePoint(
        cp_id="CP-0001",
        path=path,
        target=target,
        change_type=change_type,
        declares_new_file=(declares_new_file),
        ac_ids=("AC-001",),
        evidence=evidence(),
        confidence=0.8,
        source="impact",
    )


def test_t07_new_symbol_with_existing_parent_is_valid() -> None:
    """T-07：新方法可以引用索引中真实存在的父类。"""

    result = change_point(
        target=SymbolRef(
            name="cancel_order",
            parent_symbol=("order_app.service.OrderService"),
            expect="new",
            kind="method",
        )
    )

    validate_change_point_references(
        result,
        existing_files={
            "order_app/service.py",
        },
        existing_symbols={
            "order_app.service.OrderService",
        },
    )


def test_t09_new_symbol_without_file_or_parent_is_rejected() -> None:
    """T-09：新符号不能同时缺少真实目标文件和父符号。"""

    result = change_point(
        target=SymbolRef(
            name="cancel_order",
            parent_symbol=None,
            expect="new",
            kind="function",
        ),
        path="order_app/missing.py",
    )

    with pytest.raises(
        ContractReferenceError,
        match="目标文件不存在",
    ):
        validate_change_point_references(
            result,
            existing_files={
                "order_app/service.py",
            },
            existing_symbols=set(),
        )


def test_t10_new_symbol_cannot_use_semantic_change() -> None:
    """T-10：expect=new 与 semantic 组合在构造期失败。"""

    with pytest.raises(
        ValidationError,
        match="expect=new",
    ):
        change_point(
            target=SymbolRef(
                name="cancel_order",
                parent_symbol=("order_app.service.OrderService"),
                expect="new",
                kind="method",
            ),
            change_type="semantic",
        )


def test_t11_module_level_anonymous_target_only_supports_add() -> None:
    """T-11：匿名模块目标只允许表示模块级新增。"""

    valid = change_point(
        target=SymbolRef(
            name=None,
            parent_symbol=None,
            expect="new",
            kind="module",
        )
    )

    validate_change_point_references(
        valid,
        existing_files={
            "order_app/service.py",
        },
        existing_symbols=set(),
    )

    with pytest.raises(
        ValidationError,
        match="expect=new|name=None",
    ):
        change_point(
            target=SymbolRef(
                name=None,
                parent_symbol=None,
                expect="new",
                kind="module",
            ),
            change_type="delete",
        )


def test_t38_contract_does_not_define_signature_after() -> None:
    """T-38：改前分析契约中禁止出现 signature_after。"""

    assert "signature_after" not in CallerRef.model_fields

    assert "signature_after" not in ChangePoint.model_fields


def test_candidate_set_is_deeply_immutable() -> None:
    """冻结后的候选文件集合不能被并行分支修改。"""

    candidate_set = CandidateSet(
        set_id="CS-0001",
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=ZERO_HASH,
        files=(
            FileRef(
                path="order_app/service.py",
                content_sha256=ZERO_HASH,
                kind="source",
                reason="命中订单服务符号",
                score=1.0,
                confidence=0.9,
                selected_by="semantic_map",
            ),
        ),
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )

    with pytest.raises(
        ValidationError,
    ):
        candidate_set.files[0].reason = "尝试修改"
