# backend · 智能医疗助手后端

FastAPI + LangGraph 多智能体服务。完整的项目介绍、架构说明与启动步骤见仓库根目录的 [README](../README.md) 与 [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)。

## 快速启动

```bash
python -m venv .venv
.venv\Scripts\activate        # macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env        # macOS / Linux: cp .env.example .env
python main.py
```

- API 文档：<http://localhost:8000/docs>
- 启动时会自动创建 MySQL `users` 表与 PostgreSQL 会话表；三个记忆库需先用 `sql/init_postgres.sql` 创建。

## 目录

| 目录 | 职责 |
| --- | --- |
| `app/agents/` | 多智能体图：supervisor / 体检员 / 主治医生 / 药师、记忆、工具 |
| `app/api/` | 路由：auth、chat、conversations、medical_upload、preferences |
| `app/config/` | 环境变量加载（读取 `backend/.env`） |
| `app/database/` | MySQL / PostgreSQL 引擎与会话工厂 |
| `app/models/` | SQLAlchemy 模型 |
| `app/rag/` | PDF 解析与结构化信息提取 |
| `app/schemas/` | Pydantic 请求 / 响应模型 |
| `sql/` | 数据库初始化脚本 |
| `uploads/` | 上传文件落盘目录（内容不入库） |
