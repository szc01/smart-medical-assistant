"""
MySQL 数据库连接模块
用途：用户认证（登录、注册）
数据库：medical_assistant
包含的表：users
"""
# 从 sqlalchemy 导入 create_engine 函数（用于创建数据库连接引擎）
from sqlalchemy import create_engine
# 从 sqlalchemy.ext.declarative 导入 declarative_base 函数（用于创建 ORM 模型基类）
from sqlalchemy.ext.declarative import declarative_base
# 从 sqlalchemy.orm 导入 sessionmaker 函数（用于创建数据库会话工厂）
from sqlalchemy.orm import sessionmaker
# 从项目配置模块导入 settings 对象（包含数据库连接字符串等配置）
from app.config.settings import settings

# 创建 MySQL 数据库连接引擎
# 功能：管理与 MySQL 数据库的连接池，负责建立和维护数据库连接
engine = create_engine(
    # 从配置中获取 MySQL 数据库连接字符串（格式：mysql://user:password@host:port/dbname）
    settings.DATABASE_URL,
    # 连接前自动 ping 测试，确保连接有效，避免使用已断开的连接
    pool_pre_ping=True,
    # 连接回收时间（秒），3600秒=1小时，定期回收旧连接避免连接过期
    pool_recycle=3600
)

# 创建数据库会话工厂
# 功能：用于创建数据库会话对象（Session），每个会话代表一次数据库交互
SessionLocal = sessionmaker(
    # 不自动提交事务，需要手动调用 commit() 提交，保证数据一致性
    autocommit=False,
    # 不自动刷新对象，需要手动调用 flush() 刷新，提高性能
    autoflush=False,
    # 绑定到上面创建的数据库引擎，使用这个引擎建立连接
    bind=engine
)

# 创建 ORM 模型基类
# 功能：所有 MySQL 数据模型的父类，模型继承此类后会自动映射到数据库表
# 使用方式：class User(Base): ... 定义模型，Base.metadata.create_all() 自动创建表
Base = declarative_base()


# 获取数据库会话（用于FastAPI依赖注入）
def get_db():
    # 创建一个新的数据库会话实例
    db = SessionLocal()
    try:
        # 将数据库会话对象返回给调用者（路由函数）
        yield db
    finally:
        # 无论请求成功还是失败，finally块都会执行
        # 关闭数据库会话，释放连接回连接池
        db.close()
