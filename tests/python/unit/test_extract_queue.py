"""画像抽取持久化队列单元测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.memory.checkpointer as cp_module
import app.memory.extract_queue as eq_module
import app.memory.profile_store as ps_module
from app.memory.extract_queue import dequeue, drain_queue, enqueue, queue_size, start_worker
from app.memory.profile_extractor import ProfileEntry, ProfileResult


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


def _make_llm_mock(entries: list[ProfileEntry] | None) -> MagicMock:
    """构造返回指定 entries 的 mock LLM。"""
    result = ProfileResult(entries=entries or [])
    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(return_value=result)
    fake_llm = MagicMock()
    fake_llm.with_structured_output = MagicMock(return_value=structured_llm)
    return fake_llm


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
                category="project",
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
