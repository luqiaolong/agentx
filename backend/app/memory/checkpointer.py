"""LangGraph SQLite checkpointer：会话状态持久化。

- 使用 ``langgraph.checkpoint.sqlite.SqliteSaver``（同步）作为主单例
- 异步场景使用 ``AsyncSqliteSaver``（基于 aiosqlite）
- 数据库路径: ``data/agentx.db``（由 ``config.DATA_DIR / "agentx.db"`` 构造）
- 提供 ``get_checkpointer()`` 单例 + ``get_async_checkpointer()`` 异步单例
- 首次调用时自动创建 ``data/`` 目录与表结构
- ``close_checkpointer()`` 在应用关闭时释放连接

``RouterState.authorized_dirs`` 等字段随 checkpoint 一起持久化，实现跨会话恢复。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import DATA_DIR
from app.observability.logger import logger

# SQLite 数据库文件名（位于 DATA_DIR 下）
_DB_FILENAME = "agentx.db"


def _db_path() -> Path:
    """返回当前 DATA_DIR 下的数据库路径。"""
    return DATA_DIR / _DB_FILENAME


# ---- 同步单例 ----
_sync_saver: SqliteSaver | None = None
_sync_conn: sqlite3.Connection | None = None

# ---- 异步单例 ----
_async_saver: AsyncSqliteSaver | None = None
_async_conn: aiosqlite.Connection | None = None


def get_checkpointer() -> SqliteSaver:
    """返回同步 ``SqliteSaver`` 单例。

    首次调用时创建 ``data/`` 目录、建立 sqlite3 连接（``check_same_thread=False``），
    并执行 ``setup()`` 建表。后续调用直接返回缓存实例。
    """
    global _sync_saver, _sync_conn
    if _sync_saver is not None:
        return _sync_saver

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _db_path()
    # check_same_thread=False：SqliteSaver 内部用锁保证线程安全
    _sync_conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _sync_saver = SqliteSaver(_sync_conn)
    _sync_saver.setup()  # 创建 checkpoint 表
    logger.info("SQLite 同步 checkpointer 已初始化", db_path=str(db_path))
    return _sync_saver


async def get_async_checkpointer() -> AsyncSqliteSaver:
    """返回异步 ``AsyncSqliteSaver`` 单例（基于 aiosqlite）。

    首次调用时创建 ``data/`` 目录、建立 aiosqlite 连接并执行 ``setup()`` 建表。
    与同步单例共享同一数据库文件，但使用独立连接。
    """
    global _async_saver, _async_conn
    if _async_saver is not None:
        return _async_saver

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _db_path()
    _async_conn = await aiosqlite.connect(str(db_path))
    _async_saver = AsyncSqliteSaver(_async_conn)
    await _async_saver.setup()  # 创建 checkpoint 表
    logger.info("SQLite 异步 checkpointer 已初始化", db_path=str(db_path))
    return _async_saver


def close_checkpointer() -> None:
    """关闭 checkpointer（仅在无运行事件循环的同步上下文中使用）。

    若从异步上下文中调用，请改用 ``aclose_checkpointer()``。
    """
    global _sync_saver, _sync_conn, _async_saver, _async_conn

    # 关闭同步 saver
    if _sync_conn is not None:
        try:
            _sync_conn.close()
            logger.info("SQLite 同步 checkpointer 已关闭")
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭同步 checkpointer 失败", error=str(exc))
        _sync_conn = None
        _sync_saver = None

    # 关闭异步 saver（best-effort）
    if _async_conn is not None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            logger.warning(
                "close_checkpointer called inside a running event loop; "
                "use aclose_checkpointer() to close the async connection properly."
            )
        else:
            try:
                asyncio.run(_async_conn.close())
                logger.info("SQLite 异步 checkpointer 已关闭")
            except Exception as exc:  # noqa: BLE001
                logger.warning("关闭异步 checkpointer 失败", error=str(exc))
        _async_conn = None
        _async_saver = None


async def aclose_checkpointer() -> None:
    """异步关闭 checkpointer 连接并重置单例。

    在异步上下文（如 CLI ``asyncio.run`` 内部）中调用，正确 ``await``
    异步连接关闭，避免 ``RuntimeWarning: coroutine never awaited``。
    """
    global _sync_saver, _sync_conn, _async_saver, _async_conn

    # 关闭同步 saver
    if _sync_conn is not None:
        try:
            _sync_conn.close()
            logger.info("SQLite 同步 checkpointer 已关闭")
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭同步 checkpointer 失败", error=str(exc))
        _sync_conn = None
        _sync_saver = None

    # 关闭异步 saver
    if _async_conn is not None:
        try:
            await _async_conn.close()
            logger.info("SQLite 异步 checkpointer 已关闭")
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭异步 checkpointer 失败", error=str(exc))
        _async_conn = None
        _async_saver = None


__all__ = [
    "aclose_checkpointer",
    "close_checkpointer",
    "get_async_checkpointer",
    "get_checkpointer",
]
