# 导入操作系统模块（用于文件路径和目录操作）
import os
# 导入 UUID 模块（用于生成唯一文件名）
import uuid
# 导入 FastAPI 相关组件：APIRouter(路由)、Depends(依赖注入)、HTTPException(HTTP异常)、status(状态码)、UploadFile(文件上传)、File(文件参数)、Form(表单参数)
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
# 导入 SQLAlchemy 数据库会话类型
from sqlalchemy.orm import Session
# 导入 PostgreSQL 数据库会话工厂函数
from app.database.postgres import get_postgres_db
# 导入用户认证函数（获取当前登录用户）
from app.api.auth import get_current_user
# 导入用户 ORM 模型
from app.models.user import User
# 导入医疗文档 ORM 模型
from app.models.medical_document import MedicalDocument
# 导入文档提取函数和保存函数
from app.rag.document_extractor import extract_medical_info_from_pdf, save_extracted_info_to_store
# 导入长记忆获取函数
from app.agents.multi_agent import get_long_term_memory

# 创建文档路由实例
router = APIRouter()

# 文档存储目录路径
UPLOAD_DIR = "./uploads"
# 如果目录不存在，则创建目录
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)


@router.post("/upload")
async def upload_document(
    # 上传的文件（必填）
    file: UploadFile = File(...),
    # 文档类型（必填，从表单获取）
    document_type: str = Form(...),
    # 用户 ID（必填，从表单获取）
    user_id: str = Form(...),
    # 当前登录用户（依赖注入，用于权限验证）
    current_user: User = Depends(get_current_user),
    # PostgreSQL 数据库会话（依赖注入）
    db: Session = Depends(get_postgres_db)
):
    """上传医疗文档，提取信息并保存到长记忆"""
    try:
        # 打印上传开始日志（文件名和用户 ID）
        print(f"[INFO] 开始上传文档: {file.filename}, 用户: {user_id}")
        
        # 检查文件扩展名是否为 PDF
        if not file.filename.endswith(".pdf"):
            # 如果不是 PDF，抛出 400 错误
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只支持PDF文件"
            )
        
        # 构建文件保存路径（格式：./uploads/{user_id}_{uuid}_{文件名}）
        file_path = os.path.join(UPLOAD_DIR, f"{user_id}_{uuid.uuid4().hex}_{file.filename}")
        # 以二进制写入模式打开文件
        with open(file_path, "wb") as f:
            # 异步读取上传文件的内容
            content = await file.read()
            # 将内容写入到本地文件
            f.write(content)
        # 打印文件保存成功日志
        print(f"[INFO] 文件保存成功: {file_path}")
        
        # 调用文档提取函数，从 PDF 中提取医疗信息（返回 MedicalRecord 对象）
        record = extract_medical_info_from_pdf(file_path)
        

        
        # 获取长记忆 store 实例（用于保存用户信息）
        store = get_long_term_memory()
        
        # 调用保存函数，将提取的信息存入长记忆
        saved_items = save_extracted_info_to_store(
            # 长记忆存储对象
            store=store,
            # 用户 ID
            user_id=user_id,
            # MedicalRecord 对象
            record=record,
            # 文件名（用于记录来源）
            filename=file.filename
        )
        
        # 创建医疗文档 ORM 对象（记录上传历史到数据库）
        #为什么需要手动？—— 因为没有自动 ORM 持久化机制
        db_document = MedicalDocument(
            # 用户 ID（字符串类型）
            user_id=user_id,
            # 文档名称
            document_name=file.filename,
            # 文档类型
            document_type=document_type,
            # 文件存储路径
            file_path=file_path,
            # 处理状态（1表示已处理）
            processed=1
        )
        # 将文档对象添加到数据库会话
        db.add(db_document)
        # 提交事务，保存到数据库
        db.commit()
        # 刷新文档对象，获取数据库生成的字段（如 id）
        db.refresh(db_document)
        
        # 返回成功响应
        return {
            # 提示信息
            "message": "文档上传成功，信息已提取并保存",
            # 文档 ID
            "document_id": db_document.id,
            # 保存的字段列表
            "extracted_items": saved_items,
            # 保存的字段总数
            "total_items": len(saved_items)
        }
    except Exception as e:
        # 导入 traceback 模块（用于打印详细错误堆栈）
        import traceback
        # 打印错误日志
        print(f"[ERROR] 上传文档失败: {e}")
        # 打印详细的错误堆栈信息
        print(traceback.format_exc())
        # 抛出 500 服务器错误
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"上传文档失败: {str(e)}"
        )



