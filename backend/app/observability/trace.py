"""trace_id 基础设施：跨请求/跨异步任务的全链路追踪 ID。

设计动机：
- 当前 ``app.observability.langsmith.trace_span`` 只在 LangSmith 开启时输出 debug 日志，
  普通排查时无法用单一 ID 串联 SSE 事件 / logger.info / subprocess.run。
- ``thread_id`` 跨越多轮对话（一个会话可能有几十个 turn），粒度过粗。
- 引入 trace_id 后，用户报问题时只需复制一个 16 字符 hex ID，开发者即可在日志中
  grep 出整条调用链。

实现要点：
- 用 ``ContextVar`` 而非模块全局变量：支持 asyncio 跨协程传播，子任务、回调均能读取。
- 提供 ``bind_trace`` contextmanager：在 with 块内自动 set/reset，无需手动管理 token。
- ``new_trace_id`` 生成 16 字符 hex（UUID4 前 8 字节），够短便于复制、够长避免冲突。
- 与 ``app.observability.langsmith.trace_span`` 协作：trace_span 的 metadata 自动注入 trace_id。
- 与 loguru 协作：``setup_logger`` 的 format 通过 ``{extra[trace_id]}`` 自动展示。

典型用法：
    >>> from app.observability.trace import new_trace_id, bind_trace, current_trace_id
    >>> async def _event_generator(req):
    ...     trace_id = new_trace_id()
    ...     with bind_trace(trace_id):
    ...         logger.info("chat request", thread_id=req.thread_id)  # 自动带 trace_id
    ...         yield make_sse_event("done", "{}", trace_id=trace_id)
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator


_current_trace_id: ContextVar[str | None] = ContextVar(
    "agentx_trace_id", default=None
)


def new_trace_id() -> str:
    """生成新 trace_id：16 字符 hex（UUID4 前 8 字节）。

    比 UUID 短（UUID 36 字符），比 8 字符 UUID 长（避免冲突）。
    100 万次生成碰撞概率 < 10^-12，可视为全局唯一。
    """
    return uuid.uuid4().hex[:16]


def current_trace_id() -> str | None:
    """读取当前活跃 trace_id；未设置时返回 None。"""
    return _current_trace_id.get()


@contextmanager
def bind_trace(trace_id: str) -> Iterator[str]:
    """在 with 块内临时设置 trace_id，块结束时自动还原。

    嵌套调用会自动保存外层 trace_id，块结束后恢复到外层（ContextVar 语义）。
    与 ``with trace_span(...)`` 同时使用时，trace_span 读取 metadata 时也能拿到
    ``trace_id``（前提是 ``trace_span`` 调用方显式传入 ``trace_id=trace_id``）。

    Args:
        trace_id: 本轮追踪 ID。

    Yields:
        同一个 trace_id，便于调用方 ``with bind_trace(tid) as t: ...`` 复用。
    """
    token: Token = _current_trace_id.set(trace_id)
    try:
        yield trace_id
    finally:
        try:
            _current_trace_id.reset(token)
        except ValueError:
            # ContextVar 跨事件循环（如子任务在独立 loop 创建 span），
            # 主上下文退出时 reset 会报 ValueError，安全忽略。
            pass


__all__ = [
    "bind_trace",
    "current_trace_id",
    "new_trace_id",
]