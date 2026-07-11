"""Execution context — the input contract for :class:`RiskClassifier`.

The context is intentionally a frozen dataclass so it can be passed through
async boundaries safely and so the policy implementations never accidentally
mutate shared state.

The :func:`build_cli_execute_context` / :func:`build_shell_backend_context`
helpers construct the context with the right ``exec_mode`` for each entry
point and pull the live ``sandbox_mode`` from settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from app.config import get_settings
from app.sandbox.path_guard import SCRATCH_DIR


# Use a Protocol so tests can pass a stub without inheriting from the real
# SessionSandbox (which is async and would require extra ceremony).
class _SandboxLike(Protocol):
    def is_full_trust_sync(self, thread_id: str) -> bool: ...
    def list_authorized_sync(self, thread_id: str) -> list[tuple[Path, bool]]: ...


@dataclass(frozen=True)
class ExecutionContext:
    """Read-only snapshot describing a single command execution attempt.

    Attributes:
        exec_mode: ``"argv_list"`` (subprocess argv, no shell parsing) or
            ``"shell_string"`` (passed to ``shell=True`` subprocess).
        trust_mode: ``"workspace"`` (normal sandbox enforcement) or
            ``"full_trust"`` (skip all per-thread authorisation checks).
        thread_id: Conversation / session identifier.
        authorized_paths: Immutable snapshot of the thread's authorised dirs.
        scratch_path: Always-writable scratch directory for the agent.
        sandbox_mode: Global sandbox mode — ``"sandbox"`` (enforce),
            ``"off"`` (skip), ``"manual"`` (reject + suggest manual).
    """

    exec_mode: Literal["argv_list", "shell_string"]
    trust_mode: Literal["workspace", "full_trust"]
    thread_id: str
    authorized_paths: tuple[Path, ...]
    scratch_path: Path
    sandbox_mode: Literal["sandbox", "off", "manual"]

    def __post_init__(self) -> None:
        # Normalise the container types so the frozen dataclass stays hashable
        # and cannot be mutated by a caller passing a list.
        if not isinstance(self.authorized_paths, tuple):
            object.__setattr__(self, "authorized_paths", tuple(self.authorized_paths))

    # ----- convenience predicates -----

    @property
    def is_argv(self) -> bool:
        return self.exec_mode == "argv_list"

    @property
    def is_full_trust(self) -> bool:
        return self.trust_mode == "full_trust"

    @property
    def is_path_unrestricted(self) -> bool:
        """True only under global ``off`` mode."""
        return self.sandbox_mode == "off"


# ----- builders -----


def _common_kwargs(thread_id: str, sandbox: _SandboxLike) -> dict[str, Any]:
    settings = get_settings()
    is_full_trust = bool(sandbox.is_full_trust_sync(thread_id))
    return dict(
        thread_id=thread_id,
        trust_mode="full_trust" if is_full_trust else "workspace",
        authorized_paths=tuple(p for p, _w in sandbox.list_authorized_sync(thread_id)),
        scratch_path=SCRATCH_DIR,
        sandbox_mode=settings.sandbox_mode,
    )


def build_cli_execute_context(
    thread_id: str,
    sandbox: _SandboxLike,
) -> ExecutionContext:
    """Build a context for the legacy ``cli_execute`` tool (argv list)."""
    return ExecutionContext(exec_mode="argv_list", **_common_kwargs(thread_id, sandbox))


def build_shell_backend_context(
    thread_id: str,
    sandbox: _SandboxLike,
) -> ExecutionContext:
    """Build a context for ``SafeLocalShellBackend`` (shell=True)."""
    return ExecutionContext(exec_mode="shell_string", **_common_kwargs(thread_id, sandbox))
