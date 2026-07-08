"""app.cli.commands 模块单元测试。

覆盖：
- CommandResult classmethods（continue_ / quit / switch_mode）
- CommandContext dataclass
- handle_command：quit / help / unknown / mode 切换
- _cmd_mode：切换模式返回 switch_mode
- 各 async 命令使用 mock 验证调用（reset / abort / pause / resume / compact / history / threads）
- _cmd_init / _cmd_clear / _cmd_thread：同步命令路径
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cli.commands import (
    CommandAction,
    CommandContext,
    CommandResult,
    handle_command,
    print_help,
)


# ============================================================
# CommandResult classmethods 测试
# ============================================================

class TestCommandResult:
    """CommandResult 工厂方法。"""

    def test_continue_classmethod(self):
        result = CommandResult.continue_()
        assert result.action == CommandAction.CONTINUE
        assert result.new_mode is None

    def test_quit_classmethod(self):
        result = CommandResult.quit()
        assert result.action == CommandAction.QUIT
        assert result.new_mode is None

    def test_switch_mode_classmethod(self):
        result = CommandResult.switch_mode("coding")
        assert result.action == CommandAction.SWITCH_MODE
        assert result.new_mode == "coding"

    def test_switch_mode_with_team(self):
        result = CommandResult.switch_mode("coding_team")
        assert result.action == CommandAction.SWITCH_MODE
        assert result.new_mode == "coding_team"

    def test_command_action_enum_values(self):
        """CommandAction 枚举值正确。"""
        assert CommandAction.CONTINUE.value == "continue"
        assert CommandAction.SWITCH_MODE.value == "switch_mode"
        assert CommandAction.QUIT.value == "quit"


# ============================================================
# CommandContext dataclass 测试
# ============================================================

class TestCommandContext:
    """CommandContext 数据类。"""

    def test_construct_default(self):
        ctx = CommandContext(
            current_mode="coding",
            thread_id="t1",
            checkpointer=None,
            workspace_path=None,
        )
        assert ctx.current_mode == "coding"
        assert ctx.thread_id == "t1"
        assert ctx.checkpointer is None
        assert ctx.workspace_path is None

    def test_construct_with_values(self):
        checkpointer = MagicMock()
        ctx = CommandContext(
            current_mode="work",
            thread_id="abc",
            checkpointer=checkpointer,
            workspace_path="/tmp",
        )
        assert ctx.current_mode == "work"
        assert ctx.thread_id == "abc"
        assert ctx.checkpointer is checkpointer
        assert ctx.workspace_path == "/tmp"


def _make_ctx(
    current_mode: str = "coding",
    thread_id: str = "t1",
    checkpointer=None,
    workspace_path: str | None = "/tmp",
) -> CommandContext:
    """构造测试用 CommandContext。"""
    return CommandContext(
        current_mode=current_mode,
        thread_id=thread_id,
        checkpointer=checkpointer,
        workspace_path=workspace_path,
    )


# ============================================================
# handle_command 分发测试
# ============================================================

class TestHandleCommandDispatch:
    """handle_command 命令分发。"""

    @pytest.mark.asyncio
    async def test_quit_returns_quit(self):
        ctx = _make_ctx()
        result = await handle_command("/quit", ctx)
        assert result.action == CommandAction.QUIT

    @pytest.mark.asyncio
    async def test_quit_alias_q(self):
        ctx = _make_ctx()
        result = await handle_command("/q", ctx)
        assert result.action == CommandAction.QUIT

    @pytest.mark.asyncio
    async def test_help_returns_continue(self, capsys):
        ctx = _make_ctx()
        result = await handle_command("/help", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "可用命令" in out

    @pytest.mark.asyncio
    async def test_unknown_command_returns_continue(self, capsys):
        ctx = _make_ctx()
        result = await handle_command("/bogus", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "未知命令" in out

    @pytest.mark.asyncio
    async def test_command_case_insensitive(self, capsys):
        """命令不区分大小写（lower 处理）。"""
        ctx = _make_ctx()
        result = await handle_command("/QUIT", ctx)
        assert result.action == CommandAction.QUIT

    @pytest.mark.asyncio
    async def test_thread_command_prints_id(self, capsys):
        ctx = _make_ctx(thread_id="abc-123")
        result = await handle_command("/thread", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "abc-123" in out


# ============================================================
# _cmd_mode 测试（通过 handle_command 入口）
# ============================================================

class TestCmdMode:
    """/mode 命令。"""

    @pytest.mark.asyncio
    async def test_mode_no_arg_shows_current(self, capsys):
        ctx = _make_ctx(current_mode="coding")
        result = await handle_command("/mode", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "coding" in out

    @pytest.mark.asyncio
    async def test_mode_switch_to_work(self, capsys):
        ctx = _make_ctx(current_mode="coding")
        result = await handle_command("/mode work", ctx)
        assert result.action == CommandAction.SWITCH_MODE
        assert result.new_mode == "work"
        out = capsys.readouterr().out
        assert "work" in out

    @pytest.mark.asyncio
    async def test_mode_switch_to_coding(self, capsys):
        ctx = _make_ctx(current_mode="work")
        result = await handle_command("/mode coding", ctx)
        assert result.action == CommandAction.SWITCH_MODE
        assert result.new_mode == "coding"

    @pytest.mark.asyncio
    async def test_mode_switch_to_coding_team(self, capsys):
        ctx = _make_ctx(current_mode="coding")
        result = await handle_command("/mode coding_team", ctx)
        assert result.action == CommandAction.SWITCH_MODE
        assert result.new_mode == "coding_team"

    @pytest.mark.asyncio
    async def test_mode_invalid_returns_continue(self, capsys):
        ctx = _make_ctx(current_mode="coding")
        result = await handle_command("/mode invalid", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "无效模式" in out

    @pytest.mark.asyncio
    async def test_mode_case_insensitive_arg(self, capsys):
        """模式参数不区分大小写。"""
        ctx = _make_ctx(current_mode="coding")
        result = await handle_command("/mode WORK", ctx)
        assert result.action == CommandAction.SWITCH_MODE
        assert result.new_mode == "work"


# ============================================================
# 异步命令的 mock 验证
# ============================================================

class TestAsyncCommands:
    """验证 handle_command 正确 await 异步命令。"""

    @pytest.mark.asyncio
    async def test_reset_calls_adelete_thread(self, capsys):
        checkpointer = MagicMock()
        checkpointer.adelete_thread = AsyncMock()
        ctx = _make_ctx(thread_id="t1", checkpointer=checkpointer)
        # _cmd_reset 内部 from app.utils.security import get_sandbox，patch 真实路径
        with patch("app.utils.security.get_sandbox") as mock_get_sandbox:
            sandbox = MagicMock()
            mock_get_sandbox.return_value = sandbox
            result = await handle_command("/reset", ctx)
        assert result.action == CommandAction.CONTINUE
        checkpointer.adelete_thread.assert_awaited_once_with("t1")
        sandbox.clear.assert_called_once_with("t1")

    @pytest.mark.asyncio
    async def test_reset_without_checkpointer(self, capsys):
        """无 checkpointer 时打印提示。"""
        ctx = _make_ctx(checkpointer=None)
        result = await handle_command("/reset", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "checkpointer" in out

    @pytest.mark.asyncio
    async def test_abort_calls_set_abort(self, capsys):
        ctx = _make_ctx(thread_id="t1")
        # _cmd_abort 内部 from app.approval.state import set_abort，patch 真实路径
        with patch("app.approval.state.set_abort", new_callable=AsyncMock) as mock_abort:
            result = await handle_command("/abort", ctx)
        assert result.action == CommandAction.CONTINUE
        mock_abort.assert_awaited_once_with("t1")
        out = capsys.readouterr().out
        assert "中止" in out

    @pytest.mark.asyncio
    async def test_pause_calls_set_pause(self, capsys):
        ctx = _make_ctx(thread_id="t1")
        with patch("app.approval.state.set_pause", new_callable=AsyncMock) as mock_pause:
            result = await handle_command("/pause", ctx)
        assert result.action == CommandAction.CONTINUE
        mock_pause.assert_awaited_once_with("t1")
        out = capsys.readouterr().out
        assert "暂停" in out

    @pytest.mark.asyncio
    async def test_resume_calls_clear_pause(self, capsys):
        ctx = _make_ctx(thread_id="t1")
        with patch("app.approval.state.clear_pause", new_callable=AsyncMock) as mock_clear:
            result = await handle_command("/resume", ctx)
        assert result.action == CommandAction.CONTINUE
        mock_clear.assert_awaited_once_with("t1")
        out = capsys.readouterr().out
        assert "恢复" in out

    @pytest.mark.asyncio
    async def test_threads_calls_list_threads(self, capsys):
        ctx = _make_ctx()
        # _cmd_threads 内部 from app.memory.checkpointer import list_threads
        with patch(
            "app.memory.list_threads",
            new_callable=AsyncMock,
            return_value=[
                {"thread_id": "t1", "message_count": 5, "updated_at": "2026-01-01"},
            ],
        ) as mock_list:
            result = await handle_command("/threads", ctx)
        assert result.action == CommandAction.CONTINUE
        mock_list.assert_awaited_once()
        out = capsys.readouterr().out
        assert "t1" in out

    @pytest.mark.asyncio
    async def test_threads_empty(self, capsys):
        ctx = _make_ctx()
        with patch(
            "app.memory.list_threads",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_list:
            result = await handle_command("/threads", ctx)
        assert result.action == CommandAction.CONTINUE
        mock_list.assert_awaited_once()
        out = capsys.readouterr().out
        assert "无历史会话" in out

    @pytest.mark.asyncio
    async def test_history_calls_checkpointer_aget(self, capsys):
        """history 命令调用 checkpointer.aget。"""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": []},
        })
        ctx = _make_ctx(thread_id="t1", checkpointer=checkpointer)
        result = await handle_command("/history", ctx)
        assert result.action == CommandAction.CONTINUE
        checkpointer.aget.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_history_with_limit_arg(self, capsys):
        """history 5 限制条数。"""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": []},
        })
        ctx = _make_ctx(thread_id="t1", checkpointer=checkpointer)
        result = await handle_command("/history 5", ctx)
        assert result.action == CommandAction.CONTINUE

    @pytest.mark.asyncio
    async def test_history_invalid_limit_arg(self, capsys):
        """history abc 提示用法。"""
        checkpointer = MagicMock()
        ctx = _make_ctx(thread_id="t1", checkpointer=checkpointer)
        result = await handle_command("/history abc", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "用法" in out

    @pytest.mark.asyncio
    async def test_history_without_checkpointer(self, capsys):
        ctx = _make_ctx(checkpointer=None)
        result = await handle_command("/history", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "checkpointer" in out

    @pytest.mark.asyncio
    async def test_compact_without_checkpointer(self, capsys):
        ctx = _make_ctx(checkpointer=None)
        result = await handle_command("/compact", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "checkpointer" in out

    @pytest.mark.asyncio
    async def test_compact_with_no_checkpoint(self, capsys):
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value=None)
        ctx = _make_ctx(thread_id="t1", checkpointer=checkpointer)
        result = await handle_command("/compact", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "无 checkpoint" in out


# ============================================================
# 同步命令测试
# ============================================================

class TestSyncCommands:
    """同步命令路径。"""

    @pytest.mark.asyncio
    async def test_init_calls_authorize(self, capsys):
        ctx = _make_ctx(workspace_path="/tmp/project")
        # _cmd_init 内部 from app.utils.security import get_sandbox，patch 真实路径
        with patch("app.utils.security.get_sandbox") as mock_get_sandbox:
            sandbox = MagicMock()
            mock_get_sandbox.return_value = sandbox
            result = await handle_command("/init", ctx)
        assert result.action == CommandAction.CONTINUE
        sandbox.authorize.assert_called_once()
        out = capsys.readouterr().out
        assert "已授权" in out

    @pytest.mark.asyncio
    async def test_init_without_workspace(self, capsys):
        ctx = CommandContext(
            current_mode="coding",
            thread_id="t1",
            checkpointer=None,
            workspace_path=None,
        )
        result = await handle_command("/init", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "未指定" in out

    @pytest.mark.asyncio
    async def test_model_prints_settings(self, capsys):
        """_cmd_model 从 get_settings 读取并打印。"""
        from app.config import Settings

        test_settings = Settings(default_model="test-model", openai_base_url="http://x")
        # _cmd_model 内部 from app.config import get_settings，patch 真实路径
        with patch("app.config.get_settings", return_value=test_settings):
            ctx = _make_ctx()
            result = await handle_command("/model", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "test-model" in out

    @pytest.mark.asyncio
    async def test_models_prints_entries(self, capsys):
        """_cmd_models 列出模型。"""
        config = {
            "models": {
                "entries": [
                    {
                        "id": "m1",
                        "model": "gpt-4o",
                        "label": "GPT-4o",
                        "providerId": "openai",
                    },
                ],
                "activeId": "m1",
            }
        }
        with patch("app.cli.store.read_config_json", return_value=config):
            ctx = _make_ctx()
            result = await handle_command("/models", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "gpt-4o" in out
        assert "*" in out

    @pytest.mark.asyncio
    async def test_models_no_config(self, capsys):
        with patch("app.cli.store.read_config_json", return_value=None):
            ctx = _make_ctx()
            result = await handle_command("/models", ctx)
        assert result.action == CommandAction.CONTINUE
        out = capsys.readouterr().out
        assert "无法读取" in out

    @pytest.mark.asyncio
    async def test_clear_runs_os_system(self):
        ctx = _make_ctx()
        with patch("app.cli.commands.os.system") as mock_system:
            result = await handle_command("/clear", ctx)
        assert result.action == CommandAction.CONTINUE
        mock_system.assert_called_once()


# ============================================================
# print_help 测试
# ============================================================

class TestPrintHelp:
    """print_help 输出。"""

    def test_print_help_contains_commands(self, capsys):
        print_help()
        out = capsys.readouterr().out
        assert "/quit" in out
        assert "/help" in out
        assert "/mode" in out
        assert "/reset" in out
        assert "/history" in out
