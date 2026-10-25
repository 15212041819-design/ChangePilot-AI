"""契约 digest 的跨平台稳定性测试。"""

import random

from app.schemas.contracts import (
    AcceptanceCriterionDraft,
    ACRegistryState,
    Budget,
    CandidateSet,
    EntityHint,
    FileRef,
    RequirementSpec,
    ScopeHints,
)
from app.services.ac_registry import (
    active_acceptance_criteria,
    allocate_acceptance_criteria,
)
from app.tools.contract_digest import (
    compute_registry_digest,
    compute_set_digest,
    compute_spec_digest,
    hash_content,
    stable_signal_sum,
)

ZERO_HASH = "sha256:" + "0" * 64


def build_registry() -> ACRegistryState:
    """创建带一个稳定 AC 的 registry。"""

    return allocate_acceptance_criteria(
        ACRegistryState(),
        [
            AcceptanceCriterionDraft(
                description=("待支付订单可以取消"),
                verification="test",
                priority="P0",
            )
        ],
        revision=1,
    )


def build_spec(
    *,
    revision: int = 1,
) -> RequirementSpec:
    """创建用于 digest 测试的需求契约。"""

    registry = build_registry()

    return RequirementSpec(
        spec_id="RS-0001",
        spec_revision=revision,
        summary="为订单系统增加取消功能",
        change_type="feature",
        acceptance_criteria=(active_acceptance_criteria(registry)),
        entity_hints=(
            EntityHint(
                concept_id="EH-001",
                concept="取消订单",
                lexical_variants=(
                    "cancel",
                    "order",
                ),
                role="action_target",
                in_repo_vocab=True,
            ),
        ),
        scope_hints=ScopeHints(),
        source_text=("为订单系统增加取消功能"),
        spec_digest=ZERO_HASH,
    )


def file_ref(
    path: str,
    *,
    content: bytes,
    reason: str = "候选理由",
    score: float = 1.0,
    confidence: float = 0.8,
) -> FileRef:
    """创建包含真实内容摘要的候选文件引用。"""

    return FileRef(
        path=path,
        content_sha256=(hash_content(content)),
        kind="source",
        symbols=("OrderService",),
        reason=reason,
        score=score,
        confidence=confidence,
        selected_by="semantic_map",
    )


def candidate_set(
    files: tuple[FileRef, ...],
    *,
    spec_digest: str = ZERO_HASH,
) -> CandidateSet:
    """创建待计算摘要的候选集。"""

    return CandidateSet(
        set_id="CS-0001",
        pool_id="PL-0001",
        commit="abc123",
        index_digest=ZERO_HASH,
        spec_digest=spec_digest,
        files=files,
        truncated=False,
        budget=Budget(),
        set_digest=ZERO_HASH,
    )


def test_t12_human_text_and_model_scores_do_not_change_set_digest() -> None:
    """T-12：理由、分数和置信度变化不影响文件身份摘要。"""

    first = candidate_set(
        (
            file_ref(
                "order_app/service.py",
                content=b"pass\n",
            ),
        )
    )

    second = candidate_set(
        (
            file_ref(
                "order_app/service.py",
                content=b"pass\n",
                reason="完全不同的说明",
                score=99.0,
                confidence=0.1,
            ),
        )
    )

    assert compute_set_digest(first) == compute_set_digest(second)


def test_t13_file_input_order_does_not_change_set_digest() -> None:
    """T-13：候选文件输入顺序不会影响摘要。"""

    first_file = file_ref(
        "a.py",
        content=b"a = 1\n",
    )

    second_file = file_ref(
        "b.py",
        content=b"b = 2\n",
    )

    first_digest = compute_set_digest(
        candidate_set(
            (
                first_file,
                second_file,
            )
        )
    )

    second_digest = compute_set_digest(
        candidate_set(
            (
                second_file,
                first_file,
            )
        )
    )

    assert first_digest == second_digest


def test_t14_content_change_updates_digest_but_crlf_does_not() -> None:
    """T-14：真实内容变化生效，CRLF 与 LF 被视为相同内容。"""

    assert hash_content(b"a = 1\r\n") == hash_content(b"a = 1\n")

    assert hash_content(b"a = 1\n") != hash_content(b"a = 2\n")

    first = candidate_set(
        (
            file_ref(
                "a.py",
                content=b"a = 1\n",
            ),
        )
    )

    changed = candidate_set(
        (
            file_ref(
                "a.py",
                content=b"a = 2\n",
            ),
        )
    )

    assert compute_set_digest(first) != compute_set_digest(changed)


def test_t15_spec_revision_change_propagates_to_set_digest() -> None:
    """T-15：需求修订会通过 spec_digest 使候选集失效。"""

    registry = build_registry()

    registry_digest = compute_registry_digest(registry)

    first_spec_digest = compute_spec_digest(
        build_spec(revision=1),
        registry_digest=(registry_digest),
    )

    revised_spec_digest = compute_spec_digest(
        build_spec(revision=2),
        registry_digest=(registry_digest),
    )

    files = (
        file_ref(
            "order_app/service.py",
            content=b"pass\n",
        ),
    )

    assert first_spec_digest != revised_spec_digest

    assert compute_set_digest(
        candidate_set(
            files,
            spec_digest=(first_spec_digest),
        )
    ) != compute_set_digest(
        candidate_set(
            files,
            spec_digest=(revised_spec_digest),
        )
    )


def test_t16_signal_sum_is_stable_for_different_iteration_orders() -> None:
    """T-16：不同集合迭代顺序不会造成浮点分数抖动。"""

    values = [
        0.1,
        0.2,
        0.3,
        0.4,
        1e-12,
        1e12,
    ]

    expected = stable_signal_sum(values)

    for seed in range(1000):
        shuffled = values.copy()

        random.Random(seed).shuffle(shuffled)

        assert stable_signal_sum(shuffled) == expected
