"""每线程 live run 生命周期管理（REQ-CHAT-1 / REQ-CHAT-2）。

为每个 ``thread_id`` 维护一个 ``ThreadRunSession`` 对象，统一管理 run_id、
generation、run state、cleanup 状态和活跃连接所有权。

核心约束（design.md D2）：

1. 同一线程任何时刻最多一个 live run
2. 新 run 启动前必须确认旧 run ``cleanup_complete=true``
3. ``compact`` / ``reset`` / ``abort`` / 新 ``chat`` 请求都通过同一个 session 协调
4. cleanup 完成前不得释放该线程对外可见的"可重入"状态

线程安全：所有公共 API 均受 ``_sessions_lock`` 保护。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

__all__ = [
    "RunState",
    "ThreadRunSession",
    "register_run",
    "get_active_run",
    "begin_cleanup",
    "complete_run_cleanup",
    "wait_for_run_cleanup",
]


class RunState(str, Enum):
    """Run 生命周期状态。"""

    STARTING = "starting"
    STREAMING = "streaming"
    WAITING_APPROVAL = "waiting_approval"
    CLEANING_UP = "cleaning_up"
    FINISHED = "finished"


@dataclass
class ThreadRunSession:
    """每线程 live run 生命周期对象（design.md D2）。

    Attributes:
        thread_id: 会话 ID。
        run_id: 本次 run 的唯一 ID（= trace_id）。
        generation: 单调递增的 generation 编号，用于区分同线程前后 run。
        state: 当前 run 状态。
        owner_connection_id: 持有该 run 的 SSE 连接 ID（可选）。
        active_approval_id: 当前活跃审批请求 ID（可选）。
        abort_requested: 是否已请求中止。
        pause_requested: 是否已请求暂停。
        cleanup_complete: cleanup 是否已完成。
        cleanup_event: cleanup 完成信号，供 ``wait_for_run_cleanup`` 等待。
    """

    thread_id: str
    run_id: str
    generation: int
    state: RunState = RunState.STARTING
    owner_connection_id: str | None = None
    active_approval_id: str | None = None
    abort_requested: bool = False
    pause_requested: bool = False
    cleanup_complete: bool = False
    cleanup_event: asyncio.Event = field(default_factory=asyncio.Event)


# ---- 内部状态 ----

# thread_id → 活跃 ThreadRunSession（同一线程最多一个）
_thread_run_sessions: dict[str, ThreadRunSession] = {}

# thread_id → 上一次 generation 编号（用于递增）
_run_generation: dict[str, int] = {}

# 协程安全锁：保护所有注册表操作
_sessions_lock = asyncio.Lock()


def _next_generation(thread_id: str) -> int:
    """获取 thread_id 的下一个 generation 编号（单调递增）。"""
    current = _run_generation.get(thread_id, 0)
    next_gen = current + 1
    _run_generation[thread_id] = next_gen
    return next_gen


async def register_run(
    thread_id: str,
    run_id: str | None = None,
    *,
    wait_for_cleanup: bool = False,
    timeout: float | None = None,
) -> ThreadRunSession | None:
    """注册新的 run session（REQ-CHAT-1）。

    同一线程任何时刻最多一个 live run。若已有活跃 run：

    - ``wait_for_cleanup=False``（默认）：立即返回 None，表示注册失败。
    - ``wait_for_cleanup=True``：等待旧 run cleanup 完成（或超时）后再注册。
      超时返回 None。

    Args:
        thread_id: 会话 ID。
        run_id: run 唯一 ID。None 时自动生成 UUID4。
        wait_for_cleanup: 已有活跃 run 时是否等待其 cleanup 完成。
        timeout: 等待 cleanup 的超时秒数。None 时无限等待。

    Returns:
        新建的 ``ThreadRunSession``；若已有活跃 run 且未等待/超时则返回 None。
    """
    rid = run_id or str(uuid4())

    # 先检查是否有活跃 run
    existing = await get_active_run(thread_id)
    if existing is not None:
        if not wait_for_cleanup:
            return None
        # 等待旧 run cleanup
        ok = await wait_for_run_cleanup(thread_id, timeout=timeout)
        if not ok:
            return None

    async with _sessions_lock:
        # double-check：等待期间可能已被另一个协程注册
        existing = _thread_run_sessions.get(thread_id)
        if existing is not None and not existing.cleanup_complete:
            return None
        session = ThreadRunSession(
            thread_id=thread_id,
            run_id=rid,
            generation=_next_generation(thread_id),
            state=RunState.STARTING,
        )
        _thread_run_sessions[thread_id] = session
        return session


async def get_active_run(thread_id: str) -> ThreadRunSession | None:
    """获取 thread_id 的活跃 run session（未 cleanup 的）。

    Args:
        thread_id: 会话 ID。

    Returns:
        活跃的 ``ThreadRunSession``；无活跃 run 时返回 None。
    """
    async with _sessions_lock:
        session = _thread_run_sessions.get(thread_id)
        if session is None or session.cleanup_complete:
            return None
        return session


async def begin_cleanup(thread_id: str, run_id: str) -> ThreadRunSession | None:
    """将 run 状态转为 cleaning_up（REQ-CHAT-2）。

    必须验证 ``run_id`` 匹配，确保 cleanup 只作用于正确的 run。

    Args:
        thread_id: 会话 ID。
        run_id: 要 cleanup 的 run ID。

    Returns:
        转入 cleaning_up 状态的 session；run_id 不匹配或无活跃 run 时返回 None。
    """
    async with _sessions_lock:
        session = _thread_run_sessions.get(thread_id)
        if session is None or session.cleanup_complete:
            return None
        if session.run_id != run_id:
            return None
        session.state = RunState.CLEANING_UP
        return session


async def complete_run_cleanup(thread_id: str, run_id: str) -> bool:
    """标记 run cleanup 完成（REQ-CHAT-2）。

    必须验证 ``run_id`` 匹配。完成后从活跃注册表移除，并唤醒所有等待者。

    Args:
        thread_id: 会话 ID。
        run_id: 已完成 cleanup 的 run ID。

    Returns:
        True 成功；False 表示 run_id 不匹配或无活跃 run。
    """
    async with _sessions_lock:
        session = _thread_run_sessions.get(thread_id)
        if session is None or session.run_id != run_id:
            return False
        if session.cleanup_complete:
            return False
        session.cleanup_complete = True
        session.state = RunState.FINISHED
        # 唤醒所有等待者
        session.cleanup_event.set()
        # 从活跃注册表移除（保留 generation 记录供下次递增）
        _thread_run_sessions.pop(thread_id, None)
        return True


async def wait_for_run_cleanup(
    thread_id: str, timeout: float | None = None
) -> bool:
    """等待 thread_id 的活跃 run cleanup 完成。

    无活跃 run 时立即返回 True。

    Args:
        thread_id: 会话 ID。
        timeout: 最大等待秒数。None 时无限等待。

    Returns:
        True 表示 cleanup 已完成（或本就无活跃 run）；False 表示超时。
    """
    # 锁内获取 session 引用和 event
    async with _sessions_lock:
        session = _thread_run_sessions.get(thread_id)
        if session is None or session.cleanup_complete:
            return True
        event = session.cleanup_event

    # 锁外等待 event
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
