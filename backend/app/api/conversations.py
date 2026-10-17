# 导入 FastAPI 相关组件：APIRouter(路由)、Depends(依赖注入)、HTTPException(HTTP异常)、status(状态码)
from fastapi import APIRouter, Depends, HTTPException, status
# 导入 SQLAlchemy 数据库会话类型
from sqlalchemy.orm import Session
# 导入 Pydantic 基础模型（用于数据验证）
from pydantic import BaseModel
# 导入日期时间模块（用于创建会话时间）
from datetime import datetime
# 导入 PostgreSQL 数据库会话工厂函数
from app.database.postgres import get_postgres_db
# 导入用户认证函数（获取当前登录用户）
from app.api.auth import get_current_user
# 导入用户 ORM 模型
from app.models.user import User
# 导入会话 ORM 模型
from app.models.conversation import Conversation

# 创建会话路由实例
router = APIRouter()

# 会话创建请求模型：定义前端发送的数据结构
class ConversationCreate(BaseModel):
    # 会话 ID（由前端生成）
    id: str
    # 会话标题
    title: str


# 创建新会话接口
@router.post("/create")
async def create_conversation(
    # 会话创建数据（从 JSON body 中解析）
    conversation: ConversationCreate,
    # 用户 ID（从查询参数获取）
    user_id: int,
    # 当前登录用户（依赖注入，用于权限验证）
    current_user: User = Depends(get_current_user),
    # PostgreSQL 数据库会话（依赖注入）
    db: Session = Depends(get_postgres_db)
):
    """创建新会话"""
    try:
        # 从数据库查询会话 ID 是否已存在
        existing_conv = db.query(Conversation).filter(
            Conversation.id == conversation.id
        ).first()
        
        # 如果会话 ID 已存在
        if existing_conv:
            # 抛出 400 错误：会话ID已存在
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="会话ID已存在"
            )
        
        # 创建新会话 ORM 对象
        new_conv = Conversation(
            id=conversation.id,                        # 会话 ID
            user_id=user_id,                           # 用户 ID
            title=conversation.title,                  # 会话标题
            last_message="",                           # 最后一条消息（初始为空）
            last_active=datetime.now(),                # 最后活跃时间（当前时间）
            created_at=datetime.now()                  # 创建时间（当前时间）
        )
        
        # 将会话对象添加到数据库会话
        db.add(new_conv)
        # 提交事务，保存到数据库
        db.commit()
        # 刷新会话对象，获取数据库生成的字段
        db.refresh(new_conv)
        
        # 返回创建成功的响应
        return {
            "message": "会话创建成功",
            "conversation": {
                # 会话 ID
                "id": new_conv.id,
                # 会话标题
                "title": new_conv.title,
                # 最后一条消息
                "last_message": new_conv.last_message,
                # 最后活跃时间
                "last_active": new_conv.last_active,
                # 创建时间
                "created_at": new_conv.created_at
            }
        }
    except HTTPException:
        # 如果是 HTTPException（如会话ID已存在），直接抛出，不包装
        raise
    except Exception as e:
        # 如果发生其他异常，抛出 500 服务器错误
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建会话失败: {str(e)}"
        )

# 获取会话列表接口
@router.get("/list")
async def list_conversations(
    # 用户 ID（从查询参数获取）
    user_id: int,
    # 当前登录用户（依赖注入，用于权限验证）
    current_user: User = Depends(get_current_user),
    # PostgreSQL 数据库会话（依赖注入）
    db: Session = Depends(get_postgres_db)
):
    """获取用户的会话列表"""
    try:
        # 从数据库查询该用户的所有会话，按最后活跃时间降序排列
        conversations = db.query(Conversation).filter(
            Conversation.user_id == user_id
        ).order_by(Conversation.last_active.desc()).all()
        
        # 初始化结果列表
        result = []
        # 遍历每个会话
        for conv in conversations:
            # 将会话对象转换为字典格式
            result.append({
                # 会话 ID
                "id": conv.id,
                # 会话标题
                "title": conv.title,
                # 最后一条消息
                "last_message": conv.last_message,
                # 最后活跃时间
                "last_active": conv.last_active,
                # 创建时间
                "created_at": conv.created_at
            })
        
        # 返回会话列表
        return {
            "conversations": result
        }
    except Exception as e:
        # 如果发生异常，抛出 500 服务器错误
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取会话列表失败: {str(e)}"
        )



# 获取会话详情接口
@router.get("/get")
async def get_conversation(
    # 会话 ID（从查询参数获取）
    conversation_id: str,
    # 当前登录用户（依赖注入，用于权限验证）
    current_user: User = Depends(get_current_user),
    # PostgreSQL 数据库会话（依赖注入）
    db: Session = Depends(get_postgres_db)
):
    """获取会话详情和消息列表（从LangGraph短记忆中读取）"""
    try:
        # 从数据库查询会话记录
        conversation = db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()
        
        # 如果会话不存在
        if not conversation:
            # 抛出 404 错误：会话不存在
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在"
            )
        
        # 初始化消息列表（用于存储对话历史）
        message_list = []
        try:
            # 导入短记忆获取函数
            from app.agents.multi_agent import get_short_term_memory
            # 获取短记忆 checkpointer 实例（PostgresSaver）
            checkpointer = get_short_term_memory()
            # 如果 checkpointer 存在
            if checkpointer:
                # 构建配置参数（指定线程 ID）
                config = {"configurable": {"thread_id": conversation_id}}
                # 列出所有 checkpoint（最新的在前面，index 0）
                checkpoints = list(checkpointer.list(config))
                
                # 如果存在 checkpoint
                if checkpoints:
                    # 获取最新的 checkpoint
                    latest = checkpoints[0]
                    # 提取 checkpoint 数据
                    checkpoint_data = latest.checkpoint
                    # 获取通道值（包含消息列表）
                    channel_values = checkpoint_data.get("channel_values", {})
                    # 提取消息列表
                    messages = channel_values.get("messages", [])
                    
                    # 遍历消息列表，只保留用户和AI的对话消息
                    for msg in messages:
                        # 如果消息对象有 content 和 type 属性
                        if hasattr(msg, 'content') and hasattr(msg, 'type'):
                            # 只保留 human（用户）和 ai（助手）类型的消息
                            # 过滤掉 tool（工具调用）、system（系统消息）等中间消息
                            if msg.type in ['human', 'ai']:
                                # 过滤掉空内容的消息（工具调用产生的空AI消息）
                                if msg.content and str(msg.content).strip():
                                    # 根据消息类型确定角色（human -> user，ai -> assistant）
                                    role = "user" if msg.type == "human" else "assistant"
                                    # 将消息添加到列表
                                    message_list.append({
                                        "role": role,           # 消息角色（user/assistant）
                                        "content": msg.content  # 消息内容
                                    })
        except Exception as e:
            # 如果读取短记忆失败，打印错误日志（非致命，不影响主流程）
            print(f"[ERROR] 读取短记忆失败: {e}")
            # 导入 traceback 模块
            import traceback
            # 打印详细的错误堆栈信息
            print(traceback.format_exc())
        
        # 返回会话详情和消息列表
        return {
            "conversation": {
                # 会话 ID
                "id": conversation.id,
                # 会话标题
                "title": conversation.title,
                # 最后一条消息
                "last_message": conversation.last_message,
                # 最后活跃时间
                "last_active": conversation.last_active,
                # 创建时间
                "created_at": conversation.created_at
            },
            # 对话历史消息列表
            "messages": message_list
        }
    except HTTPException:
        # 如果是 HTTPException（如会话不存在），直接抛出，不包装
        raise
    except Exception as e:
        # 如果发生其他异常，抛出 500 服务器错误
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取会话详情失败: {str(e)}"
        )

# 删除会话接口
@router.delete("/delete")
async def delete_conversation(
    # 会话 ID（从查询参数获取）
    conversation_id: str,
    # 当前登录用户（依赖注入，用于权限验证）
    current_user: User = Depends(get_current_user),
    # PostgreSQL 数据库会话（依赖注入）
    db: Session = Depends(get_postgres_db)
):
    """删除会话（MySQL会话记录 + LangGraph短记忆checkpoints）"""
    try:
        # 从数据库查询会话记录
        conversation = db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()
        
        # 如果会话不存在
        if not conversation:
            # 抛出 404 错误：会话不存在
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在"
            )
        
        # 1. 删除 LangGraph 短记忆中的 checkpoints 数据（PostgreSQL smart_short 库）
        try:
            # 导入短记忆获取函数
            from app.agents.multi_agent import get_short_term_memory
            # 获取 checkpointer 实例
            checkpointer = get_short_term_memory()
            # 如果 checkpointer 存在
            if checkpointer:
                # 删除该线程的所有 checkpoint
                checkpointer.delete_thread(thread_id=conversation_id)
                # 打印成功日志
                print(f"[OK] 清理短记忆checkpoint: thread_id={conversation_id}")
        except Exception as e:
            # 如果清理短记忆失败，打印警告日志（非致命，继续删除数据库记录）
            print(f"[WARN] 清理短记忆失败（非致命）: {e}")
        
        # 2. 删除 PostgreSQL 数据库中的会话记录
        db.delete(conversation)
        # 提交事务，保存删除操作
        db.commit()
        
        # 返回删除成功的响应
        return {
            "message": "会话删除成功"
        }
    except HTTPException:
        # 如果是 HTTPException（如会话不存在），直接抛出，不包装
        raise
    except Exception as e:
        # 如果发生其他异常，抛出 500 服务器错误
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除会话失败: {str(e)}"
        )
