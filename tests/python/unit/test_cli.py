"""cli 模块单元测试。"""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cli import (
    _build_parser,
    _consume_events,
    _handle_approval,
    _resolve_agent_mode,
    _read_stdin_if_piped,
)


# ============================================================
# 参数解析测试
# ============================================================

class TestArgParser:
    """命令行参数解析。"""

    def test_no_args_defaults_to_coding(self):
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.message is None
        assert args.work is False
        assert args.coding is False
        assert args.coding_team is False

    def test_message_positional(self):
        parser = _build_parser()
        args = parser.parse_args(["hello world"])
        assert args.message == "hello world"

    def test_work_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--work", "test"])
        assert args.work is True

    def test_coding_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--coding", "test"])
        assert args.coding is True

    def test_coding_team_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--coding-team", "test"])
        assert args.coding_team is True

    def test_verbose_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["-v"])
        assert args.verbose is True

    def test_json_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["--json"])
        assert args.json_mode is True

    def test_thread_option(self):
        parser = _build_parser()
        args = parser.parse_args(["--thread", "abc123"])
        assert args.thread == "abc123"

    def test_workspace_option(self):
        parser = _build_parser()
        args = parser.parse_args(["-w", "/tmp/project"])
        assert args.workspace == "/tmp/project"


class TestResolveAgentMode:
    """agent_mode 解析。"""

    def test_default_is_coding(self):
        args = argparse.Namespace(work=False, coding=False, coding_team=False)
        assert _resolve_agent_mode(args) == "coding"

    def test_work_flag(self):
        args = argparse.Namespace(work=True, coding=False, coding_team=False)
        assert _resolve_agent_mode(args) == "work"

    def test_coding_team_flag(self):
        args = argparse.Namespace(work=False, coding=False, coding_team=True)
        assert _resolve_agent_mode(args) == "coding_team"

    def test_work_overrides_coding(self):
        """--work 优先于 --coding。"""
        args = argparse.Namespace(work=True, coding=True, coding_team=False)
        assert _resolve_agent_mode(args) == "work"

    def test_coding_team_overrides_coding(self):
        """--coding-team 优先于 --coding。"""
        args = argparse.Namespace(work=False, coding=True, coding_team=True)
        assert _resolve_agent_mode(args) == "coding_team"


# ============================================================
# stdin 管道测试
# ============================================================

class TestStdinPiped:
    """管道输入检测。"""

    def test_tty_returns_none(self):
        with patch("sys.stdin.isatty", return_value=True):
            assert _read_stdin_if_piped() is None

    def test_piped_returns_content(self):
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        mock_stdin.read.return_value = "piped content\n"
        with patch("sys.stdin", mock_stdin):
            result = _read_stdin_if_piped()
            assert result == "piped content"


# ============================================================
# _consume_events 测试
# ============================================================

class TestConsumeEvents:
    """事件流消费。"""

    @pytest.mark.asyncio
    async def test_normal_completion(self):
        """正常事件流完成。"""
        events = [
            {"event": "token", "data": "Hello"},
            {"event": "done", "data": "{}"},
        ]

        async def gen():
            for e in events:
                yield e

        renderer = MagicMock()
        renderer.render = MagicMock()
        result = await _consume_events(gen(), renderer, "test-thread")
        assert result is True

    @pytest.mark.asyncio
    async def test_error_event(self):
        """error 事件返回 False。"""
        events = [
            {"event": "token", "data": "Hello"},
            {"event": "error", "data": "Something failed"},
        ]

        async def gen():
            for e in events:
                yield e

        renderer = MagicMock()
        result = await _consume_events(gen(), renderer, "test-thread")
        assert result is False

    @pytest.mark.asyncio
    async def test_approval_request_triggers_handler(self):
        """approval_request 事件触发审批处理。"""
        events = [
            {"event": "approval_request", "data": '{"tool_name": "write_file"}'},
            {"event": "done", "data": "{}"},
        ]

        async def gen():
            for e in events:
                yield e

        renderer = MagicMock()
        with patch("app.cli._handle_approval", new_callable=AsyncMock):
            result = await _consume_events(gen(), renderer, "test-thread")
            assert result is True


# ============================================================
# _handle_approval 测试
# ============================================================

class TestHandleApproval:
    """审批交互。"""

    @pytest.mark.asyncio
    async def test_approve_yes(self):
        with patch("builtins.input", return_value="y"):
            with patch("app.security.approval.submit_approval") as mock_submit:
                await _handle_approval("test-thread")
                mock_submit.assert_called_once()
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "approve"

    @pytest.mark.asyncio
    async def test_approve_no(self):
        with patch("builtins.input", return_value="n"):
            with patch("app.security.approval.submit_approval") as mock_submit:
                await _handle_approval("test-thread")
                mock_submit.assert_called_once()
                decision = mock_submit.call_args[0][1]
                assert decision.approved is False
                assert decision.decision == "deny"

    @pytest.mark.asyncio
    async def test_approve_once(self):
        with patch("builtins.input", return_value="o"):
            with patch("app.security.approval.submit_approval") as mock_submit:
                await _handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "once"

    @pytest.mark.asyncio
    async def test_approve_session(self):
        with patch("builtins.input", return_value="s"):
            with patch("app.security.approval.submit_approval") as mock_submit:
                await _handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "session"

    @pytest.mark.asyncio
    async def test_invalid_then_valid(self):
        """先输入无效值，再输入有效值。"""
        inputs = iter(["invalid", "y"])
        with patch("builtins.input", side_effect=lambda *a: next(inputs)):
            with patch("app.security.approval.submit_approval") as mock_submit:
                await _handle_approval("test-thread")
                mock_submit.assert_called_once()
