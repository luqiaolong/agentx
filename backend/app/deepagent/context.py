"""contextvar 传递 thread_id，供 AuthorizedLocalShellBackend 读取。"""

from __future__ import annotations

import contextvars

current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "thread_id", default=""
)

# 父 thread_id（Team 模式授权继承），供 AuthorizedLocalShellBackend 读取。
# 与 current_thread_id 分离：backend 无法从 current_thread_id 推断父子关系。
current_parent_thread_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_parent_thread_id", default=None
)
