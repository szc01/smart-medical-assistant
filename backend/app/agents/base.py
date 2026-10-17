"""
基础模块 - 提供模型初始化和通用工具
"""
from langchain_openai import ChatOpenAI
from app.config.settings import settings


# 获取聊天模型实例（使用阿里云百炼的 qwen3.5-plus 模型）
def get_model():
    return ChatOpenAI(
        model="qwen3.5-plus",  # 使用 qwen3.5-plus 模型
        api_key=settings.DASHSCOPE_API_KEY,  # 阿里云百炼 API Key
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",  # API 基础 URL
        temperature=0.3  # 控制输出的随机性，0.3 表示相对保守
    )
