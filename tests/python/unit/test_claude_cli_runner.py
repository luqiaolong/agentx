"""ClaudeCliRunner 单元测试。"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from app.eval.claude_cli_runner import ClaudeCliError, ClaudeCliRunner


@pytest.fixture
def runner() -> ClaudeCliRunner:
    """返回默认配置的 Runner（不检查真实 CLI 是否存在）。"""
    with patch(
        "app.eval.claude_cli_runner.claude_cli_available", return_value=True
    ):
        return ClaudeCliRunner()


class TestBuildCommand:
    """_build_command 应正确处理不同平台与安装形态。"""

    def test_non_windows_uses_resolved_path(self, runner: ClaudeCliRunner) -> None:
        with patch.object(sys, "platform", "linux"):
            with patch("app.eval.claude_cli_runner.shutil.which") as mock_which:
                mock_which.return_value = "/usr/local/bin/claude"
                cmd = runner._build_command()

        assert cmd[0] == "/usr/local/bin/claude"
        assert "-p" in cmd
        assert "--output-format" in cmd

    def test_windows_cmd_resolves_to_node_exe(
        self, runner: ClaudeCliRunner
    ) -> None:
        """Windows .cmd 包装应解析为 node.exe + cli.js 直调（绕过 cmd /c）。"""
        with patch.object(sys, "platform", "win32"):
            with patch("app.eval.claude_cli_runner.shutil.which") as mock_which:
                mock_which.return_value = r"C:\Users\foo\AppData\Roaming\npm\claude.CMD"
                with patch(
                    "app.eval.claude_cli_runner._resolve_npm_wrapper"
                ) as mock_resolve:
                    mock_resolve.return_value = (
                        r"C:\Users\foo\AppData\Roaming\npm\node.exe",
                        r"C:\Users\foo\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\cli.js",
                    )
                    cmd = runner._build_command()

        assert cmd[0] == r"C:\Users\foo\AppData\Roaming\npm\node.exe"
        assert cmd[1].endswith("cli.js")
        assert "-p" in cmd

    def test_windows_cmd_resolve_fails_falls_back_to_cmd(
        self, runner: ClaudeCliRunner
    ) -> None:
        """_resolve_npm_wrapper 失败时回退到 cmd /c。"""
        with patch.object(sys, "platform", "win32"):
            with patch("app.eval.claude_cli_runner.shutil.which") as mock_which:
                mock_which.return_value = r"C:\Users\foo\AppData\Roaming\npm\claude.CMD"
                with patch(
                    "app.eval.claude_cli_runner._resolve_npm_wrapper",
                    return_value=None,
                ):
                    cmd = runner._build_command()

        assert cmd[:3] == ["cmd", "/c", r"C:\Users\foo\AppData\Roaming\npm\claude.CMD"]
        assert "-p" in cmd

    def test_windows_ps1_resolves_to_node_exe(
        self, runner: ClaudeCliRunner
    ) -> None:
        """Windows .ps1 包装也应解析为 node.exe + cli.js 直调。"""
        with patch.object(sys, "platform", "win32"):
            with patch("app.eval.claude_cli_runner.shutil.which") as mock_which:
                mock_which.return_value = r"C:\Users\foo\AppData\Roaming\npm\claude.ps1"
                with patch(
                    "app.eval.claude_cli_runner._resolve_npm_wrapper"
                ) as mock_resolve:
                    mock_resolve.return_value = (
                        r"C:\Users\foo\AppData\Roaming\npm\node.exe",
                        r"C:\Users\foo\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\cli.js",
                    )
                    cmd = runner._build_command()

        assert cmd[0] == r"C:\Users\foo\AppData\Roaming\npm\node.exe"
        assert cmd[1].endswith("cli.js")

    def test_windows_ps1_resolve_fails_falls_back_to_pwsh(
        self, runner: ClaudeCliRunner
    ) -> None:
        """_resolve_npm_wrapper 失败时 .ps1 回退到 pwsh -File。"""
        with patch.object(sys, "platform", "win32"):
            with patch("app.eval.claude_cli_runner.shutil.which") as mock_which:
                mock_which.side_effect = lambda name: {
                    "claude": r"C:\Users\foo\AppData\Roaming\npm\claude.ps1",
                    "pwsh": r"C:\Program Files\PowerShell\7\pwsh.exe",
                }.get(name)
                with patch(
                    "app.eval.claude_cli_runner._resolve_npm_wrapper",
                    return_value=None,
                ):
                    cmd = runner._build_command()

        assert cmd[0] == r"C:\Program Files\PowerShell\7\pwsh.exe"
        assert cmd[1:4] == ["-ExecutionPolicy", "Bypass", "-File"]

    def test_windows_ps1_resolve_fails_no_pwsh_returns_exe(
        self, runner: ClaudeCliRunner
    ) -> None:
        """_resolve_npm_wrapper 失败 + 无 pwsh 时直接返回 exe 路径（不 raise）。"""
        with patch.object(sys, "platform", "win32"):
            with patch("app.eval.claude_cli_runner.shutil.which") as mock_which:
                mock_which.side_effect = lambda name: (
                    r"C:\Users\foo\AppData\Roaming\npm\claude.ps1"
                    if name == "claude"
                    else None
                )
                with patch(
                    "app.eval.claude_cli_runner._resolve_npm_wrapper",
                    return_value=None,
                ):
                    cmd = runner._build_command()

        assert cmd[0] == r"C:\Users\foo\AppData\Roaming\npm\claude.ps1"
        assert "-p" in cmd

    def test_missing_cli_raises(self, runner: ClaudeCliRunner) -> None:
        with patch("app.eval.claude_cli_runner.shutil.which", return_value=None):
            with pytest.raises(ClaudeCliError, match="未安装"):
                runner._build_command()


class TestResolveNpmWrapper:
    """_resolve_npm_wrapper 应正确解析 npm 包装脚本。"""

    def test_resolves_cmd_wrapper(self, tmp_path) -> None:
        """模拟 npm .cmd 包装脚本，验证能提取 node.exe + cli.js 路径。"""
        import os

        # 模拟 npm 全局目录结构
        npm_bin = tmp_path / "npm_bin"
        npm_bin.mkdir()
        node_modules = npm_bin / "node_modules" / "@anthropic-ai" / "claude-code"
        node_modules.mkdir(parents=True)
        cli_js = node_modules / "cli.js"
        cli_js.write_text("// fake cli")

        # 模拟 node.exe
        node_exe = npm_bin / "node.exe"
        node_exe.write_text("")

        # 模拟 .cmd 包装脚本
        cmd_wrapper = npm_bin / "claude.cmd"
        cmd_wrapper.write_text(
            '@ECHO off\n'
            'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
            '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*\n'
        )

        from app.eval.claude_cli_runner import _resolve_npm_wrapper

        result = _resolve_npm_wrapper(str(cmd_wrapper))
        assert result is not None
        resolved_node, resolved_cli = result
        assert resolved_node == str(node_exe)
        assert os.path.normpath(resolved_cli) == os.path.normpath(str(cli_js))

    def test_returns_none_for_invalid_script(self, tmp_path) -> None:
        from app.eval.claude_cli_runner import _resolve_npm_wrapper

        bad_script = tmp_path / "bad.cmd"
        bad_script.write_text("echo no cli.js reference here")

        result = _resolve_npm_wrapper(str(bad_script))
        assert result is None
