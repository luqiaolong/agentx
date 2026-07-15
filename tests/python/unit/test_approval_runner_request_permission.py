"""approval_runner 中 request_permission 拦截分支单元测试。

覆盖：
1. pending_calls 含 request_permission（有 path）→ approval_request 事件 kind=directory_extension
2. pending_calls 含 request_permission（无 path）→ approval_request 事件 kind=sandbox_escalation
3. approval_request 事件携带 approval_id + run_id（register_approval_request 被调用）
4. 审批通过 → sandbox.authorize_temp 被调用 + _stream(resume=approve) 被调用
5. 审批拒绝 → 注入 error ToolMessage + Command(resume=reject) + 发 error 事件
6. 多个 request_permission 批量处理
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.deepagent.approval_runner import run_agent_with_approval
from app.security.approval import ApprovalDecision, ApprovalResult
from app.security.approval.request import ApprovalRequest

# 被测模块，用于 monkeypatch 模块级导入的函数
import app.deepagent.approval_runner as exec_module


# ============================================================
# 辅助函数与 fixtures
# ============================================================


def _make_approval_req(approval_id: str = "apr-1", run_id: str = "run-1") -> ApprovalRequest:
    """构造假的 ApprovalRequest，供 register_approval_request mock 返回。"""
    return ApprovalRequest(
        approval_id=approval_id,
        thread_id="t1",
        run_id=run_id,
        request_kind="directory_extension",
        tool_call_id="tc-1",
        created_at=0.0,
        expires_at=999999.0,
        consumed_at=None,
    )


def _make_agent() -> MagicMock:
    """构造 mock agent，aget_state 返回 None 使 _state_msg_count 跳过检测。"""
    agent = MagicMock()
    agent.aget_state = AsyncMock(return_value=None)
    return agent


def _make_sandbox() -> MagicMock:
    """构造 mock sandbox。"""
    sandbox = MagicMock()
    sandbox.is_path_authorized = AsyncMock(return_value=False)
    sandbox.authorize_temp = AsyncMock()
    sandbox.authorize = AsyncMock()
    sandbox.clear_temp = AsyncMock()
    sandbox.set_full_trust = AsyncMock()
    return sandbox


async def _initial_stream(
    agent: Any, inputs: Any, config: dict, source: str
) -> AsyncIterator[dict[str, str]]:
    """初始流：yield 一个 token 后结束。"""
    yield {"event": "token", "data": "ok"}


def _make_tracked_stream():
    """创建可追踪调用次数和 inputs 的 stream_fn。

    首次调用 yield 一个 token（模拟初始流），后续调用 yield 无（模拟 resume 流）。
    """
    call_inputs: list[Any] = []

    async def stream_fn(
        agent: Any, inputs: Any, config: dict, source: str
    ) -> AsyncIterator[dict[str, str]]:
        call_inputs.append(inputs)
        if len(call_inputs) == 1:
            yield {"event": "token", "data": "ok"}

    return stream_fn, call_inputs


def _make_empty_astream():
    """创建可追踪的空 async generator，用于 mock agent.astream。"""
    call_args: list[tuple] = []

    async def astream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        call_args.append((args, kwargs))
        return
        yield  # 标记为 async generator（不可达）

    return astream, call_args


def _common_kwargs(
    agent: Any,
    sandbox: Any,
    stream_fn: Any = None,
    is_interrupted_fn: Any = None,
    get_pending_calls_fn: Any = None,
    inject_tool_error_for_call_fn: Any = None,
    inject_tool_error_messages_fn: Any = None,
) -> dict:
    """run_agent_with_approval 公共参数。"""
    return {
        "thread_id": "t1",
        "workspace_path": None,
        "permission_mode": "standard",
        "runtime_dangerous": set(),
        "source": "deep",
        "inputs": {"messages": []},
        "sandbox": sandbox,
        "stream_fn": stream_fn or _initial_stream,
        "is_interrupted_fn": is_interrupted_fn,
        "get_pending_calls_fn": get_pending_calls_fn,
        "inject_tool_error_for_call_fn": inject_tool_error_for_call_fn,
        "inject_tool_error_messages_fn": inject_tool_error_messages_fn,
    }


def _apply_common_mocks(monkeypatch: pytest.MonkeyPatch, approval_req: ApprovalRequest | None = None):
    """统一 monkeypatch 模块级函数，避免真实审批状态副作用。"""
    req = approval_req or _make_approval_req()
    monkeypatch.setattr(
        exec_module, "register_approval_request", AsyncMock(return_value=req)
    )
    monkeypatch.setattr(exec_module, "is_paused", AsyncMock(return_value=False))
    monkeypatch.setattr(exec_module, "is_aborted", AsyncMock(return_value=False))
    monkeypatch.setattr(exec_module, "_resolve_max_wait", MagicMock(return_value=300.0))
    return req


def _parse_event_data(event: dict) -> dict:
    """解析 SSE 事件的 data 字段为 dict。"""
    return json.loads(event["data"])


def _find_approval_events(events: list[dict]) -> list[dict]:
    """从事件列表中筛选 approval_request 事件。"""
    return [e for e in events if e.get("event") == "approval_request"]


def _find_error_events(events: list[dict]) -> list[dict]:
    """从事件列表中筛选 error 事件。"""
    return [e for e in events if e.get("event") == "error"]


# ============================================================
# 测试用例
# ============================================================


@pytest.mark.asyncio
async def test_request_permission_with_path_sends_directory_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有 path 的 request_permission → approval_request 事件 kind=directory_extension。"""
    _apply_common_mocks(monkeypatch)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.APPROVE)),
    )

    agent = _make_agent()
    sandbox = _make_sandbox()
    stream_fn, _ = _make_tracked_stream()

    # is_interrupted: 首次 True（触发拦截），第二次 False（退出循环）
    call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    pending_calls = [
        {
            "name": "request_permission",
            "args": {"path": "/tmp/test", "writable": True, "reason": "写入测试"},
            "id": "tc-1",
        }
    ]

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
            ),
        )
    ]

    approval_events = _find_approval_events(events)
    assert len(approval_events) == 1, "应发送一个 approval_request 事件"
    data = _parse_event_data(approval_events[0])
    assert data["kind"] == "directory_extension"
    assert data["requestedPath"] == "/tmp/test"
    assert data["writable"] is True


@pytest.mark.asyncio
async def test_request_permission_without_path_sends_sandbox_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 path 的 request_permission → approval_request 事件 kind=sandbox_escalation。"""
    _apply_common_mocks(monkeypatch)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.APPROVE)),
    )

    agent = _make_agent()
    sandbox = _make_sandbox()
    stream_fn, _ = _make_tracked_stream()

    call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    # 无 path → sandbox_escalation
    pending_calls = [
        {
            "name": "request_permission",
            "args": {"writable": False, "reason": "需要系统级权限"},
            "id": "tc-2",
        }
    ]

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
            ),
        )
    ]

    approval_events = _find_approval_events(events)
    assert len(approval_events) == 1, "应发送一个 approval_request 事件"
    data = _parse_event_data(approval_events[0])
    assert data["kind"] == "sandbox_escalation"
    assert data.get("reason") == "需要系统级权限"


@pytest.mark.asyncio
async def test_approval_event_contains_approval_id_and_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """approval_request 事件携带 approval_id + run_id（register_approval_request 被调用）。"""
    fake_req = _make_approval_req(approval_id="apr-xyz", run_id="run-abc")
    _apply_common_mocks(monkeypatch, approval_req=fake_req)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.APPROVE)),
    )

    agent = _make_agent()
    sandbox = _make_sandbox()
    stream_fn, _ = _make_tracked_stream()

    call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    pending_calls = [
        {
            "name": "request_permission",
            "args": {"path": "/tmp/data", "writable": True, "reason": "test"},
            "id": "tc-1",
        }
    ]

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
            ),
        )
    ]

    # register_approval_request 被调用
    exec_module.register_approval_request.assert_awaited_once()

    # 事件携带 approval_id 和 run_id
    approval_events = _find_approval_events(events)
    assert len(approval_events) == 1
    data = _parse_event_data(approval_events[0])
    assert data["approval_id"] == "apr-xyz"
    assert data["run_id"] == "run-abc"


@pytest.mark.asyncio
async def test_approval_approved_calls_authorize_temp_and_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """审批通过 → sandbox.authorize_temp 被调用 + _stream(resume=approve) 被调用。"""
    _apply_common_mocks(monkeypatch)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.APPROVE)),
    )

    agent = _make_agent()
    sandbox = _make_sandbox()
    stream_fn, stream_inputs = _make_tracked_stream()

    call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    pending_calls = [
        {
            "name": "request_permission",
            "args": {"path": "/tmp/project", "writable": True, "reason": "npm install"},
            "id": "tc-1",
        }
    ]

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
            ),
        )
    ]

    # sandbox.authorize_temp 被调用，传入正确参数
    sandbox.authorize_temp.assert_awaited_once_with(
        "t1", "/tmp/project", writable=True
    )

    # _stream(resume=approve) 被调用 → stream_fn 被调用两次（初始 + resume）
    assert len(stream_inputs) == 2, "stream_fn 应被调用两次（初始流 + resume 流）"
    # 第二次调用的 inputs 应为 Command(resume=...) 对象
    from langgraph.types import Command

    assert isinstance(stream_inputs[1], Command), "resume 调用应传入 Command 对象"

    # clear_temp 被调用（审批通过后清理临时授权）
    sandbox.clear_temp.assert_awaited()


@pytest.mark.asyncio
async def test_approval_denied_injects_error_and_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """审批拒绝 → 注入 error ToolMessage + Command(resume=reject) + 发 error 事件。"""
    _apply_common_mocks(monkeypatch)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.DENY)),
    )

    agent = _make_agent()
    # agent.astream 在拒绝分支被调用（Command(resume=reject)）
    astream_fn, astream_calls = _make_empty_astream()
    agent.astream = astream_fn

    sandbox = _make_sandbox()
    stream_fn, stream_inputs = _make_tracked_stream()
    inject_call = AsyncMock()

    call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    pending_calls = [
        {
            "name": "request_permission",
            "args": {"path": "/tmp/secret", "writable": True, "reason": "test"},
            "id": "tc-1",
        }
    ]

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=inject_call,
            ),
        )
    ]

    # 注入 error ToolMessage（_inject_call 被调用）
    inject_call.assert_awaited_once()
    call_args = inject_call.call_args
    # 第二个位置参数为 tool_call dict
    assert call_args.args[2] == pending_calls[0]
    # 错误消息含拒绝信息
    assert "拒绝" in call_args.args[3]

    # 发 error 事件
    error_events = _find_error_events(events)
    assert len(error_events) == 1, "应发送一个 error 事件"
    error_data = _parse_event_data(error_events[0])
    assert "拒绝" in error_data["message"]

    # agent.astream 被调用（Command(resume=reject)）
    assert len(astream_calls) == 1, "agent.astream 应被调用一次（resume=reject）"
    # 传入的第一个参数应为 Command 对象
    from langgraph.types import Command

    resume_cmd = astream_calls[0][0][0]
    assert isinstance(resume_cmd, Command), "应传入 Command(resume=reject)"

    # sandbox.authorize_temp 不应被调用（审批被拒绝）
    sandbox.authorize_temp.assert_not_awaited()

    # stream_fn 只被调用一次（初始流，无 resume approve）
    assert len(stream_inputs) == 1


@pytest.mark.asyncio
async def test_multiple_request_permission_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多个 request_permission 批量处理：批量发事件 + 批量授权 + 一次 resume。"""
    _apply_common_mocks(monkeypatch)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.APPROVE)),
    )

    agent = _make_agent()
    sandbox = _make_sandbox()
    stream_fn, stream_inputs = _make_tracked_stream()

    call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    pending_calls = [
        {
            "name": "request_permission",
            "args": {"path": "/tmp/dir_a", "writable": True, "reason": "写 A"},
            "id": "tc-1",
        },
        {
            "name": "request_permission",
            "args": {"path": "/tmp/dir_b", "writable": False, "reason": "读 B"},
            "id": "tc-2",
        },
    ]

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
            ),
        )
    ]

    # 2 个 approval_request 事件
    approval_events = _find_approval_events(events)
    assert len(approval_events) == 2, "应发送两个 approval_request 事件"

    # 验证两个事件的 kind 和路径
    data_list = [_parse_event_data(e) for e in approval_events]
    paths = {d["requestedPath"] for d in data_list}
    assert paths == {"/tmp/dir_a", "/tmp/dir_b"}

    # register_approval_request 被调用两次
    exec_module.register_approval_request.assert_awaited()
    assert exec_module.register_approval_request.await_count == 2

    # sandbox.authorize_temp 被调用两次（两个路径都有 path）
    assert sandbox.authorize_temp.await_count == 2

    # _stream(resume=approve) 只被调用一次（批量 resume）
    assert len(stream_inputs) == 2, "stream_fn 应被调用两次（初始 + 一次 resume）"


@pytest.mark.asyncio
async def test_approval_timeout_injects_error_and_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_await_approval 超时返回 None → 走与拒绝相同的 reject 路径。

    Major 5: 验证 decision=None 时：
    - 注入 error ToolMessage（_inject_call 被调用）
    - 发 error 事件
    - agent.astream 被调用（Command(resume=reject)）
    - sandbox.authorize_temp 不被调用
    - stream_fn 只被调用一次（初始流，无 resume approve）
    """
    _apply_common_mocks(monkeypatch)
    # _await_approval 返回 None（超时）
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=None),
    )

    agent = _make_agent()
    # agent.astream 在拒绝分支被调用（Command(resume=reject)）
    astream_fn, astream_calls = _make_empty_astream()
    agent.astream = astream_fn

    sandbox = _make_sandbox()
    stream_fn, stream_inputs = _make_tracked_stream()
    inject_call = AsyncMock()

    call_count = 0

    async def is_int_fn(a: Any, c: dict) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    pending_calls = [
        {
            "name": "request_permission",
            "args": {"path": "/tmp/timeout", "writable": True, "reason": "test"},
            "id": "tc-1",
        }
    ]

    events = [
        evt
        async for evt in run_agent_with_approval(
            agent,
            {"configurable": {"thread_id": "t1"}},
            **_common_kwargs(
                agent,
                sandbox,
                stream_fn=stream_fn,
                is_interrupted_fn=is_int_fn,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=inject_call,
            ),
        )
    ]

    # 注入 error ToolMessage（_inject_call 被调用）
    inject_call.assert_awaited_once()
    call_args = inject_call.call_args
    # 第二个位置参数为 tool_call dict
    assert call_args.args[2] == pending_calls[0]
    # 错误消息含拒绝信息
    assert "拒绝" in call_args.args[3]

    # 发 error 事件
    error_events = _find_error_events(events)
    assert len(error_events) == 1, "应发送一个 error 事件"
    error_data = _parse_event_data(error_events[0])
    assert "拒绝" in error_data["message"]

    # agent.astream 被调用（Command(resume=reject)）
    assert len(astream_calls) == 1, "agent.astream 应被调用一次（resume=reject）"
    # 传入的第一个参数应为 Command 对象
    from langgraph.types import Command

    resume_cmd = astream_calls[0][0][0]
    assert isinstance(resume_cmd, Command), "应传入 Command(resume=reject)"

    # sandbox.authorize_temp 不应被调用（超时走 reject 路径）
    sandbox.authorize_temp.assert_not_awaited()

    # stream_fn 只被调用一次（初始流，无 resume approve）
    assert len(stream_inputs) == 1
