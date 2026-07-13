"""LLM 流响应中止的测试。

场景化架构下，路径 A（CHAT）已删除，本测试覆盖：
1. DeepAgent 流式事件生成器中止行为

AgentTeam v2 的中止测试见 ``test_team_v2_concurrency.py``。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clear_abort_state() -> None:
    """每个测试前清理全局 abort 状态，避免事件泄漏。"""
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    yield
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()


@pytest.mark.asyncio
async def test_deep_stream_responds_to_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """DeepAgent 流式事件生成器在中止后应抛出 CancelledError。"""
    from langchain_core.messages import AIMessage
    from app.deepagent.streaming import _stream_agent_events
    from app.security.approval import set_abort

    async def _fake_astream(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        # astream(stream_mode="values")：先产生一个含 AIMessage 的 state（hello），
        # 随后 sleep 模拟长任务，等 abort 触发 CancelledError
        yield {"messages": [AIMessage(content="hello")]}
        await asyncio.sleep(10)

    fake_agent = MagicMock()
    fake_agent.astream = _fake_astream

    async def _abort_after() -> None:
        await asyncio.sleep(0.05)
        await set_abort("t-abort-deep")

    task = asyncio.create_task(_abort_after())
    events: list[dict[str, str]] = []
    try:
        async for event in _stream_agent_events(
            fake_agent, {"messages": []}, {"configurable": {"thread_id": "t-abort-deep"}}
        ):
            events.append(event)
    except asyncio.CancelledError:
        pass
    finally:
        await asyncio.gather(task, return_exceptions=True)

    assert any(e.get("event") == "token" for e in events)


# AgentTeam v2 的中止测试见 tests/python/unit/test_team_v2_concurrency.py
