"""app.cli.approval 模块单元测试。

覆盖 handle_approval 的所有分支：
- y / yes → approve
- n / no → deny
- o / once → once
- s / session → session
- 无效输入 → 重新询问
- EOFError → deny
- KeyboardInterrupt → deny
- 大小写不敏感
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.cli.approval import handle_approval


class TestHandleApproval:
    """handle_approval 行为测试。"""

    @pytest.mark.asyncio
    async def test_approve_yes(self):
        with patch("builtins.input", return_value="y"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                mock_submit.assert_awaited_once()
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "approve"

    @pytest.mark.asyncio
    async def test_approve_yes_full(self):
        """yes 完整形式。"""
        with patch("builtins.input", return_value="yes"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "approve"

    @pytest.mark.asyncio
    async def test_approve_no(self, capsys):
        with patch("builtins.input", return_value="n"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                mock_submit.assert_awaited_once()
                decision = mock_submit.call_args[0][1]
                assert decision.approved is False
                assert decision.decision == "deny"
        out = capsys.readouterr().out
        assert "已拒绝" in out

    @pytest.mark.asyncio
    async def test_approve_no_full(self, capsys):
        with patch("builtins.input", return_value="no"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is False
                assert decision.decision == "deny"

    @pytest.mark.asyncio
    async def test_approve_once(self):
        with patch("builtins.input", return_value="o"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "once"

    @pytest.mark.asyncio
    async def test_approve_once_full(self):
        with patch("builtins.input", return_value="once"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "once"

    @pytest.mark.asyncio
    async def test_approve_session(self):
        with patch("builtins.input", return_value="s"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "session"

    @pytest.mark.asyncio
    async def test_approve_session_full(self):
        with patch("builtins.input", return_value="session"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "session"

    @pytest.mark.asyncio
    async def test_invalid_then_valid(self, capsys):
        """先输入无效值，再输入有效值。"""
        inputs = iter(["invalid", "y"])
        with patch("builtins.input", side_effect=lambda *a: next(inputs)):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                mock_submit.assert_awaited_once()
        out = capsys.readouterr().out
        # 应该提示重新输入
        assert "y/n/o/s" in out

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        """大写字母也接受。"""
        with patch("builtins.input", return_value="Y"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "approve"

    @pytest.mark.asyncio
    async def test_whitespace_stripped(self):
        """前后空白被剥离。"""
        with patch("builtins.input", return_value="  y  "):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is True
                assert decision.decision == "approve"

    @pytest.mark.asyncio
    async def test_eof_returns_deny(self, capsys):
        """Ctrl+D（EOFError）→ 拒绝。"""
        with patch("builtins.input", side_effect=EOFError()):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is False
                assert decision.decision == "deny"
        out = capsys.readouterr().out
        assert "已拒绝" in out

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_returns_deny(self, capsys):
        """Ctrl+C（KeyboardInterrupt）→ 拒绝。"""
        with patch("builtins.input", side_effect=KeyboardInterrupt()):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("test-thread")
                decision = mock_submit.call_args[0][1]
                assert decision.approved is False
                assert decision.decision == "deny"
        out = capsys.readouterr().out
        assert "已拒绝" in out

    @pytest.mark.asyncio
    async def test_submit_called_with_correct_thread_id(self):
        """write_approval_decision 接收正确的 thread_id。"""
        with patch("builtins.input", return_value="y"):
            with patch("app.security.approval.write_approval_decision", new_callable=AsyncMock) as mock_submit:
                await handle_approval("thread-xyz-123")
                thread_id, decision = mock_submit.call_args[0]
                assert thread_id == "thread-xyz-123"
                assert decision.approved is True
                assert decision.decision == "approve"
