"""Regression tests for sandbox-off shell fallback boundaries (RED phase).

Covers OpenSpec change ``deepagent-maintainability-refactor`` task 2.11.

Expected behavior (per spec):
- When sandbox-off fallback invokes a local subprocess, the subprocess ``cwd``
  MUST equal the backend workspace root (``root_dir``), NOT the AgentX server
  process CWD.

Current bug: ``SafeLocalShellBackend.execute`` calls
``subprocess.run(..., cwd=kwargs.get("cwd"), ...)``. When ``execute`` is invoked
without an explicit ``cwd`` kwarg (the common case — deepagents' FilesystemMiddleware
does not pass ``cwd``), ``kwargs.get("cwd")`` returns ``None``, so
``subprocess.run(cwd=None)`` inherits the AgentX server process CWD.

RED: ``subprocess.run`` is called with ``cwd=None`` instead of the backend's
``root_dir``.

Note: env reuse, timeout preservation, and output truncation behaviors already
work correctly in the current code and therefore are not included here — a RED
test must FAIL against the current code.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from app.deepagent.safe_shell_backend import SafeLocalShellBackend
from app.security.sandbox_escalation import SandboxFailureAnalysis


def _setup_sandbox_off_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Wire up mocks so that ``backend.execute`` reaches the sandbox-off
    subprocess.run fallback branch.

    Returns ``(mock_run, backend)``.
    """
    monkeypatch.setattr(
        "app.deepagent.safe_shell_backend._is_sandbox_off", lambda: True
    )
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
    failed_result = ExecuteResponse(
        output="Permission denied", exit_code=1, truncated=False
    )
    monkeypatch.setattr(
        LocalShellBackend, "execute", lambda self, cmd, **kw: failed_result
    )
    mock_proc = MagicMock(stdout="success", stderr="", returncode=0)
    mock_run = MagicMock(return_value=mock_proc)
    monkeypatch.setattr("subprocess.run", mock_run)

    backend = SafeLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
    return mock_run, backend


# ============================================================
# 2.11a — subprocess cwd equals backend workspace root
# ============================================================


def test_sandbox_off_fallback_cwd_equals_backend_root(tmp_path, monkeypatch) -> None:
    """The subprocess ``cwd`` MUST equal the backend's ``root_dir`` (workspace
    root), NOT ``None`` (which would inherit the AgentX server process CWD).

    RED: current code uses ``cwd=kwargs.get("cwd")`` which is ``None`` when
    ``execute`` is called without an explicit ``cwd`` kwarg.
    """
    mock_run, backend = _setup_sandbox_off_fallback(monkeypatch, tmp_path)

    # execute without explicit cwd kwarg (the common deepagents path)
    backend.execute("echo test")

    mock_run.assert_called_once()
    called_cwd = mock_run.call_args.kwargs.get("cwd")
    assert called_cwd == tmp_path, (
        f"subprocess cwd should equal backend root_dir ({tmp_path}), "
        f"got cwd={called_cwd!r} — current code falls back to None (server process CWD)"
    )
