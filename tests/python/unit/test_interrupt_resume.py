"""暂停/恢复状态机测试。

覆盖：
1. DeepAgent 主循环在迭代起点检测到 pause 后 yield paused 事件并阻塞
2. clear_pause 后循环继续执行（P0 移除了 resumed 事件，非 SSE 契约）
3. pause/resume 不清理 abort 标志或 pending approvals
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeApprovalDecision:
    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.decision = "approve" if approved else "deny"


def _fake_tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


@pytest.fixture(autouse=True)
def _clear_pause_state() -> None:
    """每个测试前清理全局 pause 状态。"""
    from app.security.approval import state as approval_state

    approval_state._pause_flags.clear()
    approval_state._pause_events.clear()
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    yield
    approval_state._pause_flags.clear()
    approval_state._pause_events.clear()
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()


@pytest.fixture
def _patch_deep_dependencies(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """打桩 run_deep_path 依赖。

    注意：``run_agent_with_approval`` 实际位于 ``app.deep.execution``，
    因此可注入函数（_is_interrupted / _get_pending_tool_calls /
    _handle_directory_extension）必须 patch 在 ``app.deep.execution``
    而非 ``app.deep.agent`` 的 re-export 上。
    """
    import app.deep.agent as agent_module
    import app.deep.execution as exec_module

    monkeypatch.setattr(
        agent_module,
        "_make_deep_tools",
        lambda *args, **kwargs: [_fake_tool("read_file")],
    )
    monkeypatch.setattr(
        agent_module,
        "_load_mcp_tools",
        AsyncMock(return_value=([], set())),
    )

    fake_agent = MagicMock()
    fake_agent.aupdate_state = AsyncMock()
    monkeypatch.setattr(
        agent_module,
        "build_deep_agent",
        AsyncMock(return_value=fake_agent),
    )

    fake_sandbox = MagicMock()
    fake_sandbox.set_full_trust = AsyncMock()
    fake_sandbox.clear_temp = AsyncMock()
    fake_sandbox.is_path_authorized = AsyncMock(return_value=False)
    monkeypatch.setattr(agent_module, "get_sandbox", lambda: fake_sandbox)

    # 默认空流；具体测试 case 会覆盖
    async def _default_stream(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "ok"}
    monkeypatch.setattr(exec_module, "_stream_default", _default_stream)

    return {"agent": fake_agent, "sandbox": fake_sandbox}


@pytest.mark.asyncio
async def test_deep_path_pause_resume(
    monkeypatch: pytest.MonkeyPatch,
    _patch_deep_dependencies: dict[str, Any],
) -> None:
    """pause 后阻塞并 yield paused，resume 后 yield resumed 并继续。"""
    from app.deep.agent import run_deep_path
    import app.deep.execution as exec_module
    from app.security.approval import set_pause, clear_pause, is_aborted, set_abort

    # 迭代 1 有非危险待执行工具，迭代 2 检测到 pause，resume 后图完成
    monkeypatch.setattr(
        exec_module,
        "_is_interrupted",
        AsyncMock(side_effect=[True, False]),
    )
    monkeypatch.setattr(
        exec_module,
        "_get_pending_tool_calls",
        AsyncMock(return_value=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/a.txt"}}]),
    )
    monkeypatch.setattr(
        exec_module,
        "_handle_directory_extension",
        AsyncMock(return_value=MagicMock(events=[], denied=False, timed_out=False)),
    )

    call_count = 0

    async def _fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {"event": "token", "data": "step1"}
            # 留出时间让外部任务设置 pause
            await asyncio.sleep(0.05)
            yield {"event": "token", "data": "step2"}
        else:
            yield {"event": "token", "data": "step3"}

    monkeypatch.setattr(exec_module, "_stream_default", _fake_stream)

    # 预置 abort 标志，验证 pause 不会清理它
    await set_abort("t-pause")

    async def _pause_resume() -> None:
        await asyncio.sleep(0.03)
        await set_pause("t-pause")
        await asyncio.sleep(0.1)
        await clear_pause("t-pause")

    task = asyncio.create_task(_pause_resume())
    events = [e async for e in run_deep_path({"thread_id": "t-pause"}, "hello")]
    await task

    event_names = [e.get("event") for e in events]
    assert "paused" in event_names
    # P0 移除了 resumed 事件（非 SSE 契约事件），resume 后循环直接继续 yield token
    assert "token" in event_names

    # abort 标志未被清理（run_deep_path 不消费 abort，只依赖 _await_approval 检查）
    assert await is_aborted("t-pause")


@pytest.mark.asyncio
async def test_clear_pause_before_wait_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
    _patch_deep_dependencies: dict[str, Any],
) -> None:
    """若 clear_pause 在 wait 前已调用，不应永久阻塞。"""
    from app.deep.agent import run_deep_path
    import app.deep.execution as exec_module
    from app.security.approval import set_pause, clear_pause

    monkeypatch.setattr(
        exec_module,
        "_is_interrupted",
        AsyncMock(side_effect=[True, False]),
    )
    monkeypatch.setattr(
        exec_module,
        "_get_pending_tool_calls",
        AsyncMock(return_value=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/a.txt"}}]),
    )
    monkeypatch.setattr(
        exec_module,
        "_handle_directory_extension",
        AsyncMock(return_value=MagicMock(events=[], denied=False, timed_out=False)),
    )

    async def _fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
        yield {"event": "token", "data": "ok"}

    monkeypatch.setattr(exec_module, "_stream_default", _fake_stream)

    # 先设置再立即清除 pause，模拟 race：run_deep_path 检查时可能仍为 True
    await set_pause("t-race")
    await clear_pause("t-race")

    events = [e async for e in run_deep_path({"thread_id": "t-race"}, "hello")]
    event_names = [e.get("event") for e in events]
    # 即使发生 race，也应安全退出（可能看到也可能看不到 paused/resumed）
    assert "token" in event_names


@pytest.mark.asyncio
async def test_chat_pause_resume_endpoints() -> None:
    """pause/resume 端点设置/清除标志。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.security.approval import is_paused

    client = TestClient(app)
    tid = "t-api"

    response = client.post("/api/chat/pause", json={"thread_id": tid})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert await is_paused(tid)

    response = client.post("/api/chat/resume", json={"thread_id": tid})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert not await is_paused(tid)
