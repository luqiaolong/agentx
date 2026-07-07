"""DeepAgent 多危险工具全量审批测试。

覆盖：
1. 多个危险工具调用时，应一次性 yield 所有 approval_request 事件
2. 用户批准后恢复执行
3. 用户拒绝后为每个 tool_call 注入 ToolMessage 错误并 yield error
4. 审批超时时同样注入错误并安全失败
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeApprovalDecision:
    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.decision = "approve" if approved else "deny"


def _fake_tool(name: str) -> MagicMock:
    """构造一个仅含 name 属性的假 LangChain BaseTool。"""
    t = MagicMock()
    t.name = name
    return t


@pytest.fixture
def _patch_deep_dependencies(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """打桩 run_deep_path 依赖，隔离 LLM / 沙箱 / 工具加载。"""
    import app.deep.agent as agent_module

    # 工具加载：包含测试中会用到的所有危险工具，确保 runtime_dangerous 命中
    monkeypatch.setattr(
        agent_module,
        "_make_deep_tools",
        lambda *args, **kwargs: [
            _fake_tool("write_file"),
            _fake_tool("edit_file"),
            _fake_tool("shell_exec"),
            _fake_tool("cli_execute"),
        ],
    )
    monkeypatch.setattr(
        agent_module,
        "_load_mcp_tools",
        AsyncMock(return_value=([], set())),
    )

    # agent 构建
    fake_agent = MagicMock()
    fake_agent.aupdate_state = AsyncMock()
    monkeypatch.setattr(
        agent_module,
        "build_deep_agent",
        AsyncMock(return_value=fake_agent),
    )

    # 沙箱
    fake_sandbox = MagicMock()
    fake_sandbox.is_path_authorized.return_value = False
    monkeypatch.setattr(
        agent_module,
        "get_sandbox",
        lambda: fake_sandbox,
    )

    # 流式事件：第一次产出 token 后结束；中断循环依赖 _is_interrupted 控制
    monkeypatch.setattr(
        agent_module,
        "_stream_agent_events",
        lambda *args, **kwargs: _empty_stream(),
    )

    return {
        "agent": fake_agent,
        "sandbox": fake_sandbox,
    }


async def _empty_stream() -> AsyncIterator[dict[str, str]]:
    yield {"event": "token", "data": "ok"}


@pytest.mark.asyncio
async def test_multiple_dangerous_tools_yield_all_approval_requests(
    monkeypatch: pytest.MonkeyPatch,
    _patch_deep_dependencies: dict[str, Any],
) -> None:
    """两个危险工具调用时，应 yield 两个 approval_request，然后一个 decision 批准。"""
    from app.deep.agent import run_deep_path
    import app.deep.agent as agent_module

    pending_calls = [
        {"id": "tc-1", "name": "write_file", "args": {"path": "/tmp/a.txt"}},
        {"id": "tc-2", "name": "shell_exec", "args": {"command": "ls"}},
    ]

    monkeypatch.setattr(
        agent_module,
        "_is_interrupted",
        AsyncMock(side_effect=[True, False]),
    )
    monkeypatch.setattr(
        agent_module,
        "_get_pending_tool_calls",
        AsyncMock(return_value=pending_calls),
    )
    monkeypatch.setattr(
        agent_module,
        "_await_approval",
        AsyncMock(return_value=_FakeApprovalDecision(approved=True)),
    )
    monkeypatch.setattr(
        agent_module,
        "_handle_directory_extension",
        AsyncMock(return_value=MagicMock(events=[], denied=False, timed_out=False)),
    )

    events = [
        e
        async for e in run_deep_path(
            {"thread_id": "t-multi"},
            "write and run",
        )
    ]

    approval_events = [e for e in events if e.get("event") == "approval_request"]
    assert len(approval_events) == 2
    data0 = json.loads(approval_events[0].get("data", "{}"))
    data1 = json.loads(approval_events[1].get("data", "{}"))
    assert data0.get("tool_name") == "write_file"
    assert data1.get("tool_name") == "shell_exec"

    # 没有 error 事件
    assert not any(e.get("event") == "error" for e in events)


@pytest.mark.asyncio
async def test_deny_multiple_dangerous_tools_injects_errors(
    monkeypatch: pytest.MonkeyPatch,
    _patch_deep_dependencies: dict[str, Any],
) -> None:
    """用户拒绝时，为每个危险 tool_call 注入 ToolMessage 错误并 yield error。"""
    from app.deep.agent import run_deep_path
    import app.deep.agent as agent_module

    pending_calls = [
        {"id": "tc-1", "name": "write_file", "args": {"path": "/tmp/a.txt"}},
        {"id": "tc-2", "name": "edit_file", "args": {"path": "/tmp/b.txt"}},
    ]

    monkeypatch.setattr(
        agent_module,
        "_is_interrupted",
        AsyncMock(side_effect=[True, False]),
    )
    monkeypatch.setattr(
        agent_module,
        "_get_pending_tool_calls",
        AsyncMock(return_value=pending_calls),
    )
    monkeypatch.setattr(
        agent_module,
        "_await_approval",
        AsyncMock(return_value=_FakeApprovalDecision(approved=False)),
    )

    events = [
        e
        async for e in run_deep_path(
            {"thread_id": "t-deny"},
            "write and edit",
        )
    ]

    approval_events = [e for e in events if e.get("event") == "approval_request"]
    assert len(approval_events) == 2

    assert any(
        e.get("event") == "error" and "拒绝" in str(e.get("data", ""))
        for e in events
    )

    fake_agent = _patch_deep_dependencies["agent"]
    # 过滤出注入 ToolMessage 的调用（aupdate_state 也可能被 _inject_tool_error_messages 调用）
    injected_calls = [
        call
        for call in fake_agent.aupdate_state.call_args_list
        if call.args and len(call.args) >= 2 and "messages" in call.args[1]
    ]
    assert len(injected_calls) == 2
    for i, tc in enumerate(pending_calls):
        injected = injected_calls[i].args[1]["messages"][0]
        assert injected.tool_call_id == tc["id"]
        assert "拒绝" in injected.content


@pytest.mark.asyncio
async def test_timeout_dangerous_tools_injects_errors(
    monkeypatch: pytest.MonkeyPatch,
    _patch_deep_dependencies: dict[str, Any],
) -> None:
    """审批超时视为未批准，同样注入错误。"""
    from app.deep.agent import run_deep_path
    import app.deep.agent as agent_module

    pending_calls = [
        {"id": "tc-1", "name": "write_file", "args": {"path": "/tmp/a.txt"}},
    ]

    monkeypatch.setattr(
        agent_module,
        "_is_interrupted",
        AsyncMock(side_effect=[True, False]),
    )
    monkeypatch.setattr(
        agent_module,
        "_get_pending_tool_calls",
        AsyncMock(return_value=pending_calls),
    )
    monkeypatch.setattr(
        agent_module,
        "_await_approval",
        AsyncMock(return_value=None),
    )

    events = [
        e
        async for e in run_deep_path(
            {"thread_id": "t-timeout"},
            "write file",
        )
    ]

    assert any(e.get("event") == "approval_request" for e in events)
    assert any(e.get("event") == "error" for e in events)

    fake_agent = _patch_deep_dependencies["agent"]
    injected_calls = [
        call
        for call in fake_agent.aupdate_state.call_args_list
        if call.args and len(call.args) >= 2 and "messages" in call.args[1]
    ]
    assert len(injected_calls) == 1
