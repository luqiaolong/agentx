"""运行时权限申请端到端集成测试（TASKS.md T6）。

覆盖 ``run_agent_with_approval`` 的多轮 request_permission 流程，比
``tests/python/unit/test_approval_runner_request_permission.py`` 更贴近真实场景：

1. ``test_runtime_permission_full_flow_approved``：
   初始流 execute 失败 → 中断 → LLM 申请 request_permission → 发 approval_request
   事件 → mock 审批通过 → sandbox.authorize_temp 被调用 → resume approve 让
   request_permission 工具返回"路径已授权" → agent 继续 → execute 重试成功。
   断言：approval_request 事件含 approval_id + run_id、register_approval_request
   被调用、sandbox.authorize_temp 入参正确、最终 tool_result 为成功、stream_fn
   被调用两次（初始 + resume）。

2. ``test_runtime_permission_flow_denied``：
   初始流 execute 失败 → LLM 申请 request_permission → 发 approval_request 事件
   → mock 审批拒绝 → 注入 error ToolMessage + 发 error 事件 + resume reject。
   断言：approval_request 事件含 approval_id + run_id、error 事件被发出、
   sandbox.authorize_temp 未被调用、stream_fn 只调用一次（无 resume approve）。

mock 策略（仍为纯 mock，不调真实 LLM）：
- ``agent.aget_state`` 返回 None（_state_msg_count 跳过重复检测，避免误判）
- ``stream_fn`` 多轮：按调用序号 yield 不同事件序列
- ``is_interrupted_fn`` 多轮：首次 True 触发拦截，第二次 False 退出循环
- ``_await_approval`` / ``register_approval_request`` / ``is_paused`` /
  ``is_aborted`` / ``_resolve_max_wait`` 通过 monkeypatch 替换
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.types import Command

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
        tool_call_id="tc-rp-1",
        created_at=0.0,
        expires_at=999999.0,
        consumed_at=None,
    )


def _make_agent() -> MagicMock:
    """构造 mock agent，aget_state 返回 None 使 _state_msg_count 跳过检测。"""
    agent = MagicMock()
    agent.aget_state = AsyncMock(return_value=None)
    # astream 在拒绝分支被调用（Command(resume=reject)），默认空 async generator
    agent.astream = _make_empty_astream()
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


def _make_empty_astream():
    """创建空 async generator，用于 mock agent.astream。"""

    async def astream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        return
        yield  # 标记为 async generator（不可达）

    return astream


def _make_multi_turn_stream_approved():
    """创建多轮 stream_fn：模拟 execute 失败 → request_permission → execute 成功。

    - call 1（初始流）：yield token + tool_result(execute 失败，含 SANDBOX_ESCALATION)
    - call 2（resume approve）：yield tool_result(request_permission 成功) +
      tool_result(execute 重试成功)

    返回 (stream_fn, call_inputs) 供断言。
    """
    call_inputs: list[Any] = []
    call_count = 0

    async def stream_fn(
        agent: Any, inputs: Any, config: dict, source: str
    ) -> AsyncIterator[dict[str, str]]:
        nonlocal call_count
        call_count += 1
        call_inputs.append(inputs)
        if call_count == 1:
            # 初始流：LLM 尝试 execute 但因权限不足失败
            yield {"event": "token", "data": "正在分析"}
            yield {
                "event": "tool_result",
                "data": json.dumps(
                    {
                        "name": "execute",
                        "result": "ls: /root: Permission denied [SANDBOX_ESCALATION]",
                        "success": False,
                    },
                    ensure_ascii=False,
                ),
            }
        elif call_count == 2:
            # resume approve 后：request_permission 工具返回成功 + execute 重试成功
            yield {
                "event": "tool_result",
                "data": json.dumps(
                    {
                        "name": "request_permission",
                        "result": "路径已授权: /root (writable=False)",
                        "success": True,
                    },
                    ensure_ascii=False,
                ),
            }
            yield {"event": "token", "data": "已授权，重试中"}
            yield {
                "event": "tool_result",
                "data": json.dumps(
                    {
                        "name": "execute",
                        "result": "config.txt\nnotes.md",
                        "success": True,
                    },
                    ensure_ascii=False,
                ),
            }

    return stream_fn, call_inputs


def _make_single_turn_stream():
    """创建单轮 stream_fn：仅初始流（拒绝场景下无 resume approve）。

    返回 (stream_fn, call_inputs)。
    """
    call_inputs: list[Any] = []

    async def stream_fn(
        agent: Any, inputs: Any, config: dict, source: str
    ) -> AsyncIterator[dict[str, str]]:
        call_inputs.append(inputs)
        # 初始流：LLM 尝试 execute 但因权限不足失败
        yield {"event": "token", "data": "正在分析"}
        yield {
            "event": "tool_result",
            "data": json.dumps(
                {
                    "name": "execute",
                    "result": "ls: /root: Permission denied [SANDBOX_ESCALATION]",
                    "success": False,
                },
                ensure_ascii=False,
            ),
        }

    return stream_fn, call_inputs


def _make_two_turn_interrupt():
    """首次 True（触发 request_permission 拦截），第二次 False（流程结束）。

    返回 (is_int_fn, call_count_holder)。
    """
    state = {"count": 0}

    async def is_int_fn(a: Any, c: dict) -> bool:
        state["count"] += 1
        return state["count"] == 1

    return is_int_fn, state


def _make_single_turn_interrupt():
    """仅首次 True（拒绝场景下 return 后不再进入循环）。

    返回 (is_int_fn, state)。
    """
    state = {"count": 0}

    async def is_int_fn(a: Any, c: dict) -> bool:
        state["count"] += 1
        return state["count"] == 1

    return is_int_fn, state


def _common_kwargs(
    agent: Any,
    sandbox: Any,
    stream_fn: Any,
    is_interrupted_fn: Any,
    get_pending_calls_fn: Any,
    inject_tool_error_for_call_fn: Any | None = None,
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
        "stream_fn": stream_fn,
        "is_interrupted_fn": is_interrupted_fn,
        "get_pending_calls_fn": get_pending_calls_fn,
        "inject_tool_error_for_call_fn": inject_tool_error_for_call_fn,
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


def _find_events(events: list[dict], name: str) -> list[dict]:
    """从事件列表中筛选指定名称的事件。"""
    return [e for e in events if e.get("event") == name]


def _request_permission_call(
    path: str = "/root",
    writable: bool = False,
    reason: str = "需要读取 /root 目录",
    call_id: str = "tc-rp-1",
) -> dict:
    """构造 request_permission tool_call。"""
    return {
        "name": "request_permission",
        "args": {"path": path, "writable": writable, "reason": reason},
        "id": call_id,
    }


# ============================================================
# 测试用例
# ============================================================


@pytest.mark.asyncio
async def test_runtime_permission_full_flow_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端权限申请流程（审批通过）。

    流程：execute 失败 → request_permission → 审批通过 → authorize_temp →
    resume approve → request_permission 返回成功 → execute 重试成功。

    断言：
    - approval_request 事件被发出且含 approval_id + run_id
    - register_approval_request 被调用
    - sandbox.authorize_temp 被调用，入参 (thread_id, path, writable) 正确
    - 最终 tool_result 事件为成功（execute 重试成功）
    - stream_fn 被调用两次（初始流 + resume approve）
    - 第二次 stream_fn 调用传入 Command(resume=...) 对象
    - 无 error 事件
    """
    fake_req = _make_approval_req(approval_id="apr-e2e-1", run_id="run-e2e-1")
    _apply_common_mocks(monkeypatch, approval_req=fake_req)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.APPROVE)),
    )

    agent = _make_agent()
    sandbox = _make_sandbox()
    stream_fn, stream_inputs = _make_multi_turn_stream_approved()
    is_int_fn, _ = _make_two_turn_interrupt()
    pending_calls = [_request_permission_call(path="/root", writable=False)]

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

    # 1. approval_request 事件被发出，含 approval_id + run_id
    approval_events = _find_events(events, "approval_request")
    assert len(approval_events) == 1, "应发送一个 approval_request 事件"
    approval_data = _parse_event_data(approval_events[0])
    assert approval_data["approval_id"] == "apr-e2e-1"
    assert approval_data["run_id"] == "run-e2e-1"
    assert approval_data["kind"] == "directory_extension"
    assert approval_data["requestedPath"] == "/root"
    assert approval_data["writable"] is False
    assert approval_data["tool_call_id"] == "tc-rp-1"

    # 2. register_approval_request 被调用
    exec_module.register_approval_request.assert_awaited_once()

    # 3. sandbox.authorize_temp 被调用，入参正确
    sandbox.authorize_temp.assert_awaited_once_with("t1", "/root", writable=False)

    # 4. stream_fn 被调用两次（初始流 + resume approve）
    assert len(stream_inputs) == 2, "stream_fn 应被调用两次（初始流 + resume）"
    # 第二次调用传入 Command(resume=...) 对象
    assert isinstance(stream_inputs[1], Command), "resume 调用应传入 Command 对象"

    # 5. 最终 tool_result 事件为成功（execute 重试成功）
    tool_results = _find_events(events, "tool_result")
    assert len(tool_results) >= 2, "应至少有两个 tool_result 事件（execute 失败 + 重试成功）"
    last_tool_result = _parse_event_data(tool_results[-1])
    assert last_tool_result["name"] == "execute"
    assert last_tool_result["success"] is True, "最终 execute 重试应成功"

    # 6. request_permission 工具返回"已授权"出现在事件流中
    rp_results = [
        tr
        for tr in tool_results
        if _parse_event_data(tr).get("name") == "request_permission"
    ]
    assert len(rp_results) == 1, "request_permission 工具应返回一次成功结果"
    rp_data = _parse_event_data(rp_results[0])
    assert rp_data["success"] is True
    assert "已授权" in rp_data["result"]

    # 7. 无 error 事件（审批通过不应产生 error）
    error_events = _find_events(events, "error")
    assert len(error_events) == 0, "审批通过不应产生 error 事件"

    # 8. clear_temp 被调用（审批通过后清理临时授权）
    sandbox.clear_temp.assert_awaited()


@pytest.mark.asyncio
async def test_runtime_permission_flow_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端权限申请流程（审批拒绝）。

    流程：execute 失败 → request_permission → 审批拒绝 → 注入 error ToolMessage
    → 发 error 事件 → Command(resume=reject) 消费 interrupt。

    断言：
    - approval_request 事件被发出且含 approval_id + run_id
    - register_approval_request 被调用
    - error 事件被发出
    - sandbox.authorize_temp 未被调用
    - stream_fn 只调用一次（无 resume approve）
    - inject_tool_error_for_call_fn 被调用（注入拒绝消息）
    - agent.astream 被调用（Command(resume=reject)）
    """
    fake_req = _make_approval_req(approval_id="apr-deny-1", run_id="run-deny-1")
    _apply_common_mocks(monkeypatch, approval_req=fake_req)
    monkeypatch.setattr(
        exec_module,
        "_await_approval",
        AsyncMock(return_value=ApprovalResult(decision=ApprovalDecision.DENY)),
    )

    agent = _make_agent()
    # 追踪 agent.astream 调用（拒绝分支会调用 resume=reject）
    astream_calls: list[tuple] = []

    async def tracking_astream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        astream_calls.append((args, kwargs))
        return
        yield  # 标记为 async generator（不可达）

    agent.astream = tracking_astream

    sandbox = _make_sandbox()
    stream_fn, stream_inputs = _make_single_turn_stream()
    is_int_fn, _ = _make_single_turn_interrupt()
    inject_call = AsyncMock()
    pending_calls = [_request_permission_call(path="/root", writable=True, reason="写入测试")]

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

    # 1. approval_request 事件被发出，含 approval_id + run_id
    approval_events = _find_events(events, "approval_request")
    assert len(approval_events) == 1, "应发送一个 approval_request 事件"
    approval_data = _parse_event_data(approval_events[0])
    assert approval_data["approval_id"] == "apr-deny-1"
    assert approval_data["run_id"] == "run-deny-1"
    assert approval_data["kind"] == "directory_extension"
    assert approval_data["requestedPath"] == "/root"

    # 2. register_approval_request 被调用
    exec_module.register_approval_request.assert_awaited_once()

    # 3. error 事件被发出
    error_events = _find_events(events, "error")
    assert len(error_events) == 1, "应发送一个 error 事件"
    error_data = _parse_event_data(error_events[0])
    assert "拒绝" in error_data["message"]

    # 4. sandbox.authorize_temp 未被调用（审批被拒绝）
    sandbox.authorize_temp.assert_not_awaited()

    # 5. stream_fn 只调用一次（初始流，无 resume approve）
    assert len(stream_inputs) == 1, "stream_fn 应只被调用一次（无 resume approve）"

    # 6. inject_tool_error_for_call_fn 被调用（注入拒绝消息）
    inject_call.assert_awaited_once()
    call_args = inject_call.call_args
    # 第二个位置参数为 tool_call dict
    assert call_args.args[2] == pending_calls[0]
    # 错误消息含拒绝信息
    assert "拒绝" in call_args.args[3]

    # 7. agent.astream 被调用（Command(resume=reject)）
    assert len(astream_calls) == 1, "agent.astream 应被调用一次（resume=reject）"
    resume_cmd = astream_calls[0][0][0]
    assert isinstance(resume_cmd, Command), "应传入 Command(resume=reject)"
