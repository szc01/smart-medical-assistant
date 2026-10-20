"""药师节点后处理判定函数的单测。

`_looks_like_pasted` 用来识别"模型把检索资料原样贴进回答"的情况，
命中后由 `_compress_answer` 二次调用模型重写。这里只测判定，不触发 LLM。
"""

from app.agents.pharmacist import REWRITE_CHAR_LIMIT, _looks_like_pasted


def test_normal_answer_not_flagged():
    """正常的口语化短回答不应被判定为照抄。"""
    assert not _looks_like_pasted("阿司匹林常见副作用是胃肠道刺激和出血风险。您有胃病史吗？")


def test_over_length_flagged():
    """超过字数上限即判定为照抄（政策型问题常出现）。"""
    assert _looks_like_pasted("阿" * (REWRITE_CHAR_LIMIT + 1))


def test_boundary_length_not_flagged():
    """恰好等于上限时不触发，避免误伤正常长回答。"""
    assert not _looks_like_pasted("阿" * REWRITE_CHAR_LIMIT)


def test_dash_prefix_flagged():
    """以 "- " 开头说明是工具返回的列表结构被直接拼进回答。"""
    assert _looks_like_pasted("- 常见的不良反应 阿司匹林会刺激胃肠道")


def test_web_symbol_flagged():
    """网页正文里的项目符号（如 ●）出现即判定为照抄。"""
    assert _looks_like_pasted("常见不良反应 ● 消化道出血")


def test_empty_not_flagged():
    """空回答不处理，交给上层逻辑。"""
    assert not _looks_like_pasted("")
