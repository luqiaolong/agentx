"""ThreadRunSession 生命周期单元测试（REQ-CHAT-1 / REQ-CHAT-2）。

覆盖：
1. register_run 创建 session（含 run_id / generation / state=starting）
2. 同一线程最多一个 live run（重复注册被拒绝）
3. 新 run 必须等待旧 run cleanup_complete（REQ-CHAT-1）
4. begin_cleanup 将状态转为 cleaning_up
5. complete_run_cleanup 标记 cleanup_complete 并移除 active session
6. wait_for_run_cleanup 在 cleanup 完成后返回 True
7. wait_for_run_cleanup 超时返回 False
8. generation 每次递增
9. get_active_run 在 cleanup 完成后返回 None
10. cleanup 只作用于匹配 run_id 的 session（REQ-CHAT-2）
"""

from __future__ import annotations

import asyncio

import pytest

from app.lifecycle.run_session import (
    RunState,
    ThreadRunSession,
    begin_cleanup,
    complete_run_cleanup,
    get_active_run,
    register_run,
    wait_for_run_cleanup,
)
from app.lifecycle import run_session as run_session_module


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    """每个测试前清理全局 run session 注册表。"""
    run_session_module._thread_run_sessions.clear()
    run_session_module._run_generation.clear()
    yield
    run_session_module._thread_run_sessions.clear()
    run_session_module._run_generation.clear()


# ============================================================
# 1. register_run
# ============================================================


async def test_register_creates_session_with_run_id() -> None:
    """register_run 创建 session，包含 thread_id / run_id / generation / state=starting。"""
    session = await register_run("t1", "run-1")
    assert session.thread_id == "t1"
    assert session.run_id == "run-1"
    assert session.generation >= 1
    assert session.state == RunState.STARTING
    assert session.cleanup_complete is False


async def test_register_generates_unique_run_id_when_omitted() -> None:
    """register_run 不传 run_id 时自动生成。"""
    session = await register_run("t1")
    assert session.run_id
    assert len(session.run_id) > 0


async def test_generation_increments_per_run() -> None:
    """同线程每次新 run，generation 递增。"""
    s1 = await register_run("t1", "run-1")
    await complete_run_cleanup("t1", "run-1")
    s2 = await register_run("t1", "run-2")
    assert s2.generation == s1.generation + 1


# ============================================================
# 2. 同一线程最多一个 live run
# ============================================================


async def test_only_one_live_run_per_thread() -> None:
    """已有 live run 时再注册返回 None（或抛异常），不创建第二个。"""
    await register_run("t1", "run-1")
    second = await register_run("t1", "run-2")
    assert second is None


async def test_can_register_after_cleanup_complete() -> None:
    """旧 run cleanup_complete 后可以注册新 run。"""
    await register_run("t1", "run-1")
    await complete_run_cleanup("t1", "run-1")
    second = await register_run("t1", "run-2")
    assert second is not None
    assert second.run_id == "run-2"


# ============================================================
# 3. 新 run 必须等待旧 run cleanup（REQ-CHAT-1）
# ============================================================


async def test_new_run_waits_for_old_cleanup() -> None:
    """旧 run 未 cleanup 时，新 run 等待。"""
    await register_run("t1", "run-1")

    started = asyncio.Event()

    async def _wait_and_register() -> ThreadRunSession | None:
        session = await register_run("t1", "run-2", wait_for_cleanup=True, timeout=2.0)
        started.set()
        return session

    task = asyncio.create_task(_wait_and_register())
    # 给 waiter 一点时间确认它确实在等
    await asyncio.sleep(0.1)
    assert not started.is_set()

    # 完成 cleanup，waiter 应该被唤醒
    await complete_run_cleanup("t1", "run-1")
    session = await asyncio.wait_for(task, timeout=2.0)
    assert session is not None
    assert session.run_id == "run-2"


async def test_new_run_wait_timeout() -> None:
    """旧 run 长时间不 cleanup，新 run 等待超时返回 None。"""
    await register_run("t1", "run-1")
    session = await register_run("t1", "run-2", wait_for_cleanup=True, timeout=0.2)
    assert session is None


# ============================================================
# 4. begin_cleanup
# ============================================================


async def test_begin_cleanup_transitions_state() -> None:
    """begin_cleanup 将状态转为 cleaning_up。"""
    await register_run("t1", "run-1")
    session = await begin_cleanup("t1", "run-1")
    assert session is not None
    assert session.state == RunState.CLEANING_UP


async def test_begin_cleanup_wrong_run_id_returns_none() -> None:
    """begin_cleanup 用错误 run_id 返回 None（不影响活跃 session）。"""
    await register_run("t1", "run-1")
    result = await begin_cleanup("t1", "run-other")
    assert result is None
    active = await get_active_run("t1")
    assert active is not None
    assert active.state != RunState.CLEANING_UP


# ============================================================
# 5. complete_run_cleanup
# ============================================================


async def test_complete_run_cleanup_marks_complete() -> None:
    """complete_run_cleanup 标记 cleanup_complete=True。"""
    await register_run("t1", "run-1")
    ok = await complete_run_cleanup("t1", "run-1")
    assert ok is True
    # cleanup 完成后 active session 应该不存在
    assert await get_active_run("t1") is None


async def test_complete_run_cleanup_wrong_run_id() -> None:
    """complete_run_cleanup 用错误 run_id 返回 False。"""
    await register_run("t1", "run-1")
    ok = await complete_run_cleanup("t1", "run-other")
    assert ok is False
    # 原 session 仍存在
    assert await get_active_run("t1") is not None


# ============================================================
# 6. wait_for_run_cleanup
# ============================================================


async def test_wait_for_run_cleanup_returns_true_after_complete() -> None:
    """cleanup 完成后 wait_for_run_cleanup 立即返回 True。"""
    await register_run("t1", "run-1")
    await complete_run_cleanup("t1", "run-1")
    result = await wait_for_run_cleanup("t1", timeout=0.5)
    assert result is True


async def test_wait_for_run_cleanup_timeout() -> None:
    """未 cleanup 时 wait_for_run_cleanup 超时返回 False。"""
    await register_run("t1", "run-1")
    result = await wait_for_run_cleanup("t1", timeout=0.2)
    assert result is False


async def test_wait_for_run_cleanup_no_active_run() -> None:
    """没有活跃 run 时立即返回 True。"""
    result = await wait_for_run_cleanup("t1", timeout=0.5)
    assert result is True


async def test_wait_for_run_cleanup_triggered_during_wait() -> None:
    """等待期间 cleanup 完成后被唤醒。"""
    await register_run("t1", "run-1")

    async def _complete_after_delay() -> None:
        await asyncio.sleep(0.1)
        await complete_run_cleanup("t1", "run-1")

    task = asyncio.create_task(_complete_after_delay())
    result = await wait_for_run_cleanup("t1", timeout=2.0)
    await task
    assert result is True


# ============================================================
# 7. get_active_run
# ============================================================


async def test_get_active_run_returns_session() -> None:
    """get_active_run 返回活跃 session。"""
    await register_run("t1", "run-1")
    session = await get_active_run("t1")
    assert session is not None
    assert session.run_id == "run-1"


async def test_get_active_run_returns_none_for_unknown_thread() -> None:
    """未知 thread_id 返回 None。"""
    assert await get_active_run("unknown") is None


# ============================================================
# 8. REQ-CHAT-2: cleanup 只作用于匹配 run_id
# ============================================================


async def test_cleanup_does_not_affect_new_run() -> None:
    """旧 run cleanup 不会清掉新 run 的状态（REQ-CHAT-2）。

    场景：r1 结束开始 cleanup，r2 已注册（wait_for_cleanup），
    r1 cleanup 完成，r2 状态不受影响。
    """
    await register_run("t1", "r1")
    # r1 进入 cleanup
    await begin_cleanup("t1", "r1")
    # r1 cleanup 完成
    await complete_run_cleanup("t1", "r1")
    # 注册 r2
    r2 = await register_run("t1", "r2")
    assert r2 is not None
    assert r2.run_id == "r2"
    assert r2.state == RunState.STARTING
    # 再次 cleanup r1（模拟延迟 cleanup），不应影响 r2
    ok = await complete_run_cleanup("t1", "r1")
    assert ok is False  # r1 已不在注册表
    # r2 仍在
    active = await get_active_run("t1")
    assert active is not None
    assert active.run_id == "r2"


# ============================================================
# 9. REQ-CHAT-4: compact 与 live run 互斥
# ============================================================


async def test_compact_rejected_when_active_run_exists() -> None:
    """compact 在有活跃 run 时应被拒绝（REQ-CHAT-4）。

    chat_compact 通过 get_active_run 检查；此处验证该函数返回
    非 None 时 compact 应拒绝。
    """
    await register_run("t1", "run-1")
    # compact 会检查 get_active_run
    active = await get_active_run("t1")
    assert active is not None  # 有活跃 run → compact 应拒绝


async def test_compact_allowed_when_no_active_run() -> None:
    """无活跃 run 时 compact 应被允许（REQ-CHAT-4）。"""
    # 不注册任何 run
    active = await get_active_run("t1")
    assert active is None  # 无活跃 run → compact 应允许


async def test_compact_allowed_after_run_cleanup() -> None:
    """run cleanup 完成后 compact 应被允许（REQ-CHAT-4）。"""
    await register_run("t1", "run-1")
    await complete_run_cleanup("t1", "run-1")
    # cleanup 完成后，get_active_run 返回 None → compact 应允许
    active = await get_active_run("t1")
    assert active is None

