"""SSE 事件流单测：假图 + 假 chunk，不连 LLM、不连数据库。"""

import asyncio
import json

from langchain_core.messages import AIMessage, AIMessageChunk

from app.api.chat import event_stream


class FakeGraph:
    def __init__(self, events):
        self._events = events

    async def astream(self, state, config=None, stream_mode=None):
        for mode, payload in self._events:
            yield mode, payload


class BoomGraph:
    async def astream(self, state, config=None, stream_mode=None):
        raise RuntimeError("boom")
        yield  # pragma: no cover


def _collect(graph) -> list[str]:
    async def run() -> list[str]:
        return [event async for event in event_stream(graph, {}, {})]

    return asyncio.run(run())


def _parse(raw_events: list[str]) -> list[dict]:
    events = []
    for raw in raw_events:
        assert raw.startswith("data: ")
        assert raw.endswith("\n\n")
        events.append(json.loads(raw[len("data: "):].strip()))
    return events


def test_event_stream_filters_route_decision_and_counts_chars() -> None:
    graph = FakeGraph(
        [
            (
                "messages",
                (
                    AIMessageChunk(content="attending_doctor"),
                    {"tags": ["route_decision"], "langgraph_node": "supervisor"},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(content="你好"),
                    {"tags": [], "langgraph_node": "attending_doctor"},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(content="，我是主治医生"),
                    {"tags": None, "langgraph_node": "attending_doctor"},
                ),
            ),
            ("values", {"messages": [AIMessage(content="你好，我是主治医生")]}),
        ]
    )

    events = _parse(_collect(graph))
    types = [event["type"] for event in events]

    assert types == ["meta", "token", "token", "done"]
    assert events[0] == {
        "type": "meta",
        "agent": "attending_doctor",
        "label": "主治医生",
    }

    text = "".join(event["text"] for event in events if event["type"] == "token")
    assert text == "你好，我是主治医生"
    assert "attending_doctor" not in text
    assert events[-1]["chars"] == len(text)


def test_event_stream_falls_back_to_final_state_without_tokens() -> None:
    content = "【体检员】\n请补充您的年龄和性别。"
    graph = FakeGraph([("values", {"messages": [AIMessage(content=content)]})])

    events = _parse(_collect(graph))

    assert [event["type"] for event in events] == ["meta", "token", "done"]
    assert events[0]["agent"] == "medical_examiner"
    assert events[0]["label"] == "体检员"
    assert events[1]["text"] == content
    assert events[-1]["chars"] == len(content)


def test_event_stream_emits_error_event_instead_of_raising() -> None:
    events = _parse(_collect(BoomGraph()))

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "boom" in events[0]["detail"]
