# ChangePilot AI

ChangePilot AI 是一个本地运行的软件变更分析工作台。输入需求并选择代码仓库后，它会先梳理需求和人工审核点，再基于指定 Git 快照给出受影响文件、测试建议、风险和实施门禁。分析结果是建议，开发者仍需核对和实施变更。

## 当前验证结果

| 项目 | 本地结果 |
| --- | --- |
| 候选池文件召回率 `pool_recall@60` | 0.792 |
| 前 12 个文件召回率 `file_recall@12` | 0.639 |
| 后端自动化测试 | 336 项通过 |
| 路径幻觉率 | 暂不可评估：该次检索评测未输出报告路径 |

召回率来自 `strong-seed-structural-rerank-v2` 的 15 条历史变更样本，其中 6 条含可评估的已存在源码文件；数值为这些样本的宏平均值。样本较少，适合跟踪迭代，不代表对其他仓库的准确率承诺。本次评测记录中的幻觉率字段为 0，但报告路径数也是 0，因此不能据此声称已验证“零幻觉”。

## 当前能力

- 正式 API：创建分析、查询已保存会话、审核需求假设并继续分析。
- 仓库选择：前端从 `sample_repos/` 读取可用目录名，避免手输错误路径。
- 证据约束：从真实 Git 快照读取代码，验证候选文件与分析结果。
- PostgreSQL：保存会话、检查点、审核决定和报告。
- 零构建前端：浏览器直接打开本地静态服务；可查看不消耗模型 Token 的离线示例。

目前定位为**本地开发与演示版本**。它不会自动修改目标仓库，也没有用户登录或公网部署配置。

## 快速启动

需要 Python 3.12、Docker Compose、Git。真实分析还需要 DeepSeek API Key。

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
Copy-Item backend\.env.example backend\.env
docker compose up -d postgres
```

将 `backend/.env` 中的 `DEEPSEEK_API_KEY` 改为自己的密钥。不要把该文件提交到 Git。随后启动后端：

```powershell
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

另开一个终端，在项目根目录启动前端：

```powershell
cd frontend
python -m http.server 5173 --bind 127.0.0.1
```

打开 <http://127.0.0.1:5173/>。如果看到 `Directory listing for /`，静态服务器是从错误目录启动的；切换到本项目的 `frontend/` 目录重启，或进入目录列表中的 `frontend/`。

在 macOS/Linux 上，使用 `python3 -m venv .venv` 创建虚拟环境、`.venv/bin/python` 运行 Python，并用 `cp backend/.env.example backend/.env` 复制配置。后端和前端仍分别从 `backend/`、`frontend/` 目录启动。数据库密码和连接串是本地演示默认值；公开部署前必须更换。

## 试用流程

1. 页面状态显示“服务已就绪”后，从下拉框选择 `order_service`。它对应 `sample_repos/order_service/`；不要填写完整路径。
2. Git 快照先使用 `HEAD`，在需求框输入“为订单系统增加取消订单功能，仅订单所属用户可取消待支付订单”。
3. 点击“开始分析”。如果系统暂停在需求审核，阅读假设后选择批准、修订或拒绝；完成后可通过会话 ID 再次读取结果。

真实分析会调用 DeepSeek 并消耗你账户的输入和输出 Token，需求修订、语义扩池和模型重试可能增加调用次数。点击“打开离线示例”、读取已保存会话、列出仓库及健康检查均不调用模型。

## 接口与目录

| 路径 | 用途 |
| --- | --- |
| `GET /api/v1/health/ready` | 检查 API 与 PostgreSQL |
| `GET /api/v1/repositories` | 列出可分析的仓库目录名 |
| `POST /api/v1/analyses` | 创建真实分析 |
| `GET /api/v1/analyses` | 分页读取历史会话摘要；`report_only=true` 仅列出已有报告 |
| `GET /api/v1/analyses/{thread_id}` | 读取持久化会话 |
| `POST /api/v1/analyses/{thread_id}/reviews` | 提交需求审核 |

后端接口文档：<http://127.0.0.1:8010/docs/>。

`backend/` 包含 FastAPI、分析流程、数据迁移、评测材料和测试；`frontend/` 是静态页面；`sample_repos/` 是允许分析的本地仓库工作区；`docs/` 包含分析契约。本地生成的评测报告不应提交。

## 检查与贡献

```powershell
cd backend
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m ruff format --check .
..\.venv\Scripts\python.exe -m pytest -q
cd ..
node --check frontend/app.js
```

提交改动前请运行上述检查。修改公开 API 时同步更新相关测试与文档；不要提交 `.env`、数据库内容、密钥或本地评测结果。GitHub Actions 会在推送和拉取请求时运行这些检查。

## 常见问题

- **“无法连接 API”**：确认后端进程仍在运行，并访问 <http://127.0.0.1:8010/api/v1/health>。
- **“数据库未就绪”**：运行 `docker compose up -d postgres` 和 `alembic upgrade head`。
- **“代码仓库不存在”**：从下拉框选 `order_service`；目录必须位于项目的 `sample_repos/` 中。
- **PyCharm 报 `app` 导入无法解析**：打开本 README 所在的项目根目录，并将 `backend/` 标记为 Sources Root，解释器选择 `.venv/Scripts/python.exe`。

## 许可证

本项目使用 [MIT License](LICENSE)。
