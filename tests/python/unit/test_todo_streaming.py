"""DeepAgent streaming 层 todo_update 事件测试。

覆盖：
1. state.todos 变化时 yield todo_update 事件（原生 {content, status} schema）
2. todos 未变化时不重复 yield（去重）
3. 普通文本仍发射 token 事件
4. tool_call / tool_result 不再附带手造 todo 事件
5. system prompt 不再包含 write_todos 指令（由 middleware 自动注入）
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest
from langchain_core.messages import AIMessage, ToolMessage


class _FakeAgent:
    """模拟 LangGraph compiled agent 的 astream 行为。"""

    def __init__(self, states: list[dict]):
        self._states = states

    async def astream(
        self, inputs: Any, config: dict, stream_mode: str
    ) -> AsyncIterator[dict]:
        for state in self._states:
            yield state


def _clear_approval_state() -> None:
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()


@pytest.mark.asyncio
async def test_todos_change_yields_todo_update() -> None:
    """state.todos 变化时产出 todo_update 事件，payload 是原生 {content, status} schema。"""
    from app.deepagent.streaming import _stream_agent_events

    _clear_approval_state()

    agent = _FakeAgent([
        {
            "messages": [AIMessage(content="开始工作")],
            "todos": [
                {"content": "读取文件", "status": "in_progress"},
                {"content": "修改代码", "status": "pending"},
            ],
        },
    ])
    config = {"configurable": {"thread_id": "t-todos-1"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    todo_events = [e for e in events if e.get("event") == "todo_update"]
    assert len(todo_events) == 1
    payload = json.loads(todo_events[0]["data"])
    assert "todos" in payload
    assert len(payload["todos"]) == 2
    assert payload["todos"][0]["content"] == "读取文件"
    assert payload["todos"][0]["status"] == "in_progress"
    assert payload["todos"][1]["content"] == "修改代码"
    assert payload["todos"][1]["status"] == "pending"
    assert payload["task_id"] == "t-todos-1"


@pytest.mark.asyncio
async def test_todos_unchanged_no_duplicate() -> None:
    """todos 未变化时不重复 yield todo_update（去重）。"""
    from app.deepagent.streaming import _stream_agent_events

    _clear_approval_state()

    todos = [{"content": "任务", "status": "in_progress"}]
    agent = _FakeAgent([
        {"messages": [AIMessage(content="step 1")], "todos": todos},
        {"messages": [ToolMessage(content="result", tool_call_id="tc-1", name="read_file")], "todos": todos},
    ])
    config = {"configurable": {"thread_id": "t-dedup"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    todo_events = [e for e in events if e.get("event") == "todo_update"]
    assert len(todo_events) == 1  # 只在首次变化时 yield


@pytest.mark.asyncio
async def test_todos_status_update_yields_new_event() -> None:
    """todo status 从 in_progress → completed 时 yield 新的 todo_update。"""
    from app.deepagent.streaming import _stream_agent_events

    _clear_approval_state()

    agent = _FakeAgent([
        {
            "messages": [AIMessage(content="working")],
            "todos": [{"content": "任务", "status": "in_progress"}],
        },
        {
            "messages": [AIMessage(content="done")],
            "todos": [{"content": "任务", "status": "completed"}],
        },
    ])
    config = {"configurable": {"thread_id": "t-status"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    todo_events = [e for e in events if e.get("event") == "todo_update"]
    assert len(todo_events) == 2
    # 第二次 todos status 应为 completed
    payload2 = json.loads(todo_events[1]["data"])
    assert payload2["todos"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_normal_text_yields_token() -> None:
    """AIMessage 内容为普通文本时发射 token 事件。"""
    from app.deepagent.streaming import _stream_agent_events

    _clear_approval_state()

    agent = _FakeAgent([{"messages": [AIMessage(content="你好")]}])
    config = {"configurable": {"thread_id": "t-token"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    token_events = [e for e in events if e.get("event") == "token"]
    assert len(token_events) == 1
    assert token_events[0]["data"] == "你好"


@pytest.mark.asyncio
async def test_tool_call_no_todo_event() -> None:
    """AIMessage with tool_calls 产出 tool_call 事件，不再附带手造 todo 事件。"""
    from app.deepagent.streaming import _stream_agent_events

    _clear_approval_state()

    agent = _FakeAgent([
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/a.txt"}}],
                )
            ]
        },
    ])
    config = {"configurable": {"thread_id": "t-toolcall"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    tool_call_events = [e for e in events if e.get("event") == "tool_call"]
    assert len(tool_call_events) == 1
    # 不应有手造的 todo_update（state.todos 为空，不变化）
    todo_events = [e for e in events if e.get("event") == "todo_update"]
    assert len(todo_events) == 0


@pytest.mark.asyncio
async def test_tool_result_no_todo_event() -> None:
    """ToolMessage 产出 tool_result 事件，不再附带手造 todo 事件。"""
    from app.deepagent.streaming import _stream_agent_events

    _clear_approval_state()

    agent = _FakeAgent([
        {"messages": [ToolMessage(content="ok", tool_call_id="tc-1", name="read_file")]},
    ])
    config = {"configurable": {"thread_id": "t-toolresult"}}

    events = [e async for e in _stream_agent_events(agent, {"messages": []}, config)]

    tool_result_events = [e for e in events if e.get("event") == "tool_result"]
    assert len(tool_result_events) == 1
    # 不应有手造的 todo_update
    todo_events = [e for e in events if e.get("event") == "todo_update"]
    assert len(todo_events) == 0


def test_system_prompt_no_write_todos_instruction() -> None:
    """DeepAgent 系统提示不再包含 write_todos 指令（由 TodoListMiddleware 自动注入）。"""
    from app.deepagent.agent import _DEEP_SYSTEM_PROMPT

    assert "write_todos" not in _DEEP_SYSTEM_PROMPT
    assert "规划" in _DEEP_SYSTEM_PROMPT


def test_make_todo_update_event_native_schema() -> None:
    """make_todo_update_event 产出原生 {content, status} schema 的 todo_update 事件。"""
    from app.sse.events import make_todo_update_event

    todos = [
        {"content": "读取文件", "status": "completed"},
        {"content": "修改代码", "status": "in_progress"},
    ]
    event = make_todo_update_event(todos, task_id="thread-123")

    assert event["event"] == "todo_update"
    payload = json.loads(event["data"])
    assert payload["todos"] == todos
    assert payload["task_id"] == "thread-123"


def test_make_todo_update_event_no_task_id() -> None:
    """task_id 省略时 payload 不含 task_id 字段。"""
    from app.sse.events import make_todo_update_event

    todos = [{"content": "任务", "status": "pending"}]
    event = make_todo_update_event(todos)

    payload = json.loads(event["data"])
    assert "task_id" not in payload
    assert payload["todos"] == todos
