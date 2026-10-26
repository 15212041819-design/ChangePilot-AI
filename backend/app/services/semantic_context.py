"""组装 evidence-grounded 工作流的语义上下文阶段。

本模块把已经审核通过的 RequirementSpec 转换成唯一、冻结的 CandidateSet：

1. 对仓库快照只建立一次 Python 符号索引；
2. 基于同一索引构建依赖图、仓库词表和 index digest；
3. 运行确定性宽召回，生成 CandidatePool；
4. 允许 Semantic Mapping Agent 通过白名单工具受控扩池；
5. 使用真实快照内容哈希冻结 CandidateSet；
6. 显式记录索引覆盖不足、扫描截断和空候选池等警告。

本阶段不执行 Impact、Test、Risk 分析，也不把大型索引对象写入 LangGraph state。
"""

from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel

from app.schemas.code_index import RepositorySymbolIndex
from app.schemas.contracts import (
    Budget,
    CandidatePool,
    CandidateSet,
    RequirementSpec,
)
from app.schemas.dependency_graph import RepositoryDependencyGraph
from app.schemas.repository import RepositorySnapshot
from app.schemas.semantic_mapping import (
    SemanticMappingDecision,
    SemanticToolTrace,
)
from app.services.semantic_mapping_agent import run_semantic_mapping
from app.tools.context_builder import (
    DEFAULT_POOL_LIMIT,
    build_repository_vocabulary,
    compute_repository_index_digest,
    retrieve_candidate_pool,
)
from app.tools.python_dependency_graph import build_python_dependency_graph
from app.tools.python_symbol_indexer import index_python_symbols

MIN_INDEX_COVERAGE = 0.70


class SemanticContextValidationError(ValueError):
    """需求审核状态或上下文摘要不满足冻结条件。"""


@dataclass(frozen=True)
class SemanticContextResult:
    """语义上下文阶段输出的完整、只读结果。"""

    requirement_spec: RequirementSpec

    symbol_index: RepositorySymbolIndex

    dependency_graph: RepositoryDependencyGraph

    repository_vocabulary: frozenset[str]

    index_digest: str

    candidate_pool: CandidatePool

    candidate_set: CandidateSet

    semantic_decisions: tuple[SemanticMappingDecision, ...]

    tool_traces: tuple[SemanticToolTrace, ...]

    query_count: int

    index_coverage: float

    warnings: tuple[str, ...]


def _validate_requirement_ready(
    requirement: RequirementSpec,
) -> None:
    """只允许已经通过人工审核的需求进入代码语义映射。"""

    unresolved = tuple(
        assumption.id for assumption in requirement.assumptions if assumption.status != "accepted"
    )

    if unresolved:
        raise SemanticContextValidationError(
            "RequirementSpec 仍包含未通过审核的假设：" + "、".join(unresolved) + "。"
        )

    active_acceptance_criteria = tuple(
        criterion for criterion in requirement.acceptance_criteria if criterion.status == "active"
    )

    if not active_acceptance_criteria:
        raise SemanticContextValidationError("RequirementSpec 至少需要一条 active 验收标准。")


def _compute_index_coverage(
    repository: RepositorySnapshot,
    symbol_index: RepositorySymbolIndex,
) -> float:
    """计算成功 AST 解析的 Python 文件占比。"""

    python_file_count = sum(
        repository_file.language.casefold() == "python" for repository_file in repository.files
    )

    if python_file_count == 0:
        return 0.0

    successfully_parsed = sum(
        indexed_file.parse_error is None for indexed_file in symbol_index.files
    )

    return round(
        successfully_parsed / python_file_count,
        3,
    )


def _build_warnings(
    repository: RepositorySnapshot,
    *,
    index_coverage: float,
    candidate_pool: CandidatePool,
) -> tuple[str, ...]:
    """生成必须向后续报告显式传播的覆盖警告。"""

    warnings: list[str] = []

    if repository.truncated:
        warnings.append("仓库扫描结果已截断，候选召回和后续分析可能不完整。")

    if index_coverage < MIN_INDEX_COVERAGE:
        warnings.append("Python AST 索引覆盖率低于 70%，当前仓库不适合直接给出完整性结论。")

    if not candidate_pool.entries:
        warnings.append("确定性检索没有找到候选文件，需要人工检查需求词汇或仓库扫描范围。")

    if candidate_pool.pool_exhausted:
        warnings.append("确定性候选池达到容量上限，低分候选未进入 Semantic Mapping。")

    return tuple(warnings)


def build_semantic_context(
    requirement: RequirementSpec,
    repository: RepositorySnapshot,
    *,
    commit: str,
    model: BaseChatModel | None = None,
    prebuilt_symbol_index: RepositorySymbolIndex | None = None,
    prebuilt_dependency_graph: RepositoryDependencyGraph | None = None,
    prebuilt_repository_vocabulary: frozenset[str] | None = None,
    pool_limit: int = DEFAULT_POOL_LIMIT,
    pool_id: str = "PL-0001",
    set_id: str = "CS-0001",
) -> SemanticContextResult:
    """运行检索、受控语义映射和冻结上下文完整阶段。"""

    normalized_commit = commit.strip()

    if not normalized_commit:
        raise SemanticContextValidationError("commit 不能为空。")

    _validate_requirement_ready(requirement)

    # 生产执行器在 Requirement Agent 之前已经需要仓库词表，
    # 因而允许把同一份预建索引继续传入，避免重复解析整个仓库。
    symbol_index = prebuilt_symbol_index or index_python_symbols(repository)

    if symbol_index.repository_name != repository.repository_name:
        raise SemanticContextValidationError(
            "预建 SymbolIndex 与 RepositorySnapshot 不属于同一仓库。"
        )

    dependency_graph = prebuilt_dependency_graph or build_python_dependency_graph(symbol_index)

    if dependency_graph.repository_name != repository.repository_name:
        raise SemanticContextValidationError(
            "预建 DependencyGraph 与 RepositorySnapshot 不属于同一仓库。"
        )

    repository_vocabulary = prebuilt_repository_vocabulary or build_repository_vocabulary(
        repository,
        symbol_index,
    )

    index_digest = compute_repository_index_digest(
        repository,
        symbol_index,
        dependency_graph,
    )

    candidate_pool = retrieve_candidate_pool(
        requirement,
        repository,
        symbol_index,
        dependency_graph,
        commit=normalized_commit,
        index_digest=index_digest,
        pool_id=pool_id,
        pool_limit=pool_limit,
    )

    budget = Budget(
        max_files=requirement.scope_hints.max_files,
        max_chars_per_file=requirement.scope_hints.max_chars_per_file,
        max_symbols_per_file=10,
    )

    semantic_result = run_semantic_mapping(
        requirement,
        candidate_pool,
        repository,
        symbol_index,
        dependency_graph,
        budget=budget,
        set_id=set_id,
        model=model,
    )

    frozen_set = semantic_result.candidate_set

    # 这里再次做跨阶段摘要断言，防止未来重构时传错仓库或需求对象。
    if frozen_set.spec_digest != requirement.spec_digest:
        raise SemanticContextValidationError(
            "CandidateSet 与 RequirementSpec 的 spec_digest 不一致。"
        )

    if frozen_set.index_digest != index_digest:
        raise SemanticContextValidationError("CandidateSet 与仓库索引的 index_digest 不一致。")

    if frozen_set.pool_id != semantic_result.candidate_pool.pool_id:
        raise SemanticContextValidationError(
            "CandidateSet 与最终 CandidatePool 的 pool_id 不一致。"
        )

    index_coverage = _compute_index_coverage(
        repository,
        symbol_index,
    )

    warnings = _build_warnings(
        repository,
        index_coverage=index_coverage,
        candidate_pool=semantic_result.candidate_pool,
    )

    return SemanticContextResult(
        requirement_spec=requirement,
        symbol_index=symbol_index,
        dependency_graph=dependency_graph,
        repository_vocabulary=repository_vocabulary,
        index_digest=index_digest,
        candidate_pool=semantic_result.candidate_pool,
        candidate_set=frozen_set,
        semantic_decisions=semantic_result.decisions,
        tool_traces=semantic_result.tool_traces,
        query_count=semantic_result.query_count,
        index_coverage=index_coverage,
        warnings=warnings,
    )
