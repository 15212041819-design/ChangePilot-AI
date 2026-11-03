# ChangePilot AI 开发文档

| 项目 | 内容 |
|---|---|
| 文档适用版本 | `main @ 94aa753`（feat: prepare ChangePilot open-source demo） |
| 契约版本 | `CONTRACT_VERSION = "1.0.0"`（冻结，见 `docs/CONTRACTS.md`） |
| 后端测试 | 336 项（当前版本的本地测试结果） |
| 静态检查 | ruff check + ruff format（CI 强制） |
| 上游规范 | 字段语义、digest 纪律、Guard 判定矩阵以 [CONTRACTS.md](CONTRACTS.md) 为准；本文档描述其实现 |

> 本文面向参与开发与维护的工程师。安装、试用与 FAQ 见项目根目录 [README](../README.md)；契约细节一律以 `docs/CONTRACTS.md` 为准，两者冲突时以契约为准。

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈与运行要求](#2-技术栈与运行要求)
3. [系统架构](#3-系统架构)
4. [目录结构](#4-目录结构)
5. [核心模块详解](#5-核心模块详解)
6. [API 参考](#6-api-参考)
7. [配置说明](#7-配置说明)
8. [本地开发流程](#8-本地开发流程)
9. [测试与 CI](#9-测试与-ci)
10. [评测体系](#10-评测体系)
11. [贡献规范](#11-贡献规范)
12. [已知限制与路线图](#12-已知限制与路线图)
13. [前端实现与交互](#13-前端实现与交互)
14. [接口调用示例](#14-接口调用示例)
15. [开发与排障速查](#15-开发与排障速查)

---

## 1. 项目概述

ChangePilot AI 是一个本地运行的**代码变更影响分析工作台**：输入一句自然语言需求并选择一个 Git 仓库快照，系统产出受影响文件、测试建议、风险评估和实施门禁，并给出统一的可视化报告。分析结果是**建议**，系统不会修改目标仓库。

正式证据分析流程把 LLM 用在需求结构化和语义映射，影响、测试、风险与报告审核由确定性代码完成。仓库仍保留旧流程代码用于对照；旧流程不在默认 API 路由中。这带来三个工程收益：

- **便于复现**：冻结同一需求规格与 commit 后，确定性引擎给出一致结果；模型步骤本身不保证逐字相同；
- **可评测**：召回率、覆盖率、幻觉率都能以脚本量化，不依赖模型抽样；
- **可校验**：LLM 提出的路径必须在真实仓库证据中验证，候选集冻结并计算 digest，校验失败不能作为可信结论输出。

当前定位为本地开发与演示版本：无登录、无公网部署配置、单用户。

## 2. 技术栈与运行要求

| 层 | 选型 |
|---|---|
| 语言 | Python 3.12（`>=3.12,<3.13`） |
| Web 框架 | FastAPI + uvicorn（同步路由，线程池执行） |
| 工作流编排 | LangGraph（并行 fan-out / fan-in） |
| 数据建模 | Pydantic v2（`frozen=True`、`extra=forbid` 契约模型） |
| LLM | DeepSeek（OpenAI 兼容协议，经 `langchain-openai` 接入） |
| 持久化 | PostgreSQL 17 + SQLAlchemy + Alembic（JSONB 存储） |
| 质量工具 | ruff（E,F,I,B,UP，line-length 100）、pytest、GitHub Actions |
| 前端 | 零构建静态页面（原生 HTML/CSS/JS，`python -m http.server` 托管） |

运行依赖：Python 3.12、Docker Compose（提供 PostgreSQL）、Git。真实分析需要 DeepSeek API Key；前端离线示例与历史查询不调用模型。

## 3. 系统架构

### 3.1 分层结构

```
api          HTTP 路由、输入归一化、错误码映射（不含业务逻辑）
services     生命周期服务、LLM Agent（仅 2 处调用 LLM）、执行器组装
graph        LangGraph 并行分析图（编排，不实现分析逻辑）
engines      确定性分析引擎：Impact / Risk / TestStrategy / Review / ReportBuilder
guards       候选映射守卫：normalize / repair / fail 三级判定
repositories SQLAlchemy 仓储：分析上下文的持久化与查询
schemas      契约模型（最底层）：contracts.py 零业务依赖
tools        仓库工具：扫描、AST 索引、依赖图、共变索引、快照、digest
```

依赖方向的硬性红线（违反会被评审拒绝）：

1. `schemas/contracts.py` 不得 import 任何 Agent、Graph、业务服务；
2. `engines/` 不得 import `app.services`（Agent）；
3. 确定性引擎禁止调用 LLM。

### 3.2 LLM 边界

正式分析主流程中使用 LLM 的位置有两处：

| 位置 | 职责 |
|---|---|
| `services/requirement_spec_agent.py` | 需求文本 → 结构化 `RequirementSpec` + 假设列表（HITL 审核对象） |
| `services/semantic_mapping_agent.py`（经 `services/semantic_context.py` 编排） | 需求语义 → 候选文件宽召回；只能通过白名单工具查询真实索引扩展候选池 |

两个步骤的输出都必须经过确定性校验才能进入下游：未确认需求假设需人工审核；候选映射需经过路径与符号 Guard。旧基线中的 Agent 文件仍存在，但正式路由不会调用它们。`engines/` 不调用 LLM。

### 3.3 端到端分析流程

```mermaid
flowchart TD
    A["POST /api/v1/analyses"] --> B["生命周期服务：create_thread + start_run"]
    B --> C["Git ref 解析为 commit SHA<br/>在临时脱离式 worktree 建立隔离快照"]
    C --> D["仓库扫描 + 符号 AST 索引<br/>+ 依赖图 + 仓库词表（只解析一次）"]
    D --> E["构建共变索引<br/>并执行时间泄漏自检（失败即终止）"]
    E --> F["Requirement Agent（LLM）<br/>结构化需求 + 假设列表"]
    F -->|存在未确认假设| G["409 暂停：mark_review_required<br/>→ 前端人工审核（approve/revise/reject）"]
    G -->|approve / revise| F
    G -->|reject| Z["会话终止：rejected"]
    F -->|假设全部 accepted| H["context retrieve：确定性检索<br/>建立初始 CandidatePool"]
    H --> I["Semantic Mapping（LLM）：<br/>有界查询与受控扩池"]
    I --> I2["context freeze：<br/>不可变 CandidateSet + 三重 digest<br/>Guard 校验映射"]
    I2 --> J["覆盖率门禁：AST 索引覆盖率 ≥ 70%<br/>否则禁止产出报告"]
    J --> K["Impact Engine：验证并冻结 ChangePoint 证据"]
    K --> L["并行三分支（LangGraph）：<br/>Impact（缓存回放）/ Risk / Test Strategy"]
    L --> M["Result Review 交叉审查<br/>+ digest 硬校验 → 统一报告"]
    M --> N["complete_run 持久化<br/>返回 AnalysisReport"]
```

关键细节：

- **隔离快照**：每个请求的 Git ref 先解析为精确 commit SHA，再在临时 worktree 中分析，不切换用户分支、不修改工作区。
- **泄漏自检**：共变索引（git 历史共变文件对）必须在任何 LLM 调用之前建好并通过时间泄漏检查——被分析 commit 自身必须被排除在历史之外，否则整次分析终止。这条纪律同时服务于线上分析与离线评测。
- **Impact 缓存回放**：Impact Engine 在进入并行图之前预先运行（作为 ChangePoint 证据验证），并行图中的 Impact 分支以缓存回放方式 fan-in，图内校验 CandidateSet 与 digest 一致性，不一致立即抛错。
- **fan-in 硬校验**：三条分支结果汇合时校验 digest 与集合成员关系，任何分支异常都转为结构化的 failed 分支结果，不会炸掉整个 superstep。

### 3.4 HITL 需求审核生命周期

`workflow_status` 状态机（`schemas/analysis_context.py`）：

```
queued → running → awaiting_requirement_review → running → completed
                                   ↘ rejected
                         （或任意阶段异常 → failed）
```

持久化语义（`services/analysis_lifecycle.py`）：

- **先写库，后执行**：thread、run 先落库，分析成功/暂停/失败都更新数据库，并记录对应时间线事件；
- **审核决定先落库再恢复**：`submit_review` 把决定写入 `review_decisions` 后才恢复分析；
- **不信任前端重放**：approve/revise 恢复分析时使用**数据库中保存的原始输入**，不接受前端重新提交的 repository/commit；
- **approve 由确定性代码生效**：`approve_requirement_assumptions` 用代码把假设标记为 accepted，不允许模型自行生成"已批准"；
- **revise 可再次暂停**：修订后若产生新的未确认假设，保持同一 `thread_id` 再次进入审核等待（HTTP 409）；
- **reject 只终止**：不启动新 run，会话状态置为 rejected。

## 4. 目录结构

```
ChangePilot-AI/
├── backend/
│   ├── app/
│   │   ├── api/            # 路由层：router.py + routes/{health,repositories,evidence_analysis}.py
│   │   ├── cli/             # 评测命令行：evaluate.py / rescore.py / diagnose_ranking.py
│   │   ├── core/            # config.py：Settings（pydantic-settings，读 backend/.env）
│   │   ├── db/              # SQLAlchemy models + session 工厂
│   │   ├── engines/         # 确定性引擎（零 LLM）：impact / risk / test_strategy /
│   │   │                    #   branch_result / result_review / report_builder
│   │   ├── graph/           # parallel_analysis.py（现行）；change_analysis.py（遗留，见 5.6）
│   │   ├── guards/          # analysis_guard.py：候选映射三级守卫
│   │   ├── repositories/    # analysis_context.py：PostgreSQL 上下文仓储
│   │   ├── schemas/         # 契约模型层；contracts.py 为最底层冻结契约
│   │   ├── services/        # 生命周期服务、Requirement/Semantic Mapping Agent、执行器
│   │   └── tools/           # 仓库扫描、AST 索引、依赖图、共变索引、快照、digest、
│   │                        #   评测数据集/运行器/指标、候选重排与排序诊断
│   ├── alembic/             # 迁移：versions/20261028_0001_create_analysis_context.py
│   ├── evals/results/       # 本地评测报告（已 gitignore，不应提交）
│   ├── scripts/             # 开发期冒烟脚本（check_*.py，非产品代码）
│   ├── tests/               # 自动化测试；当前版本本地运行结果为 336 项通过
│   ├── pyproject.toml        # 依赖与 ruff/pytest 配置（单一依赖来源）
│   └── .env.example          # 环境变量模板
├── frontend/                 # 零构建静态界面：index.html / app.js / styles.css / experience.css
├── docs/                     # CONTRACTS.md（契约规范）+ 本文档
├── sample_repos/             # 唯一允许分析的仓库工作区（含演示用 order_service）
├── docker-compose.yml        # PostgreSQL 17（本地开发凭据，仅限本地）
└── .github/workflows/ci.yml   # CI：postgres service + 迁移 + ruff + pytest
```

安全约束：`tools/workspace.py` 将可分析仓库限制在 `sample_repos/` 根目录内（`resolve_repository_directory` 做路径归一与逃逸检查），API 层只接受目录名，不接受任意路径。

## 5. 核心模块详解

### 5.1 契约层（`schemas/contracts.py` + `tools/contract_digest.py`）

- 核心分析契约继承 `FrozenContractModel`（`frozen=True` + `extra="forbid"`）；HTTP 请求与响应使用独立的 Pydantic 模型；
- 构造期校验"结构组合合法性"（例如拒绝 `expect=new × semantic` 等非法 ChangePoint 组合）；
- `contract_digest.py` 实现 digest 纪律：排序确定性（迭代顺序、键排序）、浮点统一 3 位定长、CRLF/BOM 归一、`math.fsum(sorted(...))` 稳定求和，运行实例 ID（pool_id/set_id）排除在输入之外；
- **任何破坏性字段变更必须走 `CONTRACTS.md` 第 13 章的契约变更流程**，并同步更新契约测试。

### 5.2 语义上下文（`services/semantic_context.py` + `guards/analysis_guard.py`）

流程：RequirementSpec → `retrieve_candidate_pool()` 确定性检索生成初始 `CandidatePool` → `run_semantic_mapping()` 用 LLM 和白名单工具进行有界查询、受控扩池与选择 → **context freeze** 生成不可变 `CandidateSet`（携带 `set_digest / spec_digest / index_digest`）→ Guard 校验。扩池结果必须先写回 CandidatePool，再由冻结步骤统一计算摘要。

- Guard 三级判定：normalize（白名单内校正，最高置信去重保序）→ repair（仅允许一次，且仅 agent 输出可修复；engine 证据直接 fail）→ fail（无效比例超过 20% 边界）；
- 覆盖率门禁：`MIN_INDEX_COVERAGE = 0.70`，Python AST 索引覆盖率不足时**禁止产出看似完整的报告**，直接抛错；
- 候选排序与诊断：`tools/candidate_reranker.py`（kind-aware 重排）与 `tools/ranking_diagnostics.py` 用于召回调优，均可离线对轨迹重放。

### 5.3 确定性引擎（`engines/`）

| 引擎 | 职责 | 关键约束 |
|---|---|---|
| `impact_engine.py` | 验证候选与 ChangePoint 证据，产出影响面 | 无法证明的事实抛 `ImpactEngineValidationError`，不猜测 |
| `risk_engine.py` | 基于已验证 ChangePoint 评估风险 | `schema_models` 显式为空时跳过 schema 规则，不虚构字段 |
| `test_strategy_engine.py` | 生成测试建议 | 拒绝虚构 `test_*.py` 路径；覆盖率分母与 Impact 独立（三路独立分母） |
| `branch_result.py` | 分支结果统一工厂 | `analyzed ⊄ assigned` 直接抛错；空分配保持契约一致 |
| `result_review.py` | 三路结果交叉审查 | 消费共享的已验证 ChangePoint |
| `report_builder.py` | 组装统一 `AnalysisReport` | digest 硬校验 |

### 5.4 并行编排（`graph/parallel_analysis.py`）

- LangGraph 状态图：state 只存 JSON 可序列化数据，大对象（符号索引、依赖图、共变索引）以 `resource_id` 引用保存在 state 之外；
- 三分支独立异常降级：单分支失败转结构化 failed 结果，不影响其他分支；
- fan-in 时做稳定排序与 digest 硬校验；
- 图只负责编排，分析逻辑全部来自注入的 engine runner（测试通过手写 fake runner 验证图行为，无 mock 滥用）。

### 5.5 持久化层（`repositories/analysis_context.py` + `db/` + Alembic）

六张表（迁移 `20261028_0001`）：

| 表 | 内容 | 要点 |
|---|---|---|
| `analysis_threads` | 分析会话 | `thread_id` 主键；`(workflow_status, updated_at)` 索引 |
| `analysis_runs` | 每次执行 | 状态 running/paused/completed/failed；预留 token_count；error_code |
| `analysis_checkpoints` | 阶段快照 | `(thread_id, sequence)` 唯一；JSONB payload；关联 run 删除时置空 |
| `review_decisions` | 人工审核决定 | 审计优先：决定先落库再恢复分析 |
| `analysis_reports` | 统一报告 | `(thread_id)` 唯一；`report_digest`；JSONB payload |
| `analysis_events` | 时间线（只追加） | 前端"分析时间线"数据源；`(thread_id, sequence)` 唯一 |

仓储对外暴露冻结的 Pydantic 快照（`ContextSnapshotModel`：frozen + extra=forbid + from_attributes），服务层不接触 ORM 对象。JSON 列使用 `sa.JSON().with_variant(postgresql.JSONB)` 兼容测试中的 SQLite。

目前仓储提供 `append_checkpoint()` 能力，但正式 `EvidenceAnalysisLifecycleService` 尚未在各分析阶段调用它；因此详情中的 `checkpoints` 可以为空。会话恢复依靠原始输入与审核决定重新执行分析，不应把它理解为从 LangGraph 节点位置无损续跑。`analysis_runs.token_count` 当前也未接入真实模型用量统计，不能用于计费核对；费用以模型服务商账单为准。

### 5.6 遗留模块（计划移除）

以下模块属于已被替换的旧架构，**不在默认路由中**，目前仍保留供对照、兼容与相关测试使用：

- `graph/change_analysis.py`（旧 LLM 串联图）
- `services/requirement_agent.py`、`code_impact_agent.py`、`test_strategy_agent.py`、`implementation_plan_agent.py`（旧 Agent）
- 对应测试 `test_change_analysis_graph.py`、`test_*_agent.py` 等
- `scripts/check_*.py`（开发期手工冒烟脚本，不属于正式产品流程）

新功能应接到正式证据分析流程。删除旧模块前要先核对导入方与测试，避免误删仍有用的契约或兼容代码。

## 6. API 参考

所有正式 API 挂载在 `/api/v1` 前缀下；CORS 放行 `http://127.0.0.1:5173` 与 `http://localhost:5173`（本地前端）。

| 方法与路径 | 用途 |
|---|---|
| `GET /api/v1/health/ready` | 检查 API 与 PostgreSQL 就绪状态 |
| `GET /api/v1/repositories` | 列出 `sample_repos/` 下可分析的仓库目录名 |
| `POST /api/v1/analyses` | 创建分析（请求体：`request_text` / `repository_name` / `commit`） |
| `GET /api/v1/analyses` | 分页查询历史（`limit ≤ 50`、`offset`、`report_only`） |
| `GET /api/v1/analyses/{thread_id}` | 读取完整持久化上下文（含时间线与报告） |
| `POST /api/v1/analyses/{thread_id}/reviews` | 提交审核决定（`action: approve / revise / reject` + `feedback`） |

错误码语义（`routes/evidence_analysis.py` 统一映射）：

| HTTP | 含义 |
|---|---|
| 422 | 请求字段缺失、长度或内容校验失败 |
| 404 | 仓库/会话不存在 |
| 409 | 需要人工审核（detail 含 `thread_id` 与 `workflow_status`）；或会话状态与操作冲突 |
| 502 | digest、契约或结果组装错误——**不允许伪装成成功报告** |
| 503 | 运行时能力不可用（快照/共变索引/泄漏自检失败）或持久化失败 |

交互文档：<http://127.0.0.1:8010/docs>（FastAPI 自动生成）。

## 7. 配置说明

`app/core/config.py` 定义 `Settings`（pydantic-settings），从 `backend/.env` 读取（模板见 `backend/.env.example`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_NAME` | `ChangePilot AI` | 应用名称 |
| `APP_ENV` | `development` | 运行环境标记 |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8010` | 服务监听地址和端口；手动启动 Uvicorn 时也可用命令行覆盖 |
| `DEEPSEEK_API_KEY` | 空（SecretStr） | 真实分析必需；缺失时报错而非静默降级 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `DEEPSEEK_MODEL` | `deepseek-v4-pro` | 模型名 |
| `DATABASE_URL` | `postgresql+psycopg://changepilot:changepilot@127.0.0.1:5432/changepilot` | 默认值仅限本地开发；生产必须覆盖 |
| `DATABASE_ECHO` | `false` | SQL 日志 |
| `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` | 5 / 10 | 连接池 |

`get_settings()` 带 `lru_cache`；LLM 客户端 `temperature=0`、`timeout=120`、`max_retries=2`。

## 8. 本地开发流程

完整安装步骤见根 [README](../README.md)。开发者日常工作流：

```powershell
# 一次性
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
Copy-Item backend\.env.example backend\.env   # 填入 DEEPSEEK_API_KEY
docker compose up -d postgres

# 每次拉取后：应用迁移
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head

# 启动后端（127.0.0.1:8010）
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010

```

另开终端，从**项目根目录**启动前端（`127.0.0.1:5173`）：

```powershell
cd frontend
python -m http.server 5173 --bind 127.0.0.1
```

提交前检查（与 CI 完全一致）：

```powershell
cd backend
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m ruff format --check .
..\.venv\Scripts\python.exe -m pytest -q
cd ..
node --check frontend/app.js   # 仅语法校验，无构建步骤
```

新数据库迁移：

```powershell
cd backend
..\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"
..\.venv\Scripts\python.exe -m alembic upgrade head
```

## 9. 测试与 CI

- **当前版本 336 项后端测试通过**：普通单元测试无需真实 DeepSeek 调用；执行时间随机器与数据库环境变化；
- 契约测试与 `CONTRACTS.md` 测试矩阵（T-01 ~ T-38）对应，含深度不可变、Guard 边界值（如 0.20 → repair、0.21 → fail）等真实断言；
- CI（`.github/workflows/ci.yml`，push + PR 触发）：ubuntu + PostgreSQL service 容器 → `pip install -e backend[dev]` → 配置 git 身份（隔离仓库测试需要）→ `ruff check` + `ruff format --check` → `alembic upgrade head` → `pytest`；
- 本地生成的评测报告（`backend/evals/results/`）已 gitignore，不要提交。

## 10. 评测体系

### 10.1 设计

- **数据集**：从目标仓库 git 历史"挖掘 feat 提交"——commit message 当需求、真实改动文件当标准答案；纯测试/合并/根目录/超短 message 的提交被过滤；
- **防泄漏**：构造共变索引时强制排除答案 commit 自身，并为每个样本生成泄漏检查报告；泄漏检查不通过则样本失败；
- **隔离运行**：每个样本在临时快照中执行，需求可冻结为 RequirementSpec JSON 工件回放（`--requirement-specs`），保证调优实验之间需求一致；
- **指标**：`pool_recall_at_k`（候选池召回）、`file_recall_at_k`（最终集合召回）、`hallucination_rate`（路径幻觉）、`failure_rate`、`coverage_rate`、`degraded_count`、`average_latency_ms`、`total_token_count`；
- **红线**：按 `CONTRACTS.md` 第 11.12 节判定：R1 是 `pool_recall@50 < 0.50`；R2 同时要求 `file_recall@12 < 0.40` 且消融 B/C 相对 A 提升不足 0.03。不要仅凭某一次召回数值推断模型优劣。

### 10.2 当前验证结果

| 指标 | 数值 |
|---|---|
| `pool_recall@60` | 0.792 |
| `file_recall@12` | 0.639 |
| 后端自动化测试 | 336 项通过 |
| 路径幻觉率 | 暂不可评估（该次检索评测未输出报告路径） |

来源：实验 `strong-seed-structural-rerank-v2`，15 条历史变更样本、其中 6 条含可评估的已存在源码文件，数值为宏平均。**样本较少，适合跟踪迭代，不代表对其他仓库的准确率承诺**；幻觉率字段为 0 但报告路径数也为 0，因此不能据此声称已验证"零幻觉"。

### 10.3 命令行

```powershell
cd backend
# 从项目 Git 历史取 3 条样本进行本地冒烟评测
..\.venv\Scripts\python.exe -m app.cli.evaluate ".." `
  --context-mode deterministic --samples 3 --scan-limit 30 `
  --experiment-name dev-smoke --output evals/results/dev-smoke.json

# 查看完整参数；正式实验应使用相同的冻结 RequirementSpec 工件
..\.venv\Scripts\python.exe -m app.cli.evaluate --help

# 对已生成轨迹离线重打分 / 排序损失诊断（不调用模型）
..\.venv\Scripts\python.exe -m app.cli.rescore --help
..\.venv\Scripts\python.exe -m app.cli.diagnose_ranking --help
```

评测命令从 `backend/` 执行，`".."` 表示本项目 Git 仓库根目录；要评测别的仓库就把该参数替换成其绝对路径。没有传入 `--requirement-specs` 时，构造需求规格可能调用 DeepSeek；`semantic` 模式还会增加语义映射调用。实验报告只写本地 `evals/results/`，调优对比时保持需求工件与参数一致，只改检索/排序变量。

## 11. 贡献规范

1. **提交信息**：Conventional Commits（`feat:` / `fix:` / `test:` / `chore:` / `docs:`），保持一行式主题；
2. **契约变更**：任何 `schemas/` 字段语义、digest 输入、Guard 判定的改动，先按 `CONTRACTS.md` 第 13 章流程更新契约文档，再改代码，同步契约测试；
3. **LLM 边界**：新功能若需要 LLM，先回答"能不能用确定性代码完成"；引擎层新增 LLM 调用一律拒绝；
4. **提交前**：跑完第 8 节的四条检查；修改公开 API 时同步更新测试与 README 接口表；
5. **不要提交**：`.env`、数据库内容、密钥、本地评测结果；
6. **新模块**：放入正确分层（见 3.1），`__init__.py` 必须存在（打包依赖）。

## 12. 已知限制与路线图

当前已知限制（诚实清单，也是路线图）：

- 幻觉率尚未端到端验证：需要一次产出报告路径的评测来把"暂不可评估"变成真实数字；
- 评测样本仅 15 条、单仓库（本项目自身历史）：结论外推需在 2-3 个外部仓库上复验；
- `schema_models` 显式为空：Risk Engine 的 schema 变更规则在接入 schema 索引器前不会触发；
- 遗留模块（5.6 节）待整体移除；
- 同步执行：创建分析请求会等待分析完成或进入人工审核；大型仓库与模型调用会增加等待时间；
- 无认证与公网部署配置，本地演示凭据写死于 docker-compose。

优先级建议：幻觉率端到端评测 → 外部仓库评测 → schema 索引器 → 遗留模块清理。

## 13. 前端实现与交互

前端是原生 HTML、CSS、JavaScript，无 npm 构建或打包步骤。入口为 [`frontend/index.html`](../frontend/index.html)，行为在 [`frontend/app.js`](../frontend/app.js)，视觉样式在 [`frontend/styles.css`](../frontend/styles.css) 和 [`frontend/experience.css`](../frontend/experience.css)。`API_BASE` 当前固定为 `http://127.0.0.1:8010/api/v1`；如果本地后端换端口，需同步改此值和后端 CORS 配置。

| 页面 | 数据来源与交互 |
|---|---|
| 变更分析 | 同时请求 `/health/ready` 与 `/repositories`；仓库选择器只提交目录名；点击“开始分析”调用正式创建接口。 |
| 分析报告 | 先请求历史摘要列表，按卡片展示；点击卡片才请求完整会话，读取 `report`；详情可返回列表。 |
| 分析时间线 | 同样先展示历史卡片，进入详情后读取 `events`；保留后端英文事件键并提供中文说明。 |
| 继续会话 | 使用 `thread_id` 查询已保存上下文；审核等待的会话可提交 `approve`、`revise` 或 `reject`。 |
| 离线示例 | 浏览器内的演示数据，不请求模型，也不写入数据库。 |

最近一次会话 ID 保存在浏览器 `localStorage`，只是便于回填的本地线索；分析、审核与报告的权威数据始终以 PostgreSQL 中的会话为准。历史列表使用轻量摘要，避免首次打开页面就下载所有报告与时间线。提交分析按钮在请求期间显示等待状态；真实分析当前是同步 HTTP 请求，等待时间取决于仓库规模和模型响应。

调整界面时至少检查以下路径：首次进入且服务正常、数据库未就绪、仓库列表为空、需要人工审核、审核后再次暂停、报告历史列表和详情返回、时间线列表和详情返回、离线示例。前端脚本的基本语法检查是 `node --check frontend/app.js`；它不能代替浏览器交互验收。

## 14. 接口调用示例

下例均假设后端运行于 `127.0.0.1:8010`，且 `sample_repos/order_service` 已存在。请求体中的 `repository_name` 是目录名，不是完整路径。PowerShell 中先设置基础地址：

```powershell
$base = 'http://127.0.0.1:8010/api/v1'
Invoke-RestMethod "$base/health/ready"
Invoke-RestMethod "$base/repositories"
```

创建分析：

```powershell
$body = @{
    request_text = '为订单系统增加取消订单功能，仅订单所属用户可取消待支付订单。'
    repository_name = 'order_service'
    commit = 'HEAD'
} | ConvertTo-Json

Invoke-RestMethod "$base/analyses" -Method Post -ContentType 'application/json; charset=utf-8' -Body $body
```

`commit` 可省略，此时默认 `HEAD`。`POST /analyses` 当前同步执行：成功时返回 `api_version`、`thread_id`、`workflow_status: completed`、`status` 和完整 `report`。`status` 是报告准入结论，取值为 `ready`、`gated` 或 `blocked`；它不是会话运行进度。如果需求假设需要人工确认，接口返回 **HTTP 409**，响应 `detail` 中含 `thread_id` 和 `workflow_status: awaiting_requirement_review`。此时应保存该 ID 并查询会话，不要重新创建分析。

查看历史与详情：

```powershell
Invoke-RestMethod "$base/analyses?limit=20&offset=0"
Invoke-RestMethod "$base/analyses?limit=20&offset=0&report_only=true"
$threadId = '把创建分析或 409 响应中的 32 位 thread_id 填在这里'
Invoke-RestMethod "$base/analyses/$threadId"
```

历史页返回 `items` 与 `has_more`。完整会话返回 `thread`、`runs`、`checkpoints`、`review_decisions`、`events` 和可能为空的 `report`；刚进入审核等待时没有最终报告是正常的。`thread.workflow_status` 的可能值是 `queued`、`running`、`awaiting_requirement_review`、`completed`、`failed`、`rejected`。审核决定必须提交给原会话：

```powershell
$review = @{
    action = 'approve'
    feedback = '同意当前假设，按需求继续分析。'
    actor_id = 'local-developer'
} | ConvertTo-Json

Invoke-RestMethod "$base/analyses/$threadId/reviews" -Method Post -ContentType 'application/json; charset=utf-8' -Body $review
```

`action` 支持 `approve`、`revise`、`reject`。`revise` 与 `reject` 必须填写非空 `feedback`；`actor_id` 可省略。审核接口返回完整会话；修订后若仍有待确认假设，也可能再次返回 409。服务端用已保存的需求、仓库名和 Git ref 继续分析，不接受在审核请求中偷偷更换仓库或快照。

## 15. 开发与排障速查

### 15.1 推荐启动顺序

在**项目根目录**先创建虚拟环境、安装依赖、复制 `backend/.env.example` 为 `backend/.env`，填入真实分析使用的 DeepSeek Key，然后启动 PostgreSQL。第一个终端：

```powershell
docker compose up -d postgres
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

第二个终端**重新从项目根目录**进入前端：

```powershell
cd frontend
python -m http.server 5173 --bind 127.0.0.1
```

浏览器打开 `http://127.0.0.1:5173/`；后端交互文档在 `http://127.0.0.1:8010/docs/`。后端和前端都保持终端进程运行；结束时分别按 `Ctrl+C`，数据库可用 `docker compose stop postgres` 停止（保留数据卷）。

### 15.2 常见症状

| 症状 | 检查方法与处理 |
|---|---|
| 页面显示 `Directory listing for /` | 静态服务器从错误目录启动。停止服务，进入本项目 `frontend/` 后重新运行 `python -m http.server 5173`。 |
| 前端提示后端未连接 | 在浏览器访问 `/api/v1/health` 对应的完整本地 URL；确认 uvicorn 正在监听 8010，且 `frontend/app.js` 中 `API_BASE` 未指向别的端口。 |
| 数据库未就绪 | 检查 `docker compose ps`、`DATABASE_URL`，再从 `backend/` 运行 `alembic upgrade head`；`GET /api/v1/health/ready` 应返回 `database: ok`。 |
| `No module named app` | 后端命令必须在 `backend/` 目录执行，且使用已安装项目依赖的解释器。 |
| PyCharm 将 `app` 或 `sqlalchemy` 标红 | 将项目根目录的 `.venv/Scripts/python.exe` 设为解释器，并把 `backend/` 标记为 Sources Root；终端测试通过时，IDE 红线通常是解释器或索引配置问题。 |
| 端口 8010 已被占用 | 先关闭旧的后端进程，避免启动两个不同版本；若确需更换端口，前端 `API_BASE` 与后端 CORS 也需同步调整。 |
| 仓库不存在或列表为空 | 当前可分析目录固定为 `sample_repos/` 的直接子目录，前端选择目录名；目录需是 Git 仓库或位于 Git worktree 内，指定的 ref 必须存在。 |
| 分析长时间等待 | 查看后端终端输出、模型服务可用性和网络；真实分析会调用 DeepSeek，并可能因人工审核返回 409。不要在未确认原会话状态前反复点击创建。 |
| `DEEPSEEK_API_KEY 未配置` | 在 `backend/.env` 填写有效密钥后重启后端。仅浏览离线示例、历史和健康接口无需此密钥。 |

### 15.3 修改模块时的最小闭环

1. 从正式入口 [`evidence_analysis.py`](../backend/app/api/routes/evidence_analysis.py) 与执行器 [`evidence_analysis.py`](../backend/app/services/evidence_analysis.py) 追踪数据流，确认改动属于 API、服务、工具、引擎、契约还是前端。
2. 改动 `schemas/`、digest 或 Guard 时先核对 [CONTRACTS.md](CONTRACTS.md)，再改实现和针对性测试。
3. 如果新增数据库字段，添加 Alembic 迁移并在 PostgreSQL 上执行；不要依赖 ORM `create_all` 替代迁移。
4. 先跑相关测试，再跑 `ruff check .`、`ruff format --check .`、`pytest -q`；改前端还要运行 `node --check frontend/app.js` 并在浏览器手动走通主路径。
5. 用 `git status --short` 和 `git diff --check` 检查提交范围；确认没有 `.env`、数据库文件和本地评测结果。
