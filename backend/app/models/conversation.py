"""PostgreSQL会话模型"""
# 导入SQLAlchemy列类型
from sqlalchemy import Column, Integer, String, DateTime, Text
# 导入SQL函数
from sqlalchemy.sql import func
# 导入PostgreSQL数据库基类
from app.database.postgres import PostgresBase


# 会话列表模型（存储用户的会话列表信息）
class Conversation(PostgresBase):
    # 表名 ORM框架需要指定
    __tablename__ = "conversations"
    
    # 会话ID（主键，字符串）
    id = Column(String(100), primary_key=True, index=True)
    # 用户ID
    user_id = Column(Integer, nullable=False, index=True)
    # 会话标题
    title = Column(String(255), nullable=False)
    # 最后一条消息内容
    last_message = Column(Text)
    # 最后活跃时间
    last_active = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # 创建时间
    created_at = Column(DateTime(timezone=True), server_default=func.now())
