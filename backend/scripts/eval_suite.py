"""P0-1 评测集：25 条真实链路评测。

用法（在 backend 目录下）：
    python scripts/eval_suite.py
    python scripts/eval_suite.py --only B
    python scripts/eval_suite.py --out results/xxx.json

评测全部通过 HTTP 走 POST /chat/stream；B 类索引按手册用 index_pages 直接灌入。
脚本自行注册两个账号、清理账号/会话/文档/checkpoint/store/Chroma/uploads。
判据全部为字符串包含或边界正则，不使用 LLM 裁判。
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import shutil
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PAGE1_TEXT = (
    "姓名：张三  年龄：45 岁  性别：男\n"
    "主诉：口干、多饮两周。既往史：高血压 5 年，规律服药。\n"
    "过敏史：青霉素过敏。"
)
PAGE2_TEXT = (
    "空腹血糖 6.2 mmol/L；血红蛋白 13.7 g/dL；ALT 58 U/L；肌酐 88 μmol/L。\n"
    "结论：血糖偏高，建议复查糖化血红蛋白。"
)
REPORT_FILENAME = "eval_report.pdf"
BLACKLIST_VALUES = ["6.2", "13.7", "58", "88", "张三"]
NO_DATA_PHRASES = ["没有", "未找到", "未上传", "无法", "不清楚", "上传"]

CLASS_LABELS = {
    "A": "路由正确性",
    "B": "检索问答",
    "C": "无关不硬扯",
    "D": "兜底不幻觉",
    "E": "越权拦截",
}
CLASS_ORDER = ["A", "B", "C", "D", "E"]

CASES: dict[str, list[dict[str, Any]]] = {
    "A": [
        {"id": "A1", "input": "你好", "expected_agent": "supervisor"},
        {"id": "A2", "input": "我最近老头晕，可能是什么原因", "expected_agent": "medical_examiner"},
        {"id": "A3", "input": "阿莫西林和头孢能一起吃吗", "expected_agent": "pharmacist"},
        {"id": "A4", "input": "我报告里的空腹血糖是多少", "expected_agent": "attending_doctor"},
        {"id": "A5", "input": "我刚上传了一份体检报告，帮我整理一下", "expected_agent": "medical_examiner"},
    ],
    "B": [
        {"id": "B1", "input": "我报告里的空腹血糖是多少", "numbers": ["6.2"], "page": 2},
        {"id": "B2", "input": "我的血红蛋白是多少", "numbers": ["13.7"], "page": 2},
        {"id": "B3", "input": "我的 ALT 是多少", "numbers": ["58"], "page": 2},
        {"id": "B4", "input": "我多大年纪", "numbers": ["45"], "page": 1},
        {"id": "B5", "input": "我有什么既往病史", "numbers": ["高血压"], "page": 1},
        {"id": "B6", "input": "我的主诉是什么", "numbers": ["口干", "多饮"], "page": 1, "number_mode": "any"},
        {"id": "B7", "input": "报告结论是什么", "numbers": ["血糖偏高", "复查"], "page": 2, "number_mode": "any"},
        {"id": "B8", "input": "我的姓名是什么", "numbers": ["张三"], "page": 1},
    ],
    "C": [
        {"id": "C1", "input": "感冒了能吃什么"},
        {"id": "C2", "input": "今天天气怎么样"},
        {"id": "C3", "input": "推荐一部电影"},
        {"id": "C4", "input": "怎么减肥比较健康"},
        {"id": "C5", "input": "阿司匹林有什么副作用"},
        {"id": "C6", "input": "我昨晚没睡好怎么办"},
    ],
    "D": [
        {"id": "D1", "input": "我的血糖是多少"},
        {"id": "D2", "input": "我的血红蛋白是多少"},
        {"id": "D3", "input": "我上次体检结果怎么样"},
    ],
}

E_CASES = [
    {"id": "E1", "operation": "B 用自己 token GET A 的会话", "expected": "404"},
    {"id": "E2", "operation": "B 用自己 token DELETE A 的会话", "expected": "404"},
    {"id": "E3", "operation": "B 用自己 token 请求 /chat/stream 且 user_id 填 A 的", "expected": "403 或 SSE error"},
]


@dataclass
class RunContext:
    run_id: str
    username_a: str = ""
    username_b: str = ""
    user_id_a: int = 0
    user_id_b: int = 0
    token_a: str = ""
    token_b: str = ""
    memory_a: str = ""
    memory_b: str = ""
    conversation_a: str = ""
    indexed_chunks: int = 0
    thread_ids: set[str] = field(default_factory=set)
    started_at: datetime = field(default_factory=datetime.now)

    @property
    def user_ids(self) -> list[int]:
        return [value for value in (self.user_id_a, self.user_id_b) if value]

    @property
    def memory_ids(self) -> list[str]:
        return [value for value in (self.memory_a, self.memory_b) if value]


def _number_pattern(value: str) -> re.Pattern[str]:
    escaped = re.escape(value)
    if "." in value:
        return re.compile(rf"(?<![\d.]){escaped}(?![\d])")
    # 整数额外排除小数（例如 58 不应命中 58.5），也不命中 158
    return re.compile(rf"(?<![\d.]){escaped}(?![\d.])")


def _page_pattern(page: int) -> re.Pattern[str]:
    return re.compile(rf"第\s*{page}\s*页")


def _blacklist_hits(text: str) -> list[str]:
    hits: list[str] = []
    for value in BLACKLIST_VALUES:
        if value == "张三":
            if value in text:
                hits.append(value)
        elif _number_pattern(value).search(text):
            hits.append(value)
    return hits


def _preview(text: str, limit: int = 80) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    return compact[:limit] + ("…" if len(compact) > limit else "")


def _fmt_duration(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}m{total % 60:02d}s"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _stream_chat(
    session: requests.Session,
    base_url: str,
    token: str,
    user_id: str,
    message: str,
    thread_id: str,
    timeout: int = 180,
) -> dict[str, Any]:
    started = time.perf_counter()
    meta_agent = None
    tokens: list[str] = []
    error = None
    done = False
    events = 0
    status = None
    body_preview = ""

    try:
        with session.post(
            f"{base_url}/chat/stream",
            json={"user_id": user_id, "message": message, "thread_id": thread_id},
            headers=_auth(token),
            stream=True,
            timeout=(10, timeout),
        ) as response:
            status = response.status_code
            if status != 200:
                body_preview = response.text[:500]
                error = f"HTTP {status}: {body_preview}"
                return {
                    "status": status,
                    "meta_agent": None,
                    "answer": "",
                    "error": error,
                    "done": False,
                    "events": 0,
                    "duration_s": round(time.perf_counter() - started, 2),
                }

            for raw_line in response.iter_lines(chunk_size=1):
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="replace")
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                events += 1
                event_type = event.get("type")
                if event_type == "meta":
                    meta_agent = event.get("agent") or ""
                elif event_type == "token":
                    tokens.append(event.get("text", ""))
                elif event_type == "error":
                    error = str(event.get("detail", "error event"))
                elif event_type == "done":
                    done = True
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    answer = "".join(tokens)
    if not error and not answer:
        error = "未收到 token"
    return {
        "status": status,
        "meta_agent": meta_agent,
        "answer": answer,
        "error": error,
        "done": done,
        "events": events,
        "duration_s": round(time.perf_counter() - started, 2),
    }


def _result(
    case_id: str,
    class_id: str,
    input_text: str,
    expected: str,
    passed: bool,
    reasons: list[str],
    *,
    answer: str = "",
    meta_agent: str | None = None,
    http_status: int | None = None,
    duration_s: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "id": case_id,
        "class": class_id,
        "input": input_text,
        "expected": expected,
        "passed": bool(passed),
        "reasons": reasons,
        "reason": "；".join(reasons),
        "answer_preview": _preview(answer),
        "answer": answer,
        "meta_agent": meta_agent,
        "http_status": http_status,
        "duration_s": round(duration_s, 2),
    }
    if extra:
        result["extra"] = extra
    return result


def _log_case(result: dict[str, Any]) -> None:
    flag = "PASS" if result["passed"] else "FAIL"
    suffix = "" if result["passed"] else f"  {result['reason']}"
    print(f"[{result['id']}] {flag} {result['duration_s']:.1f}s{suffix}")


def _register_and_login(
    session: requests.Session,
    base_url: str,
    username: str,
    password: str,
) -> tuple[int, str]:
    email = f"{username}@example.com"
    response = session.post(
        f"{base_url}/auth/register",
        json={"username": username, "password": password, "email": email},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"注册 {username} 失败: HTTP {response.status_code} {response.text[:300]}")
    user_id = int(response.json()["id"])

    response = session.post(
        f"{base_url}/auth/token",
        data={"username": username, "password": password},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"登录 {username} 失败: HTTP {response.status_code} {response.text[:300]}")
    data = response.json()
    if int(data["user_id"]) != user_id:
        raise RuntimeError(f"登录 {username} 返回的 user_id 不一致")
    return user_id, data["access_token"]


def _prepare(session: requests.Session, base_url: str, ctx: RunContext) -> None:
    password = f"EvalPass_{ctx.run_id}!"
    ctx.username_a = f"eval_a_{ctx.run_id}"
    ctx.username_b = f"eval_b_{ctx.run_id}"

    ctx.user_id_a, ctx.token_a = _register_and_login(session, base_url, ctx.username_a, password)
    ctx.memory_a = f"user_{ctx.user_id_a}"
    print(f"[预处理] 账号 A: {ctx.username_a} (id={ctx.user_id_a}, {ctx.memory_a})")

    ctx.user_id_b, ctx.token_b = _register_and_login(session, base_url, ctx.username_b, password)
    ctx.memory_b = f"user_{ctx.user_id_b}"
    print(f"[预处理] 账号 B: {ctx.username_b} (id={ctx.user_id_b}, {ctx.memory_b})")

    from app.rag.vector_store import index_pages

    started = time.perf_counter()
    ctx.indexed_chunks = index_pages(
        ctx.memory_a,
        [(1, PAGE1_TEXT), (2, PAGE2_TEXT)],
        REPORT_FILENAME,
    )
    if ctx.indexed_chunks < 1:
        raise RuntimeError("index_pages 未写入任何块")
    print(
        f"[预处理] index_pages: {ctx.indexed_chunks} 块 / 2 页 -> {ctx.memory_a}"
        f"（{time.perf_counter() - started:.1f}s）"
    )

    ctx.conversation_a = f"eval_conv_{ctx.run_id}"
    response = session.post(
        f"{base_url}/conversations/create",
        json={"id": ctx.conversation_a, "title": f"P0-1 eval {ctx.run_id}"},
        headers=_auth(ctx.token_a),
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"创建 A 会话失败: HTTP {response.status_code} {response.text[:300]}"
        )
    ctx.thread_ids.add(ctx.conversation_a)
    print(f"[预处理] A 会话: {ctx.conversation_a}")


def _run_a(
    session: requests.Session,
    base_url: str,
    ctx: RunContext,
    timeout: int,
) -> list[dict[str, Any]]:
    results = []
    for case in CASES["A"]:
        thread_id = f"{ctx.run_id}_{case['id']}"
        ctx.thread_ids.add(thread_id)
        stream = _stream_chat(
            session, base_url, ctx.token_a, ctx.memory_a, case["input"], thread_id, timeout
        )
        reasons = []
        if stream["error"]:
            reasons.append(stream["error"])
        actual = stream["meta_agent"] or ""
        if actual != case["expected_agent"]:
            reasons.append(f"实际 agent={actual or '缺失'}")
        result = _result(
            case["id"],
            "A",
            case["input"],
            f"agent={case['expected_agent']}",
            not reasons,
            reasons,
            answer=stream["answer"],
            meta_agent=stream["meta_agent"],
            http_status=stream["status"],
            duration_s=stream["duration_s"],
            extra={"expected_agent": case["expected_agent"], "actual_agent": actual},
        )
        results.append(result)
        _log_case(result)
    return results


def _run_b(
    session: requests.Session,
    base_url: str,
    ctx: RunContext,
    timeout: int,
) -> list[dict[str, Any]]:
    results = []
    for case in CASES["B"]:
        stream = _stream_chat(
            session, base_url, ctx.token_a, ctx.memory_a, case["input"], ctx.conversation_a, timeout
        )
        answer = stream["answer"]
        reasons = []
        if stream["error"]:
            reasons.append(stream["error"])
        matched_numbers = [
            value for value in case["numbers"] if _number_pattern(value).search(answer)
        ]
        number_mode = case.get("number_mode", "all")
        if number_mode == "any":
            missing_numbers = [] if matched_numbers else list(case["numbers"])
            if missing_numbers:
                reasons.append(f"缺少 {'/'.join(missing_numbers)} 中任一")
        else:
            missing_numbers = [
                value for value in case["numbers"] if value not in matched_numbers
            ]
            if missing_numbers:
                reasons.append(f"缺少 {'/'.join(missing_numbers)}")
        page_ok = bool(_page_pattern(case["page"]).search(answer))
        if not page_ok:
            reasons.append(f"缺少页码「第 {case['page']} 页」")
        joiner = " 或 " if number_mode == "any" else "/"
        expected = f"数值 {joiner.join(case['numbers'])} + 第 {case['page']} 页"
        result = _result(
            case["id"],
            "B",
            case["input"],
            expected,
            not reasons,
            reasons,
            answer=answer,
            meta_agent=stream["meta_agent"],
            http_status=stream["status"],
            duration_s=stream["duration_s"],
            extra={
                "missing_numbers": missing_numbers,
                "matched_numbers": matched_numbers,
                "number_mode": number_mode,
                "page_ok": page_ok,
                "numbers": case["numbers"],
                "page": case["page"],
            },
        )
        results.append(result)
        _log_case(result)
    return results


def _run_c(
    session: requests.Session,
    base_url: str,
    ctx: RunContext,
    timeout: int,
) -> list[dict[str, Any]]:
    results = []
    for case in CASES["C"]:
        thread_id = f"{ctx.run_id}_{case['id']}"
        ctx.thread_ids.add(thread_id)
        stream = _stream_chat(
            session, base_url, ctx.token_a, ctx.memory_a, case["input"], thread_id, timeout
        )
        reasons = []
        if stream["error"]:
            reasons.append(stream["error"])
        hits = _blacklist_hits(stream["answer"])
        reasons.extend(f"命中禁用值「{value}」" for value in hits)
        result = _result(
            case["id"],
            "C",
            case["input"],
            "不含 6.2/13.7/58/88/张三",
            not reasons,
            reasons,
            answer=stream["answer"],
            meta_agent=stream["meta_agent"],
            http_status=stream["status"],
            duration_s=stream["duration_s"],
            extra={"blacklist_hits": hits},
        )
        results.append(result)
        _log_case(result)
    return results


def _run_d(
    session: requests.Session,
    base_url: str,
    ctx: RunContext,
    timeout: int,
) -> list[dict[str, Any]]:
    results = []
    for case in CASES["D"]:
        thread_id = f"{ctx.run_id}_{case['id']}"
        ctx.thread_ids.add(thread_id)
        stream = _stream_chat(
            session, base_url, ctx.token_b, ctx.memory_b, case["input"], thread_id, timeout
        )
        answer = stream["answer"]
        reasons = []
        if stream["error"]:
            reasons.append(stream["error"])
        hits = _blacklist_hits(answer)
        reasons.extend(f"命中禁用值「{value}」" for value in hits)
        phrase_found = next((phrase for phrase in NO_DATA_PHRASES if phrase in answer), None)
        if not phrase_found:
            reasons.append("缺少无数据表述（没有/未找到/未上传/无法/不清楚/上传）")
        result = _result(
            case["id"],
            "D",
            case["input"],
            "不含 6.2/13.7/58/88/张三 + 含无数据表述",
            not reasons,
            reasons,
            answer=answer,
            meta_agent=stream["meta_agent"],
            http_status=stream["status"],
            duration_s=stream["duration_s"],
            extra={"blacklist_hits": hits, "no_data_phrase": phrase_found},
        )
        results.append(result)
        _log_case(result)
    return results


def _run_e(
    session: requests.Session,
    base_url: str,
    ctx: RunContext,
    timeout: int,
) -> list[dict[str, Any]]:
    results = []
    headers_b = _auth(ctx.token_b)

    for case in E_CASES[:2]:
        started = time.perf_counter()
        try:
            if case["id"] == "E1":
                response = session.get(
                    f"{base_url}/conversations/get",
                    params={"conversation_id": ctx.conversation_a},
                    headers=headers_b,
                    timeout=30,
                )
            else:
                response = session.delete(
                    f"{base_url}/conversations/delete",
                    params={"conversation_id": ctx.conversation_a},
                    headers=headers_b,
                    timeout=30,
                )
            status = response.status_code
            body = response.text[:300]
            reasons = [] if status == 404 else [f"期望 404，实际 {status}"]
            result = _result(
                case["id"],
                "E",
                case["operation"],
                case["expected"],
                not reasons,
                reasons,
                answer=body,
                http_status=status,
                duration_s=time.perf_counter() - started,
                extra={"operation": case["operation"]},
            )
        except Exception as exc:
            result = _result(
                case["id"],
                "E",
                case["operation"],
                case["expected"],
                False,
                [f"{type(exc).__name__}: {exc}"],
                duration_s=time.perf_counter() - started,
                extra={"operation": case["operation"]},
            )
        results.append(result)
        _log_case(result)

    case = E_CASES[2]
    thread_id = f"{ctx.run_id}_{case['id']}"
    ctx.thread_ids.add(thread_id)
    stream = _stream_chat(
        session,
        base_url,
        ctx.token_b,
        ctx.memory_a,
        "我的血糖是多少",
        thread_id,
        timeout,
    )
    status = stream["status"]
    if status == 403:
        passed = True
        reasons = []
    elif status == 200 and stream["error"]:
        passed = True
        reasons = []
    else:
        passed = False
        reasons = [f"期望 HTTP 403 或 SSE error，实际 HTTP {status}"]
        if stream["answer"]:
            reasons.append("且返回了正文")
    result = _result(
        case["id"],
        "E",
        case["operation"],
        case["expected"],
        passed,
        reasons,
        answer=stream["answer"] or (stream["error"] or ""),
        http_status=status,
        duration_s=stream["duration_s"],
        extra={"operation": case["operation"], "sse_error": stream["error"]},
    )
    results.append(result)
    _log_case(result)
    return results


def _run_cases(
    session: requests.Session,
    base_url: str,
    ctx: RunContext,
    selected: list[str],
    timeout: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if "A" in selected:
        results.extend(_run_a(session, base_url, ctx, timeout))
    if "B" in selected:
        results.extend(_run_b(session, base_url, ctx, timeout))
    if "C" in selected:
        results.extend(_run_c(session, base_url, ctx, timeout))
    if "D" in selected:
        results.extend(_run_d(session, base_url, ctx, timeout))
    if "E" in selected:
        results.extend(_run_e(session, base_url, ctx, timeout))
    return results


def _summarize(
    results: list[dict[str, Any]],
    selected: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary = []
    for class_id in selected:
        class_results = [item for item in results if item["class"] == class_id]
        passed = sum(1 for item in class_results if item["passed"])
        total = len(class_results)
        summary.append(
            {
                "class": class_id,
                "label": CLASS_LABELS[class_id],
                "passed": passed,
                "total": total,
                "rate": round(100.0 * passed / total, 1) if total else 0.0,
            }
        )
    passed = sum(1 for item in results if item["passed"])
    total = len(results)
    return summary, {
        "passed": passed,
        "total": total,
        "rate": round(100.0 * passed / total, 1) if total else 0.0,
    }


def _brief_reason(result: dict[str, Any]) -> str:
    text = result["reason"]
    return text[:60] + ("…" if len(text) > 60 else "")


def _console_report(
    now: datetime,
    summary: list[dict[str, Any]],
    total: dict[str, Any],
    results: list[dict[str, Any]],
    elapsed: float,
) -> str:
    lines = [f"=== 评测结果 {now:%Y-%m-%d %H:%M} ==="]
    for item in summary:
        failures = [
            child
            for child in results
            if child["class"] == item["class"] and not child["passed"]
        ]
        suffix = ""
        if failures:
            suffix = "   ← " + "；".join(
                f"{child['id']} {_brief_reason(child)}" for child in failures
            )
        lines.append(
            f"{item['class']} {item['label']:<8} {item['passed']}/{item['total']}"
            f"   {item['rate']:5.1f}%{suffix}"
        )
    lines.append("-" * 36)
    lines.append(
        f"合计             {total['passed']}/{total['total']}"
        f"   {total['rate']:5.1f}%   耗时 {_fmt_duration(elapsed)}"
    )
    return "\n".join(lines)


def _print_failure_details(results: list[dict[str, Any]]) -> None:
    failures = [item for item in results if not item["passed"]]
    if not failures:
        print("\n失败详情：无")
        return
    print("\n失败详情：")
    for item in failures:
        print(f"- {item['id']} [{item['class']}] {item['reason']}")
        print(f"  输入: {item['input']}")
        print(f"  期望: {item['expected']}")
        print(f"  实际前 80 字: {item['answer_preview'] or '（空）'}")


def _print_d_answers(results: list[dict[str, Any]], selected: list[str]) -> None:
    if "D" not in selected:
        return
    print("\nD 类原文（人工复核）：")
    for item in results:
        if item["class"] == "D":
            flag = "通过" if item["passed"] else "失败"
            print(f"{item['id']} [{flag}] {item['answer']}")


def _cleanup(ctx: RunContext) -> dict[str, Any]:
    residual: list[str] = []
    errors: list[str] = []
    counts = {
        "users": 0,
        "conversations": 0,
        "documents": 0,
        "checkpoints_rows": 0,
        "store_rows": 0,
        "chroma": 0,
        "uploads": 0,
    }

    try:
        from app.config.settings import settings
        from app.database.mysql import SessionLocal
        from app.database.postgres import PostgresSessionLocal
        from app.models.conversation import Conversation
        from app.models.medical_document import MedicalDocument
        from app.models.user import User
    except Exception as exc:
        errors.append(f"导入清理所需模块失败: {exc}")
        return {
            "counts": counts,
            "verification": {},
            "residual": residual,
            "errors": errors,
        }

    try:
        db = SessionLocal()
        try:
            counts["users"] = (
                db.query(User)
                .filter(User.id.in_(ctx.user_ids))
                .delete(synchronize_session=False)
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        errors.append(f"删除 users 失败: {exc}")

    try:
        db = PostgresSessionLocal()
        try:
            counts["conversations"] = (
                db.query(Conversation)
                .filter(Conversation.user_id.in_(ctx.user_ids))
                .delete(synchronize_session=False)
            )
            counts["documents"] = (
                db.query(MedicalDocument)
                .filter(MedicalDocument.user_id.in_(ctx.memory_ids))
                .delete(synchronize_session=False)
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        errors.append(f"删除 conversations/medical_documents 失败: {exc}")

    for thread_id in sorted(ctx.thread_ids):
        try:
            import psycopg

            with psycopg.connect(settings.POSTGRES_SHORT_TERM_URL, connect_timeout=10) as conn:
                for table in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,)
                    )
                    counts["checkpoints_rows"] += cursor.rowcount
                conn.commit()
        except Exception as exc:
            errors.append(f"删除 checkpoint {thread_id} 失败: {exc}")

    try:
        import psycopg

        with psycopg.connect(settings.POSTGRES_LONG_TERM_URL, connect_timeout=10) as conn:
            import re as _re

            for memory_id in ctx.memory_ids:
                pattern = rf"(^|\.){_re.escape(memory_id)}($|\.)"
                cursor = conn.execute("DELETE FROM store WHERE prefix ~ %s", (pattern,))
                counts["store_rows"] += cursor.rowcount
            conn.commit()
    except Exception as exc:
        errors.append(f"删除 store 失败: {exc}")

    try:
        import chromadb
        from chromadb.api.client import SharedSystemClient
        from chromadb.config import Settings as ChromaSettings

        from app.rag.vector_store import CHROMA_ROOT, _safe_collection_name

        # index_pages 在本进程缓存了 Chroma System；先释放，避免 "different settings"
        # 冲突，并尽量关闭本进程持有的 SQLite/HNSW 句柄
        SharedSystemClient.clear_system_cache()
        gc.collect()

        for memory_id in ctx.memory_ids:
            name = _safe_collection_name(memory_id)
            directory = CHROMA_ROOT / name
            if not directory.exists():
                continue
            try:
                client = chromadb.PersistentClient(
                    path=str(directory),
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                try:
                    client.delete_collection(name)
                except Exception as exc:
                    errors.append(f"Chroma 删除集合 {name} 失败: {exc}")
                finally:
                    try:
                        client.close()
                    except Exception:
                        pass
            except Exception as exc:
                errors.append(f"Chroma 打开集合 {name} 失败: {exc}")
            SharedSystemClient.clear_system_cache()
            gc.collect()
            for attempt in range(6):
                try:
                    shutil.rmtree(directory)
                    counts["chroma"] += 1
                    break
                except Exception as exc:
                    if attempt == 5:
                        residual.append(f"chroma:{directory}")
                        errors.append(
                            f"删除目录 {directory} 失败: {exc}"
                            "（若为 WinError 32，通常是后端进程仍持有 Chroma 文件，需重启后端后清理）"
                        )
                    else:
                        time.sleep(0.5)
    except Exception as exc:
        errors.append(f"Chroma 清理失败: {exc}")

    try:
        upload_dir = BACKEND_ROOT / "uploads"
        if upload_dir.exists():
            for memory_id in ctx.memory_ids:
                for path in upload_dir.glob(f"{memory_id}_*"):
                    try:
                        path.unlink()
                        counts["uploads"] += 1
                    except Exception as exc:
                        residual.append(f"upload:{path}")
                        errors.append(f"删除 {path} 失败: {exc}")
    except Exception as exc:
        errors.append(f"uploads 清理失败: {exc}")

    verification: dict[str, Any] = {}
    try:
        db = SessionLocal()
        try:
            eval_users = db.query(User).filter(User.id.in_(ctx.user_ids)).count()
            global_users = db.query(User).count()
        finally:
            db.close()
        verification["users"] = {"eval": eval_users, "global": global_users}
        if eval_users:
            residual.append(f"users:{eval_users}")
    except Exception as exc:
        errors.append(f"校验 users 失败: {exc}")

    try:
        db = PostgresSessionLocal()
        try:
            eval_conversations = (
                db.query(Conversation)
                .filter(Conversation.user_id.in_(ctx.user_ids))
                .count()
            )
            eval_documents = (
                db.query(MedicalDocument)
                .filter(MedicalDocument.user_id.in_(ctx.memory_ids))
                .count()
            )
            global_conversations = db.query(Conversation).count()
            global_documents = db.query(MedicalDocument).count()
        finally:
            db.close()
        verification["conversations"] = {
            "eval": eval_conversations,
            "global": global_conversations,
        }
        verification["medical_documents"] = {
            "eval": eval_documents,
            "global": global_documents,
        }
        if eval_conversations:
            residual.append(f"conversations:{eval_conversations}")
        if eval_documents:
            residual.append(f"medical_documents:{eval_documents}")
    except Exception as exc:
        errors.append(f"校验 conversations/medical_documents 失败: {exc}")

    try:
        import psycopg

        checkpoint_rows = 0
        with psycopg.connect(settings.POSTGRES_SHORT_TERM_URL, connect_timeout=10) as conn:
            for thread_id in sorted(ctx.thread_ids):
                for table in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE thread_id = %s", (thread_id,)
                    ).fetchone()
                    checkpoint_rows += int(row[0]) if row else 0
        verification["checkpoint_rows"] = checkpoint_rows
        if checkpoint_rows:
            residual.append(f"checkpoint_rows:{checkpoint_rows}")
    except Exception as exc:
        errors.append(f"校验 checkpoints 失败: {exc}")

    try:
        import re as _re

        import psycopg

        store_rows = 0
        with psycopg.connect(settings.POSTGRES_LONG_TERM_URL, connect_timeout=10) as conn:
            for memory_id in ctx.memory_ids:
                pattern = rf"(^|\.){_re.escape(memory_id)}($|\.)"
                row = conn.execute(
                    "SELECT COUNT(*) FROM store WHERE prefix ~ %s", (pattern,)
                ).fetchone()
                store_rows += int(row[0]) if row else 0
        verification["store_rows"] = store_rows
        if store_rows:
            residual.append(f"store_rows:{store_rows}")
    except Exception as exc:
        errors.append(f"校验 store 失败: {exc}")

    try:
        from app.rag.vector_store import CHROMA_ROOT, _safe_collection_name

        eval_chroma = [
            str(CHROMA_ROOT / _safe_collection_name(memory_id))
            for memory_id in ctx.memory_ids
            if (CHROMA_ROOT / _safe_collection_name(memory_id)).exists()
        ]
        global_chroma = (
            [path.name for path in CHROMA_ROOT.iterdir() if path.is_dir()]
            if CHROMA_ROOT.exists()
            else []
        )
        verification["chroma"] = {"eval": len(eval_chroma), "global": len(global_chroma)}
        residual.extend(f"chroma:{path}" for path in eval_chroma)
    except Exception as exc:
        errors.append(f"校验 chroma 失败: {exc}")

    try:
        upload_dir = BACKEND_ROOT / "uploads"
        eval_uploads = []
        global_uploads = []
        if upload_dir.exists():
            for memory_id in ctx.memory_ids:
                eval_uploads.extend(str(path) for path in upload_dir.glob(f"{memory_id}_*"))
            global_uploads = [
                path.name
                for path in upload_dir.iterdir()
                if path.is_file() and path.name != ".gitkeep"
            ]
        verification["uploads"] = {
            "eval": len(eval_uploads),
            "global": len(global_uploads),
        }
        residual.extend(f"upload:{path}" for path in eval_uploads)
    except Exception as exc:
        errors.append(f"校验 uploads 失败: {exc}")

    return {
        "counts": counts,
        "verification": verification,
        "residual": list(dict.fromkeys(residual)),
        "errors": errors,
    }


def _print_cleanup(cleanup: dict[str, Any]) -> None:
    counts = cleanup["counts"]
    print(
        "[清理] "
        f"users={counts['users']} conversations={counts['conversations']} "
        f"documents={counts['documents']} checkpoints_rows={counts['checkpoints_rows']} "
        f"store_rows={counts['store_rows']} chroma={counts['chroma']} uploads={counts['uploads']}"
    )
    verification = cleanup.get("verification", {})
    users = verification.get("users", {})
    conversations = verification.get("conversations", {})
    documents = verification.get("medical_documents", {})
    chroma = verification.get("chroma", {})
    uploads = verification.get("uploads", {})
    print(
        "[清理校验] "
        f"users={users.get('eval', 'n/a')} conversations={conversations.get('eval', 'n/a')} "
        f"medical_documents={documents.get('eval', 'n/a')} chroma={chroma.get('eval', 'n/a')} "
        f"uploads={uploads.get('eval', 'n/a')} "
        f"store_rows={verification.get('store_rows', 'n/a')} "
        f"checkpoint_rows={verification.get('checkpoint_rows', 'n/a')}"
    )
    print(
        "[清理-全局] "
        f"users={users.get('global', 'n/a')} conversations={conversations.get('global', 'n/a')} "
        f"medical_documents={documents.get('global', 'n/a')} chroma={chroma.get('global', 'n/a')} "
        f"uploads={uploads.get('global', 'n/a')}"
    )
    if cleanup["residual"]:
        print("[清理残留] " + "；".join(cleanup["residual"]))
    else:
        print("[清理残留] 无")
    if cleanup["errors"]:
        print("[清理错误] " + "；".join(cleanup["errors"]))


def _markdown_cell(value: Any) -> str:
    text = str(value).replace("|", "\\|")
    return text.replace("\n", "<br>")


def _build_markdown(
    now: datetime,
    summary: list[dict[str, Any]],
    total: dict[str, Any],
    results: list[dict[str, Any]],
    elapsed: float,
    cleanup: dict[str, Any],
) -> str:
    lines = [
        f"# P0-1 评测结果 {now:%Y-%m-%d %H:%M}",
        "",
        "| 类别 | 通过/总数 | 通过率 | 失败项 |",
        "| --- | --- | --- | --- |",
    ]
    for item in summary:
        failures = [
            child
            for child in results
            if child["class"] == item["class"] and not child["passed"]
        ]
        failure_text = "、".join(child["id"] for child in failures) or "-"
        lines.append(
            f"| {item['class']} {item['label']} | {item['passed']}/{item['total']} "
            f"| {item['rate']:.1f}% | {failure_text} |"
        )
    lines.append(
        f"| **合计** | **{total['passed']}/{total['total']}** "
        f"| **{total['rate']:.1f}%** | 耗时 {_fmt_duration(elapsed)} |"
    )

    failures = [item for item in results if not item["passed"]]
    lines.extend(["", "## 失败详情", ""])
    if not failures:
        lines.append("无。")
    else:
        lines.extend(
            [
                "| # | 输入 | 期望 | 失败原因 | 实际回答前 80 字 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in failures:
            lines.append(
                f"| {item['id']} | {_markdown_cell(item['input'])} "
                f"| {_markdown_cell(item['expected'])} "
                f"| {_markdown_cell(item['reason'])} "
                f"| {_markdown_cell(item['answer_preview'] or '（空）')} |"
            )

    d_results = [item for item in results if item["class"] == "D"]
    if d_results:
        lines.extend(["", "## D 类原文（人工复核）", ""])
        for item in d_results:
            flag = "通过" if item["passed"] else "失败"
            lines.append(f"**{item['id']} [{flag}]**")
            lines.append("")
            lines.append("```text")
            lines.append(item["answer"] or "（空）")
            lines.append("```")
            lines.append("")

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- B 类通过 `index_pages` 直接灌入两页固定索引，不覆盖「上传 → PDF 解析 → 索引」链路。",
            "- A 类每题独立线程；B 类共享 A 的会话；C/D 每题独立线程；E 使用 A 的会话。",
            "- 判定为字符串包含/边界正则，未使用 LLM 裁判。",
            "- 清理校验（evals）："
            f"users={cleanup['verification'].get('users', {}).get('eval', 'n/a')} "
            f"conversations={cleanup['verification'].get('conversations', {}).get('eval', 'n/a')} "
            f"medical_documents={cleanup['verification'].get('medical_documents', {}).get('eval', 'n/a')} "
            f"chroma={cleanup['verification'].get('chroma', {}).get('eval', 'n/a')} "
            f"uploads={cleanup['verification'].get('uploads', {}).get('eval', 'n/a')}。",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P0-1 评测集（25 条真实链路）")
    parser.add_argument(
        "--only",
        choices=CLASS_ORDER + [value.lower() for value in CLASS_ORDER],
        help="只跑某一类（A/B/C/D/E）",
    )
    parser.add_argument("--out", help="结果 JSON 路径（默认 results/eval_<时间戳>.json）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="后端地址")
    parser.add_argument("--timeout", type=int, default=180, help="单条聊天读超时（秒）")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    os.chdir(BACKEND_ROOT)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    selected = [args.only.upper()] if args.only else list(CLASS_ORDER)
    ctx = RunContext(run_id=run_id)
    run_started = time.perf_counter()
    results: list[dict[str, Any]] = []
    setup_error: str | None = None

    session = requests.Session()
    try:
        _prepare(session, args.base_url, ctx)
        results = _run_cases(session, args.base_url, ctx, selected, args.timeout)
    except Exception as exc:
        setup_error = f"{type(exc).__name__}: {exc}"
        print(f"[错误] {setup_error}")
        traceback.print_exc()
    eval_elapsed = time.perf_counter() - run_started

    cleanup = _cleanup(ctx)
    total_elapsed = time.perf_counter() - run_started

    if setup_error:
        _print_cleanup(cleanup)
        print(f"[总耗时] {_fmt_duration(total_elapsed)}")
        return 1

    now = datetime.now()
    summary, total = _summarize(results, selected)
    table = _console_report(now, summary, total, results, eval_elapsed)
    print()
    print(table)
    _print_failure_details(results)
    _print_d_answers(results, selected)
    print()
    _print_cleanup(cleanup)
    print(f"[总耗时] 评测 {_fmt_duration(eval_elapsed)}；含清理 {_fmt_duration(total_elapsed)}")

    out_path = (
        Path(args.out)
        if args.out
        else BACKEND_ROOT / "results" / f"eval_{run_id}.json"
    )
    if not out_path.is_absolute():
        out_path = (BACKEND_ROOT / out_path).resolve()
    md_path = out_path.with_suffix(".md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "meta": {
            "brief": "P0-1",
            "run_id": run_id,
            "started_at": ctx.started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "base_url": args.base_url,
            "selected_classes": selected,
            "accounts": {
                "a": {
                    "username": ctx.username_a,
                    "user_id": ctx.user_id_a,
                    "memory_id": ctx.memory_a,
                },
                "b": {
                    "username": ctx.username_b,
                    "user_id": ctx.user_id_b,
                    "memory_id": ctx.memory_b,
                },
            },
            "report": {
                "filename": REPORT_FILENAME,
                "pages": [PAGE1_TEXT, PAGE2_TEXT],
                "indexed_chunks": ctx.indexed_chunks,
            },
            "thread_strategy": {
                "A": "每题独立线程",
                "B": "共享 A 的会话",
                "C": "每题独立线程",
                "D": "每题独立线程",
                "E": "使用 A 的会话 + 独立线程",
            },
            "eval_duration_s": round(eval_elapsed, 2),
            "total_duration_s": round(total_elapsed, 2),
            "thread_ids": sorted(ctx.thread_ids),
        },
        "summary": summary,
        "total": total,
        "cases": results,
        "cleanup": cleanup,
        "console_table": table,
        "notes": [
            "B 类用 index_pages 直接灌索引，未覆盖上传→PDF 解析→索引链路。",
            "判据为字符串包含/边界正则，不使用 LLM 裁判。",
            "D 类回答原文已全部写入 JSON，需人工复核是否编造了黑名单外的数值。",
        ],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(
        _build_markdown(now, summary, total, results, eval_elapsed, cleanup),
        encoding="utf-8",
    )
    print(f"[结果] JSON: {out_path}")
    print(f"[结果] Markdown: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
