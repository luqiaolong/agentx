"""Checkpointer 关闭行为回归测试。

验证：
- ``aclose_checkpointer()`` 在异步上下文中正确关闭异步连接。
- ``close_checkpointer()`` 在运行事件循环中调用时发出警告，不抛 RuntimeError。
"""

from __future__ import annotations

import asyncio

import pytest

import app.memory.checkpointer as cp_module
from app.memory.checkpointer import (
    aclose_checkpointer,
    close_checkpointer,
    get_async_checkpointer,
)


@pytest.fixture(autouse=True)
def _isolate_checkpointer_singleton(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """每个测试隔离 DATA_DIR 与 checkpointer 单例。"""
    monkeypatch.setattr(cp_module, "DATA_DIR", tmp_path)
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None
    yield
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None


@pytest.mark.asyncio
async def test_aclose_checkpointer_closes_async_connection():
    saver = await get_async_checkpointer()
    assert saver is not None

    await aclose_checkpointer()

    # 再次获取应创建新连接（单例已被重置）
    saver2 = await get_async_checkpointer()
    assert saver2 is not saver
    await aclose_checkpointer()


def test_close_checkpointer_warns_inside_loop(monkeypatch: pytest.MonkeyPatch):
    warnings: list[str] = []

    def _capture_warning(msg: str, *args, **kwargs):
        warnings.append(msg)

    monkeypatch.setattr(cp_module.logger, "warning", _capture_warning)

    async def _inner():
        await get_async_checkpointer()
        conn = cp_module._async_conn
        close_checkpointer()
        assert cp_module._async_conn is None
        # 手动关闭底层连接，避免 aiosqlite 后台线程在 loop 关闭后抛异常
        await conn.close()

    asyncio.run(_inner())

    assert any(
        "close_checkpointer called inside a running event loop" in msg
        for msg in warnings
    )
