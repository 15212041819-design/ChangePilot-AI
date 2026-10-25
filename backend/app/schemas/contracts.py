"""ChangePilot AI 全链路共享的稳定数据契约。

本模块只定义数据结构和不依赖仓库内容的构造期约束。
文件、符号和行号是否真实存在，需要由后续 Guard 使用索引证据校验。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.0.0"
HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"

ChangeType = Literal[
    "feature",
    "fix",
    "refactor",
    "perf",
    "config",
    "remove",
    "unknown",
]

CriterionVerification = Literal[
    "test",
    "manual",
    "static",
]

CriterionPriority = Literal[
    "P0",
    "P1",
    "P2",
]

CriterionStatus = Literal[
    "active",
    "dropped",
    "superseded",
    "merged",
]

SymbolExpectation = Literal[
    "existing",
    "new",
]

SymbolKind = Literal[
    "class",
    "function",
    "method",
    "attribute",
    "const",
    "module",
    "config_key",
]

ChangePointType = Literal[
    "add",
    "signature",
    "semantic",
    "delete",
]

FileKind = Literal[
    "source",
    "test",
    "config",
    "doc",
]


class FrozenContractModel(BaseModel):
    """禁止未知字段和赋值修改的契约模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )


class CriterionHistory(FrozenContractModel):
    """验收标准一次创建、改写、拆分或合并记录。"""

    revision: int = Field(
        ge=1,
    )

    similarity: float = Field(
        ge=0.0,
        le=1.0,
    )

    event: Literal[
        "created",
        "rewritten",
        "split",
        "merged",
    ]


class AcceptanceCriterionDraft(FrozenContractModel):
    """需求 Agent 提交给 ACRegistry 的无 ID 验收标准。"""

    description: str = Field(
        min_length=1,
        max_length=200,
    )

    verification: CriterionVerification

    priority: CriterionPriority


class AcceptanceCriterion(FrozenContractModel):
    """由 ACRegistry 分配稳定 ID 后的验收标准。"""

    id: str = Field(
        pattern=r"^AC-\d{3}$",
    )

    description: str = Field(
        min_length=1,
        max_length=200,
    )

    verification: CriterionVerification

    priority: CriterionPriority

    status: CriterionStatus = "active"

    # 合并 AC 时记录被当前 AC 替代的其他旧 ID。
    # 当前 AC 自己的 ID 不应出现在这里。
    supersedes: tuple[str, ...] = ()

    # 拆分产生的新 AC 指向原始 AC。
    derived_from: str | None = None


class ACRegistryRecord(FrozenContractModel):
    """ACRegistry 保存的验收标准及其生命周期。"""

    criterion: AcceptanceCriterion

    normalized_description: str = Field(
        min_length=1,
    )

    first_seen_revision: int = Field(
        ge=1,
    )

    history: tuple[CriterionHistory, ...] = ()


class ACRegistryState(FrozenContractModel):
    """可序列化的 ACRegistry 状态。"""

    version: int = Field(
        default=0,
        ge=0,
    )

    criteria: dict[str, ACRegistryRecord] = Field(
        default_factory=dict,
    )

    warnings: tuple[str, ...] = ()


class Assumption(FrozenContractModel):
    """需要人工确认的需求假设。"""

    id: str = Field(
        pattern=r"^AS-\d{3}$",
    )

    text: str = Field(
        min_length=1,
    )

    status: Literal[
        "accepted",
        "rejected",
        "unconfirmed",
    ] = "unconfirmed"

    # 记录该假设会影响哪些验收标准 ID。
    impact: tuple[str, ...] = ()


class ScopeHints(FrozenContractModel):
    """用户或人工审核提供的代码搜索范围。"""

    include: tuple[str, ...] = ()

    exclude: tuple[str, ...] = (
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".mypy_cache",
    )

    target_modules: tuple[str, ...] = ()

    allow_breaking: bool = False

    max_files: int = Field(
        default=12,
        ge=1,
        le=60,
    )

    max_chars_per_file: int = Field(
        default=4000,
        ge=1,
    )

    @model_validator(mode="after")
    def preserve_required_exclusions(
        self,
    ) -> "ScopeHints":
        """禁止清空保护性目录排除列表。"""

        if not self.exclude:
            raise ValueError("scope_hints.exclude 不能为空。")

        return self


class EntityHint(FrozenContractModel):
    """自然语言概念及其检索词形。"""

    concept_id: str = Field(
        pattern=r"^EH-\d{3}$",
    )

    concept: str = Field(
        min_length=1,
    )

    lexical_variants: tuple[str, ...] = ()

    # 这些内容只用于召回候选符号，
    # 不能用于证明符号已经存在。
    symbol_candidates: tuple[str, ...] = ()

    role: Literal[
        "subject",
        "object",
        "action_target",
        "constraint",
        "quantity",
    ]

    # 该字段必须由确定性代码根据索引计算，
    # 不能直接相信 LLM 的判断。
    in_repo_vocab: bool

    # 保存没有在索引或仓库词表中命中的候选名称。
    unverified: tuple[str, ...] = ()


class SymbolRef(FrozenContractModel):
    """已有或计划新增的代码符号引用。"""

    # 新增整个模块时允许为 None。
    name: str | None = None

    # 新增方法或属性时可以指向已经存在的父类。
    parent_symbol: str | None = None

    # existing 表示符号必须已经存在；
    # new 表示计划新增该符号。
    expect: SymbolExpectation

    kind: SymbolKind


class RequirementSpec(FrozenContractModel):
    """全链路引用的唯一需求词汇表。"""

    spec_id: str = Field(
        pattern=r"^RS-\d{4}$",
    )

    spec_revision: int = Field(
        ge=1,
    )

    summary: str = Field(
        min_length=1,
        max_length=200,
    )

    change_type: ChangeType

    acceptance_criteria: tuple[
        AcceptanceCriterion,
        ...,
    ] = Field(
        min_length=1,
    )

    entity_hints: tuple[
        EntityHint,
        ...,
    ] = Field(
        min_length=1,
    )

    symbol_hints: tuple[
        SymbolRef,
        ...,
    ] = ()

    constraints: tuple[str, ...] = Field(
        default=(),
        max_length=10,
    )

    assumptions: tuple[
        Assumption,
        ...,
    ] = Field(
        default=(),
        max_length=10,
    )

    scope_hints: ScopeHints

    # 保存用户提交的原始需求，
    # 禁止由模型重写后覆盖。
    source_text: str = Field(
        min_length=1,
    )

    spec_digest: str = Field(
        pattern=HASH_PATTERN,
    )


class CallerRef(FrozenContractModel):
    """改前源码中一个可核验的调用点。"""

    path: str = Field(
        min_length=1,
    )

    symbol: str = Field(
        min_length=1,
    )

    line: int = Field(
        ge=1,
    )

    call_style: Literal[
        "pos",
        "kw",
        "mixed",
        "dynamic",
        "unknown",
    ]

    positional_count: int | None = Field(
        default=None,
        ge=0,
    )

    has_kwargs: bool = False

    @model_validator(mode="after")
    def validate_dynamic_call(
        self,
    ) -> "CallerRef":
        """动态调用不能宣称已知位置参数数量。"""

        if self.call_style == "dynamic" and self.positional_count is not None:
            raise ValueError("dynamic 调用的 positional_count 必须为 None。")

        return self


class EvidencePointer(FrozenContractModel):
    """指向仓库中具体代码位置的证据。"""

    path: str = Field(
        min_length=1,
    )

    line: int = Field(
        ge=1,
    )

    end_line: int | None = Field(
        default=None,
        ge=1,
    )

    kind: Literal[
        "def",
        "call",
        "import",
        "config",
        "schema",
        "test",
        "comment",
    ]

    excerpt_hash: str | None = Field(
        default=None,
        pattern=HASH_PATTERN,
    )

    @model_validator(mode="after")
    def validate_line_range(
        self,
    ) -> "EvidencePointer":
        """证据结束行不能位于开始行之前。"""

        if self.end_line is not None and self.end_line < self.line:
            raise ValueError("evidence.end_line 不能小于 line。")

        return self


class ChangePoint(FrozenContractModel):
    """经过结构约束的候选代码变更点。"""

    cp_id: str = Field(
        pattern=r"^CP-\d{4}$",
    )

    path: str = Field(
        min_length=1,
    )

    # expect 只存在于内嵌的 SymbolRef，
    # ChangePoint 不再重复声明 symbol 或 expect。
    target: SymbolRef

    change_type: ChangePointType

    declares_new_file: bool = False

    callers: tuple[
        CallerRef,
        ...,
    ] = ()

    # signature 变更没有调用方时，
    # 必须明确说明依赖反查已经完成。
    callers_verified_empty: bool = False

    ac_ids: tuple[str, ...] = Field(
        min_length=1,
    )

    evidence: tuple[
        EvidencePointer,
        ...,
    ] = Field(
        min_length=1,
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    source: Literal[
        "impact",
        "test",
        "risk",
        "human",
    ]

    rule_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_structural_combinations(
        self,
    ) -> "ChangePoint":
        """拒绝无需仓库证据即可判断的非法字段组合。"""

        # 还不存在的符号只能作为新增目标。
        if self.target.expect == "new" and self.change_type != "add":
            raise ValueError("expect=new 只允许用于 add 变更。")

        # 新建文件只能属于 add 类型。
        if self.declares_new_file and self.change_type != "add":
            raise ValueError("declares_new_file=true 只允许用于 add 变更。")

        # name=None 只用于表达模块级新增目标。
        if self.target.name is None:
            if self.change_type != "add" or self.target.kind != "module":
                raise ValueError("name=None 只允许表示模块级 add 目标。")

            if self.target.parent_symbol is not None:
                raise ValueError("模块级匿名目标不能设置 parent_symbol。")

        # 改前分析不生成 signature_after。
        # 但签名变更必须证明已经检查过调用方。
        if self.change_type == "signature" and not self.callers and not self.callers_verified_empty:
            raise ValueError("signature 变更必须提供调用点或声明调用方已验证为空。")

        return self


class SignalScore(FrozenContractModel):
    """一个确定性候选召回信号。"""

    name: Literal[
        "keyword",
        "symbol",
        "dep_closure",
        "cochange",
        "testmap",
    ]

    value: float


class PoolEntry(FrozenContractModel):
    """宽召回候选池中的一个文件。"""

    path: str = Field(
        min_length=1,
    )

    kind: FileKind

    signals: tuple[
        SignalScore,
        ...,
    ] = ()

    pre_score: float = 0.0

    symbols: tuple[str, ...] = Field(
        default=(),
        max_length=10,
    )

    source: Literal[
        "baseline",
        "llm_expansion",
    ] = "baseline"

    requested_by: str | None = None

    expansion_round: int | None = Field(
        default=None,
        ge=1,
        le=3,
    )

    @model_validator(mode="after")
    def validate_expansion_metadata(
        self,
    ) -> "PoolEntry":
        """扩池条目必须记录请求者和轮次。"""

        if self.source == "llm_expansion":
            if self.requested_by is None or self.expansion_round is None:
                raise ValueError("LLM 扩池条目必须记录 requested_by 和 expansion_round。")

        elif self.requested_by is not None or self.expansion_round is not None:
            raise ValueError("baseline 条目不能携带扩池元数据。")

        return self


class ExpansionRecord(FrozenContractModel):
    """Semantic Mapping Agent 一轮受控扩池记录。"""

    round: int = Field(
        ge=1,
        le=3,
    )

    tool: Literal[
        "search_identifiers",
        "expand_callers",
        "read_signature",
        "list_module_symbols",
    ]

    args_digest: str = Field(
        pattern=HASH_PATTERN,
    )

    added_paths: tuple[str, ...] = ()

    added_symbols: tuple[str, ...] = ()

    # 保存被预算或白名单规则拒绝的请求及原因。
    rejected: tuple[str, ...] = ()


class CandidatePool(FrozenContractModel):
    """允许受控扩展、尚未冻结的宽召回候选池快照。"""

    pool_id: str = Field(
        pattern=r"^PL-\d{4}$",
    )

    commit: str = Field(
        min_length=1,
    )

    index_digest: str = Field(
        pattern=HASH_PATTERN,
    )

    spec_digest: str = Field(
        pattern=HASH_PATTERN,
    )

    entries: tuple[
        PoolEntry,
        ...,
    ] = Field(
        max_length=60,
    )

    expansion_rounds_used: int = Field(
        ge=0,
        le=3,
    )

    expansion_records: tuple[
        ExpansionRecord,
        ...,
    ] = ()

    pool_exhausted: bool

    pool_digest: str = Field(
        pattern=HASH_PATTERN,
    )


class Budget(FrozenContractModel):
    """冻结候选集允许下游读取的上下文预算。"""

    max_files: int = Field(
        default=12,
        ge=1,
        le=60,
    )

    max_chars_per_file: int = Field(
        default=4000,
        ge=1,
    )

    max_symbols_per_file: int = Field(
        default=10,
        ge=1,
        le=50,
    )


class FileRef(FrozenContractModel):
    """冻结候选集中的真实文件引用。"""

    path: str = Field(
        min_length=1,
    )

    content_sha256: str = Field(
        pattern=HASH_PATTERN,
    )

    kind: FileKind

    symbols: tuple[str, ...] = Field(
        default=(),
        max_length=10,
    )

    line: int | None = Field(
        default=None,
        ge=1,
    )

    # 以下三个字段面向模型或人类，
    # 不参与 CandidateSet digest。
    reason: str = Field(
        min_length=1,
    )

    score: float

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    signals: tuple[
        SignalScore,
        ...,
    ] = ()

    selected_by: Literal[
        "baseline",
        "semantic_map",
        "human",
    ]


class CandidateSet(FrozenContractModel):
    """供并行分支只读消费的冻结候选文件集合。"""

    set_id: str = Field(
        pattern=r"^CS-\d{4}$",
    )

    pool_id: str = Field(
        pattern=r"^PL-\d{4}$",
    )

    commit: str = Field(
        min_length=1,
    )

    index_digest: str = Field(
        pattern=HASH_PATTERN,
    )

    spec_digest: str = Field(
        pattern=HASH_PATTERN,
    )

    # tuple 与 frozen 子模型共同保证候选集只读。
    files: tuple[
        FileRef,
        ...,
    ]

    truncated: bool

    budget: Budget

    set_digest: str = Field(
        pattern=HASH_PATTERN,
    )


class ContractReferenceError(ValueError):
    """契约引用与仓库事实不一致。"""


def validate_change_point_references(
    change_point: ChangePoint,
    *,
    existing_files: set[str],
    existing_symbols: set[str],
) -> None:
    """使用外部索引事实校验符号期望，不修改模型对象。

    Pydantic 构造器只能检查字段之间的结构关系。
    文件和符号是否真实存在，需要把仓库索引显式传入本函数。
    """

    target = change_point.target

    if target.expect == "existing":
        if target.name is None or target.name not in existing_symbols:
            raise ContractReferenceError("expect=existing 的符号不在索引中。")

        return

    if change_point.declares_new_file:
        if change_point.path in existing_files:
            raise ContractReferenceError("声明的新文件已经存在。")

        return

    if change_point.path not in existing_files:
        raise ContractReferenceError("新增符号的目标文件不存在。")

    if target.parent_symbol is not None and target.parent_symbol not in existing_symbols:
        raise ContractReferenceError("新增符号的父符号不在索引中。")
