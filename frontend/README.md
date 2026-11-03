# ChangePilot 前端

零构建的本地演示界面。页面调用正式 API 1.0，自动检查服务和数据库状态，并从 `sample_repos/` 加载可选代码仓库。完整安装和运行说明见项目根目录的 [README](../README.md)。

在项目根目录运行前端（系统 Python 只负责提供静态文件）：

```powershell
cd frontend
python -m http.server 5173 --bind 127.0.0.1
```

打开 `http://127.0.0.1:5173`。如果看到 `Directory listing for /`，说明静态服务器从错误目录启动；请先停止该服务，再从本项目的 `frontend/` 目录启动。另一个终端回到项目根目录，启动数据库，再用项目虚拟环境的 Python 运行后端：

```powershell
docker compose up -d postgres
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

真实分析还需要在 `backend/.env` 配置模型密钥。仓库从下拉框选择，例如 `order_service`，无需输入路径。没有后端时可点击“打开离线示例”预览报告界面；示例不会写入数据库或消耗模型 Token。

左侧“变更分析”“分析报告”“分析时间线”会切换独立页面。报告和时间线先显示数据库中的历史卡片，点击后才加载完整内容，详情页可返回卡片列表。时间线同时显示中文事件说明和后端英文事件标识，方便阅读与排查。

如果 PyCharm 显示 `sqlalchemy` 等导入无法解析，请在 **Settings → Project → Python Interpreter** 中选择项目根目录的 `.venv\Scripts\python.exe`。如果后端命令报 `No module named app`，确认终端当前位于 `backend` 目录。
