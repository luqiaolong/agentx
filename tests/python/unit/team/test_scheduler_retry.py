"""scheduler retry + semaphore 测试。"""
import asyncio

import pytest

from app.team.blackboard import TeamSubtaskResult
from app.team.scheduler import _get_team_semaphore, acquire_and_run, run_with_retry
from app.team.state import TeamTask


@pytest.mark.asyncio
async def test_run_with_retry_retries_on_transient_with_thread_id():
    """thread_id 非空时，瞬态错误应触发重试。"""
    import httpx

    call_count = 0

    async def flaky_runner():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ConnectError("transient")
        return TeamSubtaskResult(agent="code", success=True, payload="ok")

    def runner_factory():
        return flaky_runner()

    task = TeamTask(id="t1", agent="code", description="test")
    result = await run_with_retry(
        task=task,
        runner_factory=runner_factory,
        max_retries=3,
        thread_id="thread-1",
        abort_event=asyncio.Event(),
    )

    assert result.success is True
    assert call_count == 3, f"应重试 3 次，实际调用 {call_count} 次"
    assert result.retries == 2


@pytest.mark.asyncio
async def test_acquire_and_run_no_semaphore_leak_on_ensure_future_failure():
    """ensure_future 失败时 semaphore 应被释放。

    BE-I 修复：ensure_future 在 try 块内，失败时 finally 释放 semaphore。
    传入非 coroutine 对象（None）触发 ensure_future TypeError。
    """
    semaphore = _get_team_semaphore()
    initial = semaphore._value

    task = TeamTask(id="t1", agent="code", description="test")

    # ensure_future(None) 抛 TypeError（非 coroutine/future/awaitable）
    # 修复前：TypeError 在 try 外传播，semaphore 泄漏
    # 修复后：TypeError 在 try 内被捕获，finally 释放 semaphore
    result = await acquire_and_run(
        task=task,
        runner_coro=None,  # type: ignore[arg-type]
        thread_id="t1",
    )

    # 应返回失败 result（不 raise）
    assert result.success is False
    # semaphore 应被释放回初始值
    assert semaphore._value == initial, "semaphore 泄漏"
