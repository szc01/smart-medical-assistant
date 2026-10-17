# 导入 FastAPI 相关组件：APIRouter(路由)、Depends(依赖注入)、HTTPException(HTTP异常)、status(状态码)
from fastapi import APIRouter, Depends, HTTPException, status
# 导入 OAuth2 认证相关：OAuth2PasswordBearer(令牌提取)、OAuth2PasswordRequestForm(登录表单)
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
# 导入 SQLAlchemy 数据库会话类型
from sqlalchemy.orm import Session
# 导入时间处理：datetime(日期时间)、timedelta(时间差)
from datetime import datetime, timedelta
# 导入 JWT 处理库：JWTError(异常)、jwt(编解码)
from jose import JWTError, jwt
# 导入密码加密库 bcrypt
import bcrypt
# 导入 MySQL 数据库会话工厂函数
from app.database.mysql import get_db
# 导入用户 ORM 模型
from app.models.user import User
# 导入用户相关的数据验证模型
from app.schemas.user import UserCreate, UserResponse, Token, TokenData
# 导入项目配置（密钥、过期时间等）
from app.config.settings import settings

# 创建认证路由实例
router = APIRouter()

# OAuth2 令牌获取方案：指定登录接口路径为 /auth/token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码：比较明文和哈希密码是否匹配"""
    # 截断密码到72字节以内，避免bcrypt库的限制
    truncated_password = plain_password[:72]
    # 使用 bcrypt 验证密码（明文编码后与哈希比较）
    return bcrypt.checkpw(truncated_password.encode('utf-8'), hashed_password.encode('utf-8'))


def get_password_hash(password: str) -> str:
    """获取密码哈希值：将明文密码加密为哈希字符串"""
    # 截断密码到72字节以内，避免bcrypt库的限制
    truncated_password = password[:72]
    # 生成随机盐值
    salt = bcrypt.gensalt()
    # 使用盐值对密码进行哈希加密
    hashed = bcrypt.hashpw(truncated_password.encode('utf-8'), salt)
    # 将字节类型转换为字符串返回
    return hashed.decode('utf-8')


def authenticate_user(db: Session, username: str, password: str):
    """认证用户：验证用户名和密码是否正确"""
    # 从数据库查询用户（根据用户名）
    user = db.query(User).filter(User.username == username).first()
    # 如果用户不存在，返回 False
    if not user:
        return False
    # 如果密码验证失败，返回 False
    if not verify_password(password, user.password_hash):
        return False
    # 验证通过，返回用户对象
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """创建访问令牌：生成 JWT token"""
    # 复制传入的数据字典，避免修改原始数据
    to_encode = data.copy()
    # 如果指定了过期时间差
    if expires_delta:
        # 计算过期时间 = 当前时间 + 时间差
        expire = datetime.utcnow() + expires_delta
    else:
        # 否则使用配置文件中的默认过期时间（分钟）
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    # 将过期时间添加到要编码的数据中
    to_encode.update({"exp": expire})
    # 使用密钥和算法对数据进行 JWT 编码
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    # 返回编码后的 token 字符串
    return encoded_jwt


async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """获取当前用户：从 token 中解析用户信息并返回用户对象"""
    # 定义认证异常（401 未授权）
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # 解码 JWT token，验证签名和过期时间
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        # 从 token 载荷中获取用户 ID（sub 字段）
        user_id: int = payload.get("sub")
        # 如果 user_id 不存在，抛出认证异常
        if user_id is None:
            raise credentials_exception
        # 创建 token 数据对象（包含用户 ID）
        token_data = TokenData(user_id=int(user_id))
    except JWTError:
        # JWT 解码失败（签名错误或已过期），抛出认证异常
        raise credentials_exception
    # 从数据库查询用户（根据用户 ID）
    user = db.query(User).filter(User.id == token_data.user_id).first()
    # 如果用户不存在，抛出认证异常
    if user is None:
        raise credentials_exception
    # 返回用户对象
    return user


@router.post("/register", response_model=UserResponse)
def register(user: UserCreate, db: Session = Depends(get_db)):
    """用户注册：创建新用户账号"""
    # 检查用户名或邮箱是否已存在（或查询）
    existing_user = db.query(User).filter((User.username == user.username) | (User.email == user.email)).first()
    # 如果用户已存在
    if existing_user:
        # 如果是用户名重复
        if existing_user.username == user.username:
            # 抛出 400 错误：用户名已存在
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="用户名已存在"
            )
        else:
            # 否则是邮箱重复
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="邮箱已被注册"
            )
    
    # 对用户密码进行哈希加密
    hashed_password = get_password_hash(user.password)
    # 创建用户 ORM 对象
    db_user = User(
        username=user.username,      # 用户名
        email=user.email,            # 邮箱
        password_hash=hashed_password  # 加密后的密码
    )
    # 将用户对象添加到数据库会话
    db.add(db_user)
    # 提交事务，保存到数据库
    db.commit()
    # 刷新用户对象，获取数据库生成的字段（如 id、created_at）
    db.refresh(db_user)
    # 返回创建成功的用户对象
    return db_user


@router.post("/token", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """用户登录，获取访问令牌"""
    # 调用认证函数，验证用户名和密码
    user = authenticate_user(db, form_data.username, form_data.password)
    # 如果认证失败（用户不存在或密码错误）
    if not user:
        # 抛出 401 错误：用户名或密码错误
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # 计算 token 过期时间（从配置读取）
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    # 创建访问令牌（用户 ID 作为 sub 字段）
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires
    )
    # 返回 token 信息（访问令牌、类型、用户 ID）
    return {"access_token": access_token, "token_type": "bearer", "user_id": user.id}
