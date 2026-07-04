"""Checkpointer 只读视图 + 单会话清理。

直接查 ``data/agent_py.db``（LangGraph SqliteSaver 创建的 ``checkpoints`` 表），
提供 thread 列表与按 thread_id 删除能力，供「记忆」tab 的 Checkpointer 子模块使用。

安全约束：
- ``thread_id`` 严格校验正则 ``^[a-zA-Z0-9_-]+$``，防 SQL 注入
- 仅 DELETE 与 SELECT，无写入/ALTER 操作
- ``checkpoint_ns = ''`` 过滤主线程，避免命名空间干扰
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import DATA_DIR
from app.memory.checkpointer import _DB_FILENAME, _db_path
from app.observability.logger import logger

# thread_id 严格校验正则（防 SQL 注入）
_THREAD_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# 主线程命名空间（langgraph 默认空字符串）
_MAIN_NS = ""


class ThreadIdInvalid(ValueError):
    """thread_id 非法（正则不匹配）。"""


def _validate_thread_id(thread_id: str) -> str:
    """校验 thread_id 合法。"""
    if not isinstance(thread_id, str) or not _THREAD_ID_RE.match(thread_id):
        raise ThreadIdInvalid("thread_id 只能含字母、数字、下划线、连字符")
    return thread_id


def _get_db_path() -> Path:
    """返回当前 checkpointer 数据库路径。

    通过 ``checkpointer._db_path`` 间接获取，确保与单例一致；
    若失败则回退到 ``DATA_DIR / _DB_FILENAME``。
    """
    try:
        return _db_path()
    except Exception:  # noqa: BLE001
        return DATA_DIR / _DB_FILENAME


async def list_threads() -> list[dict[str, Any]]:
    """返回所有 thread 的 checkpoint 汇总信息。

    每项结构::

        {
          "thread_id": "abc",
          "checkpoint_count": 5,
          "last_updated": "2026-07-04T10:00:00+00:00",  # MAX(checkpoint_id)
          "size_bytes": 20480                              # SUM(LENGTH(checkpoint))
        }

    数据库不存在或表不存在时返回空列表（不报错）。
    """
    db_path = _get_db_path()
    if not db_path.exists():
        return []
    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            # 防御性：先检查表是否存在（首次启动尚未 setup 时表可能缺失）
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'"
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None:
                return []
            cur = await conn.execute(
                """
                SELECT
                    thread_id,
                    COUNT(*) AS checkpoint_count,
                    MAX(checkpoint_id) AS last_updated,
                    SUM(LENGTH(checkpoint)) AS size_bytes
                FROM checkpoints
                WHERE checkpoint_ns = ?
                GROUP BY thread_id
                ORDER BY last_updated DESC
                """,
                (_MAIN_NS,),
            )
            rows = await cur.fetchall()
            await cur.close()
    except Exception as exc:  # noqa: BLE001 — 视图层兜底
        logger.warning("list_threads 查询失败", error=str(exc))
        return []
    return [
        {
            "thread_id": r[0],
            "checkpoint_count": r[1],
            "last_updated": r[2],
            "size_bytes": int(r[3] or 0),
        }
        for r in rows
    ]


async def get_db_size() -> int:
    """返回 ``data/agent_py.db`` 文件大小（字节）。

    文件不存在时返回 0。
    """
    db_path = _get_db_path()
    try:
        return db_path.stat().st_size
    except OSError:
        return 0


async def delete_thread(thread_id: str) -> int:
    """删除指定 thread 的所有 checkpoint（含 writes 表）。

    Args:
        thread_id: 待删除的会话 ID。

    Returns:
        实际删除的 checkpoint 行数。

    Raises:
        ThreadIdInvalid: thread_id 非法。
    """
    _validate_thread_id(thread_id)
    db_path = _get_db_path()
    if not db_path.exists():
        return 0
    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            # 同时清理 writes 表（外键关联），避免残留
            await conn.execute(
                "DELETE FROM writes WHERE thread_id = ?", (thread_id,)
            )
            cur = await conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ?",
                (thread_id, _MAIN_NS),
            )
            deleted = cur.rowcount or 0
            await cur.close()
            await conn.commit()
    except Exception as exc:  # noqa: BLE001 — 视图层兜底
        logger.warning("delete_thread 失败", thread_id=thread_id, error=str(exc))
        return 0
    logger.info("thread checkpoint 已删除", thread_id=thread_id, deleted=deleted)
    return deleted


__all__ = [
    "ThreadIdInvalid",
    "delete_thread",
    "get_db_size",
    "list_threads",
]
