"""主治医生智能体

职责：
- 根据症状和报告给出诊断
- 制定治疗方案
- 开具药物处方
"""

from langchain_core.messages import AIMessage
import uuid

from app.agents.base import get_model
from langchain.agents import create_agent

from app.agents.state import MedicalAgentState
from app.agents.memory import (
    get_long_term_memory,
    get_user_long_memory,
    save_user_long_memory
)
from app.agents.tools import search_web, save_medical_info


# 主治医生提示词
ATTENDING_DOCTOR_PROMPT = """
【角色定义】
你是主治医生，只负责诊断和制定治疗方案。

【回复规则】
- 回复简洁专业

【职责范围】
你的职责（只做这些）：
1. 根据体检员收集的症状和报告，给出诊断结果（疾病名称、严重程度）
2. 制定治疗方案：药物处方（名称、剂量、用法、疗程）+ 生活/饮食建议
3. 注意用户过敏史和禁忌（如怀孕），有禁忌时提醒并换药
4. 开具的药物必须避开用户已知的过敏和禁忌
5. 病情严重时建议线下就医，一般情况直接给方案
6. 如果开了药物，回复结尾引导用户："如需了解用药注意事项或副作用，可以询问药师"
7. 可以使用 search_web 工具搜索药物相关知识（如药物相互作用、替代方案）

不是你的职责（不要做）：
- 不要询问用户基本信息（姓名、年龄等），那是体检员的工作
- 不要详细解释药物使用方法，那是药师的工作
- 不要做导诊引导，那是顾问的工作

【语言风格】
专业简洁，像门诊医生问诊。
"""


# 主治医生节点：诊断疾病、制定治疗方案、开药建议
def attending_doctor_node(state: MedicalAgentState):
    """主治医生：诊断疾病、制定治疗方案、开药建议"""
    try:
        # 获取聊天模型实例
        model = get_model()
        # 从状态中获取消息列表
        messages = state["messages"]
        # 从状态中获取用户ID
        user_id = state["user_id"]
        # 获取长记忆存储器实例
        store = get_long_term_memory()
        
        # 获取用户长期记忆（个人信息、医疗历史等）
        user_memory = get_user_long_memory(user_id, store)
        # 如果有用户历史信息，添加到消息列表开头
        if user_memory:
            from langchain_core.messages import SystemMessage
            # 创建系统消息，包含用户历史信息
            system_message = SystemMessage(content=f"用户历史信息：\n\n{user_memory}")
            # 将系统消息插入到消息列表最前面
            messages = [system_message] + messages
        
        # 创建主治医生智能体（包含联网搜索、保存医疗信息工具）
        agent = create_agent(
            model=model,
            tools=[search_web, save_medical_info],
            system_prompt=ATTENDING_DOCTOR_PROMPT
        )
        
        # 构建配置信息（用户ID和会话ID）
        config = {"configurable": {"user_id": user_id, "thread_id": state["thread_id"]}}
        # 调用智能体处理消息
        response = agent.invoke({"messages": messages}, config=config)
        
        # 如果有响应消息，添加角色前缀并保存诊断结果
        if response["messages"]:
            # 获取最后一条消息
            last_msg = response["messages"][-1]
            # 如果是 AI 消息
            if isinstance(last_msg, AIMessage):
                # 如果还没有角色前缀，添加【主治医生】
                if not last_msg.content.startswith("【主治医生】"):
                    last_msg.content = "【主治医生】\n" + last_msg.content
                # 保存诊断结果到长记忆数据库
                save_user_long_memory(
                    store,
                    ("user_medical_records", user_id, "diagnosis"),
                    f"diagnosis_{uuid.uuid4().hex[:8]}",
                    {"content": last_msg.content, "timestamp": str(uuid.uuid1())}
                )
        
        # 返回响应消息、诊断结果并结束流程
        return {
            "messages": response["messages"],
            # 提取诊断结果（最后一条消息的内容）
            "diagnosis": response["messages"][-1].content if response["messages"] else "",
            "next": "__end__"
        }
    except Exception as e:
        # 打印错误信息
        print(f"主治医生智能体执行出错: {e}")
        import traceback
        # 打印完整的错误堆栈信息,便于定位错误发生的代码行
        print(traceback.format_exc())
        # 返回错误消息并结束流程
        return {
            "messages": [AIMessage(content=f"【主治医生】\n抱歉，系统暂时无法处理您的请求。错误：{str(e)}")],
            "diagnosis": "",
            "next": "__end__"
        }
