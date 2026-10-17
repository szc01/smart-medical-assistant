"""医疗文档信息提取模块
上传医疗文档后，使用 LLM + Pydantic 提取关键信息并保存到长记忆
"""
# 导入 PDF 文档加载器
from langchain_community.document_loaders import PyPDFLoader
# 导入 ChatOpenAI 模型
from langchain_openai import ChatOpenAI
# 导入项目配置
from app.config.settings import settings
# 导入 Pydantic
from pydantic import BaseModel, Field
from typing import Optional, List, Any


class MedicalRecord(BaseModel):
    #医疗记录结构化数据模型
    #注意：字段类型设计为宽松模式，兼容 LLM 返回的不同格式
    
    # 基本信息
    basic_info: Optional[dict] = Field(default_factory=dict, description="基本信息对象，包含：name（姓名）、age（年龄）、gender（性别）、phone（电话）、email（邮箱）、address（地址）、id_card（身份证号）")
    
    # 医疗信息（使用 Any 类型兼容 LLM 返回的字符串、列表、字典等不同格式）
    symptoms: Any = Field(default_factory=list, description="症状列表")
    medical_history: Any = Field(default_factory=list, description="既往病史列表")
    allergies: Any = Field(default_factory=list, description="过敏史列表")
    diagnoses: Any = Field(default_factory=list, description="诊断结果列表")
    medications: Any = Field(default_factory=list, description="用药记录列表")
    test_results: Any = Field(default_factory=list, description="检查结果列表")
    doctor_notes: Any = Field(default=None, description="医生备注（可能是字符串或列表）")


def extract_medical_info_from_pdf(file_path: str) -> MedicalRecord:
# 从 PDF 文档中提取医疗信息（使用 Pydantic 结构化输出）
# 参数: file_path - PDF 文件路径
# 返回值: MedicalRecord 对象，包含结构化的医疗信息
    try:
        # 打印开始处理日志
        print(f"[INFO] 开始处理 PDF: {file_path}")
        
        # 1. 加载 PDF 文档
        loader = PyPDFLoader(file_path)
        documents = loader.load()
        
        # 2. 合并所有页面文本
        full_text = "\n\n".join([doc.page_content for doc in documents])
        print(f"[INFO] PDF 加载成功，共 {len(documents)} 页，{len(full_text)} 字符")
        
        # 3. 初始化 LLM
        llm = ChatOpenAI(
            model="qwen3.5-plus",
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            temperature=0.1
        )
        
        # 4. 使用 with_structured_output 绑定 Pydantic 模型
        # include_raw=True 可以同时获取原始输出和解析后的对象
        structured_llm = llm.with_structured_output(MedicalRecord)
        
        # 5. 构建提示词
        system_prompt = """
你是专业的医疗信息提取专家，从医疗文档中提取关键信息。
【必须提取的字段】
1. basic_info（基本信息）- 必须提取！包含：
   - name: 患者姓名（字符串）
   - age: 年龄（整数或字符串，如"30"或"30岁"）
   - gender: 性别（男/女）
   - phone: 联系电话（字符串，如"13812345678"）
   - email: 电子邮箱（字符串，如"zhangsan@example.com"）
   - address: 家庭地址（字符串）
   - id_card: 身份证号（字符串）
   注意：如果文档中有这些信息，必须提取；如果没有，可以省略该字段
   
2. symptoms（症状）- 症状列表，每项可以是字符串或对象
3. medical_history（既往病史）- 历史疾病列表
4. allergies（过敏史）- 过敏药物或食物列表
5. diagnoses（诊断结果）- 医生诊断列表
6. medications（用药记录）- 当前或历史用药列表
7. test_results（检查结果）- 化验/检查结果列表
8. doctor_notes（医生备注）- 其他备注（字符串或列表）

【提取规则】
1. 只提取文档中明确提到的信息，不要臆测或编造
2. 如果文档中有患者姓名、年龄、性别、电话、邮箱、地址等，必须填入 basic_info
3. 如果某类信息确实不存在，返回空列表 [] 或 null
4. 保持原文表述，不要改写
5. 必须返回符合 MedicalRecord 格式的 JSON
"""

        # 6. 调用 LLM 提取信息（单次执行，失败即报错）
        print("[INFO] 正在调用 LLM 提取信息...")
        result = structured_llm.invoke([
            ("system", system_prompt),
            ("human", f"请从以下医疗文档中提取信息：\n\n{full_text[:8000]}")
        ])
        
        # 7. 打印提取结果（添加详细调试信息）
        print(f"[INFO] 信息提取成功")
        print(f"[DEBUG] basic_info = {result.basic_info}")
        print(f"[DEBUG] symptoms = {result.symptoms}")
        print(f"[DEBUG] medical_history = {result.medical_history}")
        print(f"[DEBUG] allergies = {result.allergies}")
        
        return result
        
    except Exception as e:
        # 打印错误日志
        print(f"[ERROR] PDF 信息提取失败: {e}")
        import traceback
        print(traceback.format_exc())
        # 返回空的 MedicalRecord 对象
        return MedicalRecord()


# 将提取的结构化信息（用户基本信息 + 医疗信息）保存到 Postgres 长记忆库，返回保存结果列表用于日志/前端
# 参数说明：store(长记忆存储器)、user_id(用户ID)、record(结构化医疗记录)、filename(原始PDF文件名)
def save_extracted_info_to_store(store, user_id: str, record: MedicalRecord, filename: str) -> list:
    saved_items = []
    
    # 1. 保存用户信息（基本信息）
    if record.basic_info:
        # 过滤 None 值并转为字符串
        basic_text = ", ".join([f"{k}: {v}" for k, v in record.basic_info.items() if v is not None])
        
        # 保存到用户基本信息 namespace
        namespace = ("user_preferences", user_id)
        store.put(namespace, f"basic_{filename}", {
            "key": "基本信息",
            "value": basic_text,
            "source": f"文档: {filename}"
        })
        saved_items.append(f"基本信息: {basic_text}")
        print(f"[INFO] 已保存用户信息到长记忆")
    
    # 2. 保存医疗信息（所有医疗相关字段）
    medical_parts = []
    
    # 定义医疗字段映射（字段名 -> 显示名称）
    medical_fields = {
        "symptoms": ("症状", record.symptoms),
        "medical_history": ("既往病史", record.medical_history),
        "allergies": ("过敏史", record.allergies),
        "diagnoses": ("诊断结果", record.diagnoses),
        "medications": ("用药记录", record.medications),
        "test_results": ("检查结果", record.test_results),
        "doctor_notes": ("医生备注", record.doctor_notes)
    }
    
    # 遍历医疗字段映射的值（显示名 + 内容），忽略键名
    for field_label, items in medical_fields.values():
        if items:
            # 兜底：若 items 是字符串，先转为单元素列表；若为 None/空，跳过
            if isinstance(items, str):
                items = [items]
            elif not isinstance(items, list):
                items = []
            content = "; ".join([str(item) for item in items if item is not None])
            medical_parts.append(f"{field_label}: {content}")
    
    # 合并所有医疗信息并保存
    if medical_parts:
        medical_text = "\n".join(medical_parts)
        namespace = ("user_medical_history", user_id)
        store.put(namespace, f"medical_{filename}", {
            "category": "医疗信息",
            "content": medical_text,
            "source": f"文档: {filename}"
        })
        saved_items.append(f"医疗信息: {medical_text[:50]}...")
        print(f"[INFO] 已保存 {len(medical_parts)} 项医疗信息到长记忆")
    
    return saved_items
