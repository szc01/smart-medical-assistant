"""
配置模块
直接从环境变量读取配置，无需 pydantic-settings
"""
import os
from dotenv import load_dotenv

# 加载 .env 文件（从 backend 根目录）
from pathlib import Path
env_path = Path(__file__).parent.parent.parent / ".env"
# 强制覆盖已存在的环境变量
load_dotenv(dotenv_path=env_path, override=True)  


class Settings:
    """应用配置 - 直接从环境变量读取"""
    
    # AI 模型配置
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
    # DeepSeek Search API（可选替代 Tavily，见 .env 注释）
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    
    # LangSmith 配置
    LANGCHAIN_TRACING_V2 = os.getenv("LANGCHAIN_TRACING_V2", "true").lower() == "true"
    LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT", "medical_assistant")
    
    # 数据库配置
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    POSTGRES_SHORT_TERM_URL = os.getenv("POSTGRES_SHORT_TERM_URL", "")
    POSTGRES_LONG_TERM_URL = os.getenv("POSTGRES_LONG_TERM_URL", "")
    POSTGRES_SESSION_URL = os.getenv("POSTGRES_SESSION_URL", "")
    
    # 应用配置
    SECRET_KEY = os.getenv("SECRET_KEY", "default-secret-key")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    
    # 服务器配置
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8000"))


# 创建全局配置实例
settings = Settings()
