"""DeepAgent 结构化任务规划 SSE 事件测试。

覆盖：
1. _extract_plan_or_update 从纯 JSON / markdown 代码块解析 plan 与 plan_update
2. _stream_agent_events 对 plan / plan_update 内容发射对应 SSE 事件而非 token
3. todo_update 事件携带 task_id（thread_id）
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest
from langchain_core.messages import AIMessage, ToolMessage


class _FakeAgent:
    def __init__(self, states: list[dict]):
        self._states = states

    async def astream(
        self, inputs: Any, config: dict, stream_mode: str
    ) -> AsyncIterator[dict]:
        for state in self._states:
            yield state


def test_extract_plan_or_update_raw_json() -> None:
    """纯 JSON plan 被正确识别。"""
    from app.deep.streaming import _extract_plan_or_update

    text = json.dumps(
        {"plan": [{"id": "1", "title": "读取文件", "status": "pending"}]},
        ensure_ascii=False,
    )
    result = _extract_plan_or_update(text)
    assert result is not None
    kind, data = result
    assert kind == "plan"
    assert data["plan"][0]["id"] == "1"


def test_extract_plan_or_update_codeblock() -> None:
    """markdown 代码块中的 plan 被正确识别。"""
    from app.deep.streaming import _extract_plan_or_update

    inner = json.dumps(
        {"plan": [{"id": "2", "title": "搜索", "status": "pending"}]},
        ensure_ascii=False,
    )
    text = f"这是计划：\n```json\n{inner}\n```\n请确认"
    result = _extract_plan_or_update(text)
    assert result is not None
    kind, data = result
    assert kind == "plan"
    assert data["plan"][0]["title"] == "搜索"


def test_extract_plan_update() -> None:
    """plan_update JSON 被正确识别。"""
    from app.deep.streaming import _extract_plan_or_update

    text = json.dumps(
        {"plan_update": {"id": "1", "status": "done"}},
        ensure_ascii=False,
    )
    result = _extract_plan_or_update(text)
    assert result is not None
    kind, data = result
    assert kind == "plan_update"
    assert data["id"] == "1"
    assert data["status"] == "done"


def test_extract_plan_or_update_normal_text() -> None:
    """普通文本返回 None。"""
    from app.deep.streaming import _extract_plan_or_update

    assert _extract_plan_or_update("你好，这是普通回复") is None
    # 大括号但不是合法 JSON/plan
    assert _extract_plan_or_update("{foo: bar}") is None


@pytest.mark.asyncio
async def test_stream_yields_plan_event() -> None:
    """AIMessage 内容为 plan JSON 时，应发射 plan SSE 事件。"""
    from app.deep.streaming import _stream_agent_events
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()

    plan = {"plan": [{"id": "1", "title": "读取文件", "status": "pending"}]}
    agent = _FakeAgent([{"messages": [AIMessage(content=json.dumps(plan, ensure_ascii=False))]}])
    config = {"configurable": {"thread_id": "t-plan"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    plan_events = [e for e in events if e.get("event") == "plan"]
    assert len(plan_events) == 1
    payload = json.loads(plan_events[0]["data"])
    assert payload["plan"][0]["id"] == "1"
    assert not any(e.get("event") == "token" for e in events)


@pytest.mark.asyncio
async def test_stream_yields_plan_update_event() -> None:
    """AIMessage 内容为 plan_update JSON 时，应发射 plan_update SSE 事件。"""
    from app.deep.streaming import _stream_agent_events
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()

    update = {"plan_update": {"id": "1", "status": "done"}}
    agent = _FakeAgent([{"messages": [AIMessage(content=json.dumps(update, ensure_ascii=False))]}])
    config = {"configurable": {"thread_id": "t-update"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    update_events = [e for e in events if e.get("event") == "plan_update"]
    assert len(update_events) == 1
    payload = json.loads(update_events[0]["data"])
    assert payload["id"] == "1"
    assert payload["status"] == "done"


@pytest.mark.asyncio
async def test_stream_yields_token_for_normal_text() -> None:
    """AIMessage 内容为普通文本时，仍发射 token 事件。"""
    from app.deep.streaming import _stream_agent_events
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()

    agent = _FakeAgent([{"messages": [AIMessage(content="你好")]}])
    config = {"configurable": {"thread_id": "t-token"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    token_events = [e for e in events if e.get("event") == "token"]
    assert len(token_events) == 1
    assert token_events[0]["data"] == "你好"


@pytest.mark.asyncio
async def test_todo_update_includes_task_id() -> None:
    """tool_call / tool_result 附带的 todo_update 应携带 task_id。"""
    from app.deep.streaming import _stream_agent_events
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()

    agent = _FakeAgent([
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/a.txt"}}],
                )
            ]
        },
        {"messages": [ToolMessage(content="ok", tool_call_id="tc-1", name="read_file")]},
    ])
    config = {"configurable": {"thread_id": "t-todo"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    todo_events = [e for e in events if e.get("event") == "todo_update"]
    assert len(todo_events) == 2
    for e in todo_events:
        payload = json.loads(e["data"])
        assert payload["todos"][0]["task_id"] == "t-todo"


def test_system_prompt_includes_plan_instructions() -> None:
    """DeepAgent 系统提示包含结构化计划指令。"""
    from app.deep.agent import _DEEP_SYSTEM_PROMPT

    assert "plan" in _DEEP_SYSTEM_PROMPT
    assert "plan_update" in _DEEP_SYSTEM_PROMPT
