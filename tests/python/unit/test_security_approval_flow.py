"""公共审批循环（security.approval_flow.run_approval_loop）单元测试。

覆盖 6 个 bug 修复 + 核心审批流行为：
1. dangerous_tool 审批（approve/deny/timeout）
2. directory_extension 审批（once/session/deny）
3. full_trust 跳过所有审批（bug #5）
4. parent_thread_id 继承（bug #2）
5. 路径基准一致 base=workspace_path（bug #1）
6. cli_execute 始终需审批（bug #3）
7. approval_max_wait=0 不无限阻塞（bug #6）
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest


# ============================================================
# 辅助：假 ApprovalDecision（兼容旧 dataclass + 新 Enum 语义）
# ============================================================


class _FakeApprovalDecision:
    """模拟 ApprovalDecision（兼容 app.approval.decision.ApprovalDecision）。"""

    def __init__(self, approved: bool, decision: str = "") -> None:
        self.approved = approved
        self.decision = decision or ("approve" if approved else "deny")


class _FakeExtensionResult:
    """模拟 approval_flow._ExtensionResult。"""

    def __init__(self, events: list[dict] | None = None, denied: bool = False, timed_out: bool = False) -> None:
        self.events = events or []
        self.denied = denied
        self.timed_out = timed_out


def _fake_tool_call(tc_id: str, name: str, args: dict | None = None) -> dict:
    """构造工具调用 dict。"""
    return {"id": tc_id, "name": name, "args": args or {}}


async def _empty_stream(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
    """空流：仅 yield 一个 token 后结束。"""
    yield {"event": "token", "data": "ok"}


async def _no_stream(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, str]]:
    """完全空流：不 yield 任何事件。"""
    return
    yield  # noqa: unreachable — make this an async generator


# ============================================================
# 1. _resolve_max_wait: bug #6 — approval_max_wait=0 → 3600
# ============================================================


class TestResolveMaxWait:
    """_resolve_max_wait() 修复 bug #6：0 不再无限阻塞。"""

    def test_zero_returns_absolute_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """approval_max_wait=0 → _ABSOLUTE_MAX_WAIT (3600)。"""
        from app.security.approval_flow import _ABSOLUTE_MAX_WAIT, _resolve_max_wait

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 0
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        assert _resolve_max_wait() == _ABSOLUTE_MAX_WAIT

    def test_positive_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """approval_max_wait=300 → 300。"""
        from app.security.approval_flow import _resolve_max_wait

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        assert _resolve_max_wait() == 300.0

    def test_exceeds_absolute_max_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """approval_max_wait=99999 → 截断到 3600。"""
        from app.security.approval_flow import _ABSOLUTE_MAX_WAIT, _resolve_max_wait

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 99999
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        assert _resolve_max_wait() == _ABSOLUTE_MAX_WAIT


# ============================================================
# 2. dangerous_tool 审批
# ============================================================


class TestDangerousToolApproval:
    """dangerous_tool 审批流：approve / deny / timeout。"""

    @pytest.fixture
    def _common_mocks(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        """打桩 run_approval_loop 依赖的模块级函数。"""
        # is_paused / is_aborted / get_pause_event
        monkeypatch.setattr("app.security.approval_flow.is_paused", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.is_aborted", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.get_pause_event", AsyncMock())

        # get_settings
        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        # sandbox
        sandbox = MagicMock()
        sandbox.is_path_authorized = AsyncMock(return_value=False)
        sandbox.clear_temp = AsyncMock()
        sandbox.authorize = AsyncMock()
        sandbox.authorize_temp = AsyncMock()
        sandbox.set_full_trust = AsyncMock()

        return {"sandbox": sandbox}

    @pytest.mark.asyncio
    async def test_dangerous_tool_approve(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """危险工具被批准后恢复执行。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = _common_mocks["sandbox"]
        pending_calls = [_fake_tool_call("tc-1", "write_file", {"path": "/external/file"})]

        # _await_approval 返回批准
        monkeypatch.setattr(
            "app.security.approval_flow._await_approval",
            AsyncMock(return_value=_FakeApprovalDecision(approved=True)),
        )
        # _handle_directory_extension 不应被调用（有 dangerous_calls）
        monkeypatch.setattr(
            "app.security.approval_flow._handle_directory_extension",
            AsyncMock(return_value=_FakeExtensionResult()),
        )

        # is_interrupted: True（首次中断）, False（恢复后不再中断）
        call_count = 0

        async def mock_is_interrupted(agent: Any, config: dict) -> bool:
            nonlocal call_count
            call_count += 1
            return call_count == 1

        agent = MagicMock()
        config = {"configurable": {"thread_id": "test-thread"}}

        events = [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "test-thread",
                "/workspace",
                "standard",
                frozenset({"write_file"}),
                [],
                yield_event=None,
                sandbox=sandbox,
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=mock_is_interrupted,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=AsyncMock(),
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        # 应有 approval_request 事件
        approval_events = [e for e in events if e.get("event") == "approval_request"]
        assert len(approval_events) == 1

    @pytest.mark.asyncio
    async def test_dangerous_tool_deny(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """危险工具被拒绝后注入错误并终止。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = _common_mocks["sandbox"]
        pending_calls = [_fake_tool_call("tc-1", "write_file", {"path": "/external/file"})]

        inject_call_fn = AsyncMock()
        monkeypatch.setattr(
            "app.security.approval_flow._await_approval",
            AsyncMock(return_value=_FakeApprovalDecision(approved=False, decision="deny")),
        )

        agent = MagicMock()
        config = {"configurable": {"thread_id": "test-thread"}}

        events = [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "test-thread",
                "/workspace",
                "standard",
                frozenset({"write_file"}),
                [],
                yield_event=None,
                sandbox=sandbox,
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=AsyncMock(return_value=True),
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=inject_call_fn,
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        # 应有 approval_request + error 事件
        approval_events = [e for e in events if e.get("event") == "approval_request"]
        error_events = [e for e in events if e.get("event") == "error"]
        assert len(approval_events) == 1
        assert len(error_events) == 1
        assert "拒绝" in error_events[0]["data"]

        # 应调用 inject_tool_error_for_call 注入错误
        inject_call_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_dangerous_tool_timeout(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """审批超时返回 None → 注入错误并终止。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = _common_mocks["sandbox"]
        pending_calls = [_fake_tool_call("tc-1", "write_file", {"path": "/external/file"})]

        inject_call_fn = AsyncMock()
        monkeypatch.setattr(
            "app.security.approval_flow._await_approval",
            AsyncMock(return_value=None),  # timeout
        )

        agent = MagicMock()
        config = {"configurable": {"thread_id": "test-thread"}}

        events = [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "test-thread",
                "/workspace",
                "standard",
                frozenset({"write_file"}),
                [],
                yield_event=None,
                sandbox=sandbox,
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=AsyncMock(return_value=True),
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=inject_call_fn,
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        approval_events = [e for e in events if e.get("event") == "approval_request"]
        error_events = [e for e in events if e.get("event") == "error"]
        assert len(approval_events) == 1
        assert len(error_events) == 1
        inject_call_fn.assert_called_once()


# ============================================================
# 3. directory_extension 审批
# ============================================================


class TestDirectoryExtension:
    """directory_extension 审批流：通过 _handle_directory_extension mock 测试。"""

    @pytest.fixture
    def _common_mocks(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        monkeypatch.setattr("app.security.approval_flow.is_paused", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.is_aborted", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.get_pause_event", AsyncMock())

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        sandbox = MagicMock()
        sandbox.is_path_authorized = AsyncMock(return_value=False)
        sandbox.clear_temp = AsyncMock()
        sandbox.authorize = AsyncMock()
        sandbox.authorize_temp = AsyncMock()

        return {"sandbox": sandbox}

    @pytest.mark.asyncio
    async def test_directory_extension_events_yielded(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非危险工具越界 → _handle_directory_extension 返回 events → yield。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = _common_mocks["sandbox"]
        # read_file 不是危险工具
        pending_calls = [_fake_tool_call("tc-1", "read_file", {"path": "/external/dir"})]

        ext_event = {"event": "approval_request", "data": "{}"}
        monkeypatch.setattr(
            "app.security.approval_flow._handle_directory_extension",
            AsyncMock(return_value=_FakeExtensionResult(events=[ext_event])),
        )

        agent = MagicMock()
        config = {"configurable": {"thread_id": "test-thread"}}

        events = [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "test-thread",
                "/workspace",
                "standard",
                frozenset(),  # 无危险工具
                [],
                yield_event=None,
                sandbox=sandbox,
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=AsyncMock(side_effect=[True, False]),
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=AsyncMock(),
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        # 应 yield directory_extension 事件
        approval_events = [e for e in events if e.get("event") == "approval_request"]
        assert len(approval_events) == 1

    @pytest.mark.asyncio
    async def test_directory_extension_denied(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """directory_extension 被拒绝 → yield error 并终止。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = _common_mocks["sandbox"]
        pending_calls = [_fake_tool_call("tc-1", "read_file", {"path": "/external/dir"})]

        ext_event = {"event": "approval_request", "data": "{}"}
        monkeypatch.setattr(
            "app.security.approval_flow._handle_directory_extension",
            AsyncMock(return_value=_FakeExtensionResult(events=[ext_event], denied=True)),
        )

        agent = MagicMock()
        config = {"configurable": {"thread_id": "test-thread"}}

        events = [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "test-thread",
                "/workspace",
                "standard",
                frozenset(),
                [],
                yield_event=None,
                sandbox=sandbox,
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=AsyncMock(return_value=True),
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=AsyncMock(),
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert "拒绝" in error_events[0]["data"]


# ============================================================
# 4. full_trust 跳过所有审批（bug #5）
# ============================================================


class TestFullTrustSkip:
    """full_trust 模式跳过所有审批（含 directory_extension 预检查）。"""

    @pytest.fixture
    def _common_mocks(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        monkeypatch.setattr("app.security.approval_flow.is_paused", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.is_aborted", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.get_pause_event", AsyncMock())

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        sandbox = MagicMock()
        sandbox.is_path_authorized = AsyncMock(return_value=False)
        sandbox.clear_temp = AsyncMock()

        return {"sandbox": sandbox}

    @pytest.mark.asyncio
    async def test_full_trust_skips_approval(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """full_trust 模式下危险工具不弹审批框，直接恢复执行。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = _common_mocks["sandbox"]
        pending_calls = [_fake_tool_call("tc-1", "write_file", {"path": "/external/file"})]

        # _handle_directory_extension 不应被调用
        handle_ext_fn = AsyncMock(return_value=_FakeExtensionResult())
        monkeypatch.setattr(
            "app.security.approval_flow._handle_directory_extension", handle_ext_fn
        )

        call_count = 0

        async def mock_is_interrupted(agent: Any, config: dict) -> bool:
            nonlocal call_count
            call_count += 1
            return call_count == 1

        agent = MagicMock()
        config = {"configurable": {"thread_id": "test-thread"}}

        events = [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "test-thread",
                "/workspace",
                "full_trust",  # ← full_trust 模式
                frozenset({"write_file"}),
                [],
                yield_event=None,
                sandbox=sandbox,
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=mock_is_interrupted,
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=AsyncMock(),
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        # 不应有 approval_request 事件
        approval_events = [e for e in events if e.get("event") == "approval_request"]
        assert len(approval_events) == 0

        # _handle_directory_extension 不应被调用
        handle_ext_fn.assert_not_called()

        # sandbox.clear_temp 应被调用（full_trust 路径中）
        sandbox.clear_temp.assert_called()


# ============================================================
# 5. parent_thread_id 继承（bug #2）
# ============================================================


class TestParentThreadIdInheritance:
    """parent_thread_id 传递到 sandbox.is_path_authorized / _handle_directory_extension。"""

    @pytest.fixture
    def _common_mocks(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        monkeypatch.setattr("app.security.approval_flow.is_paused", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.is_aborted", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.get_pause_event", AsyncMock())

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        return {}

    @pytest.mark.asyncio
    async def test_parent_thread_id_passed_to_is_path_authorized(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """危险工具路径检查传 parent_thread_id 到 sandbox.is_path_authorized。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = MagicMock()
        sandbox.is_path_authorized = AsyncMock(return_value=False)
        sandbox.clear_temp = AsyncMock()

        monkeypatch.setattr(
            "app.security.approval_flow._await_approval",
            AsyncMock(return_value=_FakeApprovalDecision(approved=True)),
        )
        monkeypatch.setattr(
            "app.security.approval_flow._handle_directory_extension",
            AsyncMock(return_value=_FakeExtensionResult()),
        )

        pending_calls = [_fake_tool_call("tc-1", "write_file", {"path": "/external/file"})]

        agent = MagicMock()
        config = {"configurable": {"thread_id": "child-thread"}}

        events = [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "child-thread",
                "/workspace",
                "standard",
                frozenset({"write_file"}),
                [],
                yield_event=None,
                sandbox=sandbox,
                parent_thread_id="parent-thread",  # ← bug #2 修复
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=AsyncMock(return_value=True),
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=AsyncMock(),
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        # sandbox.is_path_authorized 应收到 parent_thread_id="parent-thread"
        for call in sandbox.is_path_authorized.call_args_list:
            kwargs = call.kwargs
            assert kwargs.get("parent_thread_id") == "parent-thread", (
                f"is_path_authorized 未传 parent_thread_id，实际 kwargs: {kwargs}"
            )

    @pytest.mark.asyncio
    async def test_parent_thread_id_passed_to_handle_directory_extension(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """directory_extension 传 parent_thread_id 到 _handle_directory_extension。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = MagicMock()
        sandbox.is_path_authorized = AsyncMock(return_value=False)
        sandbox.clear_temp = AsyncMock()

        handle_ext_fn = AsyncMock(return_value=_FakeExtensionResult())
        monkeypatch.setattr(
            "app.security.approval_flow._handle_directory_extension", handle_ext_fn
        )

        # read_file 非危险工具 → 走 directory_extension 路径
        pending_calls = [_fake_tool_call("tc-1", "read_file", {"path": "/external/dir"})]

        agent = MagicMock()
        config = {"configurable": {"thread_id": "child-thread"}}

        [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "child-thread",
                "/workspace",
                "standard",
                frozenset(),
                [],
                yield_event=None,
                sandbox=sandbox,
                parent_thread_id="parent-thread",
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=AsyncMock(return_value=True),
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=AsyncMock(),
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        # _handle_directory_extension 应收到 parent_thread_id
        call_kwargs = handle_ext_fn.call_args.kwargs
        assert call_kwargs.get("parent_thread_id") == "parent-thread"


# ============================================================
# 6. 路径基准一致 base=workspace_path（bug #1）
# ============================================================


class TestPathBaseConsistency:
    """bug #1：is_path_authorized 调用传 base=workspace_path。"""

    @pytest.fixture
    def _common_mocks(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        monkeypatch.setattr("app.security.approval_flow.is_paused", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.is_aborted", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.get_pause_event", AsyncMock())

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        return {}

    @pytest.mark.asyncio
    async def test_base_workspace_path_passed(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """危险工具路径检查传 base=workspace_path。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = MagicMock()
        sandbox.is_path_authorized = AsyncMock(return_value=False)
        sandbox.clear_temp = AsyncMock()

        monkeypatch.setattr(
            "app.security.approval_flow._await_approval",
            AsyncMock(return_value=_FakeApprovalDecision(approved=True)),
        )
        monkeypatch.setattr(
            "app.security.approval_flow._handle_directory_extension",
            AsyncMock(return_value=_FakeExtensionResult()),
        )

        workspace = "/my/workspace"
        pending_calls = [_fake_tool_call("tc-1", "write_file", {"path": "relative/path"})]

        agent = MagicMock()
        config = {"configurable": {"thread_id": "test-thread"}}

        [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "test-thread",
                workspace,
                "standard",
                frozenset({"write_file"}),
                [],
                yield_event=None,
                sandbox=sandbox,
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=AsyncMock(return_value=True),
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=AsyncMock(),
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        # 所有 is_path_authorized 调用应传 base=workspace
        for call in sandbox.is_path_authorized.call_args_list:
            assert call.kwargs.get("base") == workspace, (
                f"is_path_authorized 未传 base=workspace_path，实际 kwargs: {call.kwargs}"
            )


# ============================================================
# 7. cli_execute 始终需审批（bug #3）
# ============================================================


class TestCliExecuteAlwaysApproval:
    """bug #3：cli_execute 始终需要审批，不因 workspace 已授权而放行。"""

    @pytest.fixture
    def _common_mocks(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        monkeypatch.setattr("app.security.approval_flow.is_paused", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.is_aborted", AsyncMock(return_value=False))
        monkeypatch.setattr("app.security.approval_flow.get_pause_event", AsyncMock())

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        return {}

    @pytest.mark.asyncio
    async def test_cli_execute_needs_approval_even_if_workspace_authorized(
        self, _common_mocks: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cli_execute 即使 workspace 已授权仍需审批。"""
        from app.security.approval_flow import run_approval_loop

        sandbox = MagicMock()
        # workspace 已授权（is_path_authorized 返回 True）
        sandbox.is_path_authorized = AsyncMock(return_value=True)
        sandbox.clear_temp = AsyncMock()

        monkeypatch.setattr(
            "app.security.approval_flow._await_approval",
            AsyncMock(return_value=_FakeApprovalDecision(approved=True)),
        )
        monkeypatch.setattr(
            "app.security.approval_flow._handle_directory_extension",
            AsyncMock(return_value=_FakeExtensionResult()),
        )

        # cli_execute 在 runtime_dangerous 中
        pending_calls = [_fake_tool_call("tc-1", "cli_execute", {"command": "ls"})]

        agent = MagicMock()
        config = {"configurable": {"thread_id": "test-thread"}}

        events = [
            e
            async for e in run_approval_loop(
                agent,
                config,
                "test-thread",
                "/workspace",
                "standard",
                frozenset({"cli_execute"}),  # cli_execute 是危险工具
                [],
                yield_event=None,
                sandbox=sandbox,
                source="work",
                inputs={"messages": []},
                stream_fn=_empty_stream,
                is_interrupted_fn=AsyncMock(side_effect=[True, False]),
                get_pending_calls_fn=AsyncMock(return_value=pending_calls),
                inject_tool_error_for_call_fn=AsyncMock(),
                inject_tool_error_messages_fn=AsyncMock(),
            )
        ]

        # 应有 approval_request 事件（cli_execute 始终需审批）
        approval_events = [e for e in events if e.get("event") == "approval_request"]
        assert len(approval_events) == 1

        # 验证 approval_request 的 data 包含 cli_execute
        approval_data = json.loads(approval_events[0]["data"])
        assert approval_data["tool_name"] == "cli_execute"


# ============================================================
# 8. _extract_paths_from_tool_call 迁移验证
# ============================================================


class TestExtractPathsMigration:
    """验证辅助函数从 deep/approval.py 迁移到 approval_flow.py 后行为一致。"""

    def test_extract_paths_read_file(self) -> None:
        from app.security.approval_flow import _extract_paths_from_tool_call

        tc = {"name": "read_file", "args": {"path": "/tmp/a.txt"}}
        assert _extract_paths_from_tool_call(tc) == ["/tmp/a.txt"]

    def test_extract_paths_cli_execute_with_cwd(self) -> None:
        from app.security.approval_flow import _extract_paths_from_tool_call

        tc = {"name": "cli_execute", "args": {"command": "git", "cwd": "/proj"}}
        assert _extract_paths_from_tool_call(tc) == ["/proj"]

    def test_extract_paths_cli_execute_falls_back_to_workspace(self) -> None:
        from app.security.approval_flow import _extract_paths_from_tool_call

        tc = {"name": "cli_execute", "args": {"command": "git"}}
        assert _extract_paths_from_tool_call(tc, workspace_path="/workspace") == ["/workspace"]


# ============================================================
# 9. _handle_directory_extension bug #1 #2 #4 集成验证
# ============================================================


class TestHandleDirectoryExtensionBugs:
    """验证 _handle_directory_extension 的 bug #1 #2 #4 修复。"""

    @pytest.mark.asyncio
    async def test_passes_base_and_parent_thread_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """bug #1 #2: is_path_authorized 传 base=workspace_path + parent_thread_id。"""
        from app.security.approval_flow import _handle_directory_extension

        sandbox = MagicMock()
        sandbox.is_path_authorized = AsyncMock(return_value=False)
        sandbox.authorize_temp = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)
        monkeypatch.setattr(
            "app.security.approval_flow._await_approval",
            AsyncMock(return_value=_FakeApprovalDecision(approved=True, decision="once")),
        )

        pending = [_fake_tool_call("tc-1", "read_file", {"path": "/external/dir"})]

        await _handle_directory_extension(
            pending,
            "child-thread",
            sandbox,
            workspace_path="/workspace",
            parent_thread_id="parent-thread",
        )

        # 所有 is_path_authorized 调用应传 base + parent_thread_id
        for call in sandbox.is_path_authorized.call_args_list:
            assert call.kwargs.get("base") == "/workspace"
            assert call.kwargs.get("parent_thread_id") == "parent-thread"

    @pytest.mark.asyncio
    async def test_writable_false_for_readonly_tools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """bug #4: all_under_workspace 用 writable=False（只读工具仅需读权限）。"""
        from app.security.approval_flow import _handle_directory_extension

        sandbox = MagicMock()
        # 第一次检查（收集越界路径）：返回 False
        # 第二次检查（all_under_workspace）：也返回 False（触发审批）
        sandbox.is_path_authorized = AsyncMock(return_value=False)
        sandbox.authorize_temp = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)
        monkeypatch.setattr(
            "app.security.approval_flow._await_approval",
            AsyncMock(return_value=_FakeApprovalDecision(approved=True, decision="once")),
        )

        pending = [_fake_tool_call("tc-1", "read_file", {"path": "/external/dir"})]

        await _handle_directory_extension(
            pending,
            "test-thread",
            sandbox,
            workspace_path="/workspace",
        )

        # 所有 is_path_authorized 调用应传 writable=False（只读工具）
        for call in sandbox.is_path_authorized.call_args_list:
            assert call.kwargs.get("writable") is False, (
                f"只读工具应传 writable=False，实际: {call.kwargs}"
            )

    @pytest.mark.asyncio
    async def test_all_under_workspace_auto_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """bug #4: 所有越界路径位于已授权目录下 → 自动放行（无审批事件）。"""
        from app.security.approval_flow import _handle_directory_extension

        sandbox = MagicMock()
        # 第一次检查（收集越界）：返回 False
        # 第二次检查（all_under_workspace）：返回 True → 自动放行
        sandbox.is_path_authorized = AsyncMock(side_effect=[False, True])
        sandbox.authorize_temp = AsyncMock()

        fake_settings = MagicMock()
        fake_settings.approval_max_wait = 300
        monkeypatch.setattr("app.security.approval_flow.get_settings", lambda: fake_settings)

        pending = [_fake_tool_call("tc-1", "read_file", {"path": "/workspace/sub/dir"})]

        result = await _handle_directory_extension(
            pending,
            "test-thread",
            sandbox,
            workspace_path="/workspace",
        )

        # 应返回空 events（自动放行）
        assert result.events == []
        assert result.denied is False
        assert result.timed_out is False
