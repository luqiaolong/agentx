"""AgentTeam v2 限流 / abort / retry 单元测试（T8 / T16-T18）。

覆盖 spec scenario：

并发限流（Requirement: 并发限流）：

- ``test_acquire_and_run_basic``：``acquire_and_run`` 正常执行返回 result
- ``test_semaphore_max_concurrency``：10 子任务 + max=5，验证最多 5 并发
- ``test_semaphore_release_on_complete``：完成后释放 semaphore
- ``test_semaphore_release_on_exception``：异常时也释放

abort cancel（Requirement: abort 响应 LLM 长调用）：

- ``test_abort_cancel_returns_aborted``：abort_event 触发后返回 "用户中止"
- ``test_abort_releases_semaphore``：abort 后 semaphore 释放
- ``test_cancel_running_tasks_clears_registry``：cancel 清空 registry

retry（Requirement: 失败重试）：

- ``test_retry_transient_timeout``：TimeoutError 重试
- ``test_retry_network_error``：ConnectError 重试
- ``test_retry_no_retry_on_4xx``：4xx 不重试
- ``test_retry_no_retry_on_value_error``：ValueError 不重试
- ``test_retry_no_retry_on_cancelled``：CancelledError 不重试（raise）
- ``test_retry_backoff``：退避 1s/2s/4s（mock sleep）
- ``test_retry_records_retries_field``：retries 字段
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.team.blackboard import TeamSubtaskResult
from app.team.scheduler import (
    TRANSIENT_ERRORS,
    _get_team_semaphore,
    _is_transient,
    _running_tasks,
    acquire_and_run,
    cancel_running_tasks,
    register_running_task,
    reset_team_semaphore,
    run_with_retry,
)
from app.team.state import TeamTask


# ============================================================
# 公共 helpers
# ============================================================


def _task(
    task_id: str = "t1",
    agent: str = "code",
    description: str = "do something",
) -> TeamTask:
    """构造 TeamTask（便于测试）。"""
    return TeamTask(id=task_id, agent=agent, description=description)


def _reset_semaphore_cache(max_concurrency: int = 5) -> asyncio.Semaphore:
    """重置 ``_get_team_semaphore`` 并注入新 semaphore。

    BE-M 修复后 ``_get_team_semaphore`` 移除 ``lru_cache``，改用模块级变量 +
    配置版本比对。测试通过 ``reset_team_semaphore()`` 清空缓存，再 patch
    ``get_settings`` 返回带新 ``team_max_concurrency`` 的 mock 重建 semaphore。
    """
    reset_team_semaphore()
    # 通过 patch get_settings 返回带 team_max_concurrency 的 mock
    mock_settings = MagicMock()
    mock_settings.team_max_concurrency = max_concurrency
    with patch("app.team.scheduler.get_settings", return_value=mock_settings):
        sem = _get_team_semaphore()
    return sem


def _reset_registry() -> None:
    """清空全局 running tasks registry，避免用例间污染。"""
    _running_tasks.clear()


@pytest.fixture(autouse=True)
def _isolate_registry_and_semaphore():
    """每个用例前后清理全局状态（registry + semaphore cache）。"""
    _reset_registry()
    reset_team_semaphore()
    yield
    _reset_registry()
    reset_team_semaphore()


# ============================================================
# acquire_and_run：基础 + 限流 + 释放
# ============================================================


@pytest.mark.asyncio
async def test_acquire_and_run_basic() -> None:
    """``acquire_and_run`` 正常执行返回 runner 的 result。"""
    expected = TeamSubtaskResult(agent="code", success=True, payload="done")
    task = _task()
    sem = _reset_semaphore_cache(max_concurrency=5)

    async def _runner() -> TeamSubtaskResult:
        return expected

    result = await acquire_and_run(task, _runner(), thread_id="th-1")
    assert result.success is True
    assert result.payload == "done"
    assert result.agent == "code"
    # semaphore 已释放（可用值恢复到 max）
    assert sem._value == 5  # type: ignore[attr-defined]
    # registry 已清理
    assert _running_tasks.get("th-1") in (None, [])


@pytest.mark.asyncio
async def test_semaphore_max_concurrency() -> None:
    """10 子任务 + max=5，验证任意时刻最多 5 个并发执行。"""
    _reset_semaphore_cache(max_concurrency=5)
    task = _task()
    current_concurrent = 0
    max_observed = 0
    lock = asyncio.Lock()

    async def _runner() -> TeamSubtaskResult:
        nonlocal current_concurrent, max_observed
        async with lock:
            current_concurrent += 1
            max_observed = max(max_observed, current_concurrent)
        await asyncio.sleep(0.05)  # 模拟 LLM 调用
        async with lock:
            current_concurrent -= 1
        return TeamSubtaskResult(agent="code", success=True, payload="ok")

    # 并发 10 个 acquire_and_run
    coros = [acquire_and_run(task, _runner(), thread_id="th-1") for _ in range(10)]
    results = await asyncio.gather(*coros)

    assert len(results) == 10
    assert all(r.success for r in results)
    # 最多 5 个并发
    assert max_observed <= 5, f"max_concurrency exceeded: observed={max_observed}"
    assert max_observed == 5, f"expected 5 concurrent, observed={max_observed}"


@pytest.mark.asyncio
async def test_semaphore_release_on_complete() -> None:
    """完成后 semaphore 释放（_value 恢复到 max）。"""
    sem = _reset_semaphore_cache(max_concurrency=3)
    task = _task()

    async def _runner() -> TeamSubtaskResult:
        return TeamSubtaskResult(agent="code", success=True, payload="ok")

    await acquire_and_run(task, _runner(), thread_id="th-1")
    # 释放后 _value 应等于 max
    assert sem._value == 3  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_semaphore_release_on_exception() -> None:
    """异常时也释放 semaphore（I3 不泄漏）。"""
    sem = _reset_semaphore_cache(max_concurrency=3)
    task = _task()

    async def _runner() -> TeamSubtaskResult:
        raise RuntimeError("boom")

    result = await acquire_and_run(task, _runner(), thread_id="th-1")
    # 异常被 acquire_and_run 捕获并包装为失败 result
    assert result.success is False
    assert "RuntimeError" in result.payload or "boom" in result.payload
    # semaphore 已释放
    assert sem._value == 3  # type: ignore[attr-defined]


# ============================================================
# abort cancel
# ============================================================


@pytest.mark.asyncio
async def test_abort_cancel_returns_aborted() -> None:
    """abort_event 触发后返回 ``TeamSubtaskResult(success=False, payload="用户中止")``。"""
    _reset_semaphore_cache(max_concurrency=5)
    task = _task()
    abort_event = asyncio.Event()
    started = asyncio.Event()

    async def _runner() -> TeamSubtaskResult:
        started.set()
        # 模拟 LLM 长调用（被 cancel 中断）
        await asyncio.sleep(10)
        return TeamSubtaskResult(agent="code", success=True, payload="should not reach")

    # 启动 acquire_and_run（不 await，让它在后台跑）
    bg = asyncio.ensure_future(
        acquire_and_run(task, _runner(), thread_id="th-1", abort_event=abort_event)
    )
    # 等 runner 真正开始
    await started.wait()
    # 触发 abort
    abort_event.set()
    result = await bg
    assert result.success is False
    assert result.payload == "用户中止"
    assert result.agent == "code"


@pytest.mark.asyncio
async def test_abort_releases_semaphore() -> None:
    """abort cancel 后 semaphore 释放（不泄漏）。"""
    sem = _reset_semaphore_cache(max_concurrency=2)
    task = _task()
    abort_event = asyncio.Event()
    started = asyncio.Event()

    async def _runner() -> TeamSubtaskResult:
        started.set()
        await asyncio.sleep(10)
        return TeamSubtaskResult(agent="code", success=True, payload="x")

    bg = asyncio.ensure_future(
        acquire_and_run(task, _runner(), thread_id="th-1", abort_event=abort_event)
    )
    await started.wait()
    abort_event.set()
    await bg
    # semaphore 释放
    assert sem._value == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cancel_running_tasks_clears_registry() -> None:
    """``cancel_running_tasks(thread_id)`` 取消所有 task，清空 registry。"""
    # 注册两个 mock task
    task1 = MagicMock(spec=asyncio.Task)
    task1.done.return_value = False
    task2 = MagicMock(spec=asyncio.Task)
    task2.done.return_value = False
    register_running_task("th-x", task1)  # type: ignore[arg-type]
    register_running_task("th-x", task2)  # type: ignore[arg-type]

    assert len(_running_tasks["th-x"]) == 2
    cancel_running_tasks("th-x")
    # 两个 task 都被 cancel
    task1.cancel.assert_called_once()
    task2.cancel.assert_called_once()
    # registry 清空
    assert _running_tasks["th-x"] == []


@pytest.mark.asyncio
async def test_cancel_running_tasks_skips_done_tasks() -> None:
    """已完成的 task 不再 cancel（避免 RuntimeError）。"""
    done_task = MagicMock(spec=asyncio.Task)
    done_task.done.return_value = True
    pending_task = MagicMock(spec=asyncio.Task)
    pending_task.done.return_value = False
    register_running_task("th-y", done_task)  # type: ignore[arg-type]
    register_running_task("th-y", pending_task)  # type: ignore[arg-type]

    cancel_running_tasks("th-y")
    done_task.cancel.assert_not_called()
    pending_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_running_tasks_unknown_thread_id_noop() -> None:
    """未注册的 thread_id 调用 cancel 不报错。"""
    cancel_running_tasks("nonexistent-thread")
    # 无异常即通过


# ============================================================
# _is_transient helper
# ============================================================


def test_is_transient_timeout() -> None:
    assert _is_transient(asyncio.TimeoutError()) is True


def test_is_transient_connect_error() -> None:
    assert _is_transient(httpx.ConnectError("conn refused")) is True


def test_is_transient_5xx() -> None:
    """5xx HTTPStatusError 视为瞬态。"""
    request = httpx.Request("GET", "http://example.com")
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError("server error", request=request, response=response)
    assert _is_transient(exc) is True


def test_is_transient_4xx() -> None:
    """4xx HTTPStatusError 不视为瞬态。"""
    request = httpx.Request("GET", "http://example.com")
    response = httpx.Response(404, request=request)
    exc = httpx.HTTPStatusError("not found", request=request, response=response)
    assert _is_transient(exc) is False


def test_is_transient_value_error() -> None:
    assert _is_transient(ValueError("logic error")) is False


def test_transient_errors_tuple_contains_expected() -> None:
    """TRANSIENT_ERRORS 元组包含预期的异常类型。"""
    assert asyncio.TimeoutError in TRANSIENT_ERRORS
    assert httpx.ConnectError in TRANSIENT_ERRORS
    assert httpx.ReadTimeout in TRANSIENT_ERRORS
    assert httpx.WriteTimeout in TRANSIENT_ERRORS
    assert httpx.PoolTimeout in TRANSIENT_ERRORS


# ============================================================
# run_with_retry：瞬态 / 非瞬态 / 退避 / retries 字段
# ============================================================


@pytest.mark.asyncio
async def test_retry_transient_timeout() -> None:
    """TimeoutError 重试 max_retries 次，最终返回失败 result。"""
    task = _task()
    call_count = 0

    async def _runner_coro() -> TeamSubtaskResult:
        nonlocal call_count
        call_count += 1
        raise asyncio.TimeoutError()

    def _factory() -> Any:
        return _runner_coro()

    with patch("app.team.scheduler.asyncio.sleep", new=AsyncMock()):
        result = await run_with_retry(task, _factory, max_retries=2)

    # 初始 + 2 重试 = 3 次
    assert call_count == 3
    assert result.success is False
    assert result.retries == 2


@pytest.mark.asyncio
async def test_retry_network_error() -> None:
    """ConnectError 重试 max_retries 次。"""
    task = _task()
    call_count = 0

    async def _runner_coro() -> TeamSubtaskResult:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("connection refused")

    def _factory() -> Any:
        return _runner_coro()

    with patch("app.team.scheduler.asyncio.sleep", new=AsyncMock()):
        result = await run_with_retry(task, _factory, max_retries=2)

    assert call_count == 3
    assert result.success is False
    assert result.retries == 2


@pytest.mark.asyncio
async def test_retry_no_retry_on_4xx() -> None:
    """4xx HTTPStatusError 不重试，retries=0。"""
    task = _task()
    call_count = 0
    request = httpx.Request("GET", "http://example.com")
    response = httpx.Response(404, request=request)
    exc = httpx.HTTPStatusError("not found", request=request, response=response)

    async def _runner_coro() -> TeamSubtaskResult:
        nonlocal call_count
        call_count += 1
        raise exc

    def _factory() -> Any:
        return _runner_coro()

    result = await run_with_retry(task, _factory, max_retries=2)
    # 不重试
    assert call_count == 1
    assert result.success is False
    assert result.retries == 0


@pytest.mark.asyncio
async def test_retry_no_retry_on_value_error() -> None:
    """ValueError 视为逻辑错误，不重试。"""
    task = _task()
    call_count = 0

    async def _runner_coro() -> TeamSubtaskResult:
        nonlocal call_count
        call_count += 1
        raise ValueError("invalid input")

    def _factory() -> Any:
        return _runner_coro()

    result = await run_with_retry(task, _factory, max_retries=2)
    assert call_count == 1
    assert result.success is False
    assert result.retries == 0


@pytest.mark.asyncio
async def test_retry_no_retry_on_cancelled() -> None:
    """CancelledError 直接 raise，不重试（I4）。"""
    task = _task()
    call_count = 0

    async def _runner_coro() -> TeamSubtaskResult:
        nonlocal call_count
        call_count += 1
        raise asyncio.CancelledError()

    def _factory() -> Any:
        return _runner_coro()

    with pytest.raises(asyncio.CancelledError):
        await run_with_retry(task, _factory, max_retries=3)
    # 只调用一次（不重试）
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_backoff() -> None:
    """退避间隔 1s/2s/4s（mock sleep 验证调用次数与时长）。"""
    task = _task()
    sleep_mock = AsyncMock()

    async def _runner_coro() -> TeamSubtaskResult:
        raise asyncio.TimeoutError()

    def _factory() -> Any:
        return _runner_coro()

    with patch("app.team.scheduler.asyncio.sleep", new=sleep_mock):
        await run_with_retry(task, _factory, max_retries=3)

    # 退避：1s（attempt 0 后）, 2s（attempt 1 后）, 4s（attempt 2 后）
    assert sleep_mock.await_count == 3
    actual_delays = [call.args[0] for call in sleep_mock.await_args_list]
    assert actual_delays == [1, 2, 4]


@pytest.mark.asyncio
async def test_retry_records_retries_field() -> None:
    """``TeamSubtaskResult.retries`` 字段记录实际重试次数。"""
    task = _task()
    call_count = 0

    async def _runner_coro() -> TeamSubtaskResult:
        nonlocal call_count
        call_count += 1
        if call_count < 3:  # 前两次失败
            raise asyncio.TimeoutError()
        # 第三次成功
        return TeamSubtaskResult(agent="code", success=True, payload="ok")

    def _factory() -> Any:
        return _runner_coro()

    with patch("app.team.scheduler.asyncio.sleep", new=AsyncMock()):
        result = await run_with_retry(task, _factory, max_retries=3)

    assert result.success is True
    assert result.retries == 2  # 重试了 2 次后成功
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_succeeds_first_attempt() -> None:
    """首次成功：retries=0，不进入退避。"""
    task = _task()

    async def _runner_coro() -> TeamSubtaskResult:
        return TeamSubtaskResult(agent="code", success=True, payload="ok")

    def _factory() -> Any:
        return _runner_coro()

    sleep_mock = AsyncMock()
    with patch("app.team.scheduler.asyncio.sleep", new=sleep_mock):
        result = await run_with_retry(task, _factory, max_retries=3)

    assert result.success is True
    assert result.retries == 0
    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_exhausted_returns_failure_with_retries() -> None:
    """重试耗尽：返回 success=False, retries=max_retries。"""
    task = _task()

    async def _runner_coro() -> TeamSubtaskResult:
        raise httpx.ReadTimeout("read timed out")

    def _factory() -> Any:
        return _runner_coro()

    with patch("app.team.scheduler.asyncio.sleep", new=AsyncMock()):
        result = await run_with_retry(task, _factory, max_retries=2)

    assert result.success is False
    assert result.retries == 2
    assert "ReadTimeout" in result.payload or "read timed out" in result.payload


# ============================================================
# register_running_task：基础行为
# ============================================================


def test_register_running_task_appends_to_list() -> None:
    """同一 thread_id 注册多个 task 时追加到列表。"""
    t1 = MagicMock(spec=asyncio.Task)
    t2 = MagicMock(spec=asyncio.Task)
    register_running_task("th-z", t1)  # type: ignore[arg-type]
    register_running_task("th-z", t2)  # type: ignore[arg-type]
    assert _running_tasks["th-z"] == [t1, t2]


def test_register_running_task_different_thread_ids() -> None:
    """不同 thread_id 独立列表。"""
    t1 = MagicMock(spec=asyncio.Task)
    t2 = MagicMock(spec=asyncio.Task)
    register_running_task("th-a", t1)  # type: ignore[arg-type]
    register_running_task("th-b", t2)  # type: ignore[arg-type]
    assert _running_tasks["th-a"] == [t1]
    assert _running_tasks["th-b"] == [t2]
