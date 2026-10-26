"""使用仓库事实构建 CandidatePool 并冻结 CandidateSet。

本模块实现 evidence-grounded 工作流中的确定性基线：

1. 从仓库快照建立 Python 符号索引和依赖图；
2. 用需求关键词、符号命中、依赖闭包、历史共变和测试映射生成宽候选池；
3. 按稳定分数和路径选出文件，并使用真实内容哈希冻结 CandidateSet；
4. 不调用 LLM，不执行用户代码，不修改仓库。
"""

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.schemas.code_index import RepositorySymbolIndex
from app.schemas.contracts import (
    Budget,
    CandidatePool,
    CandidateSet,
    FileKind,
    FileRef,
    PoolEntry,
    RequirementSpec,
    SignalScore,
)
from app.schemas.dependency_graph import (
    RepositoryDependencyGraph,
)
from app.schemas.repository import (
    RepositorySnapshot,
)
from app.tools.contract_digest import (
    canonical_digest,
    compute_pool_digest,
    compute_set_digest,
    hash_content,
    normalize_digest_path,
    stable_signal_sum,
)
from app.tools.git_cochange import (
    CoChangeIndex,
    find_cochange_neighbors,
)
from app.tools.python_dependency_graph import (
    build_python_dependency_graph,
)
from app.tools.python_symbol_indexer import (
    index_python_symbols,
)

EMPTY_SHA256 = "sha256:" + "0" * 64
DEFAULT_POOL_LIMIT = 50
MAX_POOL_LIMIT = 60
DEPENDENCY_DEPTH = 2

# 至少共同修改两次才作为候选证据，
# 避免单次偶然提交产生大量噪声。
MIN_COCHANGE_SHARED_COMMITS = 2

# 每个直接命中文件最多扩展 10 个历史共变邻居。
MAX_COCHANGE_NEIGHBORS_PER_SEED = 10

# 共变信号的最高权重低于符号命中，
# 防止历史相关性覆盖当前代码事实。
MAX_COCHANGE_SIGNAL = 1.25

CONFIG_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
}

DOCUMENT_SUFFIXES = {
    ".md",
    ".rst",
    ".txt",
}

TEST_DIRECTORY_NAMES = {
    "test",
    "tests",
    "spec",
    "specs",
}


@dataclass(frozen=True)
class ContextBuildResult:
    """一次确定性上下文构建的完整结果。"""

    symbol_index: RepositorySymbolIndex

    dependency_graph: RepositoryDependencyGraph

    repository_vocabulary: frozenset[str]

    index_digest: str

    candidate_pool: CandidatePool

    candidate_set: CandidateSet


def classify_file_kind(
    path: str,
) -> FileKind:
    """根据规范化仓库路径判断契约文件类型。"""

    normalized = normalize_digest_path(path)
    pure_path = PurePosixPath(normalized)

    lowered_parts = tuple(part.casefold() for part in pure_path.parts)

    file_name = pure_path.name.casefold()
    suffix = pure_path.suffix.casefold()

    if (
        any(part in TEST_DIRECTORY_NAMES for part in lowered_parts[:-1])
        or file_name.startswith("test_")
        or file_name.endswith("_test.py")
    ):
        return "test"

    if suffix in CONFIG_SUFFIXES or file_name in {
        "dockerfile",
        "makefile",
        "pyproject.toml",
    }:
        return "config"

    if suffix in DOCUMENT_SUFFIXES:
        return "doc"

    return "source"


def build_repository_vocabulary(
    repository: RepositorySnapshot,
    symbol_index: RepositorySymbolIndex,
) -> frozenset[str]:
    """构建只由真实路径、模块、符号和 import 组成的仓库词表。"""

    vocabulary: set[str] = set()

    for repository_file in repository.files:
        path = PurePosixPath(normalize_digest_path(repository_file.relative_path))

        vocabulary.add(path.as_posix())
        vocabulary.add(path.stem)
        vocabulary.update(path.parts)

    for indexed_file in symbol_index.files:
        vocabulary.add(indexed_file.module_name)

        for symbol in indexed_file.symbols:
            vocabulary.add(symbol.name)
            vocabulary.add(symbol.qualified_name)

        for import_reference in indexed_file.imports:
            if import_reference.module:
                vocabulary.add(import_reference.module)

            if import_reference.imported_name:
                vocabulary.add(import_reference.imported_name)

    return frozenset(item for item in vocabulary if item)


def compute_repository_index_digest(
    repository: RepositorySnapshot,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
) -> str:
    """对快照内容、符号事实和依赖边生成稳定摘要。"""

    if repository.repository_name != symbol_index.repository_name:
        raise ValueError("RepositorySnapshot 与 RepositorySymbolIndex 仓库名不一致。")

    if repository.repository_name != dependency_graph.repository_name:
        raise ValueError("RepositorySnapshot 与 RepositoryDependencyGraph 仓库名不一致。")

    files = [
        {
            "path": normalize_digest_path(item.relative_path),
            "language": item.language,
            "content_sha256": hash_content(item.content.encode("utf-8")),
        }
        for item in sorted(
            repository.files,
            key=lambda candidate: normalize_digest_path(candidate.relative_path),
        )
    ]

    indexed_files = [
        {
            "path": normalize_digest_path(item.file_path),
            "module": item.module_name,
            "parse_error": item.parse_error,
            "symbols": [
                {
                    "qualified_name": (symbol.qualified_name),
                    "kind": symbol.kind,
                    "line_start": (symbol.line_start),
                    "line_end": (symbol.line_end),
                    "signature": (symbol.signature),
                }
                for symbol in sorted(
                    item.symbols,
                    key=lambda candidate: (
                        candidate.qualified_name,
                        candidate.line_start,
                    ),
                )
            ],
            "imports": [
                {
                    "module": (reference.module),
                    "imported_name": (reference.imported_name),
                    "relative_level": (reference.relative_level),
                    "line_number": (reference.line_number),
                }
                for reference in sorted(
                    item.imports,
                    key=lambda candidate: (
                        candidate.module,
                        (candidate.imported_name or ""),
                        candidate.relative_level,
                        candidate.line_number,
                    ),
                )
            ],
        }
        for item in sorted(
            symbol_index.files,
            key=lambda candidate: normalize_digest_path(candidate.file_path),
        )
    ]

    dependencies = [
        {
            "source_module": (item.source_module),
            "source_file": normalize_digest_path(item.source_file),
            "target_module": (item.target_module),
            "target_file": (
                normalize_digest_path(item.target_file) if item.target_file is not None else None
            ),
            "imported_name": (item.imported_name),
            "kind": item.kind,
            "line_number": (item.line_number),
        }
        for item in sorted(
            dependency_graph.dependencies,
            key=lambda candidate: (
                candidate.source_module,
                candidate.target_module,
                (candidate.imported_name or ""),
                candidate.line_number,
            ),
        )
    ]

    return canonical_digest(
        "repository_index",
        {
            "repository_name": (repository.repository_name),
            "snapshot_truncated": (repository.truncated),
            "files": files,
            "indexed_files": indexed_files,
            "dependencies": dependencies,
        },
    )


def _search_terms(
    requirement: RequirementSpec,
) -> tuple[str, ...]:
    """从需求契约中提取用于确定性召回的词。"""

    raw_terms: list[str] = [
        *requirement.scope_hints.target_modules,
    ]

    for hint in requirement.entity_hints:
        raw_terms.extend(
            (
                hint.concept,
                *hint.lexical_variants,
                *hint.symbol_candidates,
            )
        )

    # 英文、数字和 snake_case 可以稳定分词。
    # 中文概念保留完整短语，避免引入不稳定分词器。
    terms: set[str] = set()

    for raw_term in raw_terms:
        normalized = " ".join(raw_term.strip().casefold().split())

        if len(normalized) >= 2:
            terms.add(normalized)

        terms.update(
            token
            for token in re.findall(
                r"[a-z_][a-z0-9_]*",
                normalized,
            )
            if len(token) >= 2
        )

    return tuple(sorted(terms))


def _path_allowed(
    path: str,
    requirement: RequirementSpec,
) -> bool:
    """应用 ScopeHints 的包含和排除边界。"""

    normalized = normalize_digest_path(path)

    parts = set(PurePosixPath(normalized).parts)

    excluded = {
        item.strip().replace("\\", "/").strip("/")
        for item in requirement.scope_hints.exclude
        if item.strip()
    }

    if any(
        exclusion in parts or normalized == exclusion or normalized.startswith(f"{exclusion}/")
        for exclusion in excluded
    ):
        return False

    includes = tuple(
        item.strip().replace("\\", "/").strip("/")
        for item in requirement.scope_hints.include
        if item.strip()
    )

    if not includes:
        return True

    return any(
        normalized == inclusion or normalized.startswith(f"{inclusion}/") for inclusion in includes
    )


def _symbol_lookup(
    symbol_index: RepositorySymbolIndex,
) -> dict[
    str,
    tuple[str, ...],
]:
    """构建文件到最多 10 个限定符号名的稳定映射。"""

    return {
        normalize_digest_path(indexed_file.file_path): tuple(
            symbol.qualified_name
            for symbol in sorted(
                indexed_file.symbols,
                key=lambda item: (
                    item.line_start,
                    item.qualified_name,
                ),
            )[:10]
        )
        for indexed_file in symbol_index.files
    }


def _base_signals(
    repository: RepositorySnapshot,
    symbol_index: RepositorySymbolIndex,
    requirement: RequirementSpec,
) -> dict[
    str,
    dict[str, float],
]:
    """计算文件的关键词与符号基础信号。"""

    terms = _search_terms(requirement)

    indexed_by_path = {normalize_digest_path(item.file_path): item for item in symbol_index.files}

    signals: dict[
        str,
        dict[str, float],
    ] = defaultdict(dict)

    for repository_file in repository.files:
        path = normalize_digest_path(repository_file.relative_path)

        if not _path_allowed(
            path,
            requirement,
        ):
            continue

        # v1 面向代码变更影响分析。
        # 文档中容易与需求文本重合，
        # 但不应因此挤占源码候选预算。
        if classify_file_kind(path) == "doc":
            continue

        lowered_path = path.casefold()
        lowered_content = repository_file.content.casefold()

        path_hits = sum(term in lowered_path for term in terms)

        content_hits = sum(term in lowered_content for term in terms)

        keyword_score = round(
            path_hits * 1.5 + min(content_hits, 10) * 0.2,
            3,
        )

        if keyword_score > 0:
            signals[path]["keyword"] = keyword_score

        indexed_file = indexed_by_path.get(path)

        if indexed_file is None:
            continue

        symbol_text = "\n".join(
            value
            for symbol in indexed_file.symbols
            for value in (
                symbol.name,
                symbol.qualified_name,
                symbol.signature,
            )
        ).casefold()

        symbol_hits = sum(term in symbol_text for term in terms)

        if symbol_hits:
            signals[path]["symbol"] = round(
                min(symbol_hits, 10) * 2.0,
                3,
            )

    return signals


def _apply_dependency_signals(
    signals: dict[
        str,
        dict[str, float],
    ],
    dependency_graph: RepositoryDependencyGraph,
    repository_paths: set[str],
) -> None:
    """从基础命中文件向上下游扩展两层依赖候选。"""

    seed_paths = {
        path
        for path, path_signals in signals.items()
        if ("keyword" in path_signals or "symbol" in path_signals)
    }

    adjacency: dict[
        str,
        set[str],
    ] = defaultdict(set)

    for dependency in dependency_graph.dependencies:
        if dependency.kind != "internal" or dependency.target_file is None:
            continue

        source = normalize_digest_path(dependency.source_file)

        target = normalize_digest_path(dependency.target_file)

        if source not in repository_paths or target not in repository_paths:
            continue

        adjacency[source].add(target)
        adjacency[target].add(source)

        if (
            source in repository_paths
            and classify_file_kind(source) == "test"
            and target in seed_paths
        ):
            signals[source]["testmap"] = max(
                signals[source].get(
                    "testmap",
                    0.0,
                ),
                1.5,
            )

    queue = deque((path, 0) for path in sorted(seed_paths))

    best_depth = {path: 0 for path in seed_paths}

    while queue:
        current, depth = queue.popleft()

        if depth >= DEPENDENCY_DEPTH:
            continue

        next_depth = depth + 1

        dependency_score = round(
            1.0 / next_depth,
            3,
        )

        for neighbor in sorted(adjacency[current]):
            previous_depth = best_depth.get(neighbor)

            if previous_depth is not None and previous_depth <= next_depth:
                continue

            best_depth[neighbor] = next_depth

            if neighbor not in seed_paths:
                signals[neighbor]["dep_closure"] = max(
                    signals[neighbor].get(
                        "dep_closure",
                        0.0,
                    ),
                    dependency_score,
                )

            queue.append(
                (
                    neighbor,
                    next_depth,
                )
            )


def _apply_cochange_signals(
    signals: dict[
        str,
        dict[str, float],
    ],
    cochange_index: CoChangeIndex,
    repository_paths: set[str],
) -> None:
    """从直接命中文件向外扩展低权重历史共变候选。"""

    seed_paths = tuple(
        sorted(
            path
            for path, path_signals in signals.items()
            if ("keyword" in path_signals or "symbol" in path_signals)
        )
    )

    for seed_path in seed_paths:
        neighbors = find_cochange_neighbors(
            cochange_index,
            seed_path,
            min_shared_commits=(MIN_COCHANGE_SHARED_COMMITS),
            limit=(MAX_COCHANGE_NEIGHBORS_PER_SEED),
        )

        for neighbor_path, pair in neighbors:
            if neighbor_path not in repository_paths:
                continue

            if classify_file_kind(neighbor_path) == "doc":
                continue

            # 共变只是补充证据，
            # 最高分不得超过依赖闭包与符号命中。
            cochange_score = round(
                min(
                    MAX_COCHANGE_SIGNAL,
                    pair.score * MAX_COCHANGE_SIGNAL,
                ),
                3,
            )

            if cochange_score <= 0:
                continue

            signals[neighbor_path]["cochange"] = max(
                signals[neighbor_path].get(
                    "cochange",
                    0.0,
                ),
                cochange_score,
            )


def retrieve_candidate_pool(
    requirement: RequirementSpec,
    repository: RepositorySnapshot,
    symbol_index: RepositorySymbolIndex,
    dependency_graph: RepositoryDependencyGraph,
    *,
    commit: str,
    index_digest: str,
    cochange_index: CoChangeIndex | None = None,
    pool_id: str = "PL-0001",
    pool_limit: int = DEFAULT_POOL_LIMIT,
) -> CandidatePool:
    """用确定性信号生成最多 60 个真实文件的候选池。"""

    normalized_commit = commit.strip()

    if not normalized_commit:
        raise ValueError("commit 不能为空。")

    if not (1 <= pool_limit <= MAX_POOL_LIMIT):
        raise ValueError(f"pool_limit 必须在 1 到 {MAX_POOL_LIMIT} 之间。")

    if repository.repository_name != symbol_index.repository_name:
        raise ValueError("仓库快照与符号索引不属于同一仓库。")

    if repository.repository_name != dependency_graph.repository_name:
        raise ValueError("仓库快照与依赖图不属于同一仓库。")

    if cochange_index is not None and cochange_index.target_commit != normalized_commit.lower():
        raise ValueError("CoChangeIndex 与候选池的 commit 不一致。")

    repository_files = {
        normalize_digest_path(item.relative_path): item
        for item in repository.files
        if _path_allowed(
            item.relative_path,
            requirement,
        )
    }

    signals = _base_signals(
        repository,
        symbol_index,
        requirement,
    )

    _apply_dependency_signals(
        signals,
        dependency_graph,
        set(repository_files),
    )

    if cochange_index is not None:
        _apply_cochange_signals(
            signals,
            cochange_index,
            set(repository_files),
        )

    symbols_by_path = _symbol_lookup(symbol_index)

    ranked: list[PoolEntry] = []

    for (
        path,
        path_signals,
    ) in signals.items():
        if path not in repository_files or not path_signals:
            continue

        signal_models = tuple(
            SignalScore(
                name=name,
                value=round(value, 3),
            )
            for name, value in sorted(path_signals.items())
        )

        ranked.append(
            PoolEntry(
                path=path,
                kind=classify_file_kind(path),
                signals=signal_models,
                pre_score=stable_signal_sum(item.value for item in signal_models),
                symbols=symbols_by_path.get(
                    path,
                    (),
                ),
                source="baseline",
            )
        )

    ranked.sort(
        key=lambda item: (
            -item.pre_score,
            item.path,
        )
    )

    selected_entries = tuple(ranked[:pool_limit])

    candidate = CandidatePool(
        pool_id=pool_id,
        commit=normalized_commit,
        index_digest=index_digest,
        spec_digest=(requirement.spec_digest),
        entries=selected_entries,
        expansion_rounds_used=0,
        expansion_records=(),
        pool_exhausted=(len(ranked) > pool_limit),
        pool_digest=EMPTY_SHA256,
    )

    return candidate.model_copy(
        update={
            "pool_digest": (compute_pool_digest(candidate)),
        }
    )


def _selection_reason(
    entry: PoolEntry,
) -> str:
    """根据确定性信号生成简短可读理由。"""

    labels = {
        "keyword": "需求关键词",
        "symbol": "代码符号",
        "dep_closure": "依赖闭包",
        "cochange": "历史共变",
        "testmap": "测试映射",
    }

    rendered = "、".join(labels[signal.name] for signal in entry.signals)

    return f"确定性召回命中：{rendered}。"


def freeze_candidate_set(
    pool: CandidatePool,
    repository: RepositorySnapshot,
    *,
    budget: Budget,
    set_id: str = "CS-0001",
    selected_paths: tuple[
        str,
        ...,
    ]
    | None = None,
    selected_by: str = "baseline",
) -> CandidateSet:
    """根据真实快照内容冻结不可变 CandidateSet。"""

    entries_by_path = {normalize_digest_path(entry.path): entry for entry in pool.entries}

    repository_by_path = {
        normalize_digest_path(item.relative_path): item for item in repository.files
    }

    if selected_paths is None:
        ordered_entries = sorted(
            pool.entries,
            key=lambda item: (
                -item.pre_score,
                item.path,
            ),
        )

        paths = tuple(entry.path for entry in ordered_entries[: budget.max_files])

    else:
        normalized_paths = tuple(normalize_digest_path(path) for path in selected_paths)

        if len(normalized_paths) != len(set(normalized_paths)):
            raise ValueError("selected_paths 包含重复路径。")

        if len(normalized_paths) > budget.max_files:
            raise ValueError("selected_paths 超过 CandidateSet 文件预算。")

        paths = normalized_paths

    unknown_pool_paths = sorted(path for path in paths if path not in entries_by_path)

    if unknown_pool_paths:
        raise ValueError("selected_paths 包含候选池外路径：" + "、".join(unknown_pool_paths) + "。")

    missing_snapshot_paths = sorted(path for path in paths if path not in repository_by_path)

    if missing_snapshot_paths:
        raise ValueError(
            "CandidatePool 路径不在仓库快照中：" + "、".join(missing_snapshot_paths) + "。"
        )

    file_refs: list[FileRef] = []

    for path in sorted(paths):
        entry = entries_by_path[path]
        repository_file = repository_by_path[path]

        # PoolEntry 不保存符号行号，
        # 因此不能根据猜测填写 line。
        first_line = None

        confidence = round(
            min(
                1.0,
                entry.pre_score / (entry.pre_score + 2.0),
            ),
            3,
        )

        file_refs.append(
            FileRef(
                path=path,
                content_sha256=hash_content(repository_file.content.encode("utf-8")),
                kind=entry.kind,
                symbols=tuple(entry.symbols[: budget.max_symbols_per_file]),
                line=first_line,
                reason=(_selection_reason(entry)),
                score=entry.pre_score,
                confidence=confidence,
                signals=entry.signals,
                selected_by=selected_by,
            )
        )

    candidate = CandidateSet(
        set_id=set_id,
        pool_id=pool.pool_id,
        commit=pool.commit,
        index_digest=pool.index_digest,
        spec_digest=pool.spec_digest,
        files=tuple(file_refs),
        truncated=(pool.pool_exhausted or len(pool.entries) > len(file_refs)),
        budget=budget,
        set_digest=EMPTY_SHA256,
    )

    return candidate.model_copy(
        update={
            "set_digest": (compute_set_digest(candidate)),
        }
    )


def build_repository_context(
    requirement: RequirementSpec,
    repository: RepositorySnapshot,
    *,
    commit: str,
    cochange_index: CoChangeIndex | None = None,
    pool_limit: int = DEFAULT_POOL_LIMIT,
    pool_id: str = "PL-0001",
    set_id: str = "CS-0001",
) -> ContextBuildResult:
    """一次完成索引、依赖图、候选召回和冻结。"""

    symbol_index = index_python_symbols(repository)

    dependency_graph = build_python_dependency_graph(symbol_index)

    vocabulary = build_repository_vocabulary(
        repository,
        symbol_index,
    )

    index_digest = compute_repository_index_digest(
        repository,
        symbol_index,
        dependency_graph,
    )

    pool = retrieve_candidate_pool(
        requirement,
        repository,
        symbol_index,
        dependency_graph,
        commit=commit,
        index_digest=index_digest,
        cochange_index=cochange_index,
        pool_id=pool_id,
        pool_limit=pool_limit,
    )

    budget = Budget(
        max_files=(requirement.scope_hints.max_files),
        max_chars_per_file=(requirement.scope_hints.max_chars_per_file),
        max_symbols_per_file=10,
    )

    frozen_set = freeze_candidate_set(
        pool,
        repository,
        budget=budget,
        set_id=set_id,
    )

    return ContextBuildResult(
        symbol_index=symbol_index,
        dependency_graph=(dependency_graph),
        repository_vocabulary=(vocabulary),
        index_digest=index_digest,
        candidate_pool=pool,
        candidate_set=frozen_set,
    )
