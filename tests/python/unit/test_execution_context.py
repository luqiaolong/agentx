"""Unit tests for :class:`app.security.context.ExecutionContext`.

The context object is the input contract for every ``RiskPolicy`` and for
``RiskClassifier.assess``. It captures execution mode, trust mode, thread
identity, the set of authorised paths and the active sandbox mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.security.context import (
    ExecutionContext,
    build_cli_execute_context,
    build_shell_backend_context,
)
from app.security.risk import RiskLevel


# ============================================================
# 1. Basic shape
# ============================================================


def _ctx(**overrides: Any) -> ExecutionContext:
    base: dict[str, Any] = dict(
        exec_mode="shell_string",
        trust_mode="workspace",
        thread_id="t1",
        authorized_paths=(Path("/data/workspace"),),
        scratch_path=Path("/data/workspace/.scratch"),
        sandbox_mode="sandbox",
    )
    base.update(overrides)
    return ExecutionContext(**base)


def test_context_stores_all_fields() -> None:
    ctx = _ctx()
    assert ctx.exec_mode == "shell_string"
    assert ctx.trust_mode == "workspace"
    assert ctx.thread_id == "t1"
    assert ctx.authorized_paths == (Path("/data/workspace"),)
    assert ctx.scratch_path == Path("/data/workspace/.scratch")
    assert ctx.sandbox_mode == "sandbox"


# ============================================================
# 2. Immutability
# ============================================================


def test_context_is_frozen() -> None:
    ctx = _ctx()
    with pytest.raises((AttributeError, TypeError)):
        ctx.thread_id = "t2"  # type: ignore[misc]


# ============================================================
# 3. Convenience properties
# ============================================================


def test_is_argv_property() -> None:
    assert _ctx(exec_mode="argv_list").is_argv is True
    assert _ctx(exec_mode="shell_string").is_argv is False


def test_is_full_trust_property() -> None:
    assert _ctx(trust_mode="full_trust").is_full_trust is True
    assert _ctx(trust_mode="workspace").is_full_trust is False


def test_is_path_unrestricted_property() -> None:
    assert _ctx(sandbox_mode="off").is_path_unrestricted is True
    assert _ctx(sandbox_mode="sandbox").is_path_unrestricted is False
    assert _ctx(sandbox_mode="manual").is_path_unrestricted is False


# ============================================================
# 4. authorized_paths is normalised to tuple
# ============================================================


def test_authorized_paths_coerced_to_tuple() -> None:
    ctx = ExecutionContext(
        exec_mode="shell_string",
        trust_mode="workspace",
        thread_id="t1",
        authorized_paths=[Path("/a"), Path("/b")],  # type: ignore[arg-type]
        scratch_path=Path("/tmp"),
        sandbox_mode="sandbox",
    )
    assert isinstance(ctx.authorized_paths, tuple)


# ============================================================
# 5. Builders
# ============================================================


class _StubSandbox:
    """Minimal stub for the SessionSandbox bits the builders read."""

    def __init__(self, full_trust: bool = False, paths: list[tuple[Path, bool]] | None = None) -> None:
        self._full_trust = full_trust
        self._paths = paths or []

    def is_full_trust_sync(self, thread_id: str) -> bool:
        return self._full_trust

    def list_authorized_sync(self, thread_id: str) -> list[tuple[Path, bool]]:
        return list(self._paths)


def test_build_cli_execute_context_uses_argv_mode() -> None:
    sandbox = _StubSandbox()
    ctx = build_cli_execute_context("t1", sandbox)  # type: ignore[arg-type]
    assert ctx.exec_mode == "argv_list"
    assert ctx.thread_id == "t1"
    assert ctx.sandbox_mode in {"sandbox", "off", "manual"}


def test_build_shell_backend_context_uses_shell_mode() -> None:
    sandbox = _StubSandbox()
    ctx = build_shell_backend_context("t1", sandbox)  # type: ignore[arg-type]
    assert ctx.exec_mode == "shell_string"
    assert ctx.thread_id == "t1"


def test_builder_propagates_full_trust() -> None:
    sandbox = _StubSandbox(full_trust=True)
    ctx = build_cli_execute_context("t1", sandbox)  # type: ignore[arg-type]
    assert ctx.is_full_trust is True


def test_builder_propagates_authorized_paths() -> None:
    paths = [(Path("/data/workspace"), True), (Path("/data/uploads"), False)]
    sandbox = _StubSandbox(paths=paths)
    ctx = build_cli_execute_context("t1", sandbox)  # type: ignore[arg-type]
    assert Path("/data/workspace") in ctx.authorized_paths
    assert Path("/data/uploads") in ctx.authorized_paths


def test_builder_handles_empty_authorized_paths() -> None:
    sandbox = _StubSandbox(paths=[])
    ctx = build_cli_execute_context("t1", sandbox)  # type: ignore[arg-type]
    assert ctx.authorized_paths == ()


# ============================================================
# 6. Sandbox mode integration with settings
# ============================================================


def test_builder_uses_current_sandbox_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``AGENTX_SANDBOX_MODE`` is set, the builder reflects it."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AGENTX_SANDBOX_MODE", "off")
    sandbox = _StubSandbox()
    ctx = build_cli_execute_context("t1", sandbox)  # type: ignore[arg-type]
    assert ctx.sandbox_mode == "off"
    assert ctx.is_path_unrestricted is True
