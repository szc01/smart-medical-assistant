"""S1 SSE smoke：测 TTFT、token 数、路由词泄漏与 done 结束标记。"""

import argparse
import json
import sys
import time
import uuid

import requests

# Windows 控制台默认 GBK，会把中文输出成问号
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROUTE_WORDS = ("attending_doctor", "medical_examiner", "pharmacist", "supervisor")


def _login(base_url: str, username: str, password: str) -> tuple[str, int]:
    response = requests.post(
        f"{base_url}/auth/token",
        data={"username": username, "password": password},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    return data["access_token"], data["user_id"]


def run_once(base_url: str, username: str, password: str, message: str) -> bool:
    token, user_id = _login(base_url, username, password)
    thread_id = f"sse_smoke_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "user_id": f"user_{user_id}",
        "message": message,
        "thread_id": thread_id,
    }

    started = time.perf_counter()
    first_token_at = None
    last_token_at = None
    token_count = 0
    char_count = 0
    text_parts: list[str] = []
    last_event_type = None
    error_detail = None

    with requests.post(
        f"{base_url}/chat/stream",
        json=payload,
        headers=headers,
        stream=True,
        timeout=180,
    ) as response:
        response.raise_for_status()
        print(f"status: {response.status_code}")
        for raw_line in response.iter_lines(chunk_size=1):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8")
            if not line.startswith("data:"):
                continue
            event = json.loads(line[len("data:"):].strip())
            last_event_type = event.get("type")
            if last_event_type == "token":
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                last_token_at = time.perf_counter()
                token_count += 1
                text = event.get("text", "")
                char_count += len(text)
                text_parts.append(text)
            elif last_event_type == "error":
                error_detail = event.get("detail")

    full_text = "".join(text_parts)
    ttft = first_token_at - started if first_token_at is not None else None
    token_span = last_token_at - first_token_at if first_token_at is not None else None
    route_leaks = sum(full_text.count(word) for word in ROUTE_WORDS)
    ended_with_done = last_event_type == "done"

    print(f"message: {message}")
    print(f"thread_id: {thread_id}")
    print(f"TTFT: {ttft:.3f}s" if ttft is not None else "TTFT: n/a")
    print(f"token_span: {token_span:.3f}s" if token_span is not None else "token_span: n/a")
    print(f"tokens: {token_count}")
    print(f"chars: {char_count}")
    print(f"route_word_leaks: {route_leaks}")
    print(f"ended_with_done: {ended_with_done}")
    print(f"last_event: {last_event_type}")
    print(f"text_head: {full_text[:80]}")
    if error_detail:
        print(f"error: {error_detail}")

    return ended_with_done and route_leaks == 0 and error_detail is None


def main() -> int:
    parser = argparse.ArgumentParser(description="S1 SSE smoke")
    parser.add_argument("--message", default="你好")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="sse_tester")
    parser.add_argument("--password", default="Test123456")
    args = parser.parse_args()

    ok = run_once(args.base_url, args.username, args.password, args.message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
