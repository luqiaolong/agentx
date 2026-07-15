"""SafeLocalShellBackend 单元测试。

覆盖安全拦截、settings 超时/输出限制透传、以及 Windows 内存命令可用性。
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.deepagent.safe_shell_backend import SafeLocalShellBackend
from app.security.sandbox_escalation import SandboxFailureAnalysis
from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse


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
        assert "元字符" in result.output
        assert "|" in result.output

    def test_python_string_metachar_allowed(self, tmp_path) -> None:
        """python -c 字符串字面量中的 ; 不应被拦截。"""
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute('python -c "print(\'a;b;c\')"')
        assert result.exit_code == 0
        assert "a;b;c" in result.output

    def test_git_write_command_rejected(self, tmp_path) -> None:
        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("git commit -m test")
        assert result.exit_code == 126
        assert "写操作" in result.output
        assert "审批" in result.output

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


class TestSafeShellBackendSandboxEscalation:
    """沙箱权限升级分支：sandbox_mode off/非off 行为验证。"""

    def test_sandbox_off_bypasses_to_subprocess(self, tmp_path, monkeypatch) -> None:
        """sandbox_mode == 'off' + 沙箱失败 + is_sandbox_limit → subprocess.run 被调用。"""
        # mock _is_sandbox_off → True（绕过沙箱）
        monkeypatch.setattr(
            "app.deepagent.safe_shell_backend._is_sandbox_off", lambda: True
        )
        # mock analyze_sandbox_failure → 确认沙箱限制
        fake_analysis = SandboxFailureAnalysis(
            is_sandbox_limit=True,
            reason="沙箱限制：权限不足",
            suggested_action="retry_with_auth",
            suggested_path="/tmp/test",
        )
        monkeypatch.setattr(
            "app.deepagent.safe_shell_backend.analyze_sandbox_failure",
            lambda *args: fake_analysis,
        )
        # mock 父类 execute → 返回失败结果（模拟沙箱拦截）
        failed_result = ExecuteResponse(
            output="Permission denied", exit_code=1, truncated=False
        )
        monkeypatch.setattr(
            LocalShellBackend, "execute", lambda self, cmd, **kw: failed_result
        )
        # mock subprocess.run → 模拟绕过沙箱后的成功执行
        mock_proc = MagicMock(stdout="success output", stderr="", returncode=0)
        mock_run = MagicMock(return_value=mock_proc)
        monkeypatch.setattr("subprocess.run", mock_run)

        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("echo test")

        # subprocess.run 被调用（绕过沙箱直接执行）
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["shell"] is True
        assert mock_run.call_args.kwargs["capture_output"] is True
        assert mock_run.call_args.kwargs["text"] is True
        # env 不能为 None（避免泄漏父进程敏感环境变量）
        assert mock_run.call_args.kwargs["env"] is not None
        # timeout 不能为 None（避免命令永久挂起）
        assert mock_run.call_args.kwargs["timeout"] is not None
        # 返回 subprocess.run 的结果
        assert result.exit_code == 0
        assert "success output" in result.output

    def test_sandbox_off_timeout_returns_124(self, tmp_path, monkeypatch) -> None:
        """sandbox off + subprocess.run 抛 TimeoutExpired → exit_code=124。"""
        # mock _is_sandbox_off → True（绕过沙箱）
        monkeypatch.setattr(
            "app.deepagent.safe_shell_backend._is_sandbox_off", lambda: True
        )
        # mock analyze_sandbox_failure → 确认沙箱限制
        fake_analysis = SandboxFailureAnalysis(
            is_sandbox_limit=True,
            reason="沙箱限制：权限不足",
            suggested_action="retry_with_auth",
            suggested_path="/tmp/test",
        )
        monkeypatch.setattr(
            "app.deepagent.safe_shell_backend.analyze_sandbox_failure",
            lambda *args: fake_analysis,
        )
        # mock 父类 execute → 返回失败结果（模拟沙箱拦截）
        failed_result = ExecuteResponse(
            output="Permission denied", exit_code=1, truncated=False
        )
        monkeypatch.setattr(
            LocalShellBackend, "execute", lambda self, cmd, **kw: failed_result
        )
        # mock subprocess.run → 抛 TimeoutExpired
        mock_run = MagicMock(
            side_effect=subprocess.TimeoutExpired(cmd="echo test", timeout=1)
        )
        monkeypatch.setattr("subprocess.run", mock_run)

        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("echo test")

        # 超时返回 124（与父类约定一致）
        assert result.exit_code == 124
        assert "超时" in result.output
        mock_run.assert_called_once()

    def test_sandbox_off_truncates_large_output(self, tmp_path, monkeypatch) -> None:
        """sandbox off + 输出超过 max_output_bytes → truncated=True 且输出被截断。"""
        # mock _is_sandbox_off → True（绕过沙箱）
        monkeypatch.setattr(
            "app.deepagent.safe_shell_backend._is_sandbox_off", lambda: True
        )
        # mock analyze_sandbox_failure → 确认沙箱限制
        fake_analysis = SandboxFailureAnalysis(
            is_sandbox_limit=True,
            reason="沙箱限制：权限不足",
            suggested_action="retry_with_auth",
            suggested_path="/tmp/test",
        )
        monkeypatch.setattr(
            "app.deepagent.safe_shell_backend.analyze_sandbox_failure",
            lambda *args: fake_analysis,
        )
        # mock 父类 execute → 返回失败结果（模拟沙箱拦截）
        failed_result = ExecuteResponse(
            output="Permission denied", exit_code=1, truncated=False
        )
        monkeypatch.setattr(
            LocalShellBackend, "execute", lambda self, cmd, **kw: failed_result
        )
        # mock subprocess.run → 返回超大 stdout
        mock_proc = MagicMock(stdout="A" * 1000, stderr="", returncode=0)
        mock_run = MagicMock(return_value=mock_proc)
        monkeypatch.setattr("subprocess.run", mock_run)

        backend = SafeLocalShellBackend(
            root_dir=tmp_path, virtual_mode=False, max_output_bytes=20
        )
        result = backend.execute("echo test")

        # 输出被截断到 max_output_bytes，truncated=True
        assert result.truncated is True
        assert len(result.output) <= 20
        mock_run.assert_called_once()

    def test_sandbox_not_off_appends_request_permission_hint(
        self, tmp_path, monkeypatch
    ) -> None:
        """sandbox_mode != 'off' + 沙箱失败 + is_sandbox_limit → hint 含 request_permission 提示。"""
        # mock _is_sandbox_off → False（不绕过沙箱）
        monkeypatch.setattr(
            "app.deepagent.safe_shell_backend._is_sandbox_off", lambda: False
        )
        # mock analyze_sandbox_failure → 确认沙箱限制
        fake_analysis = SandboxFailureAnalysis(
            is_sandbox_limit=True,
            reason="沙箱限制：权限不足",
            suggested_action="retry_with_auth",
            suggested_path="/tmp/project",
        )
        monkeypatch.setattr(
            "app.deepagent.safe_shell_backend.analyze_sandbox_failure",
            lambda *args: fake_analysis,
        )
        # mock 父类 execute → 返回失败结果（模拟沙箱拦截）
        failed_result = ExecuteResponse(
            output="Permission denied", exit_code=1, truncated=False
        )
        monkeypatch.setattr(
            LocalShellBackend, "execute", lambda self, cmd, **kw: failed_result
        )

        backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
        result = backend.execute("echo test")

        # 输出含 [SANDBOX_ESCALATION] 标记和 request_permission 提示
        assert "[SANDBOX_ESCALATION]" in result.output
        assert "request_permission" in result.output
        assert "path" in result.output.lower() or "路径" in result.output
        # 保持原 exit_code（不绕过沙箱）
        assert result.exit_code == 1
        # 原始输出仍保留
        assert "Permission denied" in result.output
