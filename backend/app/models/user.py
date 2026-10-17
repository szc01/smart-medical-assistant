"""MySQL 数据模型
用于用户认证相关的数据表
"""
# 导入 SQLAlchemy 列类型：整数、字符串、日期时间
from sqlalchemy import Column, Integer, String, DateTime
# 导入 SQL 函数（用于自动设置时间戳）
from sqlalchemy.sql import func
# 导入 MySQL 数据库基类
from app.database.mysql import Base


# 用户模型
class User(Base):
    # 表名 ORM框架需要指定
    __tablename__ = "users"
    
    # 用户ID（主键，自增）
    id = Column(Integer, primary_key=True, index=True)
    # 用户名（唯一，必填）
    username = Column(String(100), unique=True, index=True, nullable=False)
    # 邮箱（唯一，必填）
    email = Column(String(100), unique=True, index=True, nullable=False)
    # 密码哈希（必填）
    password_hash = Column(String(255), nullable=False)
    # 创建时间
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # 更新时间
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
