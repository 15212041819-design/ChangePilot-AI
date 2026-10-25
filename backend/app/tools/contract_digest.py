"""生成跨平台稳定的契约内容摘要。"""

import hashlib
import json
import math
import unicodedata
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from app.schemas.contracts import (
    CONTRACT_VERSION,
    ACRegistryState,
    CandidatePool,
    CandidateSet,
    RequirementSpec,
)
from app.services.ac_registry import (
    normalize_criterion_description,
)


def canonical_digest(
    kind: str,
    payload: Any,
) -> str:
    """使用固定 JSON 编码生成带算法前缀的 SHA-256。"""

    body = {
        "kind": kind,
        "v": CONTRACT_VERSION,
        "payload": payload,
    }

    text = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_digest_path(
    path: str,
) -> str:
    """把路径转换成 NFC 规范化的 POSIX 相对路径。"""

    candidate = unicodedata.normalize(
        "NFC",
        path.strip().replace("\\", "/"),
    )

    return PurePosixPath(candidate).as_posix().removeprefix("./")


def hash_content(
    content: bytes,
) -> str:
    """移除 UTF-8 BOM，并在 CRLF 转 LF 后计算内容摘要。"""

    normalized = content.removeprefix(b"\xef\xbb\xbf").replace(
        b"\r\n",
        b"\n",
    )

    return "sha256:" + hashlib.sha256(normalized).hexdigest()


def stable_signal_sum(
    values: Iterable[float],
) -> float:
    """使用稳定顺序与统一精度汇总候选召回分数。"""

    # 在求和前排序，避免集合迭代顺序变化。
    # math.fsum 降低普通 sum 的浮点累加误差。
    return round(
        math.fsum(sorted(values)),
        3,
    )


def compute_registry_digest(
    registry: ACRegistryState,
) -> str:
    """计算包含稳定 ID、描述和生命周期状态的 registry digest。"""

    criteria = [
        {
            "id": criterion_id,
            "description": (record.normalized_description),
            "status": (record.criterion.status),
        }
        for criterion_id, record in sorted(registry.criteria.items())
    ]

    return canonical_digest(
        "ac_registry",
        {
            "version": registry.version,
            "criteria": criteria,
        },
    )


def compute_spec_digest(
    spec: RequirementSpec,
    *,
    registry_digest: str,
) -> str:
    """计算需求语义、范围和 AC 生命周期的稳定摘要。"""

    acceptance_criteria = [
        {
            "id": criterion.id,
            "description": (normalize_criterion_description(criterion.description)),
            "verification": (criterion.verification),
            # AC priority 会影响门禁和实施行为，
            # 因此必须进入 spec_digest。
            "priority": criterion.priority,
            "status": criterion.status,
        }
        for criterion in sorted(
            spec.acceptance_criteria,
            key=lambda item: item.id,
        )
    ]

    # 假设文本属于面向人的自然语言，
    # 不进入 digest。
    # 但 ID、状态和影响范围会改变流程行为，
    # 因此必须进入 digest。
    assumptions = [
        {
            "id": assumption.id,
            "status": assumption.status,
            "impact": sorted(assumption.impact),
        }
        for assumption in sorted(
            spec.assumptions,
            key=lambda item: item.id,
        )
    ]

    entity_hints = [
        {
            "concept_id": (hint.concept_id),
            "concept": " ".join(hint.concept.strip().split()),
            "lexical_variants": sorted(hint.lexical_variants),
        }
        for hint in sorted(
            spec.entity_hints,
            key=lambda item: item.concept_id,
        )
    ]

    symbol_hints = [
        {
            "name": hint.name,
            "parent_symbol": (hint.parent_symbol),
            "expect": hint.expect,
            "kind": hint.kind,
        }
        for hint in sorted(
            spec.symbol_hints,
            key=lambda item: (
                item.name or "",
                item.parent_symbol or "",
            ),
        )
    ]

    scope = {
        "include": sorted(spec.scope_hints.include),
        "exclude": sorted(spec.scope_hints.exclude),
        "target_modules": sorted(spec.scope_hints.target_modules),
        "allow_breaking": (spec.scope_hints.allow_breaking),
        "max_files": (spec.scope_hints.max_files),
        "max_chars_per_file": (spec.scope_hints.max_chars_per_file),
    }

    return canonical_digest(
        "requirement_spec",
        {
            "spec_id": spec.spec_id,
            "spec_revision": (spec.spec_revision),
            "registry_digest": (registry_digest),
            "change_type": spec.change_type,
            "acceptance_criteria": (acceptance_criteria),
            "assumptions": assumptions,
            "entity_hints": entity_hints,
            "symbol_hints": symbol_hints,
            "constraints": sorted(spec.constraints),
            "scope_hints": scope,
        },
    )


def compute_pool_digest(
    pool: CandidatePool,
) -> str:
    """计算候选池事实内容摘要，不纳入运行实例 ID 和模型分数。"""

    entries = [
        {
            "path": normalize_digest_path(entry.path),
            "kind": entry.kind,
            "signals": [
                {
                    "name": signal.name,
                    # 浮点不能直接进入 digest。
                    # 先统一到 3 位精度，再转换为字符串。
                    "value": (f"{round(signal.value, 3):.3f}"),
                }
                for signal in sorted(
                    entry.signals,
                    key=lambda item: item.name,
                )
            ],
            "symbols": sorted(entry.symbols),
            "source": entry.source,
            "requested_by": (entry.requested_by),
            "expansion_round": (entry.expansion_round),
        }
        for entry in sorted(
            pool.entries,
            key=lambda item: item.path,
        )
    ]

    expansion_records = [
        {
            "round": record.round,
            "tool": record.tool,
            "added_paths": sorted(record.added_paths),
        }
        for record in sorted(
            pool.expansion_records,
            key=lambda item: (
                item.round,
                item.tool,
            ),
        )
    ]

    return canonical_digest(
        "candidate_pool",
        {
            # pool_id 是运行实例标识，
            # 不进入内容 digest。
            "commit": pool.commit,
            "index_digest": (pool.index_digest),
            "spec_digest": (pool.spec_digest),
            "entries": entries,
            "expansion_records": (expansion_records),
            "pool_exhausted": (pool.pool_exhausted),
        },
    )


def compute_set_digest(
    candidate_set: CandidateSet,
) -> str:
    """计算冻结候选文件身份摘要，排除理由和模型评分。"""

    files = [
        {
            "path": normalize_digest_path(file_ref.path),
            "content_sha256": (file_ref.content_sha256),
            "kind": file_ref.kind,
            "symbols": sorted(file_ref.symbols),
            "line": file_ref.line,
            "selected_by": (file_ref.selected_by),
        }
        for file_ref in sorted(
            candidate_set.files,
            key=lambda item: item.path,
        )
    ]

    return canonical_digest(
        "candidate_set",
        {
            # set_id 和 pool_id 是运行实例标识，
            # 不进入候选内容 digest。
            "commit": candidate_set.commit,
            "index_digest": (candidate_set.index_digest),
            "spec_digest": (candidate_set.spec_digest),
            "files": files,
            "truncated": (candidate_set.truncated),
            "budget": (candidate_set.budget.model_dump(mode="json")),
        },
    )
