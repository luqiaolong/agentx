"""真实 AsyncSqliteSaver 上的 checkpointer 写回集成测试。

验证 ``_append_messages_to_checkpointer`` 在真实 LangGraph SQLite checkpointer
上能正确写入并被后续读取。该函数仅用于 coding_team 路径（team graph 无
checkpointer，由 Router 手动写回 user + assistant 消息）。

使用临时 SQLite 数据库（不污染生产 data/agentx.db）。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from app.observability.logger import logger


@pytest.fixture
async def async_sqlite_checkpointer():
    """返回 (checkpointer, cleanup_fn) — 使用临时 SQLite 数据库。"""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    import aiosqlite

    tmp_dir = Path(tempfile.mkdtemp(prefix="agentx_test_chk_"))
    db_path = tmp_dir / "test_checkpoints.db"

    conn = await aiosqlite.connect(str(db_path))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()

    async def cleanup() -> None:
        try:
            await conn.close()
        except Exception as exc:
            logger.warning("aiosqlite close failed", error=str(exc))
        shutil.rmtree(tmp_dir, ignore_errors=True)

    yield saver, cleanup


@pytest.mark.asyncio
async def test_append_messages_to_real_sqlite(async_sqlite_checkpointer):
    """真实 SQLite 写回：写两次 messages 后用 aget_state 应能读出全部。"""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    from app.router.graph import _append_messages_to_checkpointer

    checkpointer, cleanup = async_sqlite_checkpointer
    thread_id = "integ-test-1"

    # 第一轮：写入 user + ai
    await _append_messages_to_checkpointer(
        checkpointer,
        thread_id,
        [
            HumanMessage(content="我叫张三"),
            AIMessage(content="好的，张三，已记住。"),
        ],
    )

    # 第二轮：再写入
    await _append_messages_to_checkpointer(
        checkpointer,
        thread_id,
        [
            HumanMessage(content="我叫什么？"),
            AIMessage(content="你叫张三。"),
        ],
    )

    # 用 LangGraph 自身读出
    async def _passthrough(state: MessagesState) -> dict:
        return {"messages": []}

    graph = StateGraph(MessagesState)
    graph.add_node("passthrough", _passthrough)
    graph.add_edge(START, "passthrough")
    graph.add_edge("passthrough", END)
    compiled = graph.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": thread_id}}
    state = await compiled.aget_state(config)
    assert state is not None, "state should be persisted"
    messages = state.values.get("messages", [])
    assert len(messages) == 4, f"expected 4 messages, got {len(messages)}"
    assert messages[0].content == "我叫张三"
    assert messages[1].content == "好的，张三，已记住。"
    assert messages[2].content == "我叫什么？"
    assert messages[3].content == "你叫张三。"

    await cleanup()


@pytest.mark.asyncio
async def test_append_messages_preserves_user_memory_across_rounds(
    async_sqlite_checkpointer,
):
    """验证场景 1（跨轮记忆）：第二轮写入后，第一轮的 HumanMessage 仍能被读到。"""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    from app.router.graph import _append_messages_to_checkpointer

    checkpointer, cleanup = async_sqlite_checkpointer
    thread_id = "integ-test-memory"

    # 第一轮
    await _append_messages_to_checkpointer(
        checkpointer,
        thread_id,
        [
            HumanMessage(content="记住：我最喜欢的颜色是蓝色"),
            AIMessage(content="好的，已记住您喜欢蓝色。"),
        ],
    )

    # 第二轮
    await _append_messages_to_checkpointer(
        checkpointer,
        thread_id,
        [
            HumanMessage(content="我最喜欢什么颜色？"),
            AIMessage(content="您最喜欢蓝色。"),
        ],
    )

    async def _passthrough(state: MessagesState) -> dict:
        return {"messages": []}

    graph = StateGraph(MessagesState)
    graph.add_node("passthrough", _passthrough)
    graph.add_edge(START, "passthrough")
    graph.add_edge("passthrough", END)
    compiled = graph.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": thread_id}}
    state = await compiled.aget_state(config)
    messages = state.values.get("messages", [])
    user_msgs = [m.content for m in messages if isinstance(m, HumanMessage)]
    assert "记住：我最喜欢的颜色是蓝色" in user_msgs
    assert "我最喜欢什么颜色？" in user_msgs

    await cleanup()
