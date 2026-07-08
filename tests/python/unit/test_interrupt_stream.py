"""LLM 流与路径 D 子任务响应中止的测试。

场景化架构下，路径 A（CHAT）已删除，本测试仅覆盖：
1. DeepAgent 流式事件生成器中止行为
2. AgentTeam（coding_team）子任务中止行为
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clear_abort_state() -> None:
    """每个测试前清理全局 abort 状态，避免事件泄漏。"""
    from app.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    yield
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()


@pytest.mark.asyncio
async def test_deep_stream_responds_to_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """DeepAgent 流式事件生成器在中止后应抛出 CancelledError。"""
    from app.deep.streaming import _stream_agent_events
    from app.approval import set_abort

    async def _fake_astream(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        from langchain_core.messages import AIMessage

        yield {"messages": [AIMessage(content="hello")]}
        await asyncio.sleep(10)

    fake_agent = MagicMock()
    fake_agent.astream = _fake_astream

    async def _abort_after() -> None:
        await asyncio.sleep(0.05)
        set_abort("t-abort-deep")

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
    """路径 D 的 _runner 在中止后应产出失败的 _subtask_done 哨兵。"""
    from app.team.orchestrator import run_team_path
    import app.team.orchestrator as orch_module

    from app.approval import set_abort

    # 降级：让简单任务不走 chat 路径，强制进入 team 路径
    monkeypatch.setattr(
        orch_module,
        "_should_downgrade_to_single",
        lambda msg: (False, ""),
    )

    # 跳过真实 LLM 调用：ainvoke 必须返回可 await 的对象
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=MagicMock(content="plan"))
    monkeypatch.setattr(
        orch_module,
        "get_chat_model",
        lambda *args, **kwargs: fake_llm,
    )

    # 模拟 Orchestrator 生成一个 deep 子任务
    fake_task = MagicMock()
    fake_task.agent = "deep"
    fake_task.input = "subtask"
    fake_task.purpose = "test"
    monkeypatch.setattr(
        orch_module,
        "_parse_plan",
        lambda text, max_tasks: ([fake_task], "reasoning"),
    )

    # 模拟 _validate_task 通过
    monkeypatch.setattr(
        orch_module,
        "_validate_task",
        lambda task, settings: (True, ""),
    )

    # 模拟 _run_subtask：正常情况下不会返回，但 _runner 会在进入前检查 abort
    async def _fake_run_subtask(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "should not see"}
        await asyncio.sleep(10)

    # _runner 在 orchestrator 模块闭包中通过模块级名称访问 _run_subtask，
    # 因此必须替换 orchestrator 模块中的绑定而非 scheduler 模块。
    monkeypatch.setattr(
        orch_module,
        "_run_subtask",
        _fake_run_subtask,
    )

    # 跳过 aggregator
    monkeypatch.setattr(
        orch_module,
        "_run_aggregator",
        lambda *args, **kwargs: _empty_stream(),
    )

    async def _abort_before() -> None:
        set_abort("t-abort-team")

    await _abort_before()

    events = [
        e
        async for e in run_team_path(
            "team task",
            "t-abort-team",
            {"thread_id": "t-abort-team", "messages": [], "agent_mode": "coding_team"},
        )
    ]

    # _subtask_done 是内部哨兵，不会透传到前端；
    # 中止后应转为 team_progress error 事件并携带“用户中止”信息。
    def _parse_data(e: dict[str, str]) -> dict:
        data = e.get("data", "{}")
        if isinstance(data, dict):
            return data
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {}

    progress_errors = [
        e
        for e in events
        if e.get("event") == "team_progress"
        and _parse_data(e).get("status") == "error"
    ]
    assert len(progress_errors) >= 1
    assert any("中止" in _parse_data(e).get("message", "") for e in progress_errors)


async def _empty_stream() -> AsyncIterator[dict[str, str]]:
    yield {"event": "token", "data": "summary"}
