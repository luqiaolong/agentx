"""跨请求审批状态管理（TTL reaper + 原子原语）。

从 ``app.approval.state`` 迁移并修复两类问题：

1. **内存泄漏**：五个 dict 的 value 改为 ``tuple[T, float]``（float 为最后活动 timestamp），
   新增 ``_reaper_loop()`` 后台协程每 5 分钟清理 30 分钟无活动的 thread_id。
   ``start_reaper()`` 返回 ``asyncio.Task``，供 ``app.main`` lifespan 启动。

2. **pause 竞态**：新增 ``wait_for_resume(thread_id, timeout)`` 原子原语
   （锁内 check + event 获取，锁外 ``await event.wait()``），消除
   "check 后、await 前 clear_pause 已 set event" 的窗口。对称提供
   ``wait_for_abort(thread_id, timeout)``。

线程安全：所有公共 API 均受 ``_state_lock`` 保护。
"""

from __future__ import annotations

import asyncio
import time

from app.security.approval.decision import ApprovalResult

__all__ = [
    "submit_approval",
    "pop_approval",
    "set_abort",
    "is_aborted",
    "clear_abort",
    "wait_for_abort",
    "get_abort_event",
    "set_pause",
    "clear_pause",
    "is_paused",
    "get_pause_event",
    "wait_for_resume",
    "start_reaper",
]

# ---- 内部状态：value 统一为 tuple[T, float]（float = 最后活动 timestamp）----

# thread_id → (审批结果, timestamp)
_pending_approvals: dict[str, tuple[ApprovalResult, float]] = {}

# thread_id → (中止标志, timestamp)
_abort_flags: dict[str, tuple[bool, float]] = {}

# thread_id → (asyncio.Event, timestamp)
_abort_events: dict[str, tuple[asyncio.Event, float]] = {}

# thread_id → (暂停标志, timestamp)
_pause_flags: dict[str, tuple[bool, float]] = {}

# thread_id → (asyncio.Event, timestamp)
_pause_events: dict[str, tuple[asyncio.Event, float]] = {}

# 协程安全锁：保护所有 dict 操作
_state_lock = asyncio.Lock()

# reaper 配置
_REAPER_INTERVAL = 300.0  # 5 分钟
_REAPER_TTL = 1800.0  # 30 分钟无活动则清理


def _now() -> float:
    return time.monotonic()


def _all_thread_ids() -> set[str]:
    """收集所有 dict 中出现过的 thread_id。"""
    return (
        set(_pending_approvals)
        | set(_abort_flags)
        | set(_abort_events)
        | set(_pause_flags)
        | set(_pause_events)
    )


def _last_activity(thread_id: str) -> float:
    """返回 thread_id 在所有 dict 中的最新 timestamp（无任何条目时返回 0）。"""
    candidates: list[float] = []
    if thread_id in _pending_approvals:
        candidates.append(_pending_approvals[thread_id][1])
    if thread_id in _abort_flags:
        candidates.append(_abort_flags[thread_id][1])
    if thread_id in _abort_events:
        candidates.append(_abort_events[thread_id][1])
    if thread_id in _pause_flags:
        candidates.append(_pause_flags[thread_id][1])
    if thread_id in _pause_events:
        candidates.append(_pause_events[thread_id][1])
    return max(candidates) if candidates else 0.0


def _cleanup_thread(thread_id: str) -> None:
    """从所有 dict 中移除 thread_id（在 ``_state_lock`` 持有时调用）。"""
    _pending_approvals.pop(thread_id, None)
    _abort_flags.pop(thread_id, None)
    _abort_events.pop(thread_id, None)
    _pause_flags.pop(thread_id, None)
    _pause_events.pop(thread_id, None)


async def _reaper_loop() -> None:
    """后台协程：每 5 分钟清理 30 分钟无活动的 thread_id。

    清理策略：thread_id 在所有 5 个 dict 中的最新 timestamp 若早于
    ``now - _REAPER_TTL``，则从所有 dict 中移除。这避免清理仍有活跃等待者
    的 thread_id。
    """
    while True:
        await asyncio.sleep(_REAPER_INTERVAL)
        now = _now()
        stale: list[str] = []
        async with _state_lock:
            for thread_id in _all_thread_ids():
                if now - _last_activity(thread_id) > _REAPER_TTL:
                    stale.append(thread_id)
            for thread_id in stale:
                _cleanup_thread(thread_id)


def start_reaper() -> asyncio.Task:
    """启动 reaper 后台协程，返回 Task 供 lifespan 管理。

    幂等性：调用方应保存返回的 Task 并在 lifespan 结束时 cancel。
    重复调用会创建多个 reaper，调用方需自行避免。
    """
    return asyncio.create_task(_reaper_loop())


# ---- 审批决策 ----


async def submit_approval(thread_id: str, decision: ApprovalResult) -> None:
    """写入审批决策，供 ``wait_for_resume`` / 轮询消费者消费。"""
    async with _state_lock:
        _pending_approvals[thread_id] = (decision, _now())


async def pop_approval(thread_id: str) -> ApprovalResult | None:
    """取出并移除审批决策（单次消费）。无决策时返回 None。"""
    async with _state_lock:
        entry = _pending_approvals.pop(thread_id, None)
        return entry[0] if entry is not None else None


# ---- abort ----


async def set_abort(thread_id: str) -> None:
    """设置中止标志并触发对应 asyncio.Event。"""
    async with _state_lock:
        ts = _now()
        _abort_flags[thread_id] = (True, ts)
        entry = _abort_events.get(thread_id)
        if entry is None:
            event = asyncio.Event()
            _abort_events[thread_id] = (event, ts)
        else:
            event = entry[0]
            _abort_events[thread_id] = (event, ts)
        event.set()


async def is_aborted(thread_id: str) -> bool:
    """检查是否已设置中止标志。"""
    async with _state_lock:
        entry = _abort_flags.get(thread_id)
        return entry[0] if entry is not None else False


async def clear_abort(thread_id: str) -> None:
    """清除中止标志（消费后清理）。同时 set event 唤醒所有等待者。"""
    async with _state_lock:
        _abort_flags.pop(thread_id, None)
        entry = _abort_events.pop(thread_id, None)
        if entry is not None:
            entry[0].set()
        # 更新活动时间（通过临时插入再弹出无意义，直接记录即可）
        # 这里不重新插入，因为已清理；下次该 thread_id 有活动时会重新插入


async def wait_for_abort(thread_id: str, timeout: float) -> bool:
    """原子等待中止信号（锁内 check + event 获取，锁外 await）。

    Returns:
        - True：已中止（或等待期间被中止）。
        - False：超时未中止。
    """
    async with _state_lock:
        ts = _now()
        entry = _abort_flags.get(thread_id)
        if entry is not None and entry[0]:
            # 更新活动时间
            _abort_flags[thread_id] = (True, ts)
            return True
        # 获取或创建 event
        event_entry = _abort_events.get(thread_id)
        if event_entry is None:
            event = asyncio.Event()
            _abort_events[thread_id] = (event, ts)
        else:
            event = event_entry[0]
            _abort_events[thread_id] = (event, ts)
    # 锁外等待
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def get_abort_event(thread_id: str) -> asyncio.Event:
    """获取或创建 thread_id 对应的中止事件。

    流式生成器内部可通过 ``event.is_set()`` 即时检查中止，避免依赖轮询。
    获取即更新活动时间（event 通常用于后续 ``await``，应被 reaper 视为活跃）。
    """
    async with _state_lock:
        ts = _now()
        entry = _abort_events.get(thread_id)
        if entry is None:
            event = asyncio.Event()
            _abort_events[thread_id] = (event, ts)
            return event
        event = entry[0]
        _abort_events[thread_id] = (event, ts)
        return event


# ---- pause ----


async def set_pause(thread_id: str) -> None:
    """设置暂停标志。"""
    async with _state_lock:
        _pause_flags[thread_id] = (True, _now())


async def clear_pause(thread_id: str) -> None:
    """清除暂停标志并唤醒等待中的协程。"""
    async with _state_lock:
        _pause_flags.pop(thread_id, None)
        entry = _pause_events.pop(thread_id, None)
        if entry is not None:
            entry[0].set()
        # 记录活动：若 thread_id 仍存在于其他 dict，则已更新；
        # 否则该 thread_id 已无任何状态，无需保留


async def is_paused(thread_id: str) -> bool:
    """检查是否已设置暂停标志。"""
    async with _state_lock:
        entry = _pause_flags.get(thread_id)
        return entry[0] if entry is not None else False


async def wait_for_resume(thread_id: str, timeout: float | None = None) -> bool:
    """原子等待恢复信号（锁内 check + event 获取，锁外 await）。

    消除竞态：在 ``_state_lock`` 内检查 ``_pause_flags`` 并获取/创建
    ``_pause_events``，锁外 ``await event.wait()``。避免
    "check 后、await 前 clear_pause 已 set event 并 pop" 的窗口。

    额外加固：
    - 使用整体 deadline 控制最大等待时间。
    - 每轮等待后重新检查 ``is_paused``；若事件被 reaper 清理或收到
      虚假唤醒，可重新创建 event 继续等待，避免永久挂起。

    Args:
        thread_id: 会话 ID。
        timeout: 最大等待秒数；None 时一直等待直到恢复。

    Returns:
        - True：已恢复（或本就未暂停）。
        - False：超时未恢复。
    """
    deadline = None if timeout is None else asyncio.get_event_loop().time() + timeout
    poll_interval = 1.0

    while True:
        async with _state_lock:
            ts = _now()
            entry = _pause_flags.get(thread_id)
            if entry is None or not entry[0]:
                # 未暂停，直接返回
                return True
            # 已暂停：获取或创建 event
            event_entry = _pause_events.get(thread_id)
            if event_entry is None:
                event = asyncio.Event()
                _pause_events[thread_id] = (event, ts)
            else:
                event = event_entry[0]
                _pause_events[thread_id] = (event, ts)

        now = asyncio.get_event_loop().time()
        if deadline is not None and now >= deadline:
            return False
        wait_time = poll_interval if deadline is None else min(poll_interval, deadline - now)
        try:
            await asyncio.wait_for(event.wait(), timeout=wait_time)
        except asyncio.TimeoutError:
            pass


async def get_pause_event(thread_id: str) -> asyncio.Event:
    """获取或创建 thread_id 对应的暂停事件。

    DeepAgent 可通过 ``await event.wait()`` 阻塞直到 ``clear_pause`` 被调用。
    获取即更新活动时间（event 通常用于后续 ``await``，应被 reaper 视为活跃）。
    """
    async with _state_lock:
        ts = _now()
        entry = _pause_events.get(thread_id)
        if entry is None:
            event = asyncio.Event()
            _pause_events[thread_id] = (event, ts)
            return event
        event = entry[0]
        _pause_events[thread_id] = (event, ts)
        return event
