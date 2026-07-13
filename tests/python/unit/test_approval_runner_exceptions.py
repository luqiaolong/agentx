"""approval_runner 异常收窄单元测试（T6.4）。

覆盖 9 处 ``except Exception: pass`` 收窄后的行为：
1. 非 interrupt 异常（RuntimeError）在 resume 上下文中应被 ``logger.warning`` 记录
2. ``GraphInterrupt`` 在 resume 上下文中应被静默吞掉（正常退出，不记录 warning）
3. ``pop_approval`` 异常（非 resume 上下文）应被 ``logger.warning`` 记录
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.errors import GraphInterrupt

from app.deepagent.approval_runner import run_agent_with_approval


def _make_raising_astream(exc: BaseException):
    """构造一个调用后即在首次迭代抛出指定异常的 async generator 函数。

    ``raise`` 在 ``yield`` 之前，但 ``yield`` 的存在使 Python 将该函数识别为
    async generator function。调用该函数返回 async generator 对象，
    首次 ``__anext__`` 执行 ``raise exc``。
    """

    async def _astream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise exc
        yield  # pragma: no cover — 标记为 async generator（不可达）

    return _astream


async def _ok_stream(agent: Any, inputs: Any, config: dict, source: str) -> AsyncIterator[dict[str, str]]:
    """初始流：yield 一个 token 后正常结束。"""
    yield {"event": "token", "data": "ok"}


def _common_kwargs(agent: Any) -> dict:
    """``run_agent_with_approval`` 公共参数。"""
    return {
        "thread_id": "t1",
        "workspace_path": None,
        "permission_mode": "standard",
        "runtime_dangerous": set(),
        "source": "deep",
        "inputs": {"messages": []},
        "sandbox": MagicMock(),
    }


@pytest.mark.asyncio
async def test_resume_runtime_error_logs_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 interrupt 异常（RuntimeError）在 pause 分支 resume 时应被 logger.warning 记录。"""
    agent = MagicMock()
    agent.aget_state = AsyncMock(return_value=None)
    config = {"configurable": {"thread_id": "t1"}}

    runtime_error = RuntimeError("boom")
    agent.astream = _make_raising_astream(runtime_error)

    import app.deepagent.approval_runner as exec_module

    monkeypatch.setattr(exec_module, "_stream_default", _ok_stream)
    monkeypatch.setattr(exec_module, "is_paused", AsyncMock(return_value=True))
    monkeypatch.setattr(exec_module, "_is_interrupted", AsyncMock(return_value=True))
    monkeypatch.setattr(
        exec_module,
        "_get_pending_tool_calls",
        AsyncMock(return_value=[{"name": "read_file", "args": {}, "id": "tc1"}]),
    )
    monkeypatch.setattr("app.security.approval.pop_approval", AsyncMock())

    mock_logger = MagicMock()
    monkeypatch.setattr(exec_module, "logger", mock_logger)

    events = [
        evt
        async for evt in run_agent_with_approval(agent, config, **_common_kwargs(agent))
    ]

    # pause 事件应被 yield
    event_names = [e.get("event") for e in events]
    assert "paused" in event_names

    # RuntimeError 应被 logger.warning 记录，而不是静默吞掉
    assert mock_logger.warning.called
    warning_args = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("unexpected resume exception" in arg for arg in warning_args)
    assert any("boom" in arg for arg in warning_args)


@pytest.mark.asyncio
async def test_resume_graph_interrupt_silently_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """GraphInterrupt 在 pause 分支 resume 时应被静默吞掉（正常退出，不记录 warning）。"""
    agent = MagicMock()
    agent.aget_state = AsyncMock(return_value=None)
    config = {"configurable": {"thread_id": "t1"}}

    agent.astream = _make_raising_astream(GraphInterrupt())

    import app.deepagent.approval_runner as exec_module

    monkeypatch.setattr(exec_module, "_stream_default", _ok_stream)
    monkeypatch.setattr(exec_module, "is_paused", AsyncMock(return_value=True))
    monkeypatch.setattr(exec_module, "_is_interrupted", AsyncMock(return_value=True))
    monkeypatch.setattr(
        exec_module,
        "_get_pending_tool_calls",
        AsyncMock(return_value=[{"name": "read_file", "args": {}, "id": "tc1"}]),
    )
    monkeypatch.setattr("app.security.approval.pop_approval", AsyncMock())

    mock_logger = MagicMock()
    monkeypatch.setattr(exec_module, "logger", mock_logger)

    events = [
        evt
        async for evt in run_agent_with_approval(agent, config, **_common_kwargs(agent))
    ]

    # pause 事件应被 yield
    event_names = [e.get("event") for e in events]
    assert "paused" in event_names

    # GraphInterrupt 应被静默吞掉——不应有 "unexpected resume exception" warning
    warning_args = [str(c) for c in mock_logger.warning.call_args_list]
    assert not any("unexpected resume exception" in arg for arg in warning_args)


@pytest.mark.asyncio
async def test_pop_approval_exception_logs_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """pop_approval 异常（非 resume 上下文）应被 logger.warning 记录。

    通过让 ``_is_interrupted`` 返回 False 跳过 astream 块，只触发 pop_approval 异常路径。
    """
    agent = MagicMock()
    agent.aget_state = AsyncMock(return_value=None)
    config = {"configurable": {"thread_id": "t1"}}

    pop_error = RuntimeError("pop failed")

    import app.deepagent.approval_runner as exec_module

    monkeypatch.setattr(exec_module, "_stream_default", _ok_stream)
    monkeypatch.setattr(exec_module, "is_paused", AsyncMock(return_value=True))
    monkeypatch.setattr(exec_module, "_is_interrupted", AsyncMock(return_value=False))
    monkeypatch.setattr("app.security.approval.pop_approval", AsyncMock(side_effect=pop_error))

    mock_logger = MagicMock()
    monkeypatch.setattr(exec_module, "logger", mock_logger)

    events = [
        evt
        async for evt in run_agent_with_approval(agent, config, **_common_kwargs(agent))
    ]

    # pause 事件应被 yield
    event_names = [e.get("event") for e in events]
    assert "paused" in event_names

    # pop_approval 异常应被 logger.warning 记录
    assert mock_logger.warning.called
    warning_args = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("unexpected pop_approval exception" in arg for arg in warning_args)
    assert any("pop failed" in arg for arg in warning_args)
