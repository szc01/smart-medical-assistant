"""
智能医疗助手 - 主入口
基于 LangChain 1.0.3 + LangGraph 1.0.2 + 多智能体协作
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config.settings import settings
from app.database.postgres import postgres_engine, PostgresBase
from app.api import auth, chat, medical_upload, conversations, preferences

# 初始化 LangSmith 追踪
import os
os.environ["LANGCHAIN_TRACING_V2"] = str(settings.LANGCHAIN_TRACING_V2).lower()
os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT

# MySQL 数据库表（用户认证）- 代码自动创建，也可手动执行 sql/init_mysql.sql
try:
    from app.database.mysql import Base as MySQLBase, engine as mysql_engine
    import app.models.user  # noqa: F401  确保模型已注册到 metadata

    MySQLBase.metadata.create_all(bind=mysql_engine)
    print("[OK] MySQL 数据库表创建成功")
except Exception as e:
    print(f"[WARN] MySQL 数据库表创建失败: {e}")

# PostgreSQL 数据库表（会话管理）- 代码自动创建
try:
    # 根据 SQLAlchemy 模型定义,在 PostgreSQL 数据库中创建所有缺失的表
    PostgresBase.metadata.create_all(bind=postgres_engine)
    print("[OK] PostgreSQL 数据库表创建成功")
except Exception as e:
    print(f"[WARN] PostgreSQL 数据库表创建失败: {e}")

# 创建 FastAPI 应用
app = FastAPI(
    title="智能医疗助手 API",
    description="基于 LangChain 1.x 和多智能体协作的医疗助手系统",
    version="1.0.0"
)

# 配置 CORS
# 添加跨域资源共享中间件,允许前端跨域访问后端API
app.add_middleware(
    # 使用FastAPI的CORS中间件
    CORSMiddleware,
    # 允许的源地址列表,["*"]表示允许所有域名(生产环境应设置具体域名)
    allow_origins=["*"],
    # 允许携带认证信息(cookies、authorization headers等)
    allow_credentials=True,
    # 允许的HTTP方法列表,["*"]表示允许所有方法(GET, POST, PUT, DELETE等)
    allow_methods=["*"],
    # 允许的HTTP请求头列表,["*"]表示允许所有请求头
    allow_headers=["*"],
)

# 注册路由 （认证、聊天、文档、会话管理、偏好设置）
app.include_router(auth.router, prefix="/auth", tags=["认证"])
app.include_router(chat.router, prefix="/chat", tags=["聊天"])
app.include_router(medical_upload.router, prefix="/documents", tags=["文档"])
app.include_router(conversations.router, prefix="/conversations", tags=["会话管理"])
app.include_router(preferences.router, prefix="/preferences", tags=["偏好设置"])


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "智能医疗助手 API",
        "version": "1.0.0",
        "docs": "/docs",
        "tech_stack": {
            "langchain": "1.0.3",
            "langgraph": "1.0.2",
            "python": "3.12.x"
        }
    }



if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*50)
    print("智能医疗助手 API 启动中...")
    print("="*50)
    print(f"访问地址: http://{settings.HOST}:{settings.PORT}")
    print(f"API 文档: http://{settings.HOST}:{settings.PORT}/docs")
    print("="*50 + "\n")
    
    # 启动FastAPI服务器
    uvicorn.run(
        # 指定应用入口: main.py文件中的app对象
        "main:app",
        # 服务器监听地址
        host=settings.HOST,
        # 服务器监听端口
        port=settings.PORT,
        # 开启热重载,代码修改后自动重启服务器
        reload=True
    )
