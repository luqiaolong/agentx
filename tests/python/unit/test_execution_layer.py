"""公共审批执行层单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.deep.execution import run_agent_with_approval


@pytest.mark.asyncio
async def test_run_agent_with_approval_yields_events_and_breaks_when_done() -> None:
    """agent 未中断时，执行层只流式输出一次事件并退出。"""
    agent = MagicMock()
    config = {"configurable": {"thread_id": "t1"}}

    async def _fake_stream(agent, inputs, config, source):
        yield {"event": "token", "data": "hello"}

    with patch("app.deep.execution._stream_default", _fake_stream):
        with patch("app.deep.execution._is_interrupted", new=AsyncMock(return_value=False)):
            events = []
            async for evt in run_agent_with_approval(
                agent,
                config,
                thread_id="t1",
                workspace_path=None,
                permission_mode="standard",
                runtime_dangerous=set(),
                source="deep",
                inputs={"messages": []},
            ):
                events.append(evt)

    assert events == [{"event": "token", "data": "hello"}]


@pytest.mark.asyncio
async def test_run_agent_with_approval_handles_interrupt_and_resumes() -> None:
    """agent 中断后，执行层进入审批循环并恢复。"""
    agent = MagicMock()
    config = {"configurable": {"thread_id": "t1"}}

    stream_calls = []
    resume_count = 0

    async def _fake_stream(agent, inputs, config, source):
        stream_calls.append(("inputs", inputs))
        if inputs is None:
            nonlocal resume_count
            resume_count += 1
            if resume_count == 1:
                yield {"event": "token", "data": "done"}

    call_idx = 0

    async def _is_interrupted(agent, config):
        nonlocal call_idx
        call_idx += 1
        # 第一次检测为 True，进入中断循环；恢复后再次检测为 False
        return call_idx <= 1

    async def _get_pending(agent, config):
        return [{"name": "read_file", "args": {"path": "/tmp/x"}, "id": "tc1"}]

    with patch("app.deep.execution._stream_default", _fake_stream):
        with patch("app.deep.execution._is_interrupted", _is_interrupted):
            with patch("app.deep.execution._get_pending_tool_calls", _get_pending):
                with patch(
                    "app.deep.execution._handle_directory_extension",
                    new=AsyncMock(return_value=MagicMock(events=[], denied=False, timed_out=False)),
                ):
                    events = []
                    async for evt in run_agent_with_approval(
                        agent,
                        config,
                        thread_id="t1",
                        workspace_path=None,
                        permission_mode="standard",
                        runtime_dangerous=set(),
                        source="deep",
                        inputs={"messages": []},
                    ):
                        events.append(evt)

    assert ("inputs", {"messages": []}) in stream_calls
    assert ("inputs", None) in stream_calls
    assert {"event": "token", "data": "done"} in events


@pytest.mark.asyncio
async def test_run_agent_with_approval_full_trust_skips_approval() -> None:
    """full_trust 模式直接恢复执行，不进入审批循环。"""
    agent = MagicMock()
    config = {"configurable": {"thread_id": "t1"}}

    async def _fake_stream(agent, inputs, config, source):
        if inputs is None:
            yield {"event": "token", "data": "ok"}

    call_idx = 0

    async def _is_interrupted(agent, config):
        nonlocal call_idx
        call_idx += 1
        # 第一次检测为 True，进入中断循环；恢复后再次检测为 False
        return call_idx <= 1

    with patch("app.deep.execution._stream_default", _fake_stream):
        with patch("app.deep.execution._is_interrupted", _is_interrupted):
            with patch(
                "app.deep.execution._get_pending_tool_calls",
                new=AsyncMock(return_value=[{"name": "write_file", "id": "tc1"}]),
            ):
                events = []
                async for evt in run_agent_with_approval(
                    agent,
                    config,
                    thread_id="t1",
                    workspace_path=None,
                    permission_mode="full_trust",
                    runtime_dangerous={"write_file"},
                    source="deep",
                    inputs={"messages": []},
                ):
                    events.append(evt)

    assert events == [{"event": "token", "data": "ok"}]


@pytest.mark.asyncio
async def test_run_agent_with_approval_readonly_streak_forces_stop() -> None:
    """只读工具连续调用超过阈值时强制停止。"""
    agent = MagicMock()
    config = {"configurable": {"thread_id": "t1"}}

    async def _fake_stream(agent, inputs, config, source):
        yield {"event": "token", "data": "thinking"}

    interrupted = True

    async def _is_interrupted(agent, config):
        return interrupted

    async def _get_pending(agent, config):
        return [{"name": "read_file", "args": {"path": "/tmp/x"}, "id": "tc1"}]

    with patch("app.deep.execution._stream_default", _fake_stream):
        with patch("app.deep.execution._is_interrupted", _is_interrupted):
            with patch("app.deep.execution._get_pending_tool_calls", _get_pending):
                with patch(
                    "app.deep.execution._inject_tool_error_for_call",
                    new=AsyncMock(),
                ) as mock_inject:
                    with patch(
                        "app.deep.execution._handle_directory_extension",
                        new=AsyncMock(
                            return_value=MagicMock(
                                events=[], denied=False, timed_out=False
                            )
                        ),
                    ):
                        events = []
                        async for evt in run_agent_with_approval(
                            agent,
                            config,
                            thread_id="t1",
                            workspace_path=None,
                            permission_mode="standard",
                            runtime_dangerous=set(),
                            source="deep",
                            inputs={"messages": []},
                            readonly_streak_threshold=2,
                        ):
                            events.append(evt)

    assert any(e.get("event") == "error" for e in events)
    assert mock_inject.call_count >= 1
