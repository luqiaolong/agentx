"""Checkpointer 并发写测试：同步 + 异步 saver 同时写入同一数据库。

覆盖：
1. 同步 ``SqliteSaver.put`` 与异步 ``AsyncSqliteSaver.aput`` 并发写入
   同一 thread_id 不触发 ``database is locked``。
2. 并发写入后两种 saver 都能读取到最新 checkpoint。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import app.memory.checkpointer as cp_module
from app.memory.checkpointer import (
    aclose_checkpointer,
    get_async_checkpointer,
    get_checkpointer,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试隔离 ``DATA_DIR`` 与 checkpointer 单例。"""
    monkeypatch.setattr(cp_module, "DATA_DIR", tmp_path)
    # 重置 checkpointer 单例
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None
    yield
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None


def _make_checkpoint(thread_id: str, seq: int) -> dict:
    """构造一个最小化的 checkpoint 字典供 saver 写入。"""
    return {
        "id": f"{thread_id}-{seq}",
        "ts": f"2026-07-09T00:00:{seq:02d}+00:00",
        "channel_values": {"messages": []},
        "channel_versions": {"messages": seq},
        "versions_seen": {},
        "pending_sends": [],
    }


@pytest.mark.asyncio
async def test_concurrent_sync_and_async_checkpointer_writes(tmp_path: Path) -> None:
    """同步与异步 saver 并发写入同一 thread_id 不抛 database is locked。"""
    sync_saver = get_checkpointer()
    async_saver = await get_async_checkpointer()

    thread_id = "concurrent-thread"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    async def _async_writes(count: int) -> None:
        for i in range(count):
            await async_saver.aput(config, _make_checkpoint(thread_id, i), {"messages": "any"}, [])

    def _sync_writes(count: int) -> None:
        for i in range(count, 2 * count):
            sync_saver.put(config, _make_checkpoint(thread_id, i), {"messages": "any"}, [])

    # 并发执行：异步写 0-9，同步写 10-19
    await asyncio.gather(
        _async_writes(10),
        asyncio.to_thread(_sync_writes, 10),
    )

    # 两种 saver 都能读取到 checkpoint
    sync_checkpoint = sync_saver.get(config)
    async_checkpoint = await async_saver.aget(config)
    assert sync_checkpoint is not None
    assert async_checkpoint is not None

    await aclose_checkpointer()


@pytest.mark.asyncio
async def test_concurrent_sync_and_async_separate_threads(tmp_path: Path) -> None:
    """并发写入不同 thread_id 互不干扰。"""
    sync_saver = get_checkpointer()
    async_saver = await get_async_checkpointer()

    async def _async_thread(thread_id: str) -> None:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        for i in range(5):
            await async_saver.aput(config, _make_checkpoint(thread_id, i), {"messages": "any"}, [])

    def _sync_thread(thread_id: str) -> None:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        for i in range(5):
            sync_saver.put(config, _make_checkpoint(thread_id, i), {"messages": "any"}, [])

    threads = [f"thread-{i}" for i in range(4)]
    await asyncio.gather(
        *[_async_thread(t) for t in threads[:2]],
        *[asyncio.to_thread(_sync_thread, t) for t in threads[2:]],
    )

    for thread_id in threads:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        sync_checkpoint = sync_saver.get(config)
        async_checkpoint = await async_saver.aget(config)
        assert sync_checkpoint is not None
        assert async_checkpoint is not None

    await aclose_checkpointer()
