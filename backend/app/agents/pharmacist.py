"""药师智能体

职责：
- 用药指导
- 药品咨询
- 药物副作用说明
- 药品替代方案
"""

import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.base import get_model, get_deepseek_model
from langchain.agents import create_agent
from langgraph.config import get_stream_writer

from app.agents.state import MedicalAgentState
from app.agents.memory import (
    get_long_term_memory,
    get_user_long_memory,
    save_user_long_memory
)
from app.agents.tools import search_web, save_medical_info


# 药师提示词
PHARMACIST_PROMPT = """【角色定义】
你是药师，只负责药品和用药相关问题。

【输出硬约束】（优先级最高，与下方任何规则冲突时以此为准）
1. 回答总长度不超过 120 字。超过 120 字即为错误回答。
2. 用 2-4 个短句或短项目符号作答，不要写分章节、不要加粗小标题。
3. search_web 返回的资料只是你的知识来源，不是答案。必须用自己的话归纳成 1-2 句，禁止复述原文段落。
4. 禁止抄录网页里的符号与结构：● ・ ▪ ■ ◆ ▶ ※ | # ## 等一律不要出现在回答里。
5. 禁止抄录网页标题、栏目名、发布日期、来源、作者、参考文献。

【正确示范】
阿司匹林最常见的副作用是胃肠道刺激和出血风险，可能引起胃痛、恶心，严重时导致消化道出血或穿孔。有胃溃疡史或正在服用华法林等抗凝药的人需要慎用。请问您有胃病史吗？

【错误示范】（禁止这样回答）
- 常见的不良反应 ● 消化道出血 阿司匹林会刺激胃肠道，出现恶心、呕吐、胃痛、消化不良，严重时可引起消化性溃疡……
（错误原因：抄了网页符号、复述了原文、超过 120 字）

【职责范围】
你的职责（只做这些）：
1. 根据诊断和处方，给出详细用药指导：药名、剂量、服用时间、注意事项
2. 审核过敏禁忌，有问题直接提醒并给替代方案
3. 药品咨询：药物副作用、相互作用、存储方式
4. 提供药品替代方案（经济型/同类替代）
5. 回复时应说明药物的禁忌症，并主动询问用户是否有相关禁忌（如胃病、肝肾功能问题、怀孕等），有则需要替换药物
6. 可以使用 search_web 工具搜索药物相关知识（如药物详细信息、最新用药指南）

不是你的职责（不要做）：
- 不要诊断疾病，那是医生的工作
- 不要收集用户基本信息，那是体检员的工作
- 不要做导诊引导，那是顾问的工作

【联网检索】
调用 search_web 后只提炼与当前问题直接相关的要点，检索结果与问题无关时直接忽略。
检索结果若是英文或其他语言，一律用中文归纳，禁止夹带原文段落与英文小标题。

【语言风格】
简明扼要，像药房窗口的药师。
**必须使用简体中文回答**，即使检索资料、药品名、文献是英文或其他语言，也要译成中文后再表述。
"""


# ---------- 后处理：拦截"照抄资料"的回答 ----------
# 背景：药师节点接 search_web 后，模型对政策型内容（如"今年有什么流感疫苗"）倾向把工具返回的
# 列表原样贴进回答，实测 414–474 字且带 "- " 前缀。事实型问题（如副作用）模型会正常重写。
# prompt 层已加硬约束与正反示范，对该类问题无效，所以在节点出口做一次兜底重写。
REWRITE_CHAR_LIMIT = 200
PASTE_SYMBOLS = ("●", "・", "▪", "■", "◆", "▶", "※")


def _looks_like_pasted(answer: str) -> bool:
    """判断回答是否在照抄检索资料。"""
    text = (answer or "").strip()
    if not text:
        return False
    if len(text) > REWRITE_CHAR_LIMIT:
        return True
    if text.startswith("-") or text.startswith("【不良反应】"):
        return True
    return any(sym in text for sym in PASTE_SYMBOLS)


def _compress_answer(model, question: str, answer: str) -> str:
    """把过长或照抄的回答重写成 120 字内。失败或没变好时返回空串，由调用方保留原回答。"""
    try:
        resp = model.invoke([
            SystemMessage(content=(
                "你是药师。下面这段回答太长，或者是直接从资料里抄来的。请用你自己的话重写。\n"
                "要求：不超过 120 字；只讲最关键的 2-3 点；不要用 ● ・ - 等符号；"
                "不要复述原文；保留禁忌提醒；结尾可以问一句是否需要进一步说明。\n"
                "只输出重写后的内容，不要解释你在做什么。"
            )),
            HumanMessage(content=f"用户问题：{question}\n\n需要重写的回答：\n{answer[:1500]}"),
        ])
        text = (resp.content or "").strip()
        # 没变短就放弃，避免二次调用反而把回答改坏
        if not text or len(text) >= len(answer):
            return ""
        return text
    except Exception:
        return ""



# 药师节点：药品咨询、用药指导、药物副作用、药品替代
def pharmacist_node(state: MedicalAgentState):
    """药师：药品咨询、用药指导、药物副作用、药品替代"""
    try:
        get_stream_writer()({"type": "agent", "agent": "pharmacist"})
    except Exception:
        pass
    try:
        # 药师用 DeepSeek：同 prompt 同资料下，qwen3.5-plus 会复述检索原文（366 字），
        # deepseek-chat 能压到 88 字并保留禁忌提醒与追问。Key 缺失时自动回退 qwen。
        model = get_deepseek_model()
        # 从状态中获取消息列表
        messages = state["messages"]
        # 从状态中获取用户ID
        user_id = state["user_id"]
        # 获取长记忆存储器实例
        store = get_long_term_memory()
        # 从状态中获取诊断信息（来自主治医生）
        diagnosis = state.get("diagnosis", "")
        
        # 如果有诊断信息，添加到消息列表末尾
        if diagnosis:
            messages = messages + [SystemMessage(content=f"\n\n【诊断信息】\n{diagnosis}")]
        
        # 获取用户长期记忆（过敏史等）
        user_memory = get_user_long_memory(user_id, store)
        # 如果有用户历史信息，添加到消息列表开头
        if user_memory:
            # 创建系统消息，特别注意过敏史
            system_message = SystemMessage(content=f"用户历史信息（特别注意过敏史）：\n\n{user_memory}")
            # 将系统消息插入到消息列表最前面
            messages = [system_message] + messages

        # 创建药师智能体（包含联网搜索、保存医疗信息工具）
        agent = create_agent(
            model=model,
            tools=[search_web, save_medical_info],
            system_prompt=PHARMACIST_PROMPT
        )
        
        # 构建配置信息（用户ID和会话ID）
        config = {"configurable": {"user_id": user_id, "thread_id": state["thread_id"]}}
        # 调用智能体处理消息
        response = agent.invoke({"messages": messages}, config=config)
        
        response_messages = response["messages"]

        # 如果有响应消息，添加角色前缀并保存处方信息
        if response_messages:
            # 获取最后一条消息
            last_msg = response_messages[-1]
            # 如果是 AI 消息
            if isinstance(last_msg, AIMessage):
                # 后处理：回答像照抄检索资料时，重写一次（只对超标回答触发，正常回答无额外开销）
                if _looks_like_pasted(last_msg.content):
                    question = ""
                    for m in reversed(messages):
                        if isinstance(m, HumanMessage):
                            question = m.content if isinstance(m.content, str) else str(m.content)
                            break
                    rewritten = _compress_answer(model, question, last_msg.content)
                    if rewritten:
                        print(f"[药师后处理] 原回答 {len(last_msg.content)} 字 -> 重写为 {len(rewritten)} 字")
                        last_msg.content = rewritten
                # 如果还没有角色前缀，添加【药师】
                if not last_msg.content.startswith("【药师】"):
                    last_msg.content = "【药师】\n" + last_msg.content
                # 保存处方信息到长记忆数据库
                save_user_long_memory(
                    store,
                    ("user_medical_records", user_id, "prescription"),
                    f"prescription_{uuid.uuid4().hex[:8]}",
                    {"content": last_msg.content, "timestamp": str(uuid.uuid1())}
                )
        
        # 返回响应消息、处方信息并结束流程
        return {
            "messages": response_messages,
            # 提取处方信息（最后一条消息的内容）
            "prescription": response_messages[-1].content if response_messages else "",
            "next": "__end__"
        }
    except Exception as e:
        # 打印错误信息
        print(f"药师智能体执行出错: {e}")
        import traceback
        # 打印完整的错误堆栈信息,便于定位错误发生的代码行
        print(traceback.format_exc())
        # 返回错误消息并结束流程
        return {
            "messages": [AIMessage(content=f"【药师】\n抱歉，系统暂时无法处理您的请求。错误：{str(e)}")],
            "prescription": "",
            "next": "__end__"
        }
