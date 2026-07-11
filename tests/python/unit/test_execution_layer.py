"""公共审批执行层单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from app.deepagent.approval_runner import run_agent_with_approval


@pytest.mark.asyncio
async def test_run_agent_with_approval_yields_events_and_breaks_when_done() -> None:
    """agent 未中断时，执行层只流式输出一次事件并退出。"""
    agent = MagicMock()
    config = {"configurable": {"thread_id": "t1"}}

    async def _fake_stream(agent, inputs, config, source):
        yield {"event": "token", "data": "hello"}

    with patch("app.deepagent.approval_runner._stream_default", _fake_stream):
        with patch("app.deepagent.approval_runner._is_interrupted", new=AsyncMock(return_value=False)):
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
        # resume 时传入 Command(resume=...) 而非 None
        if isinstance(inputs, Command):
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

    with patch("app.deepagent.approval_runner._stream_default", _fake_stream):
        with patch("app.deepagent.approval_runner._is_interrupted", _is_interrupted):
            with patch("app.deepagent.approval_runner._get_pending_tool_calls", _get_pending):
                with patch(
                    "app.deepagent.approval_runner._handle_directory_extension",
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
    # resume 时传入 Command(resume={"decisions": [{"type": "approve"}]})
    assert any(
        isinstance(call[1], Command) and call[1].resume == {"decisions": [{"type": "approve"}]}
        for call in stream_calls if call[0] == "inputs"
    )
    assert {"event": "token", "data": "done"} in events


@pytest.mark.asyncio
async def test_run_agent_with_approval_full_trust_skips_approval() -> None:
    """full_trust 模式直接恢复执行，不进入审批循环。"""
    agent = MagicMock()
    config = {"configurable": {"thread_id": "t1"}}

    async def _fake_stream(agent, inputs, config, source):
        # resume 时传入 Command(resume=...) 而非 None
        if isinstance(inputs, Command):
            yield {"event": "token", "data": "ok"}

    call_idx = 0

    async def _is_interrupted(agent, config):
        nonlocal call_idx
        call_idx += 1
        # 第一次检测为 True，进入中断循环；恢复后再次检测为 False
        return call_idx <= 1

    # 模拟 resume 后 state msg_count 增长，避免 stuck state 检测触发
    state_msg_count = 0

    async def _aget_state(config):
        nonlocal state_msg_count
        state_msg_count += 1
        mock_state = MagicMock()
        mock_state.values = {"messages": [MagicMock()] * state_msg_count}
        return mock_state

    agent.aget_state = _aget_state

    with patch("app.deepagent.approval_runner._stream_default", _fake_stream):
        with patch("app.deepagent.approval_runner._is_interrupted", _is_interrupted):
            with patch(
                "app.deepagent.approval_runner._get_pending_tool_calls",
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

    with patch("app.deepagent.approval_runner._stream_default", _fake_stream):
        with patch("app.deepagent.approval_runner._is_interrupted", _is_interrupted):
            with patch("app.deepagent.approval_runner._get_pending_tool_calls", _get_pending):
                with patch(
                    "app.deepagent.approval_runner._inject_tool_error_for_call",
                    new=AsyncMock(),
                ) as mock_inject:
                    with patch(
                        "app.deepagent.approval_runner._handle_directory_extension",
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


@pytest.mark.asyncio
async def test_stuck_state_requires_consecutive_stalls() -> None:
    """单次 resume 未推进不强制停止，连续 2 次停滞才触发 stuck state 终止。"""
    agent = MagicMock()
    config = {"configurable": {"thread_id": "t1"}}

    async def _fake_stream(agent, inputs, config, source):
        # 初始流 + resume 流都 yield 一点内容，但 state msg_count 不会增长
        yield {"event": "token", "data": "thinking"}
        if isinstance(inputs, Command):
            yield {"event": "token", "data": "after resume"}

    interrupted = True

    async def _is_interrupted(agent, config):
        return interrupted

    async def _get_pending(agent, config):
        return [{"name": "read_file", "args": {"path": "/tmp/x"}, "id": "tc1"}]

    # state messages 数量固定：模拟每次 resume 后 state 都未推进
    mock_state = MagicMock()
    mock_state.values = {"messages": [MagicMock()] * 3}
    agent.aget_state = AsyncMock(return_value=mock_state)

    with patch("app.deepagent.approval_runner._stream_default", _fake_stream):
        with patch("app.deepagent.approval_runner._is_interrupted", _is_interrupted):
            with patch(
                "app.deepagent.approval_runner._get_pending_tool_calls", _get_pending
            ):
                with patch(
                    "app.deepagent.approval_runner._handle_directory_extension",
                    new=AsyncMock(
                        return_value=MagicMock(
                            events=[], denied=False, timed_out=False
                        )
                    ),
                ):
                    with patch(
                        "app.deepagent.approval_runner._inject_tool_error_messages",
                        new=AsyncMock(),
                    ) as mock_inject:
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
                            max_iterations=5,
                        ):
                            events.append(evt)

    # 连续 2 次停滞后应强制停止
    assert any(e.get("event") == "error" for e in events)
    assert mock_inject.call_count >= 1


@pytest.mark.asyncio
async def test_stuck_state_single_stall_does_not_force_stop() -> None:
    """单次停滞后下一轮正常推进，则不应触发强制停止。"""
    agent = MagicMock()
    config = {"configurable": {"thread_id": "t1"}}

    call_idx = 0

    async def _fake_stream(agent, inputs, config, source):
        nonlocal call_idx
        call_idx += 1
        yield {"event": "token", "data": f"chunk-{call_idx}"}
        if isinstance(inputs, Command):
            yield {"event": "token", "data": f"resume-{call_idx}"}

    interrupted_calls = 0

    async def _is_interrupted(agent, config):
        nonlocal interrupted_calls
        interrupted_calls += 1
        # 前两次返回 True（进入审批循环），之后返回 False 退出
        return interrupted_calls <= 2

    async def _get_pending(agent, config):
        return [{"name": "read_file", "args": {"path": "/tmp/x"}, "id": "tc1"}]

    msg_count = 2

    async def _aget_state(config):
        nonlocal msg_count
        msg_count += 1
        mock_state = MagicMock()
        mock_state.values = {"messages": [MagicMock()] * msg_count}
        return mock_state

    agent.aget_state = _aget_state

    with patch("app.deepagent.approval_runner._stream_default", _fake_stream):
        with patch("app.deepagent.approval_runner._is_interrupted", _is_interrupted):
            with patch(
                "app.deepagent.approval_runner._get_pending_tool_calls", _get_pending
            ):
                with patch(
                    "app.deepagent.approval_runner._handle_directory_extension",
                    new=AsyncMock(
                        return_value=MagicMock(
                            events=[], denied=False, timed_out=False
                        )
                    ),
                ):
                    with patch(
                        "app.deepagent.approval_runner._inject_tool_error_messages",
                        new=AsyncMock(),
                    ) as mock_inject:
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
                            max_iterations=5,
                        ):
                            events.append(evt)

    # 不应触发强制停止
    assert not any(e.get("event") == "error" for e in events)
    assert mock_inject.call_count == 0
    assert any(e.get("data") == "resume-2" for e in events)
