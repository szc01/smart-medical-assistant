"""聊天 API 模块
提供聊天接口（非流式，一次性返回完整内容）
短记忆由LangGraph自动管理（PostgresSaver）
支持文档上传后的智能分析
"""
# 导入正则表达式模块（用于匹配文档上传消息）
import re
# 导入 FastAPI 相关组件：APIRouter(路由)、Depends(依赖注入)、HTTPException(HTTP异常)、status(状态码)
from fastapi import APIRouter, Depends, HTTPException, status
# 导入 JSON 响应类（用于返回 JSON 格式数据）
from fastapi.responses import JSONResponse
# 导入 SQLAlchemy 数据库会话类型
from sqlalchemy.orm import Session
# 导入 Pydantic 基础模型（用于数据验证）
from pydantic import BaseModel
# 导入 LangChain 的人类消息类（用于构建对话消息）
from langchain_core.messages import HumanMessage
# 导入日期时间模块（用于更新会话时间）
from datetime import datetime
# 导入 PostgreSQL 数据库会话工厂函数
from app.database.postgres import get_postgres_db
# 导入用户认证函数（获取当前登录用户）
from app.api.auth import get_current_user
# 导入用户归属校验函数（与偏好接口共用，避免复制校验逻辑）
from app.api.preferences import _resolve_user_id
# 导入用户 ORM 模型
from app.models.user import User
# 导入会话 ORM 模型
from app.models.conversation import Conversation
# 导入医疗智能体系统创建函数
from app.agents.multi_agent import create_medical_agent_system

# 创建聊天路由实例
router = APIRouter()


class ChatRequest(BaseModel):
    """聊天请求数据模型：定义前端发送的数据结构"""
    # 用户 ID（字符串类型）
    user_id: str
    # 用户消息内容
    message: str
    # 会话线程 ID（用于区分不同会话）
    thread_id: str


@router.post("/send")
async def send_message(
    # 聊天请求数据（从 JSON body 中解析）
    request: ChatRequest,
    # 当前登录用户（依赖注入，用于权限验证）
    current_user: User = Depends(get_current_user),
    # PostgreSQL 数据库会话（依赖注入）
    db: Session = Depends(get_postgres_db)
):
    # 校验请求中的 user_id 与登录态一致，防止越权读写长记忆
    user_id = _resolve_user_id(request.user_id, current_user)
    # 发送消息接口（非流式，短记忆自动持久化，支持文档上传分析）
    try:
        # 从数据库查询会话记录（根据 thread_id）
        conversation = db.query(Conversation).filter(
            Conversation.id == request.thread_id
        ).first()
        # 会话归属校验：禁止通过 thread_id 访问他人会话
        if conversation and conversation.user_id != current_user.id:
            raise HTTPException(403, "无权访问该会话")
        # 如果会话存在
        if conversation:
            # 更新会话的最后一条消息（截断到50字符，超长则加省略号）
            conversation.last_message = request.message[:50] + ("..." if len(request.message) > 50 else "")
            # 更新会话的最后活跃时间为当前时间
            conversation.last_active = datetime.now()
            # 提交事务，保存更新到数据库
            db.commit()
        
        # 创建医疗智能体系统（包含4个智能体节点）
        graph = create_medical_agent_system()
        
        # 构建智能体的初始状态
        initial_state = {
            # 用户消息列表（包含当前消息）
            "messages": [HumanMessage(content=request.message)],
            # 用户 ID
            "user_id": user_id,
            # 会话线程 ID
            "thread_id": request.thread_id,
            # 医疗文档列表（初始为空）
            "medical_documents": [],
            # 下一个节点（由路由决定）
            "next": None
        }
        
        # 配置参数：用于 LangGraph 检查点（自动保存短记忆）
        config = {
            "configurable": {
                # 用户 ID（用于长记忆命名空间）
                "user_id": user_id,
                # 会话线程 ID（用于短记忆隔离）
                "thread_id": request.thread_id
            }
        }
        
        # 初始化助手回复内容为空字符串
        assistant_content = ""
        try:
            # 调用智能体系统，传入初始状态和配置（非阻塞，等待完整回复）
            # 短记忆会自动保存到PostgresSaver（对话历史）
            result = graph.invoke(initial_state, config=config)
            # 如果返回结果中包含消息列表
            if result.get("messages"):
                # 获取最后一条消息（助手的回复）
                last_message = result["messages"][-1]
                # 提取消息内容（文本）
                assistant_content = last_message.content
        except Exception as e:
            # 如果智能体执行失败，打印错误日志
            print(f"[ERROR] 智能体执行失败: {e}")
            # 导入 traceback 模块
            import traceback
            # 打印详细的错误堆栈信息
            print(traceback.format_exc())
            # 抛出 500 服务器错误
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"智能体执行失败: {str(e)}"
            )
        
        # 返回完整回复内容（非流式，一次性返回）
        return JSONResponse(
            content={
                # 请求成功标志
                "success": True,
                # 助手的完整回复内容
                "message": assistant_content
            },
            # HTTP 状态码 200（成功）
            status_code=200
        )
    except HTTPException:
        # 归属校验等 HTTPException 直接抛出，避免被兜底包装成 500
        raise
    except Exception as e:
        # 如果发生未捕获的异常，导入 traceback 模块
        import traceback
        # 打印详细错误堆栈信息
        print(f"发送消息失败: {traceback.format_exc()}")
        # 抛出 500 服务器错误
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"发送消息失败: {str(e)}"
        )
