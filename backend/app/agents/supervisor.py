"""私人医疗顾问（监督者 + 导诊）

职责：
- 问候用户，了解就诊目的
- 引导用户描述症状或需求
- LLM 路由决策，判断下一步由哪个角色处理
"""

from langchain_core.messages import AIMessage, SystemMessage

from app.agents.base import get_model
from langchain.agents import create_agent

from app.agents.state import MedicalAgentState
from app.agents.memory import (
    get_long_term_memory,
    get_user_long_memory
)
from app.agents.tools import search_web


# 路由决策提示词
ROUTE_PROMPT = """
你是智能医疗助手的调度中心。根据对话上下文，判断下一步应该由哪个角色处理。
{memory_section}

【角色分工】
- supervisor: 问候、闲聊、导诊引导、用户不确定该看什么科时的引导、所有智能体都处理完毕时的总结收尾
- medical_examiner: 需要收集/补充用户基本信息（姓名、年龄、性别）、症状详情、病史、过敏史、体检相关
- attending_doctor: 用户信息已充足，需要诊断、治疗方案、病情分析、开药建议
- pharmacist: 药品咨询、用药指导、药物副作用、药品替代

【决策规则】
1. 用户说上传了文档/病历/报告 -> medical_examiner（体检员会读取文档信息）
2. 新用户首次对话或基本信息不全 -> medical_examiner
3. 用户描述症状但还没收集完整信息 -> medical_examiner
4. 【重要】如果体检员已经回复“将为您转接主治医生”或已生成健康摘要，说明信息收集完成 -> attending_doctor
5. 用户信息充足（有姓名、年龄、症状、病史），需要诊断治疗 -> attending_doctor
6. 医生已给出诊断和处方，需要用药指导 -> pharmacist
7. 明确问药品相关问题 -> pharmacist
8. 简单问候/闲聊/不确定需求 -> supervisor
9. 子智能体已完成处理，对话可以结束 -> supervisor

【注意事项】
- 如果对话中已经有【体检员】的回复，并且体检员说“将为您转接主治医生”或已生成摘要，必须路由到 attending_doctor
- 不要重复路由到 medical_examiner，除非用户主动提供新信息需要补充

【输出要求】
只回复一个单词: supervisor 或 medical_examiner 或 attending_doctor 或 pharmacist
"""


# 私人医疗顾问提示词
SUPERVISOR_PROMPT = """
【角色定义】
你是私人医疗顾问，负责导诊和协调。

【回复规则】
- 回复简洁专业
- 每次只问1-2个关键问题
{memory_section}

【职责范围】
你的职责：
1. 问候用户，了解就诊目的
2. 引导用户描述症状或需求

不是你的职责（交给其他智能体）：
- 不负责收集详细信息（交给体检员）
- 不负责诊断开药（交给医生）
- 不负责用药指导（交给药师）

【处理规则】
- 如果用户提到症状或需要收集信息，告诉用户将转给体检员

【语言风格】
简洁、温暖、专业，像真实的导诊护士。
"""


# 监督者节点：负责路由决策和导诊（私人医疗顾问）
def supervisor_node(state: MedicalAgentState):
    """私人医疗顾问 = 监督者 + 导诊"""
    try:
        # 获取聊天模型实例
        model = get_model()
        # 从状态中获取消息列表
        messages = state["messages"]
        # 从状态中获取用户ID
        user_id = state["user_id"]
        # 获取长记忆存储器实例
        store = get_long_term_memory()
        
        # 获取用户长记忆（个人信息、医疗历史等）
        user_context = get_user_long_memory(user_id, store)
        # 初始化记忆信息段落为空
        memory_section = ""
        # 如果有用户历史信息，格式化后添加到提示词中
        if user_context:
            memory_section = f"\n=== 用户历史信息 ===\n{user_context}\n=== 历史信息结束 ==="
        
        # LLM 路由决策
        # 将用户历史信息填充到路由决策提示词模板中,生成完整的路由提示词
        route_prompt = ROUTE_PROMPT.format(memory_section=memory_section)
        
        # 组合系统提示词和对话历史,构建完整的消息列表供LLM进行路由决策
        route_messages = [SystemMessage(content=route_prompt)] + messages
        route_response = model.invoke(route_messages)
        # 提取LLM回复的路由决策文本,去除首尾空格并转为小写,便于后续匹配解析
        route_text = route_response.content.strip().lower()
        
        # 解析路由结果
        valid_routes = ["medical_examiner", "attending_doctor", "pharmacist", "supervisor"]
        next_node = "supervisor"
        for r in valid_routes:
            if r in route_text:
                next_node = r
                break
        
        # 打印路由决策日志
        print(f"[路由决策] LLM决策结果: {route_text} -> {next_node}")
        
        # 路由到其他智能体
        if next_node != "supervisor":
            return {"next": next_node}
        
        # 自己处理：问候、闲聊、导诊引导
        # 创建监督者智能体（包含联网搜索工具）
        agent = create_agent(
            model=model,
            tools=[search_web],
            system_prompt=SUPERVISOR_PROMPT.format(memory_section=memory_section)
        )
        
        # 构建配置信息（用户ID和会话ID）
        config = {"configurable": {"user_id": user_id, "thread_id": state["thread_id"]}}
        # 调用智能体处理消息
        response = agent.invoke({"messages": messages}, config=config)
        
        # 如果有响应消息，添加角色前缀
        if response["messages"]:
            # 获取最后一条消息
            last_msg = response["messages"][-1]
            # 如果是 AI 消息
            if isinstance(last_msg, AIMessage):
                # 如果还没有角色前缀，添加【私人医疗顾问】
                if not last_msg.content.startswith("【私人医疗顾问】"):
                    last_msg.content = "【私人医疗顾问】\n" + last_msg.content
        
        return {
            "messages": response["messages"],
            "next": "__end__"
        }
    except Exception as e:
        print(f"[ERROR] 监督者执行出错: {e}")
        import traceback
        print(traceback.format_exc())
        return {
            "messages": [AIMessage(content=f"【私人医疗顾问】\n抱歉，系统暂时无法处理您的请求。错误：{str(e)}")],
            "next": "__end__"
        }
