"""Phase 1 稳定性硬化测试。

覆盖：
- D1: ``_run_subtask_stream`` / ``_run_team_role_subtask`` 超时包裹（``asyncio.wait_for``）
- D2: ``TeamState`` 新增 ``team_semaphore`` / ``subtask_timeout`` 字段
- D3: 超时分支发射 ``delegation`` 事件让前端 trace 可见
- D4: ``team_done`` 后必须跟 ``done`` 事件（降级路径 / 正常流 / 异常路径）
- 并发信号量 ``_acquire_semaphore`` 行为
- ``_resolve_team_settings`` 从 settings 读取并发参数
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.team.orchestrator import (
    _acquire_semaphore,
    _resolve_team_settings,
    run_team_path,
)


# ============================================================
# 公共 fixtures / helpers
# ============================================================


@pytest.fixture(autouse=True)
def _clear_abort_state():
    """每个用例前后清理全局 abort 状态。"""
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    yield
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()


async def _collect_events(gen: AsyncIterator[dict]) -> list[dict]:
    events: list[dict] = []
    async for event in gen:
        events.append(event)
    return events


def _make_fake_llm_for_aggregator() -> MagicMock:
    """构造 mock LLM：astream 返回汇总 chunk（供 Aggregator 使用）。"""
    mock = MagicMock()

    async def _fake_astream(messages: Any) -> AsyncIterator:
        yield SimpleNamespace(content="最终汇总")

    mock.astream = _fake_astream
    return mock


def _patch_orchestrator_to_return_todos(
    monkeypatch: pytest.MonkeyPatch,
    todos: list[dict],
) -> MagicMock:
    """patch get_chat_model 返回 mock LLM（ainvoke 返回 [agent:xxx] 任务行文本）。"""
    fake_llm = _make_fake_llm_for_aggregator()
    todo_lines = "\n".join(t["content"] for t in todos)
    fake_response = SimpleNamespace(content=todo_lines)
    fake_llm.ainvoke = AsyncMock(return_value=fake_response)
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: fake_llm)
    return fake_llm


def _todo(content: str, status: str = "pending") -> dict:
    return {"content": content, "status": status}


# ============================================================
# _resolve_team_settings
# ============================================================


def test_resolve_team_settings_reads_from_settings() -> None:
    """``_resolve_team_settings`` 从 settings 顶层字段读取并发参数。"""
    settings = SimpleNamespace(
        agent_team_max_parallel=4,
        agent_team_subtask_timeout=600,
    )
    max_parallel, subtask_timeout = _resolve_team_settings(settings)
    assert max_parallel == 4
    assert subtask_timeout == 600


def test_resolve_team_settings_defaults_on_missing_attr() -> None:
    """settings 缺少字段时降级到默认值 (3, 300)。"""
    settings = SimpleNamespace()
    max_parallel, subtask_timeout = _resolve_team_settings(settings)
    assert max_parallel == 3
    assert subtask_timeout == 300


def test_resolve_team_settings_partial_missing() -> None:
    """只有 max_parallel 字段时，subtask_timeout 降级到默认值。"""
    settings = SimpleNamespace(agent_team_max_parallel=2)
    max_parallel, subtask_timeout = _resolve_team_settings(settings)
    assert max_parallel == 2
    assert subtask_timeout == 300


def test_resolve_team_settings_rejects_none_value() -> None:
    """值为 None 时降级到默认值（H1 防御性校验）。"""
    settings = SimpleNamespace(
        agent_team_max_parallel=None,
        agent_team_subtask_timeout=None,
    )
    max_parallel, subtask_timeout = _resolve_team_settings(settings)
    assert max_parallel == 3
    assert subtask_timeout == 300


def test_resolve_team_settings_rejects_zero_and_negative() -> None:
    """0 / 负数降级到默认值（H1 防御性校验，防 Semaphore(0) 死锁）。"""
    settings = SimpleNamespace(
        agent_team_max_parallel=0,
        agent_team_subtask_timeout=-10,
    )
    max_parallel, subtask_timeout = _resolve_team_settings(settings)
    assert max_parallel == 3
    assert subtask_timeout == 300


# ============================================================
# _acquire_semaphore
# ============================================================


def test_acquire_semaphore_none_returns_nullcontext() -> None:
    """semaphore=None 时返回 nullcontext（no-op）。"""
    cm = _acquire_semaphore(None)
    assert isinstance(cm, contextlib.nullcontext)


def test_acquire_semaphore_valid_returns_semaphore() -> None:
    """有效 Semaphore 时直接返回它本身。"""
    sem = asyncio.Semaphore(2)
    cm = _acquire_semaphore(sem)
    assert cm is sem


# ============================================================
# D4: team_done + done 配对 — 降级路径
# ============================================================


async def test_downgrade_path_emits_done_without_team_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """降级路径：T10 不产出 team 生命周期事件，只发射 token + done。"""
    def _orchestrator_should_not_be_called(**_: Any) -> Any:
        raise AssertionError("Orchestrator should not be called for simple message")
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", _orchestrator_should_not_be_called)

    events = await _collect_events(
        run_team_path("你好", "t-downgrade-done", {"thread_id": "t-downgrade-done", "messages": []})
    )

    event_types = [e["event"] for e in events]
    # T10: 降级路径不产出 team 生命周期事件
    assert "team_done" not in event_types
    assert "team_init" not in event_types
    # 但必须以 done 终结
    assert "done" in event_types

    # done 是终端信号，data 固定为 "{}"（make_sse_event 对 done 事件的设计）
    done_evt = next(e for e in events if e["event"] == "done")
    assert done_evt["data"] == "{}"


# ============================================================
# D4: team_done + done 配对 — 正常流
# ============================================================


async def test_normal_flow_emits_done_after_team_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正常流：_aggregate_node 发射 team_done 后 run_team_path 补 done。"""
    async def _fake_run_coding_expert(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "standard",
        workspace_path: str | None = None,
        parent_thread_id: str | None = None,
        chat_model=None,
    ) -> AsyncIterator[dict]:
        yield {"event": "token", "data": "代码结果"}

    todos = [_todo("[agent:code] 读 main.py")]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

    events = await _collect_events(
        run_team_path(
            "分析项目入口文件",
            "t-normal-done",
            {"thread_id": "t-normal-done", "messages": []},
            subtask_runners={"code": _fake_run_coding_expert},
        )
    )

    event_types = [e["event"] for e in events]
    assert "team_done" in event_types
    assert "done" in event_types

    # done 必须在 team_done 之后
    team_done_idx = event_types.index("team_done")
    done_idx = event_types.index("done")
    assert done_idx > team_done_idx


# ============================================================
# D4: team_done + done 配对 — 异常路径（coding_team/agent.py）
# ============================================================


async def test_error_path_emits_done_after_team_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """异常路径：run_coding_team catch 后发射 team_done{error} + done。"""
    from app.scenarios.coding_team.agent import run_coding_team

    # 让 run_team_path 抛异常
    async def _failing_run_team_path(*args, **kwargs):
        raise RuntimeError("simulated failure")
        yield  # unreachable — 让函数成为 async generator

    monkeypatch.setattr(
        "app.scenarios.coding_team.agent.run_team_path",
        _failing_run_team_path,
    )

    events = await _collect_events(
        run_coding_team(
            "触发异常的消息",
            "t-error-done",
        )
    )

    event_types = [e["event"] for e in events]
    assert "error" in event_types
    assert "team_done" in event_types
    assert "done" in event_types

    # 顺序：error → team_done → done
    error_idx = event_types.index("error")
    team_done_idx = event_types.index("team_done")
    done_idx = event_types.index("done")
    assert team_done_idx > error_idx
    assert done_idx > team_done_idx

    # done 是终端信号，data 固定为 "{}"
    done_evt = next(e for e in events if e["event"] == "done")
    assert done_evt["data"] == "{}"


# ============================================================
# D2: Semaphore 创建并存储到 TeamState
# ============================================================


async def test_semaphore_created_and_stored_in_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_team_path 创建 Semaphore 并通过 TeamState 传递到子任务节点。"""
    captured_semaphore: dict[str, Any] = {}

    async def _fake_run_coding_expert(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "standard",
        workspace_path: str | None = None,
        parent_thread_id: str | None = None,
        chat_model=None,
    ) -> AsyncIterator[dict]:
        yield {"event": "token", "data": "结果"}

    # 拦截 _run_subtask_node，验证 semaphore 传入
    from app.team import orchestrator as orch_mod

    original_subtask_node = orch_mod._run_subtask_node

    async def _spy_subtask_node(state):
        captured_semaphore["semaphore"] = state.get("team_semaphore")
        captured_semaphore["subtask_timeout"] = state.get("subtask_timeout", 0)
        return await original_subtask_node(state)

    monkeypatch.setattr(orch_mod, "_run_subtask_node", _spy_subtask_node)

    # 重置 LangGraph 单例，让 _build_team_graph 重新编译时使用 patched 的 _run_subtask_node
    monkeypatch.setattr(orch_mod, "_team_graph", None)

    todos = [_todo("[agent:code] 读 main.py")]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

    await _collect_events(
        run_team_path(
            "分析项目入口文件",
            "t-semaphore",
            {"thread_id": "t-semaphore", "messages": []},
            subtask_runners={"code": _fake_run_coding_expert},
        )
    )

    # semaphore 应被传入子任务节点
    assert captured_semaphore.get("semaphore") is not None
    assert isinstance(captured_semaphore["semaphore"], asyncio.Semaphore)
    # subtask_timeout 默认值应 >= 30
    assert captured_semaphore.get("subtask_timeout", 0) >= 30


# ============================================================
# D1 + D3: 子任务超时 → delegation 事件
# ============================================================


async def test_subtask_timeout_emits_delegation_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子任务超时后 _run_subtask_stream 发射 delegation{event:timeout} 事件。"""
    from app.team.scheduler import _run_subtask_stream

    writer_events: list[dict] = []

    def _writer(event: dict) -> None:
        writer_events.append(event)

    async def _slow_runner(*args, **kwargs) -> AsyncIterator[dict]:
        """模拟超时 runner：sleep 超过 timeout。"""
        await asyncio.sleep(10)
        yield {"event": "token", "data": "should not see"}

    abort_event = asyncio.Event()

    result = await _run_subtask_stream(
        _slow_runner,
        runner_args=(),
        runner_kwargs={},
        agent_name="slow-agent",
        abort_event=abort_event,
        writer=_writer,
        subtask_timeout=1,  # 1 秒超时
    )

    # 返回失败结果
    assert result.success is False
    assert "超时" in result.payload

    # delegation 事件被发射
    delegation_events = [e for e in writer_events if e.get("event") == "delegation"]
    assert len(delegation_events) >= 1

    # delegation 事件包含超时信息
    timeout_event = delegation_events[0]
    data = json.loads(timeout_event["data"])
    assert data.get("event") == "timeout"
    assert data.get("agent") == "slow-agent"
    assert data.get("timeout") == 1


# ============================================================
# D1: 超时不影响正常完成的子任务
# ============================================================


async def test_subtask_timeout_does_not_affect_normal_completion() -> None:
    """正常完成的子任务不受超时包裹影响。"""
    from app.team.scheduler import _run_subtask_stream

    writer_events: list[dict] = []

    def _writer(event: dict) -> None:
        writer_events.append(event)

    async def _fast_runner(*args, **kwargs) -> AsyncIterator[dict]:
        yield {"event": "token", "data": "快速结果"}
        yield {"event": "_subtask_done", "data": json.dumps({"agent": "fast", "success": True, "payload": "ok"})}

    abort_event = asyncio.Event()

    result = await _run_subtask_stream(
        _fast_runner,
        runner_args=(),
        runner_kwargs={},
        agent_name="fast-agent",
        abort_event=abort_event,
        writer=_writer,
        subtask_timeout=300,
    )

    assert result.success is True
    assert result.payload == "ok"

    # 没有 delegation timeout 事件
    delegation_events = [e for e in writer_events if e.get("event") == "delegation"]
    timeout_events = [e for e in delegation_events if json.loads(e["data"]).get("event") == "timeout"]
    assert len(timeout_events) == 0


# ============================================================
# D1: abort 仍然优先于超时
# ============================================================


async def test_abort_takes_priority_over_timeout() -> None:
    """用户中止时 abort 优先于超时返回。"""
    from app.team.scheduler import _run_subtask_stream

    writer_events: list[dict] = []

    def _writer(event: dict) -> None:
        writer_events.append(event)

    async def _blocking_runner(*args, **kwargs) -> AsyncIterator[dict]:
        # 长时间运行，但 abort 会提前触发
        await asyncio.sleep(10)
        yield {"event": "token", "data": "should not see"}

    abort_event = asyncio.Event()
    abort_event.set()  # 预先设置中止

    result = await _run_subtask_stream(
        _blocking_runner,
        runner_args=(),
        runner_kwargs={},
        agent_name="blocked-agent",
        abort_event=abort_event,
        writer=_writer,
        subtask_timeout=300,
    )

    # abort 导致返回失败（payload 包含"中止"）
    assert result.success is False
    assert "中止" in result.payload
