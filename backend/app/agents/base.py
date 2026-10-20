"""
基础模块 - 提供模型初始化和通用工具
"""
from langchain_openai import ChatOpenAI
from app.config.settings import settings


# 获取聊天模型实例（使用阿里云百炼的 qwen3.5-plus 模型）
def get_model(disable_thinking: bool = False):
    extra = {"enable_thinking": False} if disable_thinking else None
    return ChatOpenAI(
        model="qwen3.5-plus",  # 使用 qwen3.5-plus 模型
        api_key=settings.DASHSCOPE_API_KEY,  # 阿里云百炼 API Key
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",  # API 基础 URL
        temperature=0.3,  # 控制输出的随机性，0.3 表示相对保守
        **({"extra_body": extra} if extra else {}),
    )


def get_deepseek_model():
    """DeepSeek 模型，目前只给药师节点用。

    原因：药师的回答需要严格控长、且不能照抄检索资料。同样的 prompt 与同一份检索结果，
    qwen3.5-plus 输出 366 字并抄下网页符号，deepseek-chat 输出 88 字且完整达标
    （含副作用、风险提示、主动追问），耗时 1.4s vs 约 6s。

    DEEPSEEK_API_KEY 未配置时回退到 qwen，避免药师节点整条链路不可用。
    """
    if not settings.DEEPSEEK_API_KEY:
        return get_model()
    return ChatOpenAI(
        model="deepseek-chat",
        api_key=settings.DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
        # temperature=0：药师的输出要的是稳定复现，不是多样性。
        # 实测 0.3 时同一问题会出现 87 字（达标）与 496 字（照抄资料）两种结果。
        temperature=0,
        timeout=60,
    )
