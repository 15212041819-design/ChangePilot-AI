# CONTRACTS — ChangePilot AI 全链路契约规范

| 项目 | 内容 |
|---|---|
| 文档版本 | `1.0.0` |
| 契约版本常量 | `CONTRACT_VERSION = "1.0.0"` |
| 状态 | **已批准并冻结** |
| 适用范围 | 需求层、ACRegistry、符号与变更点、候选池、候选集、Guard、分析分支、并行编排、风险、审核、实施计划、评测与统一报告 |
| 当前已落地代码 | `backend/app/schemas/contracts.py`、`backend/app/services/ac_registry.py`、`backend/app/tools/contract_digest.py` |
| 当前契约测试 | `backend/tests/test_contracts.py`、`backend/tests/test_ac_registry.py`、`backend/tests/test_contract_digest.py` |
| 不适用范围 | LLM 提示词具体措辞、模型供应商、模型版本、重试退避参数和报告视觉样式 |

任何破坏性字段变更、字段语义变更或 digest 输入变更，都必须执行第 13 章规定的契约变更流程。

---

## 0. 规范性约定

### 0.1 规范性关键词

本文档中的关键词采用 RFC 2119 语义。

| 关键词 | 含义 |
|---|---|
| 必须 / MUST | 无条件要求，违反时契约测试必须失败 |
| 禁止 / MUST NOT | 无条件禁止 |
| 应当 / SHOULD | 推荐要求，偏离时必须在代码注释和变更说明中解释原因 |
| 可以 / MAY | 可选能力 |

### 0.2 核心术语

| 术语 | 定义 |
|---|---|
| CandidatePool | 宽召回候选池，允许 Semantic Mapping Agent 通过受控工具扩展 |
| CandidateSet | 从候选池选择并冻结后的文件集合，是并行分析分支唯一允许消费的文件来源 |
| context freeze | 将 CandidatePool 转换为不可变 CandidateSet 并计算 digest 的过程 |
| expansion | Semantic Mapping Agent 通过白名单查询工具向 CandidatePool 增加真实索引结果 |
| ChangePoint | 带文件、符号、变更类型、AC 引用和证据的候选变更点 |
| finding | 任一分析分支输出的单条结论 |
| blocker | 阻止实施继续执行的结论，必须转换成 PlanGate |
| gate | 实施步骤开始前必须解除的条件 |
| degraded | 分支仅完成部分分析，结果可以使用，但必须公开覆盖不足 |
| coverage | 分支已分析单元与分配单元的比值 |
| normalize | 不改变语义的机械修正 |
| repair | 需要原 Agent 重新判断一次的可修复错误 |
| fail | 事实不成立、越权或二次修复失败 |
| digest | 用于标识上游事实快照和检测输入漂移的稳定 SHA-256 摘要 |

### 0.3 通用数据规则

- 所有路径必须是仓库相对路径。
- 路径必须使用 POSIX 分隔符 `/`。
- 路径不得以 `./` 开头。
- 路径必须保留磁盘真实大小写。
- 路径必须进行 Unicode NFC 归一化。
- 所有时间必须使用 UTC ISO-8601。
- 所有 SHA-256 必须使用：

```text
sha256:<64 位小写十六进制>
```

- 对外展示的浮点数统一保留 3 位小数。
- 自然语言理由、置信度和模型评分不得进入 CandidateSet digest。
- ID 必须由确定性代码分配，禁止由 LLM 自行生成或修改。

---

## 1. Agent 与确定性引擎定义

### 1.1 Agent 定义

**C-TERM-01**

本项目中的 Agent 是满足以下条件的 LLM 步骤：

1. 具有独立提示词；
2. 具有结构化输出契约；
3. 具有明确的输入和工具边界；
4. 具有可声明的失败模式；
5. 输出必须经过 Guard 或 Pydantic 校验。

Agent 不要求拥有无限循环、自主规划或自由调用工具的能力。

### 1.2 能力分类

**C-TERM-02**

下列能力禁止被称为 Agent：

- AST 索引；
- 依赖图查询；
- 影响传播；
- 测试映射；
- 风险规则；
- 实施计划排序；
- Guard；
- 报告模板渲染。

这些能力必须归类为 `engine`、`tool`、`guard` 或 `node`。

### 1.3 全链路能力分类

| 能力 | 实现方式 | 分类 |
|---|---|---|
| 需求结构化与修订 | LLM + Pydantic + ACRegistry | Agent |
| 语义映射 | LLM + 受控扩池工具 + context freeze | Agent |
| 语义与兼容性审核 | LLM + 调用形态证据 | Agent |
| Python 符号索引 | AST | tool |
| Python 依赖图 | import 图与反向依赖 | tool |
| 影响传播 | AST + 依赖图 | engine |
| 测试策略映射 | AST + 导入反查 + 映射规则 | engine |
| 风险分析 v1 | missing caller + schema change | engine |
| 实施计划 | 规则 + 拓扑排序 + PlanGate | engine |
| 事实真实性校验 | 文件系统 + SymbolIndex | Guard |
| 统一报告 | 结构化数据 + 模板 | engine |

### 1.4 LLM 调用边界

**C-TERM-03**

v1 只允许以下三个步骤调用 LLM：

1. Requirement Agent；
2. Semantic Mapping Agent；
3. Semantic Review Agent。

确定性 engine 禁止直接调用 LLM。

### 1.5 分层依赖方向

```text
schemas/contracts.py
        ↑
services/ac_registry.py
tools/contract_digest.py
        ↑
tools / engines / guards
        ↑
agents
        ↑
graph
        ↑
report
```

**C-TERM-04**

- `contracts.py` 禁止 import Agent、Graph 或业务服务。
- engine 禁止 import Agent。
- report 只能消费结构化契约对象。
- Agent 禁止直接写入冻结后的 CandidateSet。
- AST 索引、依赖图和文件正文缓存禁止进入 LangGraph state。

---

## 2. 总体工作流

```text
用户需求
   │
   ▼
Requirement Agent
   │
   ├── ACRegistry 分配稳定 AC ID
   │
   └── RequirementSpec
           │
           ▼
确定性 context retrieve
           │
           └── CandidatePool
                   │
                   ▼
Semantic Mapping Agent
   │       │
   │       └── 受控扩池工具
   │
   ▼
context freeze
   │
   └── CandidateSet（冻结）
           │
           ├──────────────┬──────────────┐
           ▼              ▼              ▼
       Impact Engine   Test Engine    Risk Engine
           │              │              │
           └──────────────┴──────────────┘
                          │
                          ▼
                      Guard
                          │
                          ▼
                Semantic Review Agent
                          │
                          ▼
             Implementation Plan Engine
                          │
                          ▼
                 Unified Report Engine
```

### 2.1 共享证据，不共享结论

**C-FLOW-01**

Impact、Test、Risk 三个分支可以共享：

- RequirementSpec；
- CandidateSet；
- SymbolIndex；
- DependencyGraph；
- 中性的需求动作与实体提示；
- 上游 digest。

三个分支禁止把其他分支的结论当成自己的事实输入。

### 2.2 候选集是唯一文件边界

**C-FLOW-02**

CandidateSet 冻结后：

- 三个并行分支只能分析 CandidateSet 中的文件；
- Agent 不得直接添加 CandidateSet 文件；
- 如需扩池，必须回到 CandidatePool；
- 扩池完成后必须重新 freeze；
- 重新 freeze 后必须重新计算 set digest。

---

## 3. 需求契约

## 3.1 RequirementSpec

**C-REQ-01**

RequirementSpec 是全链路唯一需求词汇表。

测试项、风险项、审核结论和实施步骤只能通过 AC ID 引用验收标准，禁止复制后形成另一套不可追踪的验收标准文本。

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `spec_id` | str | 是 | `RS-\d{4}` |
| `spec_revision` | int | 是 | 大于等于 1 |
| `summary` | str | 是 | 1～200 字 |
| `change_type` | enum | 是 | `feature/fix/refactor/perf/config/remove/unknown` |
| `acceptance_criteria` | tuple[AcceptanceCriterion] | 是 | 至少 1 条 |
| `entity_hints` | tuple[EntityHint] | 是 | 至少 1 条 |
| `symbol_hints` | tuple[SymbolRef] | 否 | 默认空 |
| `constraints` | tuple[str] | 否 | 最多 10 条 |
| `assumptions` | tuple[Assumption] | 否 | 最多 10 条 |
| `scope_hints` | ScopeHints | 是 | 见 3.6 |
| `source_text` | str | 是 | 用户原始需求，禁止被改写后覆盖 |
| `spec_digest` | str | 是 | `sha256:` 格式 |

### 3.2 AcceptanceCriterionDraft

**C-AC-01**

Requirement Agent 首先输出不包含 ID 的草稿。

| 字段 | 类型 | 约束 |
|---|---|---|
| `description` | str | 1～200 字、可验证 |
| `verification` | enum | `test/manual/static` |
| `priority` | enum | `P0/P1/P2` |

LLM 禁止生成 `AC-xxx`。

### 3.3 AcceptanceCriterion

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `id` | str | 是 | `AC-\d{3}`，由 ACRegistry 分配 |
| `description` | str | 是 | 可验证表述 |
| `verification` | enum | 是 | `test/manual/static` |
| `priority` | enum | 是 | `P0/P1/P2` |
| `status` | enum | 是 | `active/dropped/superseded/merged` |
| `supersedes` | tuple[str] | 否 | 合并时记录被当前 AC 替代的其他 ID |
| `derived_from` | str 或 null | 否 | 拆分产生的新 AC 指向原 AC |

**C-AC-02**

AC ID 在整个分析线程生命周期中不得复用。

已经进入 `dropped`、`superseded` 或 `merged` 状态的 ID 仍然必须保留在 Registry 中。

### 3.4 ACRegistry

ACRegistry 状态：

```text
ACRegistryState
├── version
├── criteria
└── warnings
```

每条 Registry 记录包含：

```text
ACRegistryRecord
├── criterion
├── normalized_description
├── first_seen_revision
└── history
```

### 3.4.1 描述归一化

**C-AC-03**

归一化顺序必须固定：

1. `strip()`；
2. 连续空白折叠为单空格；
3. `lower()`；
4. 去除尾部标点；
5. 不改变正文内部语义。

### 3.4.2 匹配算法

旧 AC 和新草稿之间使用：

```python
difflib.SequenceMatcher(
    None,
    old_normalized,
    new_normalized,
).ratio()
```

冻结阈值：

```python
SIMILARITY_THRESHOLD = 0.80
DUPLICATE_THRESHOLD = 0.90
```

### 3.4.3 一对一改写

**C-AC-04**

当新草稿与一个 active 旧 AC 的相似度大于等于 `0.80`：

- 必须沿用旧 ID；
- 更新描述；
- history 追加 `rewritten`；
- 禁止生成新 ID。

### 3.4.4 拆分

**C-AC-05**

一条旧 AC 拆分为多条新 AC 时：

- 与旧 AC 最接近的新条目继承旧 ID；
- 继承旧 ID 的条目继续保持 `active`；
- 继承旧 ID 的 history 记录 `split`；
- 其他拆分条目获得新 ID；
- 其他拆分条目的 `derived_from` 指向原 ID；
- 原 ID 不进入独立的 `split` 状态。

### 3.4.5 合并

**C-AC-06**

多条旧 AC 合并为一条新 AC 时：

- 新 AC 继承参与合并的最小 ID；
- 继承的最小 ID 保持 `active`；
- 其他旧 ID 标记为 `merged`；
- active AC 的 `supersedes` 记录其他被合并 ID；
- active AC 自身 ID 不得出现在 `supersedes` 中。

### 3.4.6 删除

**C-AC-07**

未被新版本继承的旧 active AC：

- 标记为 `dropped`；
- ID 永不复用；
- 必须出现在报告的“本次修订删除或未覆盖的验收标准”中。

### 3.4.7 重复草稿

**C-AC-08**

两条新草稿之间相似度大于等于 `0.90` 时：

- 只允许分配一个 ID；
- 保留优先级更高的条目；
- 优先级顺序为 `P0 > P1 > P2`；
- Registry 必须记录 warning。

### 3.4.8 幂等性

**C-AC-09**

相同 revision 和完全相同草稿再次提交时：

- Registry 内容不得变化；
- Registry version 不得增加；
- 不得重复写 history。

### 3.5 Assumption

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | str | `AS-\d{3}` |
| `text` | str | 不得为空 |
| `status` | enum | `accepted/rejected/unconfirmed` |
| `impact` | tuple[str] | 受影响 AC ID |

人工审核可以修改 Assumption 的 `status`。

修改 Assumption 的 `text` 必须视为新假设，并分配新 ID。

### 3.6 ScopeHints

| 字段 | 类型 | 默认值 |
|---|---|---|
| `include` | tuple[str] | 空，表示全仓 |
| `exclude` | tuple[str] | 保护目录列表 |
| `target_modules` | tuple[str] | 空 |
| `allow_breaking` | bool | `false` |
| `max_files` | int | `12` |
| `max_chars_per_file` | int | `4000` |

默认排除：

```text
.git
node_modules
.venv
venv
__pycache__
dist
build
.mypy_cache
```

**C-REQ-02**

`exclude` 禁止为空，LLM 不得移除全部保护目录。

### 3.7 EntityHint

| 字段 | 类型 | 说明 |
|---|---|---|
| `concept_id` | str | `EH-\d{3}` |
| `concept` | str | 自然语言概念 |
| `lexical_variants` | tuple[str] | 词形和近义表达 |
| `symbol_candidates` | tuple[str] | 仅用于检索的候选符号 |
| `role` | enum | `subject/object/action_target/constraint/quantity` |
| `in_repo_vocab` | bool | 必须由代码计算 |
| `unverified` | tuple[str] | 未在索引中命中的候选名称 |

**C-REQ-03**

- LLM 可以提出 `symbol_candidates`；
- LLM 不得声明该符号真实存在；
- `in_repo_vocab` 和 `unverified` 必须由 SymbolIndex 计算；
- `unverified` 不得作为高权重强证据。

### 3.8 spec digest 输入

spec digest 必须包含：

- CONTRACT_VERSION；
- spec_id；
- spec_revision；
- registry digest；
- change_type；
- AC 的 ID、归一化描述、verification、priority、status；
- Assumption 的 ID、status、impact；
- EntityHint 的 concept_id、concept、lexical_variants；
- SymbolRef 的 name、parent_symbol、expect、kind；
- constraints；
- ScopeHints 全字段。

spec digest 必须排除：

- summary 的自然语言措辞；
- Assumption.text；
- source_text；
- 模型置信度；
- 时间戳和耗时。

**C-REQ-04**

AC priority 会改变后续门禁和实施行为，因此必须进入 spec digest。

---

## 4. 符号与变更点契约

### 4.1 SymbolRef

| 字段 | 类型 | 约束 |
|---|---|---|
| `name` | str 或 null | 新增整个模块时允许为 null |
| `parent_symbol` | str 或 null | 新增成员时指向父类或外层符号 |
| `expect` | enum | `existing/new` |
| `kind` | enum | `class/function/method/attribute/const/module/config_key` |

**C-SYM-01**

`expect` 只允许出现在 SymbolRef 上。

ChangePoint 禁止再次平铺：

- `symbol`；
- `expect`。

### 4.2 existing 符号

**C-SYM-02**

`expect = existing` 时：

- name 不得为空；
- 符号必须存在于 SymbolIndex；
- kind 必须与索引事实一致。

Pydantic 只负责字段结构校验。

符号是否真实存在必须由 Guard 或 `validate_change_point_references()` 使用外部索引校验。

### 4.3 new 符号

**C-SYM-03**

`expect = new` 时，目标只能用于 `change_type = add`。

新增符号必须满足以下至少一项：

1. 目标文件已存在；
2. parent_symbol 已存在；
3. ChangePoint 声明 `declares_new_file = true`。

当 `declares_new_file = true` 时，目标路径在分析快照中不得已经存在。

### 4.4 CallerRef

| 字段 | 类型 | 说明 |
|---|---|---|
| `path` | str | 调用点文件 |
| `symbol` | str | 调用点所在函数或方法 |
| `line` | int | 1-based |
| `call_style` | enum | `pos/kw/mixed/dynamic/unknown` |
| `positional_count` | int 或 null | 已知的位置参数数量 |
| `has_kwargs` | bool | 是否使用 `**kwargs` |

**C-SYM-04**

`call_style = dynamic` 时，`positional_count` 必须为 null。

### 4.5 禁止 signature_after

**C-SYM-05**

契约中禁止出现 `signature_after`。

改前影响分析没有真实补丁，因此不能假装已经知道变更后的函数签名。

兼容性风险只能根据以下事实评估：

- 当前签名；
- 调用方数量；
- call_style；
- positional_count；
- has_kwargs；
- 动态调用情况；
- 用户是否允许 breaking change。

v1 不提供 `proposed_signature`。

### 4.6 EvidencePointer

| 字段 | 类型 | 约束 |
|---|---|---|
| `path` | str | 仓库相对路径 |
| `line` | int | 大于等于 1 |
| `end_line` | int 或 null | 不得小于 line |
| `kind` | enum | `def/call/import/config/schema/test/comment` |
| `excerpt_hash` | str 或 null | `sha256:` 格式 |

EvidencePointer 必须可以通过文件系统和 SymbolIndex 验证。

### 4.7 ChangePoint

| 字段 | 类型 | 约束 |
|---|---|---|
| `cp_id` | str | `CP-\d{4}` |
| `path` | str | 仓库相对路径 |
| `target` | SymbolRef | 唯一符号目标 |
| `change_type` | enum | `add/signature/semantic/delete` |
| `declares_new_file` | bool | 仅 add 允许为 true |
| `callers` | tuple[CallerRef] | 默认空 |
| `callers_verified_empty` | bool | 无调用方时说明反查已经完成 |
| `ac_ids` | tuple[str] | 至少一个 active AC |
| `evidence` | tuple[EvidencePointer] | 至少一条 |
| `confidence` | float | 0～1，不进入 digest |
| `source` | enum | `impact/test/risk/human` |
| `rule_ids` | tuple[str] | 确定性规则 ID |

### 4.8 ChangePoint 合法性矩阵

| expect / change_type | add | signature | semantic | delete |
|---|---:|---:|---:|---:|
| existing | 合法 | 合法 | 合法 | 合法 |
| new | 合法 | 非法 | 非法 | 非法 |

**C-CP-01**

`expect = new` 与非 add 类型组合时，必须在构造阶段拒绝。

**C-CP-02**

`declares_new_file = true` 只允许与 add 组合。

**C-CP-03**

`name = null` 只允许表示模块级 add：

- target.kind 必须为 module；
- parent_symbol 必须为 null。

**C-CP-04**

signature 变更必须满足：

- callers 非空；或
- `callers_verified_empty = true`。

---

## 5. 候选池与候选集

### 5.1 结构关系

```text
确定性召回
    │
    ▼
CandidatePool
    │
    ├── Semantic Mapping 重排
    └── 白名单工具受控扩池
            │
            ▼
context freeze
            │
            ▼
CandidateSet
```

### 5.2 CandidatePool

| 字段 | 类型 | 约束 |
|---|---|---|
| `pool_id` | str | `PL-\d{4}` |
| `commit` | str | 分析快照 commit |
| `index_digest` | str | 索引 digest |
| `spec_digest` | str | RequirementSpec digest |
| `entries` | tuple[PoolEntry] | 最多 60 条 |
| `expansion_rounds_used` | int | 0～3 |
| `expansion_records` | tuple[ExpansionRecord] | 受控扩池记录 |
| `pool_exhausted` | bool | 候选不足时为 true |
| `pool_digest` | str | 候选池内容 digest |

CandidatePool 是某个时点的不可变快照。

扩池操作不得原地修改对象，必须根据旧 Pool 构造新的 Pool 快照。

### 5.3 PoolEntry

| 字段 | 类型 | 说明 |
|---|---|---|
| `path` | str | 真实仓库路径 |
| `kind` | enum | `source/test/config/doc` |
| `signals` | tuple[SignalScore] | 确定性召回信号 |
| `pre_score` | float | 不进入 digest |
| `symbols` | tuple[str] | 最多 10 个 |
| `source` | enum | `baseline/llm_expansion` |
| `requested_by` | str 或 null | 扩池请求者 |
| `expansion_round` | int 或 null | 扩池轮次 |

允许的 signal 名称：

```text
keyword
symbol
dep_closure
cochange
testmap
```

禁止使用未注册的 signal 名称。

### 5.4 受控扩池工具

Semantic Mapping Agent 只能使用以下工具：

| 工具 | 参数限制 | 返回限制 |
|---|---|---|
| `search_identifiers` | terms 最多 5 个，limit 最大 10 | 最多 10 条 |
| `expand_callers` | depth 只能为 1 或 2 | 最多 10 条 |
| `read_signature` | 一个真实符号 | 一条签名和 docstring 首行 |
| `list_module_symbols` | 一个索引中的真实路径 | 最多 20 条 |

### 5.5 扩池冻结预算

```text
MAX_POOL_SIZE = 60
MAX_EXPANSION_ROUNDS = 3
MAX_EXPANSION_QUERIES = 12
MAX_EXPANSION_ADDITIONS = 20
MAX_ADDITIONS_PER_ROUND = 10
```

**C-POOL-01**

超出预算的查询：

- 必须被拒绝；
- 必须记录 rejection；
- 禁止静默忽略；
- 禁止直接写入 CandidateSet。

### 5.6 ExpansionRecord

| 字段 | 类型 | 说明 |
|---|---|---|
| `round` | int | 1～3 |
| `tool` | enum | 白名单工具 |
| `args_digest` | str | 查询参数摘要 |
| `added_paths` | tuple[str] | 新增真实路径 |
| `added_symbols` | tuple[str] | 新增真实符号 |
| `rejected` | tuple[str] | 被拒请求和原因 |

### 5.7 CandidateSet

| 字段 | 类型 | 约束 |
|---|---|---|
| `set_id` | str | `CS-\d{4}` |
| `pool_id` | str | 来源池 ID |
| `commit` | str | 分析 commit |
| `index_digest` | str | 索引摘要 |
| `spec_digest` | str | 需求摘要 |
| `files` | tuple[FileRef] | 不超过 budget.max_files |
| `truncated` | bool | 发生截断时必须为 true |
| `budget` | Budget | 上下文预算 |
| `set_digest` | str | 冻结集合摘要 |

### 5.8 FileRef

| 字段 | 是否进入 set digest |
|---|---:|
| path | 是 |
| content_sha256 | 是 |
| kind | 是 |
| symbols | 是 |
| line | 是 |
| selected_by | 是 |
| reason | 否 |
| score | 否 |
| confidence | 否 |
| signals | 否 |

### 5.9 选择和冻结规则

**C-SET-01**

候选排序必须使用：

```text
score 降序
path 升序
```

禁止依赖：

- dict 插入顺序；
- set 迭代顺序；
- 对象内存地址；
- 并行任务完成顺序。

**C-SET-02**

CandidateSet 和内部 FileRef 必须冻结。

任何下游分支尝试修改：

- files；
- FileRef；
- budget；
- digest；

都必须抛出异常。

**C-SET-03**

三个并行分支消费的以下字段必须完全一致：

- commit；
- set_digest；
- spec_digest；
- index_digest。

---

## 6. digest 规范

### 6.1 标准算法

```python
def canonical_digest(kind, payload):
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

    return "sha256:" + sha256(
        text.encode("utf-8"),
    ).hexdigest()
```

### 6.2 内容归一化

| 内容 | 规则 |
|---|---|
| 路径 | POSIX、去 `./`、NFC、保留真实大小写 |
| 字典 | `sort_keys=True` |
| 无语义顺序的列表 | 按稳定键排序 |
| 文本 | `strip()` 并折叠空白 |
| 内容字节 | 去 UTF-8 BOM，CRLF 转 LF |
| 浮点信号 | 先稳定排序，再 `math.fsum`，最后 `round(..., 3)` |
| 缺失值 | 使用 null，不用空字符串代替 |

### 6.3 必须排除的字段

以下字段禁止进入 CandidateSet digest：

- reason；
- score；
- confidence；
- pre_score；
- summary 自然语言措辞；
- source_text；
- Assumption.text；
- 时间戳；
- elapsed_ms；
- excerpt_hash。

### 6.4 priority 的冻结规则

priority 不采用“一律排除”策略。

必须按其是否改变程序行为决定：

| 位置 | 是否进入 digest |
|---|---:|
| AC priority | 是，进入 spec digest |
| finding severity/priority | 是，进入 branch digest |
| PlanGate severity | 是，进入 plan/report digest |
| 模型 confidence | 否 |
| 候选 score | 否 |
| CandidatePool pre_score | 否 |

### 6.5 Registry digest

包含：

- Registry version；
- AC ID；
- 归一化描述；
- AC 状态。

### 6.6 Spec digest

包含第 3.8 节规定的字段。

spec revision 变化必须导致 spec digest 变化。

### 6.7 Pool digest

包含：

- commit；
- index digest；
- spec digest；
- 排序后的 path；
- kind；
- 规范化后的确定性 signals；
- symbols；
- source；
- expansion metadata；
- ExpansionRecord 的 round、tool、added_paths；
- pool_exhausted。

排除：

- pool_id；
- pre_score；
- 模型理由；
- 模型置信度。

### 6.8 Set digest

包含：

- commit；
- index digest；
- spec digest；
- budget；
- truncated；
- 排序后的 path；
- content_sha256；
- kind；
- symbols；
- line；
- selected_by。

排除：

- set_id；
- pool_id；
- reason；
- score；
- confidence；
- signals。

### 6.9 嵌套传播

```text
registry_digest
      │
      ▼
spec_digest
      │
      ▼
set_digest
```

因此：

```text
spec_revision 变化
→ spec_digest 变化
→ set_digest 变化
→ 下游旧分析结果失效
```

这是有意设计，不是 bug。

---

## 7. Guard 契约

### 7.1 三层处理边界

Guard 只有三种处理结果：

| 层级 | 含义 | 是否计入 invalid | 是否消耗 repair |
|---|---|---:|---:|
| normalize | 机械等价变换 | 否 | 否 |
| repair | 需要原 Agent 重新判断 | 是 | 是，最多一次 |
| fail | 事实错误、越权或修复失败 | 是 | 不适用 |

### 7.2 normalize 白名单

只允许以下机械修正：

1. Windows 路径转换为 POSIX；
2. 去除 `./` 和重复分隔符；
3. 在大小写不敏感文件系统上校正为真实大小写；
4. 对重复 finding 去重；
5. 按稳定排序键重新排序。

重复 finding 的身份键：

```text
path
symbol
change_type
source
```

发生重复时，可以保留最高 confidence 的条目，但 confidence 不进入 digest。

### 7.3 repair 白名单

repair 仅允许以下两类：

1. 文件和符号真实存在，但行号没有落在符号范围内；
2. 符号限定名不一致，且 normalize 无法唯一确定正确名称。

### 7.4 repair 适用范围

**C-GRD-01**

repair 只允许用于 `branch_kind = agent`。

确定性 engine 出现相同错误时必须直接 fail，因为确定性 engine 重跑仍会产生同样结果。

### 7.5 repair 次数

```text
repair_attempt 初始值 = 0
最多允许一次 repair
```

repair 后仍不通过：

- 必须 fail；
- 禁止第三次尝试。

### 7.6 fail 条件

命中以下任意条件必须 fail：

1. 文件路径不存在；
2. 路径越出仓库根目录；
3. `expect=existing` 的符号不存在；
4. `expect=new` 的父符号和目标文件都不存在，且没有声明新文件；
5. 分支使用的 set/spec/index digest 不一致；
6. 分支尝试修改 CandidateSet；
7. 分支绕过 CandidatePool 直接扩展 CandidateSet；
8. 引用不存在或非 active 的 AC；
9. EvidencePointer 的路径或行号不存在；
10. `kind=def` 的 EvidencePointer 不在目标符号范围内；
11. CallerRef.path 不存在；
12. invalid ratio 大于 0.20；
13. repair 后仍然失败；
14. signature 变更既没有 callers，也没有声明 callers_verified_empty；
15. SymbolRef 和 change_type 违反合法性矩阵。

### 7.7 判定规则

| verdict | 条件 |
|---|---|
| pass | invalid_count 为 0，且上游 digest 一致 |
| repair | invalid ratio 在 `(0, 0.20]`，全部错误可修复，branch_kind 为 agent，且尚未 repair |
| fail | 命中任意 fail 条件，或 invalid ratio 大于 0.20，或 repair 后仍失败 |

边界规定：

```text
invalid_ratio == 0.20 → 可以 repair
invalid_ratio > 0.20  → fail
```

### 7.8 GuardReport

| 字段 | 类型 |
|---|---|
| branch | `impact/test/risk/map/review` |
| branch_kind | `agent/engine` |
| checked_count | int |
| normalize_actions | tuple[NormalizeAction] |
| invalid | tuple[InvalidFinding] |
| invalid_ratio | float |
| repair_attempt | int |
| verdict | `pass/repair/fail` |
| blocking_reasons | tuple[str] |
| repair_hints | tuple[str] |
| digests_echo | Digests |
| guard_version | str |

Guard 只能返回 repair hint，禁止直接替 Agent 生成“正确答案”。

---

## 8. 并行分支与覆盖率

### 8.1 BranchResult

| 字段 | 类型 |
|---|---|
| branch | `impact/test/risk` |
| status | `success/degraded/failed` |
| findings | tuple |
| errors | tuple[BranchError] |
| coverage | Coverage |
| consumed_digests | Digests |
| degraded_reasons | tuple[str] |
| elapsed_ms | int |

### 8.2 Coverage

```text
Coverage
├── assigned
├── analyzed
├── ratio
├── unit
├── missing
└── note
```

### 8.3 三个分支的覆盖分母

| 分支 | unit | assigned | analyzed |
|---|---|---|---|
| impact | file | CandidateSet 中 source 文件数 | 已形成变更结论或有证据的“无需修改”结论的文件数 |
| test | file | CandidateSet 中 source 文件数 | 已完成测试映射或明确判定需要新增测试的文件数 |
| risk | file | CandidateSet 中 source 文件数 | 已完成 v1 风险扫描并给出规则化结论的文件数 |

风险分支禁止把 Impact 分支产生的 ChangePoint 数作为并行阶段的初始分母，否则 Risk 将依赖 Impact 结论，破坏三路独立性。

如 Risk 在 fan-in 后对已验证 ChangePoint 进行第二阶段审核，应单独使用 `review_coverage`，不得覆盖原始 branch coverage。

### 8.4 coverage 计算

```python
ratio = round(analyzed / assigned, 3)
```

当 assigned 为 0 时：

```text
ratio = 1.0
note = "no_items"
status = success
```

### 8.5 状态规则

| ratio | status |
|---:|---|
| 1.0 | success |
| 大于 0 且小于 1 | degraded |
| 0 且 assigned 大于 0 | failed |

degraded 时：

- degraded_reasons 不得为空；
- 报告必须显示覆盖率；
- 禁止渲染成“没有发现风险”。

### 8.6 并行异常降级

每个分支必须独立捕获异常，并转化为：

- BranchError；
- degraded；或
- failed。

禁止单个分支异常导致整个 LangGraph superstep 失败。

### 8.7 State 与资源容器

允许进入 state：

- RequirementSpec；
- ACRegistryState；
- digest；
- CandidatePool 摘要；
- CandidateSet；
- BranchResult；
- GuardReport；
- Semantic Review；
- Implementation Plan；
- Report reference；
- errors。

禁止进入 state：

- SymbolIndex 实例；
- DependencyGraph 实例；
- TestMap 实例；
- Workspace；
- GitRepo；
- 文件正文缓存；
- 打开的文件句柄。

资源对象必须放在运行期容器：

```text
resources
└── resource_id
    ├── symbol_index
    ├── dependency_graph
    ├── test_map
    ├── workspace
    └── git
```

state 只保存：

```text
index_resource_id
index_digest
```

### 8.8 稳定 fan-in

统一排序键：

```text
priority_rank
path
symbol_or_empty
finding_id
```

priority rank：

```text
blocker = 0
major   = 1
minor   = 2
info    = 3
```

并行任务完成顺序不得影响最终报告顺序。

---

## 9. 风险与语义审核

### 9.1 风险引擎 v1 范围

v1 只把以下两类风险作为确定性能力：

1. `missing_caller`；
2. `schema_change`。

### 9.2 missing caller

当反向依赖图中存在调用方，但候选集或实施计划遗漏该调用方时，可以产生 missing caller finding。

该结论必须包含：

- 被调用符号；
- 调用方路径；
- 调用行号；
- dependency rule ID；
- 对应 ChangePoint 或 AC。

### 9.3 schema change

v1 可以扫描：

- dataclass 字段；
- TypedDict 字段；
- Pydantic BaseModel 字段；
- attrs 类字段。

无法解析的动态 schema 必须进入“需人工确认”，不得假装没有风险。

### 9.4 api break

图只能证明存在调用方，不能独立证明一定破坏兼容性。

`api_break` 只有同时满足以下条件，才能升级为 blocker：

1. 目标是 `expect=existing` 的符号；
2. change_type 为 `delete`；
3. 已验证存在真实调用方；
4. `scope_hints.allow_breaking = false`。

其他签名或语义变更最多为：

- major；
- gated；
- needs_human_confirmation。

不得仅因为“存在位置参数调用”就自动判定 blocker。

### 9.5 并发与事务风险

v1 不把以下内容作为 blocker：

- 关键词命中的锁风险；
- 事务边界推测；
- 并发竞态推测；
- 缺少源码证据的回滚风险。

如输出此类内容，必须满足：

```text
confidence < 0.4
category = needs_human_confirmation
```

并且禁止进入自动 blocker。

### 9.6 Semantic Review Agent 输入

Semantic Review Agent 可以读取：

- 已通过 Guard 的 ChangePoint；
- CallerRef 调用形态；
- Risk Engine finding；
- Test Engine finding；
- RequirementSpec；
- 所有证据指针；
- coverage 和 degraded 状态。

禁止读取或虚构：

- signature_after；
- 未冻结候选文件；
- 不在索引中的路径；
- 未经 Guard 的 ChangePoint。

### 9.7 Semantic Review Agent 职责

Semantic Review 负责：

- 判断不同分支结论是否矛盾；
- 检查 active AC 是否有实施和测试覆盖；
- 判断调用形态是否使建议具有明显兼容性风险；
- 将证据不足项降级为人工确认；
- 生成审核结论和 gate 建议。

Semantic Review 禁止重新实现：

- 文件存在性校验；
- SymbolIndex 查询；
- 路径规范化；
- CandidateSet digest 校验。

这些属于 Guard。

---

## 10. 实施计划与门禁

### 10.1 PlanGate

| 字段 | 类型 |
|---|---|
| gate_id | `PG-\d{3}` |
| severity | `blocker/major` |
| reason | str |
| required_action | str |
| evidence_ids | tuple[str] |
| ac_ids | tuple[str] |
| blocks_step_ids | tuple[str] |

required_action 必须是可执行动作，禁止使用：

- “请注意”；
- “建议关注”；
- “可能需要处理”。

### 10.2 ImplementationStep

| 字段 | 类型 | 约束 |
|---|---|---|
| step_id | str | `PS-\d{3}` |
| order | int | 从 1 开始 |
| status | enum | `ready/gated/blocked` |
| targets | tuple[str] | ChangePoint ID，可以为空 |
| required_tests | tuple[str] | 测试项 ID |
| ac_covered | tuple[str] | active AC ID |
| gate_ids | tuple[str] | 关联 PlanGate |
| depends_on | tuple[str] | 前置 step ID |
| cycle_detected | bool | 默认 false |

### 10.3 targets 为空的唯一合法场景

**C-PLAN-01**

正常 ready 或 gated 步骤必须至少包含一个已通过 Guard 的 ChangePoint。

blocked 步骤可以没有 targets，但必须满足：

- gate_ids 非空；
- ac_covered 非空；
- gate reason 说明没有可验证 ChangePoint；
- required_action 说明需要补充什么证据。

### 10.4 AC 实施覆盖

每条 active AC 必须满足以下至少一项：

1. 被一个已通过 Guard 的 ChangePoint 覆盖；
2. 自动生成一个 blocked ImplementationStep。

未找到 ChangePoint 时，默认 gate reason：

```text
证据不足：未找到与该验收标准对应的已验证变更点。
```

### 10.5 门禁传播

如果某步骤依赖 gated 或 blocked 步骤：

- 依赖步骤不得为 ready；
- blocker 必须向下游传播；
- 报告必须显示门禁传播链。

### 10.6 排序规则

步骤按依赖图进行拓扑排序：

1. 被依赖模块先修改；
2. 同层 path 升序；
3. 测试步骤位于对应代码步骤之后；
4. 循环依赖时禁止死循环；
5. 循环组按 path 升序；
6. 循环组标记 `cycle_detected = true`；
7. 循环组默认不得自动进入 ready。

---

## 11. 评测契约

### 11.1 快照纪律

评测某个提交 `commit` 时：

```text
分析输入 = commit^
ground truth = commit 的 diff
```

禁止使用已经包含答案的 HEAD 作为分析输入。

### 11.2 Worktree

评测必须使用：

```bash
git worktree add <temp-directory> <commit>^
```

禁止在用户当前工作区执行 checkout。

评测结束后必须：

- 移除临时 worktree；
- 保证原工作区 HEAD 不变；
- 保证原工作区 git status 不变。

### 11.3 索引缓存

SymbolIndex 必须在 worktree 快照中重建。

缓存 key 必须至少包含：

```text
repository identity
commit
parser version
scope digest
```

禁止复用当前工作区或其他 commit 的索引。

### 11.4 co-change 时间边界

分析 commit 时，co-change 只能使用：

```bash
git log <commit>^ --name-only
```

co-change 特征来源禁止包含：

- 答案 commit 本身；
- 答案 commit 的任何后代。

### 11.5 cochange leak check

每次评测必须输出：

```text
cochange_leak_check: pass
```

自检的对象是“特征历史中是否出现答案 commit”，不是“答案文件名是否出现”。

同一个文件在更早历史中出现是合法的。

如果答案 commit 或其后代进入特征来源：

```text
cochange_leak_check: fail
```

本次评测结果必须作废。

### 11.6 样本规则

样本必须满足：

- 非 merge；
- 改动文件数为 2～8；
- 非纯测试提交；
- commit message 长度大于 15；
- 非纯格式化；
- 非批量重命名；
- 人工检查没有明显夹带无关改动。

### 11.7 数据划分

v1 使用：

```text
开发集：10 条
留出集：5 条
```

5 条留出集只作为回归护栏：

- 以前命中的样本不得退化成未命中；
- 禁止用它调权重；
- 禁止用它选择提示词；
- 禁止根据 5 条样本宣称统计显著。

需要进行正式模型或架构选择时，样本数应扩充至至少 30 条。

### 11.8 指标

| 指标 | 定义 |
|---|---|
| pool_recall@50 | CandidatePool 命中真实文件数 / 真实源码文件数 |
| file_recall@12 | CandidateSet 命中真实文件数 / 真实源码文件数 |
| cross_module_recall | 跨模块真实文件的命中比例 |
| hallucination_rate | Guard 判定虚构条目数 / 过滤前全部条目数 |
| failure_rate | 分析失败样本数 / 总样本数 |
| index_coverage | 成功解析源码文件数 / 源码文件数 |
| test_hit_rate | 测试建议命中的真实测试文件比例 |
| cost | tokens、时延、人工干预次数 |

### 11.9 pool recall 诊断

| pool_recall | file_recall@12 | 结论 |
|---:|---:|---|
| 低 | 低 | 检索或扩池有问题 |
| 高 | 低 | 排序或 Semantic Mapping 有问题 |
| 高 | 高 | 候选链路正常 |

禁止在未查看 pool recall 的情况下直接修改 Semantic Mapping 提示词。

### 11.10 A/B/C 消融

| 分组 | 能力 |
|---|---|
| A | 关键词 + 符号 |
| B | A + 依赖闭包 |
| C | B + 时间安全的 co-change |

三组必须使用：

- 同一批样本；
- 同一 commit^ 快照；
- 同一索引版本；
- 同一 CandidateSet 上限。

### 11.11 2×2 对照

|  | 旧 LLM 分析 | 新确定性分析 |
|---|---|---|
| 旧检索 | baseline | 只测分析引擎变化 |
| 新检索 | 只测检索变化 | evidence-grounded pipeline |

该矩阵用于区分：

- 是检索提升带来的收益；
- 还是分析引擎替换带来的收益。

禁止只用旧端到端和新端到端两个结果决定删除某个 Agent。

### 11.12 评测红线

| 红线 | 条件 | 动作 |
|---|---|---|
| R1 | pool_recall@50 小于 0.50 | 停止增加下游 Agent，先修召回 |
| R2 | file_recall@12 小于 0.40，且 B/C 相对 A 提升不足 0.03 | 优先修语义映射 |
| R3 | index_coverage 小于 0.70 | 报告声明覆盖不全，禁止输出全局结论 |
| R4 | cochange_leak_check 为 fail | 本次评测作废 |
| R5 | 三路 digest 不一致 | 停止 fan-in，Guard fail |

n=15 时，架构决策优先参考：

- hallucination_rate；
- failure_rate；
- cost；
- 时延；
- 是否需要人工干预。

recall 和 test_hit_rate 只作趋势指标。

---

## 12. 统一报告契约

### 12.1 主产物

JSON 是报告主产物。

Markdown 只能由 JSON 模板渲染生成，禁止手工修改 Markdown 后作为正式结果。

### 12.2 双层报告结构

报告必须分为：

1. 摘要层；
2. 证据附录层。

摘要层不超过 20 行，应包含：

- 需求结论；
- 需要修改的主要文件；
- 推荐实施顺序；
- blocker 和 gate 数量；
- 三路 coverage；
- 是否有 degraded 分支；
- index coverage。

### 12.3 必须包含的章节

报告必须包含：

1. 需求理解；
2. Assumption 状态；
3. CandidatePool 与 CandidateSet 摘要；
4. 变更点；
5. 测试策略；
6. 风险；
7. Semantic Review 结论；
8. 执行前必须解除的门禁；
9. 实施计划；
10. 未覆盖或 blocked AC；
11. coverage 与 degraded 说明；
12. 未知与需人工确认；
13. CONTRACT_VERSION；
14. registry/spec/pool/set/branch digest。

### 12.4 证据要求

每条变更、测试、风险和 gate 必须具有：

- finding ID 或 ChangePoint ID；
- AC ID；
- evidence ID；
- 可追溯的 path:line。

无法提供代码证据的内容必须放入：

```text
未知与需人工确认
```

禁止包装成确定性结论。

### 12.5 门禁单独展示

PlanGate 必须位于独立章节：

```text
执行前必须解除的门禁
```

禁止将 blocker 混入普通风险列表。

### 12.6 degraded 展示

当任一分支 degraded 时，摘要必须明确写出：

```text
风险分析覆盖 9/12 个分配文件。
未分析文件：……
降级原因：……
```

禁止将覆盖不足显示成“未发现问题”。

### 12.7 报告确定性

相同输入和相同契约版本下：

- JSON 必须逐字节一致；
- Markdown 必须逐字节一致；
- 并行任务完成顺序不得改变报告。

时间戳和 elapsed_ms 必须可以关闭或从稳定报告中排除。

---

## 13. 契约变更管理

### 13.1 版本规则

`CONTRACT_VERSION` 使用语义化版本。

| 变更 | 版本动作 |
|---|---|
| 删除字段、修改字段含义、修改枚举语义 | 主版本 |
| 向后兼容地新增字段或能力 | 次版本 |
| 仅文档措辞和注释修正 | 补丁版本 |

### 13.2 破坏性变更流程

任何破坏性变更必须按以下顺序执行：

1. 提交契约提案；
2. 说明受影响字段；
3. 提供迁移路径；
4. 更新本文件；
5. 更新 `contracts.py`；
6. 更新 digest 规则；
7. 更新对应测试；
8. 重跑全量测试；
9. 重跑评测；
10. 归档旧版和新版 digest；
11. 才允许合并。

### 13.3 三处一致性

以下三处必须保持一致：

1. `docs/CONTRACTS.md`；
2. `backend/app/schemas/contracts.py`；
3. 契约测试矩阵。

任一处缺失，都视为契约变更未完成。

---

## 14. 已冻结的架构决策

以下事项已经批准，不再作为待决定项。

| # | 事项 | 冻结决定 |
|---|---|---|
| 1 | CandidatePool 最大容量 | 60 |
| 2 | 扩池轮次 | 最多 3 轮 |
| 3 | 扩池查询总次数 | 最多 12 次 |
| 4 | 扩池新增条目 | 累计最多 20 条 |
| 5 | 单轮新增条目 | 最多 10 条 |
| 6 | AC 相似度阈值 | 0.80 |
| 7 | 重复 AC 阈值 | 0.90 |
| 8 | embedding | v1 不引入 |
| 9 | AC priority | 必须进入 spec digest |
| 10 | finding priority | 必须进入 branch digest |
| 11 | gate severity | 必须进入 plan/report digest |
| 12 | 留出集 | 5 条，仅作回归护栏 |
| 13 | proposed_signature | v1 不添加 |
| 14 | signature_after | 契约中禁止出现 |
| 15 | api_break blocker | 仅限删除已有符号、存在真实调用方且 allow_breaking=false |
| 16 | AC 拆分 | 原 ID 保持 active，history 记录 split，新子项 derived_from |
| 17 | AC 合并 | 最小 ID 保持 active，其他 ID 标记 merged |
| 18 | blocked plan target | 可以为空，但必须有关联 gate |
| 19 | risk coverage 分母 | CandidateSet 中的 source 文件，不依赖 Impact 结果 |
| 20 | co-change 泄漏检查 | 检查答案 commit 是否进入特征历史 |

---

## 15. 契约测试矩阵

### 15.1 ACRegistry

| Test | 规则 | 断言 |
|---|---|---|
| T-01 | C-AC-03/C-AC-09 | 首次分配生成稳定 ID；相同 revision 和输入保持幂等 |
| T-02 | C-AC-04 | 高相似度改写沿用旧 ID，并追加 rewritten history |
| T-03 | C-AC-05 | 拆分时原 ID 保持 active，history 记录 split，新子项 derived_from |
| T-04 | C-AC-06 | 合并继承最小 ID，其他 ID 进入 merged |
| T-05 | C-AC-07 | dropped ID 永不复用 |
| T-06 | C-AC-08 | 重复草稿只保留高优先级条目并产生 warning |

### 15.2 SymbolRef 与 ChangePoint

| Test | 规则 | 断言 |
|---|---|---|
| T-07 | C-SYM-03 | expect=new、父符号存在时允许新增成员 |
| T-08 | Guard | expect=existing 但索引无符号时 fail |
| T-09 | C-SYM-03 | 新符号没有父符号、没有目标文件且未声明新文件时拒绝 |
| T-10 | C-CP-01 | expect=new 与 semantic 组合在构造期失败 |
| T-11 | C-CP-03 | name=null 仅允许 module add |
| T-38 | C-SYM-05 | 反射检查契约中不存在 signature_after |

### 15.3 Digest

| Test | 规则 | 断言 |
|---|---|---|
| T-12 | 6.3 | reason、score、confidence 变化不影响 set digest |
| T-13 | 6.2 | files 输入顺序变化不影响 set digest |
| T-14 | 6.2 | 内容变化影响 digest，CRLF 和 LF 归一后相同 |
| T-15 | 6.9 | spec revision 变化导致 spec 和 set digest 变化 |
| T-16 | 6.2 | 1000 种迭代顺序下 signal sum 保持一致 |

### 15.4 Guard

| Test | 规则 | 断言 |
|---|---|---|
| T-17 | 7.2 | 反斜杠路径被 normalize，不增加 invalid |
| T-18 | 7.2 | 文件大小写被校正为磁盘真实大小写 |
| T-19 | 7.2 | 重复 finding 被去重 |
| T-20 | 7.4 | 同一行号错误：Agent repair、engine fail |
| T-21 | 7.5 | 一次 repair 后仍失败则直接 fail |
| T-22 | 7.7 | invalid ratio 0.20 可 repair，0.21 fail |
| T-23 | 5.9/7.6 | 修改 CandidateSet 时抛异常并生成 fail |

### 15.5 CandidatePool

| Test | 规则 | 断言 |
|---|---|---|
| T-24 | 5.5 | 第 4 轮、第 13 次查询或超过 20 条新增时拒绝并记录 |

### 15.6 Branch 与并行

| Test | 规则 | 断言 |
|---|---|---|
| T-25 | 8.5 | 9/12 coverage 生成 degraded 和明确声明 |
| T-26 | 8.4 | assigned=0 时 ratio=1.0、success、no_items |
| T-27 | 8.8 | 打乱并行结果和 finding 顺序后报告保持一致 |
| T-28 | 8.7 | state 可 JSON 序列化且不含索引实例 |
| T-29 | 5.9/7.6 | 三路 consumed digest 不一致时 fail |

### 15.7 计划

| Test | 规则 | 断言 |
|---|---|---|
| T-30 | 10.4 | active AC 无 ChangePoint 时生成 targets 为空的 blocked step 和 gate |
| T-31 | 10.5 | 前置 gated/blocked 时后继不得为 ready |
| T-32 | 10.6 | 拓扑排序稳定，循环依赖不死循环并标记 cycle_detected |

### 15.8 评测

| Test | 规则 | 断言 |
|---|---|---|
| T-33 | 11.4/11.5 | co-change 特征提交集合不包含答案 commit 或其后代 |
| T-34 | 11.2 | 评测结束后原工作区 HEAD 和 status 不变 |
| T-35 | 11.9 | 可区分“池命中但集合未命中”和“池本身漏召回” |
| T-36 | 1.4 | engine import 图和运行期钩子均没有 LLM 调用 |
| T-37 | 12.5/12.7 | 报告包含独立门禁章节，且稳定模式下逐字节一致 |

---

## 16. 当前落地状态

### 16.1 已实现

当前已经实现：

- `CONTRACT_VERSION = "1.0.0"`；
- frozen Pydantic 契约基类；
- AcceptanceCriterionDraft；
- AcceptanceCriterion；
- ACRegistryRecord；
- ACRegistryState；
- CriterionHistory；
- Assumption；
- ScopeHints；
- EntityHint；
- SymbolRef；
- RequirementSpec；
- CallerRef；
- EvidencePointer；
- ChangePoint；
- CandidatePool；
- CandidateSet；
- FileRef；
- Budget；
- `validate_change_point_references()`；
- ACRegistry 稳定 ID 分配；
- AC 改写、拆分、合并、删除和去重；
- Registry digest；
- RequirementSpec digest；
- CandidatePool digest；
- CandidateSet digest；
- CRLF/LF 内容归一；
- 稳定 signal sum。

### 16.2 已实现测试

已实现：

```text
T-01 ～ T-07
T-09 ～ T-16
T-38
CandidateSet frozen 约束测试
```

当前契约阶段定向测试：

```text
17 passed
```

加入现有项目后的全量测试基线：

```text
112 passed
```

### 16.3 下一批实现

下一批按以下顺序实施：

1. Guard 数据契约；
2. normalize 纯函数；
3. Guard pass/repair/fail 判定；
4. CandidateSet 越权写入检测；
5. T-08；
6. T-17～T-23；
7. 扩池预算控制；
8. T-24。

后续再实施：

1. BranchResult 和 Coverage；
2. 三路并行异常降级；
3. Risk Engine；
4. Semantic Review Agent；
5. PlanGate；
6. Implementation Plan；
7. 评测工具；
8. Unified Report。

---

## 17. 文件映射

| 规范范围 | 落地文件 |
|---|---|
| 基础数据契约 | `backend/app/schemas/contracts.py` |
| ACRegistry | `backend/app/services/ac_registry.py` |
| 稳定 digest | `backend/app/tools/contract_digest.py` |
| ACRegistry 测试 | `backend/tests/test_ac_registry.py` |
| 基础契约测试 | `backend/tests/test_contracts.py` |
| digest 测试 | `backend/tests/test_contract_digest.py` |
| Guard 契约与实现 | `backend/app/guards/` |
| 扩池预算控制 | `backend/app/tools/candidate_expansion.py` |
| 分支结果与 coverage | `backend/app/schemas/branch_result.py` |
| 风险引擎 | `backend/app/engines/risk_engine.py` |
| Semantic Review | `backend/app/services/semantic_review_agent.py` |
| 实施计划 | `backend/app/engines/implementation_plan.py` |
| 评测工具 | `backend/app/eval/` |
| 统一报告 | `backend/app/report/` |

---

## 18. 验收命令

在项目根目录执行：

```powershell
cd backend

python -m ruff check app/schemas/contracts.py app/services/ac_registry.py app/tools/contract_digest.py tests/test_contracts.py tests/test_ac_registry.py tests/test_contract_digest.py

python -m pytest tests/test_contracts.py tests/test_ac_registry.py tests/test_contract_digest.py -q

python -m ruff check .

python -m pytest -q
```

当前预期结果：

```text
All checks passed!
17 passed
112 passed
```

---

## 19. 冻结声明

本文件从 `1.0.0` 开始是 ChangePilot AI 新架构的正式事实来源。

以下约束不得通过修改提示词绕过：

- AC ID 必须由 ACRegistry 分配；
- expect 只能存在于 SymbolRef；
- signature_after 禁止存在；
- CandidateSet 必须冻结；
- Agent 只能通过 CandidatePool 受控扩池；
- 三路分支必须消费同一个 digest；
- 覆盖不足必须公开；
- blocker 必须转换为 PlanGate；
- co-change 必须遵守 commit 时间边界；
- report 必须可追溯到真实代码证据；
- 任何破坏性契约变更必须同步修改文档、代码和测试。