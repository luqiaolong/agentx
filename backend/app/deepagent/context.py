"""contextvar 传递 thread_id，供 AuthorizedLocalShellBackend 读取。"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "thread_id", default=""
)

# 父 thread_id（Team 模式授权继承），供 AuthorizedLocalShellBackend 读取。
# 与 current_thread_id 分离：backend 无法从 current_thread_id 推断父子关系。
current_parent_thread_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_parent_thread_id", default=None
)


def _safe_reset(var: "contextvars.ContextVar[str]", token: "contextvars.Token[str]") -> None:
    """Reset a ContextVar token, tolerating cross-Context restore failures.

    ``bind_agent_context`` is used inside async generators (``with ...: yield``).
    When the consumer drives ``asend``/``aclose`` from a different
    ``contextvars.Context`` (e.g. FastAPI/Starlette spins a fresh task to
    ``aclose`` a StreamingResponse generator after client disconnect), the
    ``finally`` runs in the new Context while the token was created in the
    original one. Python then raises
    ``ValueError: <Token ...> was created in a different Context``.

    In that case the original set() is scoped to the original Context and
    cannot leak into the current one, so skipping ``reset`` is safe.
    """
    try:
        var.reset(token)
    except ValueError:
        pass


@contextmanager
def bind_agent_context(
    thread_id: str, parent_thread_id: str | None = None
) -> Iterator[None]:
    """Bind ``current_thread_id`` and ``current_parent_thread_id`` for the lifetime
    of an agent run, guaranteeing ContextVar reset on every exit path.

    Covers normal completion, exception, ``asyncio.CancelledError``, and
    async-generator ``aclose()`` via a single ``finally`` that resets both
    tokens to their pre-bind values. When ``aclose`` happens in a different
    ``contextvars.Context`` (async-generator consumed across tasks), the
    ``ValueError`` from cross-Context reset is swallowed by ``_safe_reset``.

    Args:
        thread_id: The active session/thread ID for this run.
        parent_thread_id: Optional parent thread ID for Team-mode authorization
            inheritance. ``None`` clears the parent binding.
    """
    token_t = current_thread_id.set(thread_id)
    token_p = current_parent_thread_id.set(parent_thread_id)
    try:
        yield
    finally:
        _safe_reset(current_parent_thread_id, token_p)
        _safe_reset(current_thread_id, token_t)
