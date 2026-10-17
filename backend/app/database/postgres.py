"""PostgreSQL数据库连接模块
用途：会话管理（会话列表、消息记录、医疗文档）
数据库：smart_session
包含的表：conversations、medical_documents
"""
# 导入SQLAlchemy相关组件
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
# 导入项目配置
from app.config.settings import settings

# 创建PostgreSQL数据库连接引擎
postgres_engine = create_engine(
    # 从配置中获取PostgreSQL会话数据库连接字符串
    settings.POSTGRES_SESSION_URL,
    # 连接前自动ping测试，确保连接有效
    pool_pre_ping=True,
    # 连接回收时间（秒），3600秒=1小时
    pool_recycle=3600
)

# 创建数据库会话工厂
PostgresSessionLocal = sessionmaker(
    # 不自动提交事务，需要手动调用commit()
    autocommit=False,
    # 不自动刷新对象，需要手动调用flush()
    autoflush=False,
    # 绑定到PostgreSQL数据库引擎
    bind=postgres_engine
)

# 创建ORM模型基类
PostgresBase = declarative_base()


# 获取PostgreSQL数据库会话（用于FastAPI依赖注入）
def get_postgres_db():
    # 创建一个新的PostgreSQL数据库会话实例
    db = PostgresSessionLocal()
    try:
        # 将数据库会话对象返回给调用者
        yield db
    finally:
        # 无论请求成功还是失败，finally块都会执行
        # 关闭数据库会话，释放连接回连接池
        db.close()
