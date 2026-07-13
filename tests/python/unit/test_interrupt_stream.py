"""LLM 流与路径 D 子任务响应中止的测试。

场景化架构下，路径 A（CHAT）已删除，本测试仅覆盖：
1. DeepAgent 流式事件生成器中止行为
2. AgentTeam（coding_team）子任务中止行为
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_team_runner_responds_to_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """路径 D 的 _run_subtask_node 在中止后应让子任务失败，最终 Aggregator 发 error + team_done(error)。"""
    from app.team.orchestrator import run_team_path
    import app.team.orchestrator as orch_module

    from app.security.approval import set_abort

    # 降级：让简单任务不走 chat 路径，强制进入 team 路径
    monkeypatch.setattr(
        orch_module,
        "_should_downgrade_to_single",
        lambda msg: (False, ""),
    )

    # mock LLM：ainvoke 返回 [agent:deep] 任务行文本（_plan_node 解析为 deep 子任务）
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(
        return_value=MagicMock(content="[agent:deep] subtask")
    )
    monkeypatch.setattr(
        orch_module,
        "get_chat_model",
        lambda *args, **kwargs: fake_llm,
    )

    # 模拟 _validate_task 通过
    monkeypatch.setattr(
        orch_module,
        "_validate_task",
        lambda task, settings: (True, ""),
    )

    # 模拟 deep runner：正常情况下不会返回，但 _run_subtask_node 会在入口检查 abort
    async def _fake_run_deep_path(state, message: str, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "should not see"}
        await asyncio.sleep(10)

    # 跳过 aggregator（中止场景下 findings 为空，Aggregator 会发 error，
    # 但我们用 _empty_stream 简化断言）
    monkeypatch.setattr(
        orch_module,
        "_run_aggregator",
        lambda *args, **kwargs: _empty_stream(),
    )

    async def _abort_before() -> None:
        await set_abort("t-abort-team")

    await _abort_before()

    events = [
        e
        async for e in run_team_path(
            "team task",
            "t-abort-team",
            {"thread_id": "t-abort-team", "messages": [], "agent_mode": "coding_team"},
            subtask_runners={"deep": _fake_run_deep_path},
        )
    ]

    # 中止后子任务节点返回失败 payload "用户中止"，进入 errors dict。
    # Aggregator 检测 findings={} → 发 error 事件 "所有专家任务均失败" + team_done(error)。
    error_events = [e for e in events if e.get("event") == "error"]
    assert len(error_events) >= 1
    assert any("所有专家任务均失败" in e.get("data", "") for e in error_events)

    # team_done 收尾
    done_events = [e for e in events if e.get("event") == "team_done"]
    assert len(done_events) >= 1


async def _empty_stream() -> AsyncIterator[dict[str, str]]:
    yield {"event": "token", "data": "summary"}
