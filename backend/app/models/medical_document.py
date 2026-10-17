"""PostgreSQL医疗文档模型"""
# 导入SQLAlchemy列类型
from sqlalchemy import Column, Integer, String, DateTime
# 导入SQL函数
from sqlalchemy.sql import func
# 导入PostgreSQL数据库基类
from app.database.postgres import PostgresBase


# 医疗文档模型（存储用户上传的医疗文档信息）
class MedicalDocument(PostgresBase):
    # 表名
    __tablename__ = "medical_documents"
    
    # 文档ID（主键，自增）
    id = Column(Integer, primary_key=True, index=True)
    # 用户ID（字符串格式，如 user_3）
    user_id = Column(String(100), nullable=False, index=True)
    # 文档名称
    document_name = Column(String(255), nullable=False)
    # 文档类型（体检报告、检测报告、病历本等）
    document_type = Column(String(100), nullable=False)
    # 文件存储路径
    file_path = Column(String(255), nullable=False)
    # 上传时间
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    # 处理状态（0=未处理，1=已处理）
    processed = Column(Integer, default=0)
