"""SafeLocalShellBackend 单元测试。

覆盖安全拦截、settings 超时/输出限制透传、以及 Windows 内存命令可用性。
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from app.config import Settings
from app.deepagent.safe_shell_backend import SafeLocalShellBackend


class TestSafeShellBackendDefaults:
    """SafeLocalShellBackend 初始化时从 settings 读取默认值。"""

    def test_uses_settings_timeout_and_max_output(self, tmp_path) -> None:
        """未显式传入 timeout / max_output_bytes 时复用 settings 的 CLI 工具配置。"""
        settings = Settings(cli_tool_timeout=123, cli_tool_max_output_chars=4321)
        with patch("app.config.get_settings", return_value=settings):
            backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)

        assert backend._default_timeout == 123
        assert backend._max_output_bytes == 4321

    def test_explicit_values_override_settings(self, tmp_path) -> None:
        """显式传入的 timeout / max_output_bytes 优先于 settings。"""
        settings = Settings(cli_tool_timeout=123, cli_tool_max_output_chars=4321)
        with patch("app.config.get_settings", return_value=settings):
            backend = SafeLocalShellBackend(
                root_dir=tmp_path,
                virtual_mode=False,
                timeout=60,
                max_output_bytes=9999,
            )

        assert backend._default_timeout == 60
        assert backend._max_output_bytes == 9999


class TestSafeShellBackendSecurity:
    """安全拦截分支。"""

    def test_empty_command_returns_error(self, tmp_path) -> None:
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("   ")
        assert result.exit_code == 1
        assert "不能为空" in result.output

    def test_blocked_command_rejected(self, tmp_path) -> None:
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("rm -rf /")
        assert result.exit_code == 126
        assert "黑名单" in result.output

    def test_forbidden_metachar_rejected(self, tmp_path) -> None:
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("echo a | cat")
        assert result.exit_code == 126
        assert "非法 shell 元字符" in result.output
        assert "|" in result.output

    def test_git_write_command_rejected(self, tmp_path) -> None:
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("git commit -m test")
        assert result.exit_code == 126
        assert "git 写操作需审批" in result.output

    def test_wrapper_blocked_command_rejected(self, tmp_path) -> None:
        """通过 cmd /c 包装的危险命令同样被拦截。"""
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute('cmd /c del file.txt')
        assert result.exit_code == 126
        assert "黑名单" in result.output


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专属命令")
class TestSafeShellBackendWindowsMemory:
    """Windows 下验证推荐的内存命令可正常执行。"""

    def test_powershell_total_memory_works(self, tmp_path) -> None:
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute(
            "powershell -Command (Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize"
        )
        assert result.exit_code == 0
        assert result.output.strip().isdigit()

    def test_powershell_free_memory_works(self, tmp_path) -> None:
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute(
            "powershell -Command (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"
        )
        assert result.exit_code == 0
        assert result.output.strip().isdigit()

    def test_wmic_command_fails_fast(self, tmp_path) -> None:
        """wmic 在已弃用/未安装环境中应快速返回错误，不会 hang。"""
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("wmic OS get TotalVisibleMemorySize")
        assert result.exit_code == 1
        assert "wmic" in result.output.lower() or "not recognized" in result.output.lower()


class TestSafeShellBackendExecution:
    """通用执行行为。"""

    def test_echo_command_works(self, tmp_path) -> None:
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.output

    def test_timeout_short_sleep(self, tmp_path) -> None:
        """超时时返回 exit_code 124。"""
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False, timeout=1)
        result = backend.execute("sleep 5" if sys.platform != "win32" else "powershell -Command Start-Sleep -Seconds 5")
        assert result.exit_code == 124
        assert "timed out" in result.output or "超时" in result.output

    def test_output_truncation(self, tmp_path) -> None:
        backend = SafeLocalShellBackend(
            root_dir=tmp_path, virtual_mode=False, max_output_bytes=20
        )
        result = backend.execute("python -c \"print('A' * 100)\"")
        assert result.truncated is True
        assert "truncated" in result.output.lower() or "截断" in result.output
