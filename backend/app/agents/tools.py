"""工具定义模块

定义智能体使用的工具：
- search_web: 联网搜索
- save_user_info: 保存用户基本信息
- save_medical_info: 保存用户医疗信息
"""

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from tavily import TavilyClient
from app.config.settings import settings
from app.agents.memory import get_long_term_memory
import json
import logging
import re
import time
import uuid
import requests


logger = logging.getLogger(__name__)


# 联网检索配置
# 主路径：DeepSeek 自研搜索（0.5-0.8s，中文为主，summary 是给模型读的摘要）
# 降级路径：Tavily（海外源，正文较长且常夹英文）
SEARCH_MAX_RESULTS = 3
SEARCH_SNIPPET_MAX_CHARS = 200   # DeepSeek：每条 snippet 上限（原始 100-200 字，短而聚焦）
SEARCH_SUMMARY_MAX_CHARS = 400   # DeepSeek 降级：summary 原始 482-1442 字，不压会 3 条 1600+
SEARCH_MAX_CHARS = 300           # Tavily：每条正文上限
SEARCH_ANSWER_MAX_CHARS = 500    # Tavily：answer 摘要上限
SEARCH_MIN_SCORE = 1.5           # DeepSeek rerank_score 阈值（文档口径：≥1.5 为优质结果）
DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"


def _clip(text: str, limit: int = SEARCH_MAX_CHARS) -> str:
    """按句读边界截断，避免把一句话切成两半。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    for sep in ("。", "；", "！", "？", ".", ";", "!", "?"):
        idx = head.rfind(sep)
        if idx >= limit // 2:
            return head[: idx + 1]
    return head + "…"


def _is_chinese_text(text: str, threshold: float = 0.2) -> bool:
    """判断文本是否以中文为主。

    检索结果里常有港台/WHO 的英文页面，段内容夹少量中文专名（如"疫苗可預防疾病科學委員會"）
    也会被误判成中文，所以看汉字占比而不是"是否含汉字"。
    """
    if not text:
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cn / len(text) >= threshold


def _clean_summary(text: str) -> str:
    """清洗 DeepSeek summary 里的网页噪声。

    summary 是网页正文的 Markdown 提取结果，实测包含这些模型会照抄的内容：
      "| 发布日期：2022-06-14 11:09:31 来源：中华医学会科学普及部"
      "2017-08-27 11:56:00来源：北京青年报"
      "作者：刘玉和 北京大学第一医院 主任医师" / "审核：郭树彬 ..."
      "\\n...\\n" 省略号分隔、Markdown 标题 "## "
    不清洗的话，模型回答会以 "- | 发布日期：..." 开头（实测发生过）。
    """
    if not text:
        return ""
    t = text
    t = re.sub(r"^\s*\|?\s*发布日期[:：][^\n]*\n?", "", t)
    t = re.sub(r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}[^\n]{0,12}来源[:：][^\n]*\n?", "", t)
    t = re.sub(r"^\s*时间[:：][^\n]*\n?", "", t)
    t = re.sub(r"\n\s*\.{3,}\s*\n", "\n", t)
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"\n\s*(作者|审核|编辑|责编|来源)[:：][^\n]*", "", t)
    # 中文日期时间戳 + "| 来源：xxx"（实测 "2026年10月19日07:48 | 来源：科技日报"）
    t = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}[^\n]{0,24}", "", t)
    t = re.sub(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(:\d{2})?[^\n]{0,24}", "", t)
    t = re.sub(r"[|｜]\s*来源[:：][^\n]{0,24}", "", t)
    # 行首残留的省略号
    t = re.sub(r"^\s*[.．]{2,}\s*", "", t)
    # 段内换行压成空格：保留换行会让模型照着分点复述
    t = re.sub(r"\s*\n\s*", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _search_deepseek(query: str) -> str:
    """DeepSeek Search：summary 是面向模型的摘要，中文占比 60-90%，实测 0.5-0.8s。"""
    # 外部接口偶发 SSL EOF / 连接被重置，重试一次；仍失败则交给上层降级到 Tavily
    last_err = None
    for attempt in range(2):
        try:
            resp = requests.post(
                DEEPSEEK_SEARCH_URL,
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "count": SEARCH_MAX_RESULTS,
                    "ori_question": query,   # 帮助搜索引擎理解意图
                },
                timeout=20,
            )
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_err = e
            if attempt == 0:
                time.sleep(0.5)
    else:
        raise last_err

    items = resp.json().get("results", [])
    # 只用重排分达标的条目：低分结果通常是标题党或弱相关内容
    items = [it for it in items if (it.get("rerank_score") or 0) >= SEARCH_MIN_SCORE]

    # 只用中文资料：实测搜「阿司匹林有什么副作用」会命中瑞典药典页面（Sällsynta / Mycket sällsynta），
    # 模型会照着抄一整段瑞典语，导致回答中文占比掉到 47%。工具层必须先把外文滤掉。
    cleaned = []
    for it in items:
        text = _clean_summary(it.get("snippet") or "") or _clean_summary(it.get("summary") or "")
        if not _is_chinese_text(text):
            continue
        snippet = _clip(text, SEARCH_SNIPPET_MAX_CHARS) if it.get("snippet") else _clip(text, SEARCH_SUMMARY_MAX_CHARS)
        if snippet:
            cleaned.append(snippet)

    if not cleaned:
        return "未找到可靠的中文资料，建议告知用户咨询专业医疗机构或查阅官方指南"
    return "\n".join(f"- {s}" for s in cleaned)


def _search_tavily(query: str) -> str:
    """Tavily 降级路径：DeepSeek 不可用（欠费 / Key 失效 / 网络）时兜底。"""
    client = TavilyClient(api_key=settings.TAVILY_API_KEY)
    result = client.search(
        query,
        max_results=SEARCH_MAX_RESULTS,
        include_answer=True,
    )

    # 只用中文资料：模型拿到英文原文会整段照抄（实测输出过 "The sources indicate that..."），
    # 在工具返回值里夹"请用中文"的指令也没用——模型会把指令当资料一起复述。
    # 所以在这一层就把非中文内容滤掉，让模型根本没有可抄的英文。
    items = result.get("results", [])[:SEARCH_MAX_RESULTS]
    cn_snippets = [
        _clip(item.get("content") or "")
        for item in items
        if _is_chinese_text(item.get("content") or "")
    ]
    cn_snippets = [s for s in cn_snippets if s]
    if cn_snippets:
        return "\n".join(f"- {s}" for s in cn_snippets)

    answer = (result.get("answer") or "").strip()
    if answer and _is_chinese_text(answer):
        return _clip(answer, SEARCH_ANSWER_MAX_CHARS)

    # 全是外文资料：宁可说没有，也不要把英文段落丢给中文对话
    return "未找到可靠的中文资料，建议告知用户咨询专业医疗机构或查阅官方指南"


@tool
def search_web(query: str) -> str:
    """
    搜索互联网获取实时信息
    参数 query 是搜索关键词
    当用户需要最新信息、新闻、医疗资讯等时使用此工具
    """
    if settings.DEEPSEEK_API_KEY:
        try:
            return _search_deepseek(query)
        except Exception as e:
            logger.warning("DeepSeek 检索失败，降级到 Tavily: %s", e)

    try:
        return _search_tavily(query)
    except Exception as e:
        logger.warning("联网检索失败: %s", e)
        return "联网检索暂时不可用，请基于已知医学知识回答"


@tool
def save_user_info(info_json: str, config: RunnableConfig) -> str:
    """
    保存或更新用户基本信息到长期记忆。
    参数 info_json 是 JSON 字符串，包含要保存的字段和值。
    当用户提供或修改个人信息时使用此工具。
    """
    try:
        # 解析 JSON 字符串为字典
        info = json.loads(info_json)
        # 从配置中获取用户ID
        user_id = config["configurable"]["user_id"]
        # 获取长记忆存储器实例
        store = get_long_term_memory()
        # 初始化已保存项列表（用于返回结果）
        saved_items = []
        
        # 字段映射：key -> 中文描述
        field_names = {
            "name": "姓名",
            "age": "年龄",
            "gender": "性别",
            "phone": "手机号码",
            "contact": "联系方式",
            "blood_type": "血型",
            "height": "身高",
            "weight": "体重"
        }
        
        # 遍历用户提供的每个字段
        for key, value in info.items():
            # 检查值是否有效（不为空且去除空格后有内容）
            if value and str(value).strip():
                # 构建命名空间：(存储类型, 用户ID)
                namespace = ("user_preferences", user_id)
                # 构建数据项ID：basic_info_字段名（相同字段会覆盖）
                item_id = f"basic_info_{key}"
                # 获取字段的中文名称（如果映射表没有，使用原始key）
                label = field_names.get(key, key)
                
                # 直接保存，相同 item_id 会覆盖旧数据
                store.put(namespace, item_id, {"key": label, "value": str(value).strip()})
                # 记录已保存的项（用于返回给用户）
                saved_items.append(f"{label}: {value}")
        
        if saved_items:
            print(f"[OK] 保存用户基本信息: {', '.join(saved_items)}")
            return f"已保存用户信息: {', '.join(saved_items)}"
        return "没有需要保存的信息"
    except Exception as e:
        print(f"[ERROR] 保存用户基本信息失败: {e}")
        return f"保存失败: {str(e)}"


@tool
def save_medical_info(category: str, content: str, config: RunnableConfig) -> str:
    """
    保存或更新用户医疗相关信息到长期记忆。
    参数 category 是信息类别（symptom/allergy/past_history/family_history/lifestyle/other）。
    参数 content 是具体内容。
    当用户提供或修改病史、过敏史、症状等医疗信息时使用此工具。
    """
    try:
        # 从配置中获取用户ID
        user_id = config["configurable"]["user_id"]
        # 获取长记忆存储器实例
        store = get_long_term_memory()
        
        # 类别映射
        category_names = {
            "symptom": "症状描述",
            "allergy": "过敏史",
            "past_history": "既往病史",
            "family_history": "家族病史",
            "lifestyle": "生活习惯",
            "other": "其他医疗信息"
        }
        # 获取类别的中文名称（如果映射表没有，使用原始category）
        label = category_names.get(category, category)
        
        # 构建命名空间：(医疗历史类型, 用户ID)
        namespace = ("user_medical_history", user_id)
        # 构建数据项ID：medical_类别_随机串（每次生成不同的ID，实现追加而非覆盖）
        item_id = f"medical_{category}_{uuid.uuid4().hex[:8]}"
        
        # 保存医疗信息到长记忆数据库
        store.put(namespace, item_id, {
            "category": label,  # 类别中文名称
            "content": content,  # 具体内容
            "timestamp": str(uuid.uuid1())  # 时间戳（用于排序）
        })
        
        print(f"[OK] 保存用户医疗信息 [{label}]: {content[:50]}")
        return f"已保存{label}: {content[:50]}..."
    except Exception as e:
        print(f"[ERROR] 保存用户医疗信息失败: {e}")
        return f"保存失败: {str(e)}"
