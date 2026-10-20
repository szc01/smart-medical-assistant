"""pytest 全局配置。

CI 环境里没有 backend/.env，而部分被测模块在 **import 阶段**就会用配置创建
SQLAlchemy engine、RAG 客户端等。连接串为空时 SQLAlchemy 会在解析阶段直接抛
ArgumentError，导致测试连收集都跑不到。

所以这里在 pytest 收集测试之前注入一组格式合法、但永远不会被真正使用的占位值：

- 单元测试本身不连数据库、不调网络（假 embedding + tmp_path）
- 本地开发时存在 .env，`load_dotenv(override=True)` 会用真实值覆盖这些占位值，
  因此不影响本地行为
"""

import os

_PLACEHOLDERS = {
    "DATABASE_URL": "mysql+mysqlconnector://user:pass@localhost:3306/placeholder",
    "POSTGRES_SHORT_TERM_URL": "postgresql://user:pass@localhost:5432/placeholder",
    "POSTGRES_LONG_TERM_URL": "postgresql://user:pass@localhost:5432/placeholder",
    "POSTGRES_SESSION_URL": "postgresql+psycopg://user:pass@localhost:5432/placeholder",
    "DASHSCOPE_API_KEY": "sk-placeholder-for-unit-tests",
    "TAVILY_API_KEY": "tvly-placeholder-for-unit-tests",
    # 留空：让 search_web 走 Tavily 降级分支，不触发 DeepSeek 请求
    "DEEPSEEK_API_KEY": "",
    "SECRET_KEY": "placeholder-secret-for-unit-tests-only",
    "ALGORITHM": "HS256",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "1440",
    "HOST": "127.0.0.1",
    "PORT": "8000",
    "LANGCHAIN_TRACING_V2": "false",
}

for key, value in _PLACEHOLDERS.items():
    os.environ.setdefault(key, value)
