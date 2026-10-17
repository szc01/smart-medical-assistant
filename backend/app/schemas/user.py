# 导入 Pydantic 基础模型和邮箱验证器
from pydantic import BaseModel, EmailStr
# 导入日期时间类型
from datetime import datetime
# 导入可选类型
from typing import Optional


# 用户基础模型：定义用户的基本字段
class UserBase(BaseModel):
    # 用户名（字符串类型）
    username: str
    # 邮箱（自动验证邮箱格式）
    email: EmailStr


# 用户注册模型：继承 UserBase，添加密码字段
class UserCreate(UserBase):
    # 密码（注册时必填）
    password: str


# 用户响应模型：返回给前端的用户信息
class UserResponse(UserBase):
    # 用户 ID（整数类型）
    id: int
    # 创建时间
    created_at: datetime
    # 更新时间
    updated_at: datetime
    
    # 配置：允许从 ORM 对象创建
    class Config:
        from_attributes = True


# Token 响应模型：登录后返回的令牌信息
class Token(BaseModel):
    # 访问令牌（JWT 字符串）
    access_token: str
    # 令牌类型（bearer）
    token_type: str
    # 用户 ID
    user_id: int


# Token 数据模型：用于解析 JWT 令牌
class TokenData(BaseModel):
    # 用户 ID（可选，解析失败时为 None）
    user_id: Optional[int] = None
