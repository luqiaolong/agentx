"""contextvar 传递 thread_id，供 AuthorizedLocalShellBackend 读取。"""

from __future__ import annotations

import contextvars

current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "thread_id", default=""
)
