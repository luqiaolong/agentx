"""画像抽取持久化队列单元测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.memory.checkpointer as cp_module
import app.memory.extract_queue as eq_module
import app.memory.profile_store as ps_module
from app.memory.extract_queue import (
    MAX_ATTEMPTS,
    _handle_job_failure,
    _get_job,
    dequeue,
    drain_queue,
    enqueue,
    queue_size,
    start_worker,
)
from app.memory.profile_extractor import (
    ExtractStatus,
    ProfileEntry,
    ProfileResult,
    extract_profile_via_llm,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试隔离 ``DATA_DIR``，避免污染真实数据库。"""
    monkeypatch.setattr(eq_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cp_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ps_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ps_module, "_PROFILE_DIR", tmp_path / "config")
    monkeypatch.setattr(ps_module, "_PROFILE_FILE", tmp_path / "config" / "profile.json")
    yield


async def _wait_for_empty_queue(timeout: float = 3.0) -> None:
    """辅助：等待队列清空。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await queue_size() == 0:
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("queue not empty in time")


async def _wait_for_condition(
    condition, timeout: float = 3.0, interval: float = 0.02
) -> None:
    """辅助：轮询等待条件满足。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await condition():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("condition not met in time")


def _make_llm_mock(entries: list[ProfileEntry] | None) -> MagicMock:
    """构造返回指定 entries 的 mock LLM。"""
    result = ProfileResult(entries=entries or [])
    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(return_value=result)
    fake_llm = MagicMock()
    fake_llm.with_structured_output = MagicMock(return_value=structured_llm)
    return fake_llm


def _make_failing_llm_mock(exc: Exception | None = None) -> MagicMock:
    """构造 ainvoke 抛异常的 mock LLM（模拟 LLM 调用失败）。"""
    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(side_effect=exc or RuntimeError("LLM 不可用"))
    fake_llm = MagicMock()
    fake_llm.with_structured_output = MagicMock(return_value=structured_llm)
    return fake_llm


def _make_empty_llm_mock() -> MagicMock:
    """构造返回空 entries 的 mock LLM（SUCCESS_EMPTY）。"""
    return _make_llm_mock([])


async def test_enqueue_and_dequeue_round_trip(tmp_path: Path) -> None:
    """enqueue 后 dequeue 能取出任务。"""
    await enqueue(
        message="我用 TypeScript",
        assistant_reply="好的，TS 项目",
        workspace_path=None,
    )
    assert await queue_size() == 1

    job = await dequeue()
    assert job is not None
    assert job.message == "我用 TypeScript"
    assert job.assistant_reply == "好的，TS 项目"
    assert job.workspace_path is None


async def test_dequeue_empty_returns_none(tmp_path: Path) -> None:
    """空队列 dequeue 返回 None。"""
    assert await dequeue() is None


async def test_enqueue_workspace_path(tmp_path: Path) -> None:
    """enqueue 保存 workspace_path。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()
    await enqueue(
        message="hi",
        assistant_reply="hello",
        workspace_path=str(ws_path),
    )
    job = await dequeue()
    assert job is not None
    assert job.workspace_path == str(ws_path)


async def test_worker_processes_global_job(tmp_path: Path) -> None:
    """工作器消费队列并写入全局画像。"""
    fake_llm = _make_llm_mock(
        [
            ProfileEntry(
                key="uses_ts",
                category="fact",
                content="用户用 TypeScript",
                title="TS 用户",
                keywords=["ts"],
                scenarios=["coding"],
            )
        ]
    )

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        await enqueue(
            message="我用 TypeScript",
            assistant_reply="好的",
            workspace_path=None,
        )
        worker_task = start_worker(poll_interval=0.05)
        await _wait_for_empty_queue()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    assert await queue_size() == 0
    from app.memory.profile_store import get

    entry = get("uses_ts")
    assert entry is not None
    assert entry.content == "用户用 TypeScript"
    assert entry.source == "llm_extracted"


async def test_worker_processes_workspace_job(tmp_path: Path) -> None:
    """工作器消费队列并写入工作区记忆。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()

    fake_llm = _make_llm_mock(
        [
            ProfileEntry(
                key="ws_pref",
                category="preference",
                content="工作区偏好",
                title="偏好",
                keywords=["pref"],
                scenarios=["work"],
            )
        ]
    )

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        await enqueue(
            message="hi",
            assistant_reply="hello",
            workspace_path=str(ws_path),
        )
        worker_task = start_worker(poll_interval=0.05)
        await _wait_for_empty_queue()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    assert await queue_size() == 0
    from app.workspace.memory_store import list_entries

    entries = list_entries(str(ws_path))
    assert any(e.key == "ws_pref" for e in entries)


async def test_drain_queue_processes_pending_jobs(tmp_path: Path) -> None:
    """drain_queue 在关闭前同步处理待处理任务。"""
    fake_llm = _make_llm_mock(
        [
            ProfileEntry(
                key="drain_key",
                category="fact",
                content="drain 测试",
                title="drain",
                keywords=["drain"],
                scenarios=["test"],
            )
        ]
    )

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        await enqueue(
            message="drain me",
            assistant_reply="ok",
            workspace_path=None,
        )
        await drain_queue(timeout=5.0)

    assert await queue_size() == 0
    from app.memory.profile_store import get

    assert get("drain_key") is not None


async def test_failed_upsert_keeps_job_for_retry(tmp_path: Path) -> None:
    """写入画像失败时任务不删除，保留在队列中下次重试。"""
    fake_llm = _make_llm_mock(
        [
            ProfileEntry(
                key="retry_key",
                category="fact",
                content="retry 测试",
            )
        ]
    )

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        with patch(
            "app.memory.profile_store.upsert_from_llm",
            new=AsyncMock(side_effect=RuntimeError("写入失败")),
        ):
            await enqueue(
                message="retry me",
                assistant_reply="ok",
                workspace_path=None,
            )
            worker_task = start_worker(poll_interval=0.05)
            await asyncio.sleep(0.15)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

    # 任务仍在队列中等待重试
    assert await queue_size() == 1


async def test_no_jobs_worker_polling_does_not_crash(tmp_path: Path) -> None:
    """空队列时工作器轮询不报错。"""
    worker_task = start_worker(poll_interval=0.05)
    await asyncio.sleep(0.15)
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    assert await queue_size() == 0


# ============================================================
# T1.9 / T1.10: ExtractResult 语义与队列失败处理
# ============================================================


async def test_llm_failure_returns_failed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """T1.9-1: LLM 失败时返回 ExtractResult(status=FAILED)。"""
    fake_llm = _make_failing_llm_mock(TimeoutError("LLM 超时"))
    monkeypatch.setattr("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm)

    result = await extract_profile_via_llm("msg", "reply")
    assert result.status == ExtractStatus.FAILED
    assert result.entries == []
    assert result.error is not None
    assert "LLM 超时" in result.error


async def test_llm_success_empty_returns_success_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T1.9-2: LLM 成功但无条目时返回 ExtractResult(status=SUCCESS_EMPTY)。"""
    fake_llm = _make_empty_llm_mock()
    monkeypatch.setattr("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm)

    result = await extract_profile_via_llm("你好", "你好")
    assert result.status == ExtractStatus.SUCCESS_EMPTY
    assert result.entries == []
    assert result.error is None


async def test_llm_success_with_entries_returns_success_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T1.9-3: LLM 成功且有条目时返回 ExtractResult(status=SUCCESS_WRITTEN)。"""
    fake_llm = _make_llm_mock(
        [
            ProfileEntry(
                key="uses_ts",
                category="project",
                content="用户用 TypeScript",
            )
        ]
    )
    monkeypatch.setattr("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm)

    result = await extract_profile_via_llm("我用 TypeScript", "好的")
    assert result.status == ExtractStatus.SUCCESS_WRITTEN
    assert len(result.entries) == 1
    assert result.entries[0]["key"] == "uses_ts"


async def test_worker_does_not_delete_job_on_failure(tmp_path: Path) -> None:
    """T1.10-4: LLM 失败时 worker 不删除任务（留在队列中）。"""
    fake_llm = _make_failing_llm_mock()

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        await enqueue(message="will fail", assistant_reply="reply", workspace_path=None)
        worker_task = start_worker(poll_interval=0.05)
        # 等待 job 进入 dead_letter（3 次失败后 dequeue 返回 None）
        await _wait_for_condition(
            lambda: _job_status_is("dead_letter"), timeout=3.0
        )
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    # 任务未被删除，仍在队列中
    assert await queue_size() == 1


async def test_worker_deletes_job_on_success_empty(tmp_path: Path) -> None:
    """T1.10-5: SUCCESS_EMPTY 时 worker 删除任务。"""
    fake_llm = _make_empty_llm_mock()

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        await enqueue(message="no profile here", assistant_reply="ok", workspace_path=None)
        worker_task = start_worker(poll_interval=0.05)
        await _wait_for_empty_queue()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    assert await queue_size() == 0


async def test_worker_deletes_job_on_success_written(tmp_path: Path) -> None:
    """T1.10-6: SUCCESS_WRITTEN 时 worker 删除任务并写入画像。"""
    fake_llm = _make_llm_mock(
        [
            ProfileEntry(
                key="written_key",
                category="fact",
                content="写入测试",
            )
        ]
    )

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        await enqueue(message="extract me", assistant_reply="ok", workspace_path=None)
        worker_task = start_worker(poll_interval=0.05)
        await _wait_for_empty_queue()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    assert await queue_size() == 0
    from app.memory.profile_store import get

    entry = get("written_key")
    assert entry is not None
    assert entry.content == "写入测试"


async def test_failed_job_increments_attempts(tmp_path: Path) -> None:
    """T1.10-7: 任务失败后 attempts 递增。"""
    await enqueue(message="fail me", assistant_reply="reply", workspace_path=None)
    job = await dequeue()
    assert job is not None
    assert job.id is not None

    # 初始 attempts 为 0
    row = await _get_job(job.id)
    assert row is not None
    assert row["attempts"] == 0

    # 第一次失败
    await _handle_job_failure(job.id, "error 1")
    row = await _get_job(job.id)
    assert row is not None
    assert row["attempts"] == 1
    assert row["status"] == "pending"
    assert row["last_error"] == "error 1"

    # 第二次失败
    await _handle_job_failure(job.id, "error 2")
    row = await _get_job(job.id)
    assert row is not None
    assert row["attempts"] == 2
    assert row["status"] == "pending"
    assert row["last_error"] == "error 2"


async def test_job_with_max_attempts_goes_to_dead_letter(tmp_path: Path) -> None:
    """T1.10-8: 达到 MAX_ATTEMPTS 次失败后进入死信状态。"""
    await enqueue(message="dead letter me", assistant_reply="reply", workspace_path=None)
    job = await dequeue()
    assert job is not None

    # 失败 MAX_ATTEMPTS 次
    for i in range(MAX_ATTEMPTS):
        await _handle_job_failure(job.id, f"failure #{i + 1}")

    row = await _get_job(job.id)
    assert row is not None
    assert row["attempts"] == MAX_ATTEMPTS
    assert row["status"] == "dead_letter"
    assert row["last_error"] is not None
    assert "failure #3" in row["last_error"]

    # dead_letter 任务不会被 dequeue 再次领取
    assert await dequeue() is None


# ============================================================
# Lease 恢复与原子失败处理
# ============================================================


async def test_dequeue_recovers_expired_lease(tmp_path: Path) -> None:
    """租约过期后 dequeue 能恢复任务（模拟 worker 崩溃后恢复）。"""
    import aiosqlite

    await enqueue(
        message="crashed worker job",
        assistant_reply="reply",
        workspace_path=None,
    )

    # 手动将任务置为 'leased' 且 leased_until 已过期
    db_path = eq_module._db_path()
    past_time = datetime.now(tz=timezone.utc) - timedelta(seconds=120)
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            f"UPDATE {eq_module._QUEUE_TABLE} "
            "SET status = 'leased', leased_until = ? "
            "WHERE id = (SELECT id FROM profile_extract_queue LIMIT 1)",
            (past_time.isoformat(),),
        )
        await conn.commit()

    # 确认任务处于 leased 状态（查询唯一的任务）
    async with aiosqlite.connect(str(db_path)) as conn:
        async with conn.execute(
            f"SELECT id, status FROM {eq_module._QUEUE_TABLE} LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            job_id, status = row[0], row[1]
            assert status == "leased"

    # dequeue 应该通过 lease 恢复机制重新领取该任务
    job = await dequeue()
    assert job is not None
    assert job.id == job_id
    assert job.message == "crashed worker job"

    # 确认任务被重新 lease（leased_until 已更新为未来时间）
    job_row = await _get_job(job.id)
    assert job_row is not None
    assert job_row["status"] == "leased"
    assert job_row["leased_until"] is not None


async def test_dequeue_does_not_recover_active_lease(tmp_path: Path) -> None:
    """未过期的 leased 任务不应被 dequeue 领取。"""
    import aiosqlite

    await enqueue(
        message="active leased job",
        assistant_reply="reply",
        workspace_path=None,
    )

    # 手动将任务置为 'leased' 且 leased_until 在未来
    db_path = eq_module._db_path()
    future_time = datetime.now(tz=timezone.utc) + timedelta(seconds=120)
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            f"UPDATE {eq_module._QUEUE_TABLE} "
            "SET status = 'leased', leased_until = ? "
            "WHERE id = (SELECT id FROM profile_extract_queue LIMIT 1)",
            (future_time.isoformat(),),
        )
        await conn.commit()

    # dequeue 不应领取未过期的 leased 任务
    assert await dequeue() is None


async def test_drain_queue_does_not_exhaust_retries_on_failure(
    tmp_path: Path,
) -> None:
    """drain_queue 失败时不递增 attempts（避免单次 drain 耗尽重试次数）。"""
    fake_llm = _make_failing_llm_mock()

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        await enqueue(
            message="will fail in drain",
            assistant_reply="reply",
            workspace_path=None,
        )
        await drain_queue(timeout=5.0)

    # 任务仍在队列中
    assert await queue_size() == 1

    # attempts 仍为 0（drain 未递增）
    db_path = eq_module._db_path()
    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as conn:
        async with conn.execute(
            f"SELECT attempts, status FROM {eq_module._QUEUE_TABLE} LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0  # attempts 未递增
            assert row[1] == "leased"  # 保持 leased 状态


# ============================================================
# 辅助函数
# ============================================================


async def _job_status_is(expected: str) -> bool:
    """检查队列中唯一的任务是否为指定状态。"""
    db_path = eq_module._db_path()
    if not db_path.exists():
        return False
    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as conn:
        async with conn.execute(
            f"SELECT status FROM {eq_module._QUEUE_TABLE} LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None and row[0] == expected
