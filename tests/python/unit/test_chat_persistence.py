"""路径 A/B/D 写回 checkpointer 的持久化测试。

覆盖：
1. CHAT 路径结束后将 user + assistant 消息写入 checkpointer
2. SINGLE_TOOL 路径结束后将 user + assistant 消息写入 checkpointer
3. AGENT_TEAM 路径结束后将 user + assistant 消息写入 checkpointer
4. 空 assistant 内容时不写入
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage


class _FakeCheckpointer:
    """内存 checkpointer，模拟 AsyncSqliteSaver 接口。"""

    def __init__(self):
        self.checkpoints: dict[str, dict] = {}

    async def aget(self, config: dict) -> dict | None:
        tid = config.get("configurable", {}).get("thread_id", "")
        return self.checkpoints.get(tid)

    async def aput(self, config: dict, checkpoint: dict, *args: Any, **kwargs: Any) -> None:
        tid = config.get("configurable", {}).get("thread_id", "")
        self.checkpoints[tid] = checkpoint


def _messages_from_checkpoint(checkpoint: dict | None) -> list:
    if not checkpoint:
        return []
    channel_values = checkpoint.get("channel_values", {})
    return list(channel_values.get("messages", []))


@pytest.mark.asyncio
async def test_chat_path_persists_messages() -> None:
    """路径 A 结束后写入 HumanMessage + AIMessage。"""
    from app.router.graph import run_router

    checkpointer = _FakeCheckpointer()

    async def _fake_run_chat_path(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "你好"}

    import app.router.graph as graph_module

    original = graph_module.run_chat_path
    graph_module.run_chat_path = _fake_run_chat_path
    try:
        events = [e async for e in run_router("hi", "t-chat", checkpointer=checkpointer)]
    finally:
        graph_module.run_chat_path = original

    assert any(e.get("event") == "done" for e in events)
    messages = _messages_from_checkpoint(checkpointer.checkpoints.get("t-chat"))
    assert len(messages) == 2
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "hi"
    assert isinstance(messages[1], AIMessage)
    assert messages[1].content == "你好"


@pytest.mark.asyncio
async def test_tool_path_persists_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """路径 B 结束后写入 HumanMessage + 聚合的 AIMessage。"""
    from app.router.graph import run_router

    checkpointer = _FakeCheckpointer()

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    async def _fake_run_tool_path(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "工具"}
        yield {"event": "token", "data": "结果"}

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)
    monkeypatch.setattr("app.router.graph.run_tool_path", _fake_run_tool_path)

    events = [e async for e in run_router("read file", "t-tool", checkpointer=checkpointer)]

    assert any(e.get("event") == "done" for e in events)
    messages = _messages_from_checkpoint(checkpointer.checkpoints.get("t-tool"))
    assert len(messages) == 2
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "read file"
    assert isinstance(messages[1], AIMessage)
    assert messages[1].content == "工具结果"


@pytest.mark.asyncio
async def test_team_path_persists_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """路径 D 结束后写入 HumanMessage + Aggregator 汇总 AIMessage。"""
    from app.router.graph import run_router

    checkpointer = _FakeCheckpointer()

    async def _fake_run_team_path(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        yield {"event": "team_plan", "data": '{"plan": []}'}
        yield {"event": "token", "data": "汇总"}
        yield {"event": "team_done", "data": '{"status": "done"}'}

    monkeypatch.setattr("app.router.graph.run_team_path", _fake_run_team_path)

    events = [
        e
        async for e in run_router(
            "team task", "t-team", checkpointer=checkpointer, agent_mode="agent_team"
        )
    ]

    assert any(e.get("event") == "done" for e in events)
    messages = _messages_from_checkpoint(checkpointer.checkpoints.get("t-team"))
    assert len(messages) == 2
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "team task"
    assert isinstance(messages[1], AIMessage)
    assert messages[1].content == "汇总"


@pytest.mark.asyncio
async def test_empty_assistant_content_skips_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    """assistant 内容为空时不写入 checkpointer（避免污染历史）。"""
    from app.router.graph import run_router

    checkpointer = _FakeCheckpointer()

    async def _fake_run_chat_path(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        # 只产生非 token 事件
        yield {"event": "reasoning", "data": "{}"}

    monkeypatch.setattr("app.router.graph.run_chat_path", _fake_run_chat_path)

    events = [e async for e in run_router("hi", "t-empty", checkpointer=checkpointer)]

    assert any(e.get("event") == "done" for e in events)
    assert checkpointer.checkpoints.get("t-empty") is None


@pytest.mark.asyncio
async def test_no_checkpointer_skips_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    """未传入 checkpointer 时不报错且不写入。"""
    from app.router.graph import run_router

    async def _fake_run_chat_path(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "ok"}

    monkeypatch.setattr("app.router.graph.run_chat_path", _fake_run_chat_path)

    events = [e async for e in run_router("hi", "t-no-cp")]

    assert any(e.get("event") == "done" for e in events)
