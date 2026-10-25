"""为需求修订分配并维护稳定的验收标准 ID。"""

import re
import string
from difflib import SequenceMatcher

from app.schemas.contracts import (
    AcceptanceCriterion,
    AcceptanceCriterionDraft,
    ACRegistryRecord,
    ACRegistryState,
    CriterionHistory,
)

SIMILARITY_THRESHOLD = 0.80
DUPLICATE_THRESHOLD = 0.90

PRIORITY_RANK = {
    "P0": 0,
    "P1": 1,
    "P2": 2,
}


def normalize_criterion_description(
    description: str,
) -> str:
    """按契约折叠空白、统一大小写并移除尾部标点。"""

    normalized = " ".join(description.strip().lower().split())

    return normalized.rstrip(string.punctuation + "，。！？；：")


def criterion_similarity(
    left: str,
    right: str,
) -> float:
    """计算两个归一化验收标准描述的可复现相似度。"""

    return SequenceMatcher(
        None,
        normalize_criterion_description(left),
        normalize_criterion_description(right),
    ).ratio()


def _criterion_number(
    criterion_id: str,
) -> int:
    """提取 AC-001 中的递增编号。"""

    match = re.fullmatch(
        r"AC-(\d{3})",
        criterion_id,
    )

    if match is None:
        raise ValueError(f"无效的验收标准 ID：{criterion_id}")

    return int(match.group(1))


def _next_id(
    criteria: dict[str, ACRegistryRecord],
) -> str:
    """返回从未使用过的下一个验收标准 ID。"""

    highest = max(
        (_criterion_number(item) for item in criteria),
        default=0,
    )

    return f"AC-{highest + 1:03d}"


def _deduplicate_drafts(
    drafts: list[AcceptanceCriterionDraft],
) -> tuple[
    list[AcceptanceCriterionDraft],
    list[str],
]:
    """删除高度相似的新条目，优先保留优先级更高者。"""

    kept: list[AcceptanceCriterionDraft] = []
    warnings: list[str] = []

    for draft in drafts:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(kept)
                if criterion_similarity(
                    existing.description,
                    draft.description,
                )
                >= DUPLICATE_THRESHOLD
            ),
            None,
        )

        if duplicate_index is None:
            kept.append(draft)
            continue

        existing = kept[duplicate_index]

        if PRIORITY_RANK[draft.priority] < PRIORITY_RANK[existing.priority]:
            kept[duplicate_index] = draft
            discarded = existing
        else:
            discarded = draft

        warnings.append(f"重复验收标准已丢弃：{discarded.description}")

    return kept, warnings


def _new_record(
    criterion_id: str,
    draft: AcceptanceCriterionDraft,
    *,
    revision: int,
    derived_from: str | None = None,
) -> ACRegistryRecord:
    """创建首次出现的 registry 条目。"""

    return ACRegistryRecord(
        criterion=AcceptanceCriterion(
            id=criterion_id,
            description=draft.description,
            verification=draft.verification,
            priority=draft.priority,
            status="active",
            derived_from=derived_from,
        ),
        normalized_description=(normalize_criterion_description(draft.description)),
        first_seen_revision=revision,
        history=(
            CriterionHistory(
                revision=revision,
                similarity=1.0,
                event="created",
            ),
        ),
    )


def _updated_record(
    record: ACRegistryRecord,
    draft: AcceptanceCriterionDraft,
    *,
    revision: int,
    similarity: float,
    event: str,
    supersedes: tuple[str, ...] = (),
) -> ACRegistryRecord:
    """沿用稳定 ID，并记录本次改写、拆分或合并。"""

    return record.model_copy(
        update={
            "criterion": record.criterion.model_copy(
                update={
                    "description": draft.description,
                    "verification": draft.verification,
                    "priority": draft.priority,
                    "status": "active",
                    "supersedes": supersedes,
                }
            ),
            "normalized_description": (normalize_criterion_description(draft.description)),
            "history": (
                *record.history,
                CriterionHistory(
                    revision=revision,
                    similarity=similarity,
                    event=event,
                ),
            ),
        }
    )


def allocate_acceptance_criteria(
    registry: ACRegistryState,
    drafts: list[AcceptanceCriterionDraft],
    *,
    revision: int,
) -> ACRegistryState:
    """用完整相似度矩阵稳定分配 ID，并返回新的 registry 状态。"""

    if revision < 1:
        raise ValueError("revision 必须大于等于 1。")

    if not drafts:
        raise ValueError("验收标准不能为空。")

    unique_drafts, duplicate_warnings = _deduplicate_drafts(drafts)

    # 创建副本，禁止原地修改传入的 registry。
    records = dict(registry.criteria)

    active_ids = sorted(
        criterion_id
        for criterion_id, record in records.items()
        if record.criterion.status == "active"
    )

    # 相同 revision 和完全相同的有效条目属于幂等重放。
    # 这种情况不能追加 history，也不能提升 registry.version。
    active_records = [records[criterion_id] for criterion_id in active_ids]

    is_idempotent_replay = (
        len(active_records) == len(unique_drafts)
        and not duplicate_warnings
        and all(
            (
                record.normalized_description == normalize_criterion_description(draft.description)
                and (record.criterion.verification == draft.verification)
                and (record.criterion.priority == draft.priority)
                and record.history
                and (record.history[-1].revision == revision)
            )
            for record, draft in zip(
                active_records,
                unique_drafts,
                strict=True,
            )
        )
    )

    if is_idempotent_replay:
        return registry

    # 保留完整相似度矩阵。
    # 拆分和合并判断不能只保留每条新需求的最高分。
    similarities = {
        (old_id, new_index): criterion_similarity(
            records[old_id].criterion.description,
            draft.description,
        )
        for old_id in active_ids
        for new_index, draft in enumerate(unique_drafts)
    }

    assigned_old: set[str] = set()
    assigned_new: set[int] = set()

    # 先处理合并：
    # 一个新条目同时匹配多个旧条目时，
    # 新条目继承数值最小的旧 ID。
    for new_index, draft in enumerate(unique_drafts):
        merge_ids = sorted(
            old_id
            for old_id in active_ids
            if (
                similarities[(old_id, new_index)] >= SIMILARITY_THRESHOLD
                and old_id not in assigned_old
            )
        )

        if len(merge_ids) < 2:
            continue

        inherited_id = merge_ids[0]

        # supersedes 只记录其他被合并 ID，
        # 不能包含当前继承的 ID。
        merged_ids = tuple(merge_ids[1:])

        records[inherited_id] = _updated_record(
            records[inherited_id],
            draft,
            revision=revision,
            similarity=max(similarities[(old_id, new_index)] for old_id in merge_ids),
            event="merged",
            supersedes=merged_ids,
        )

        for merged_id in merged_ids:
            record = records[merged_id]

            records[merged_id] = record.model_copy(
                update={
                    "criterion": (
                        record.criterion.model_copy(
                            update={
                                "status": "merged",
                            }
                        )
                    )
                }
            )

        assigned_old.update(merge_ids)
        assigned_new.add(new_index)

    # 再处理拆分：
    # 最相似子项继承旧 ID 并保持 active；
    # 其余子项获得新 ID，通过 derived_from 追踪来源。
    for old_id in active_ids:
        if old_id in assigned_old:
            continue

        split_indices = [
            index
            for index in range(len(unique_drafts))
            if (index not in assigned_new and similarities[(old_id, index)] >= SIMILARITY_THRESHOLD)
        ]

        if len(split_indices) < 2:
            continue

        split_indices.sort(
            key=lambda index: (
                -similarities[(old_id, index)],
                index,
            )
        )

        inherited_index = split_indices[0]

        records[old_id] = _updated_record(
            records[old_id],
            unique_drafts[inherited_index],
            revision=revision,
            similarity=similarities[(old_id, inherited_index)],
            event="split",
        )

        assigned_old.add(old_id)
        assigned_new.add(inherited_index)

        for child_index in split_indices[1:]:
            new_id = _next_id(records)

            records[new_id] = _new_record(
                new_id,
                unique_drafts[child_index],
                revision=revision,
                derived_from=old_id,
            )

            assigned_new.add(child_index)

    # 剩余条目进行稳定的一对一贪心匹配。
    # 排序顺序为：
    # 1. 相似度从高到低；
    # 2. 旧 AC ID；
    # 3. 新条目原始顺序。
    pairs = sorted(
        (
            (
                -similarity,
                old_id,
                new_index,
            )
            for (
                old_id,
                new_index,
            ), similarity in similarities.items()
            if (
                similarity >= SIMILARITY_THRESHOLD
                and old_id not in assigned_old
                and new_index not in assigned_new
            )
        )
    )

    for (
        negative_similarity,
        old_id,
        new_index,
    ) in pairs:
        if old_id in assigned_old or new_index in assigned_new:
            continue

        records[old_id] = _updated_record(
            records[old_id],
            unique_drafts[new_index],
            revision=revision,
            similarity=-negative_similarity,
            event="rewritten",
        )

        assigned_old.add(old_id)
        assigned_new.add(new_index)

    # 未匹配的新条目获得只增不减的新 ID。
    for new_index, draft in enumerate(unique_drafts):
        if new_index in assigned_new:
            continue

        new_id = _next_id(records)

        records[new_id] = _new_record(
            new_id,
            draft,
            revision=revision,
        )

        assigned_new.add(new_index)

    # 没有被新版本继承的旧条目进入 dropped 状态，
    # 但它们的 ID 永远保留，不能再次分配。
    for old_id in active_ids:
        if old_id in assigned_old:
            continue

        record = records[old_id]

        records[old_id] = record.model_copy(
            update={
                "criterion": (
                    record.criterion.model_copy(
                        update={
                            "status": "dropped",
                        }
                    )
                )
            }
        )

    next_warnings = tuple(
        (
            *registry.warnings,
            *duplicate_warnings,
        )
    )

    candidate_state = ACRegistryState(
        version=registry.version + 1,
        criteria=records,
        warnings=next_warnings,
    )

    # 即使没有命中前面的快速幂等检查，
    # 只要最终内容没有变化，也不能增加版本。
    comparable_old = registry.model_dump(
        exclude={"version"},
    )

    comparable_new = candidate_state.model_dump(
        exclude={"version"},
    )

    if comparable_old == comparable_new:
        return registry

    return candidate_state


def active_acceptance_criteria(
    registry: ACRegistryState,
) -> tuple[AcceptanceCriterion, ...]:
    """按 ID 返回当前仍然生效的验收标准。"""

    return tuple(
        registry.criteria[criterion_id].criterion
        for criterion_id in sorted(registry.criteria)
        if (registry.criteria[criterion_id].criterion.status == "active")
    )
