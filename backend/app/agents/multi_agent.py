"""多智能体系统主入口

负责构建智能体图结构

4个智能体已拆分到独立文件：
- supervisor.py: 私人医疗顾问（监督者 + 导诊）
- medical_examiner.py: 体检员（信息收集）
- attending_doctor.py: 主治医生（诊断开药）
- pharmacist.py: 药师（用药指导）
"""

# 导入LangGraph相关组件
from langgraph.graph import StateGraph, END

# 导入状态定义
from app.agents.state import MedicalAgentState

# 导入记忆管理
from app.agents.memory import (
    init_memory_system,
    get_short_term_memory,
    get_long_term_memory
)

# 导入智能体节点
from app.agents.supervisor import supervisor_node
from app.agents.medical_examiner import medical_examiner_node
from app.agents.attending_doctor import attending_doctor_node
from app.agents.pharmacist import pharmacist_node

# ==================== 构建多智能体系统 ====================
# 创建医疗智能体系统（中心辐射式架构：supervisor路由到子智能体，一问一答模式）
def create_medical_agent_system():
    # 初始化记忆系统
    init_memory_system()
    
    # 获取记忆实例（使用函数确保获取最新实例）
    checkpointer = get_short_term_memory()
    store = get_long_term_memory()
    
    builder = StateGraph(MedicalAgentState)
    
    # 添加节点（4个智能体，从各自文件导入）
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("attending_doctor", attending_doctor_node)
    builder.add_node("medical_examiner", medical_examiner_node)
    builder.add_node("pharmacist", pharmacist_node)
    
    # 入口点
    builder.set_entry_point("supervisor")
    
    # supervisor 的条件边：根据 LLM 路由决策
    builder.add_conditional_edges(
        "supervisor",
        lambda state: state.get("next", "__end__"),
        {
            "attending_doctor": "attending_doctor",
            "medical_examiner": "medical_examiner",
            "pharmacist": "pharmacist",
            "__end__": END
        }
    )
    
    # 所有子智能体完成后直接结束（一问一答）
    builder.add_edge("attending_doctor", END)
    builder.add_edge("medical_examiner", END)
    builder.add_edge("pharmacist", END)
    
    # 使用函数返回的记忆实例（确保已初始化）
    graph = builder.compile(checkpointer=checkpointer, store=store)
    
    return graph
