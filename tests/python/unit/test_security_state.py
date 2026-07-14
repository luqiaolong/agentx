"""审批状态机单元测试：TTL reaper + wait_for_resume 竞态 + wait_for_abort。

覆盖：
1. submit/pop_approval 基本流程
2. abort: set/is/clear/wait_for_abort
3. pause: set/clear/wait_for_resume（含竞态消除）
4. TTL reaper 清理 30 分钟无活动的 thread_id
5. start_reaper 返回可 cancel 的 Task
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.security.approval import (
    ApprovalDecision,
    ApprovalResult,
    clear_abort,
    clear_pause,
    is_aborted,
    pop_approval,
    set_abort,
    set_pause,
    start_reaper,
    wait_for_abort,
    wait_for_resume,
    write_approval_decision,
)
from app.security.approval import state as security_state


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    """每个测试前清理全局审批状态。"""
    security_state._pending_approvals.clear()
    security_state._abort_flags.clear()
    security_state._abort_events.clear()
    security_state._pause_flags.clear()
    security_state._pause_events.clear()
    security_state._active_approval_requests.clear()
    yield
    security_state._pending_approvals.clear()
    security_state._abort_flags.clear()
    security_state._abort_events.clear()
    security_state._pause_flags.clear()
    security_state._pause_events.clear()
    security_state._active_approval_requests.clear()


# ============================================================
# 1. submit/pop_approval
# ============================================================


async def test_submit_and_pop_approval() -> None:
    """submit 写入决策，pop 取出并移除。"""
    decision = ApprovalResult(decision=ApprovalDecision.APPROVE)
    await write_approval_decision("t1", decision)
    result = await pop_approval("t1")
    assert result is decision
    # 二次 pop 返回 None
    assert await pop_approval("t1") is None


async def test_pop_approval_nonexistent() -> None:
    """不存在的 thread_id pop 返回 None。"""
    assert await pop_approval("unknown") is None


# ============================================================
# 2. abort
# ============================================================


async def test_set_is_clear_abort() -> None:
    """set → is_aborted=True → clear → is_aborted=False。"""
    assert await is_aborted("t1") is False
    await set_abort("t1")
    assert await is_aborted("t1") is True
    await clear_abort("t1")
    assert await is_aborted("t1") is False


async def test_wait_for_abort_already_aborted() -> None:
    """已 abort 时 wait_for_abort 立即返回 True。"""
    await set_abort("t1")
    result = await wait_for_abort("t1", timeout=0.5)
    assert result is True


async def test_wait_for_abort_timeout() -> None:
    """未 abort 时 wait_for_abort 超时返回 False。"""
    result = await wait_for_abort("t1", timeout=0.1)
    assert result is False


async def test_wait_for_abort_triggered_during_wait() -> None:
    """等待期间被 abort 后立即返回 True。"""
    async def _abort_after_delay() -> None:
        await asyncio.sleep(0.1)
        await set_abort("t1")

    task = asyncio.create_task(_abort_after_delay())
    result = await wait_for_abort("t1", timeout=2.0)
    await task
    assert result is True


# ============================================================
# 3. pause + wait_for_resume（竞态消除）
# ============================================================


async def test_set_and_clear_pause() -> None:
    """set_pause → wait_for_resume 阻塞 → clear_pause → 恢复。"""
    await set_pause("t1")

    async def _resume_after_delay() -> None:
        await asyncio.sleep(0.1)
        await clear_pause("t1")

    task = asyncio.create_task(_resume_after_delay())
    result = await wait_for_resume("t1", timeout=2.0)
    await task
    assert result is True


async def test_wait_for_resume_not_paused_returns_immediately() -> None:
    """未暂停时 wait_for_resume 立即返回 True。"""
    result = await wait_for_resume("t1", timeout=0.5)
    assert result is True


async def test_wait_for_resume_timeout() -> None:
    """暂停后超时未恢复返回 False。"""
    await set_pause("t1")
    result = await wait_for_resume("t1", timeout=0.1)
    assert result is False


async def test_wait_for_resume_recovers_when_event_evicted() -> None:
    """wait_for_resume 在 reaper 清理 event 后仍能正确恢复。

    场景：reaper 在 waiter 等待期间把 _pause_events 清理掉，但 pause flag 仍
    为 True。随后 clear_pause 清除 flag（此时 dict 中已没有 event 可 set）。
    旧实现若事件引用丢失会永久挂起；新实现通过轮询重新创建 event 并在 pause
    解除后返回 True。
    """
    await set_pause("t1")

    async def _evict_and_clear() -> None:
        # 等 waiter 进入等待
        await asyncio.sleep(0.2)
        # 模拟 reaper 只清理 event，不清理 flag
        async with security_state._state_lock:
            security_state._pause_events.pop("t1", None)
        await asyncio.sleep(0.2)
        # 恢复：此时 dict 中没有 event，waiter 应通过轮询检测到 flag 清除
        await clear_pause("t1")

    task = asyncio.create_task(_evict_and_clear())
    result = await wait_for_resume("t1", timeout=2.0)
    await task
    assert result is True


async def test_wait_for_resume_no_race_deadlock() -> None:
    """验证 wait_for_resume 无竞态死锁。

    场景：协程 A 调用 wait_for_resume，协程 B 在 A check 后、A await event.wait() 前
    调用 clear_pause（set event + pop event）。

    旧实现（check + get_event 分离）可能因 event 被 pop 导致 A 永远阻塞。
    新实现（锁内 check + event 获取）保证 A 拿到 event 引用后再 await，
    即使 B pop 了 dict 中的 event，A 持有的引用仍可被 set 唤醒。
    """
    await set_pause("t1")

    # 用 barrier 确保 A 先进入 wait_for_resume（拿到 event 引用）
    barrier = asyncio.Event()

    original_wait_for_resume = security_state.wait_for_resume

    async def _patched_wait(thread_id: str, timeout: float) -> bool:
        # 调用原始函数，但在锁释放后、await 前通知 barrier
        # 通过 monkeypatch _state_lock 来注入钩子较复杂，
        # 这里用更简单的方式：直接测试端到端行为
        return await original_wait_for_resume(thread_id, timeout)

    async def _waiter() -> bool:
        result = await _patched_wait("t1", timeout=2.0)
        barrier.set()
        return result

    async def _resumer() -> None:
        # 等一小会让 _waiter 进入 await event.wait()
        await asyncio.sleep(0.05)
        await clear_pause("t1")

    waiter_task = asyncio.create_task(_waiter())
    resumer_task = asyncio.create_task(_resumer())

    result = await asyncio.wait_for(waiter_task, timeout=3.0)
    await resumer_task

    # 应在超时前恢复，无死锁
    assert result is True


async def test_concurrent_wait_for_resume_all_wake_on_clear() -> None:
    """多个协程同时 wait_for_resume，clear_pause 后全部唤醒。"""
    await set_pause("t1")

    async def _waiter() -> bool:
        return await wait_for_resume("t1", timeout=2.0)

    tasks = [asyncio.create_task(_waiter()) for _ in range(3)]
    await asyncio.sleep(0.05)
    await clear_pause("t1")

    results = await asyncio.gather(*tasks)
    assert all(r is True for r in results)


# ============================================================
# 4. TTL reaper
# ============================================================


async def test_reaper_cleans_stale_thread_ids() -> None:
    """reaper 清理 30 分钟无活动的 thread_id。"""
    # 写入一个审批决策
    await write_approval_decision("stale", ApprovalResult(decision=ApprovalDecision.APPROVE))
    # 篡改 timestamp 为 35 分钟前
    stale_ts = time.monotonic() - 35 * 60
    security_state._pending_approvals["stale"] = (
        security_state._pending_approvals["stale"][0],
        stale_ts,
    )

    # 手动触发 reaper 一次（不等 5 分钟）
    # 直接调用内部清理逻辑
    now = security_state._now()
    stale_ids = []
    async with security_state._state_lock:
        for tid in security_state._all_thread_ids():
            if now - security_state._last_activity(tid) > security_state._REAPER_TTL:
                stale_ids.append(tid)
        for tid in stale_ids:
            security_state._cleanup_thread(tid)

    assert "stale" not in security_state._pending_approvals
    assert "stale" not in security_state._abort_flags
    assert "stale" not in security_state._pause_flags


async def test_reaper_preserves_active_thread_ids() -> None:
    """reaper 不清理近期有活动的 thread_id。"""
    await write_approval_decision("active", ApprovalResult(decision=ApprovalDecision.APPROVE))
    # active 的 timestamp 是现在，不应被清理

    now = security_state._now()
    stale_ids = []
    async with security_state._state_lock:
        for tid in security_state._all_thread_ids():
            if now - security_state._last_activity(tid) > security_state._REAPER_TTL:
                stale_ids.append(tid)
        for tid in stale_ids:
            security_state._cleanup_thread(tid)

    assert "active" in security_state._pending_approvals


async def test_start_reaper_returns_task() -> None:
    """start_reaper 返回可 cancel 的 Task。"""
    task = start_reaper()
    assert isinstance(task, asyncio.Task)
    assert not task.done()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done()


async def test_reaper_preserves_thread_with_mixed_activity() -> None:
    """thread_id 在多个 dict 中有条目时，只要有一个 dict 的 timestamp 较新就不清理。"""
    await write_approval_decision("mixed", ApprovalResult(decision=ApprovalDecision.APPROVE))
    # 篡改 _pending_approvals 的 timestamp 为旧，但 _abort_flags 为新
    stale_ts = time.monotonic() - 35 * 60
    security_state._pending_approvals["mixed"] = (
        security_state._pending_approvals["mixed"][0],
        stale_ts,
    )
    await set_abort("mixed")  # 新 timestamp

    now = security_state._now()
    stale_ids = []
    async with security_state._state_lock:
        for tid in security_state._all_thread_ids():
            if now - security_state._last_activity(tid) > security_state._REAPER_TTL:
                stale_ids.append(tid)
        for tid in stale_ids:
            security_state._cleanup_thread(tid)

    # mixed 在 _abort_flags 中有新 timestamp，不应被清理
    assert "mixed" in security_state._abort_flags
    assert "mixed" in security_state._pending_approvals  # 也保留（因为整体仍活跃）
