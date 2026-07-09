"""场景 runner 写回 checkpointer 的持久化测试。

覆盖（Phase 2e 新架构）：
1. work 场景结束后将 user + assistant 消息写入 checkpointer
2. coding 场景结束后将 user + assistant 消息写入 checkpointer
3. coding_team 场景结束后将 user + assistant 消息写入 checkpointer
4. 空 assistant 内容时不写入
5. 无 checkpointer 时跳过持久化

使用 LangGraph 官方 ``InMemorySaver`` 作为 checkpointer 而非自定义 mock，
确保与真实 SQLite AsyncSqliteSaver 行为一致（mock 过去能绕过
``BaseCheckpointSaver`` 校验导致 bug 漏检）。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver


def _collect_messages(saver: InMemorySaver, thread_id: str) -> list:
    """从 InMemorySaver 中读取指定 thread_id 的全部 messages。"""
    config = {"configurable": {"thread_id": thread_id}}
    checkpoint = saver.get(config)
    if not checkpoint:
        return []
    channel_values = checkpoint.get("channel_values", {})
    return list(channel_values.get("messages", []))


@pytest.mark.asyncio
async def test_work_mode_persists_messages() -> None:
    """work 场景结束后写入 HumanMessage + AIMessage。"""
    from app.router.graph import run_router

    checkpointer = InMemorySaver()

    async def _fake_run_work_supervisor(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "你好"}

    import app.router.graph as graph_module

    original = graph_module.run_work_supervisor
    graph_module.run_work_supervisor = _fake_run_work_supervisor
    try:
        events = [e async for e in run_router("hi", "t-work", checkpointer=checkpointer, agent_mode="work")]
    finally:
        graph_module.run_work_supervisor = original

    assert any(e.get("event") == "done" for e in events)
    messages = _collect_messages(checkpointer, "t-work")
    assert len(messages) >= 2, f"expected at least 2 messages, got {len(messages)}"
    assert any(isinstance(m, HumanMessage) and m.content == "hi" for m in messages)
    assert any(
        isinstance(m, AIMessage) and "你好" in m.content for m in messages
    )


@pytest.mark.asyncio
async def test_coding_mode_persists_messages() -> None:
    """coding 场景结束后写入 user + assistant 消息。"""
    from app.router.graph import run_router

    checkpointer = InMemorySaver()

    async def _fake_run_coding_expert(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "code result"}

    import app.router.graph as graph_module

    original = graph_module.run_coding_expert
    graph_module.run_coding_expert = _fake_run_coding_expert
    try:
        events = [
            e
            async for e in run_router(
                "查找文件", "t-coding", checkpointer=checkpointer, agent_mode="coding"
            )
        ]
    finally:
        graph_module.run_coding_expert = original

    assert any(e.get("event") == "done" for e in events)
    messages = _collect_messages(checkpointer, "t-coding")
    assert len(messages) >= 2
    assert any(isinstance(m, HumanMessage) and m.content == "查找文件" for m in messages)
    assert any(isinstance(m, AIMessage) and "code result" in m.content for m in messages)


@pytest.mark.asyncio
async def test_coding_team_mode_persists_messages() -> None:
    """coding_team 场景结束后写入 user + assistant 消息。"""
    from app.router.graph import run_router

    checkpointer = InMemorySaver()

    async def _fake_run_coding_team(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "team summary"}

    import app.router.graph as graph_module

    original = graph_module.run_coding_team
    graph_module.run_coding_team = _fake_run_coding_team
    try:
        events = [
            e
            async for e in run_router(
                "复杂任务",
                "t-team",
                checkpointer=checkpointer,
                agent_mode="coding_team",
            )
        ]
    finally:
        graph_module.run_coding_team = original

    assert any(e.get("event") == "done" for e in events)
    messages = _collect_messages(checkpointer, "t-team")
    assert len(messages) >= 2
    assert any(isinstance(m, HumanMessage) and m.content == "复杂任务" for m in messages)
    assert any(isinstance(m, AIMessage) and "team summary" in m.content for m in messages)


@pytest.mark.asyncio
async def test_empty_assistant_content_skips_persistence() -> None:
    """场景 runner 产生的 assistant 内容为空时跳过持久化（仅 error/done 时不写）。"""
    from app.router.graph import run_router

    checkpointer = InMemorySaver()

    async def _fake_run_work_supervisor(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, str]]:
        yield {"event": "error", "data": "LLM 不可用"}

    import app.router.graph as graph_module

    original = graph_module.run_work_supervisor
    graph_module.run_work_supervisor = _fake_run_work_supervisor
    try:
        _ = [e async for e in run_router("hi", "t-empty", checkpointer=checkpointer, agent_mode="work")]
    finally:
        graph_module.run_work_supervisor = original

    # error 时没有 assistant_content 应跳过持久化
    messages = _collect_messages(checkpointer, "t-empty")
    assert len(messages) == 0, f"expected 0 messages, got {len(messages)}"


@pytest.mark.asyncio
async def test_no_checkpointer_skips_persistence() -> None:
    """无 checkpointer 参数时不应抛错。"""
    from app.router.graph import run_router

    async def _fake_run_work_supervisor(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "ok"}

    import app.router.graph as graph_module

    original = graph_module.run_work_supervisor
    graph_module.run_work_supervisor = _fake_run_work_supervisor
    try:
        events = [e async for e in run_router("hi", "t-no-cp", checkpointer=None, agent_mode="work")]
    finally:
        graph_module.run_work_supervisor = original

    assert any(e.get("event") == "done" for e in events)
