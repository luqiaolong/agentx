"""Checkpointer 只读视图 + 单会话清理 + 精准回退。

直接查 ``data/agentx.db``（LangGraph SqliteSaver 创建的 ``checkpoints`` 表），
提供 thread 列表、按 thread_id 删除、以及精准回退（rewind）能力，供「记忆」tab 的
Checkpointer 子模块与「编辑历史消息」功能使用。

安全约束：
- ``thread_id`` 严格校验正则 ``^[a-zA-Z0-9_-]+$``，防 SQL 注入
- 仅 DELETE 与 SELECT，无写入/ALTER 操作
- ``checkpoint_ns = ''`` 过滤主线程，避免命名空间干扰
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import DATA_DIR, get_settings
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
          "last_updated": "2026-07-14T10:00:00+00:00",  # thread_meta.last_active_at
          "size_bytes": 20480                              # SUM(LENGTH(checkpoint))
        }

    ``last_updated`` 取自 ``thread_meta.last_active_at``（由 ``touch_thread`` 在
    chat 请求入口更新），是真实的 ISO 时间戳。
    旧 thread 若未在 ``thread_meta`` 中登记，``last_updated`` 为 ``None``。

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
            # 确保 thread_meta 表存在（LEFT JOIN 需要）
            await _ensure_thread_meta_table(conn)
            # LEFT JOIN thread_meta 取 last_active_at（真实时间戳），
            # 替代旧的 checkpoint_id（UUID，不是时间戳，前端格式化为时间是错误的）
            cur = await conn.execute(
                """
                SELECT
                    c.thread_id,
                    COUNT(*) AS checkpoint_count,
                    tm.last_active_at AS last_updated,
                    SUM(LENGTH(c.checkpoint)) AS size_bytes
                FROM checkpoints c
                LEFT JOIN thread_meta tm ON tm.thread_id = c.thread_id
                WHERE c.checkpoint_ns = ?
                GROUP BY c.thread_id
                ORDER BY MAX(c.rowid) DESC
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
    """返回 ``data/agentx.db`` 文件大小（字节）。

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
        实际删除的 checkpoint 行数；thread 不存在时返回 0。

    Raises:
        ThreadIdInvalid: thread_id 非法。
        sqlite3.Error: 数据库错误（如文件损坏、表缺失、DB 锁定），
            由上层 HTTP 端点映射为 500。
    """
    _validate_thread_id(thread_id)
    db_path = _get_db_path()
    if not db_path.exists():
        return 0
    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            # 先查是否存在，区分「不存在返回 0」与「DB 错误」
            cur = await conn.execute(
                "SELECT COUNT(*) FROM checkpoints "
                "WHERE thread_id = ? AND checkpoint_ns = ?",
                (thread_id, _MAIN_NS),
            )
            row = await cur.fetchone()
            await cur.close()
            count = row[0] if row else 0
            if count == 0:
                return 0  # 正常路径：thread 不存在
            # 存在则删除（先 writes 后 checkpoints，避免残留）
            await conn.execute(
                "DELETE FROM writes WHERE thread_id = ?", (thread_id,)
            )
            del_cur = await conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ?",
                (thread_id, _MAIN_NS),
            )
            deleted = del_cur.rowcount or 0
            await del_cur.close()
            await conn.commit()
    except sqlite3.Error as exc:
        logger.warning(
            "delete_thread DB 错误",
            thread_id=thread_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    logger.info("thread checkpoint 已删除", thread_id=thread_id, deleted=deleted)
    return deleted


async def list_checkpoints(thread_id: str) -> list[dict[str, Any]]:
    """返回指定 thread 的 checkpoint 列表（按插入顺序升序）。

    每项结构::

        {
          "checkpoint_id": "1ef4a3b0-...",
          "parent_checkpoint_id": "..." | null,
          "rowid": 1
        }

    用于前端「编辑历史消息」场景：前端根据消息数量估算保留到第几个
    checkpoint，然后将其 ``checkpoint_id`` 传给 :func:`rewind_thread`。

    Args:
        thread_id: 会话 ID。

    Returns:
        checkpoint 列表（空 thread 或数据库不存在时返回空列表）。

    Raises:
        ThreadIdInvalid: thread_id 非法。
    """
    _validate_thread_id(thread_id)
    db_path = _get_db_path()
    if not db_path.exists():
        return []
    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            cur = await conn.execute(
                "SELECT rowid, checkpoint_id, parent_checkpoint_id "
                "FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? "
                "ORDER BY rowid ASC",
                (thread_id, _MAIN_NS),
            )
            rows = await cur.fetchall()
            await cur.close()
    except sqlite3.Error as exc:
        logger.warning("list_checkpoints 查询失败", thread_id=thread_id, error=str(exc))
        return []
    return [
        {
            "checkpoint_id": r[1],
            "parent_checkpoint_id": r[2],
            "rowid": r[0],
        }
        for r in rows
    ]


async def rewind_thread(thread_id: str, checkpoint_id: str) -> dict[str, Any]:
    """回退指定 thread 的 checkpoint 到 ``checkpoint_id``（含）。

    用于「编辑历史消息」场景：用户编辑第 N 条消息后重新发送，需要回退到
    编辑点之前的状态。调用方通过 :func:`list_checkpoints` 获取该 thread 的
    checkpoint 列表，选出要保留到的 checkpoint_id（含），本函数删除该
    checkpoint 之后的所有 checkpoints 和 writes。

    修复点（Phase 2）：
    - 不再使用 ``keep_messages_count * 2 + 1`` 猜测保留数量，改为直接
      接受真实 ``checkpoint_id`` 定位截止点
    - 不再跨表比较 ``writes.rowid > checkpoints.rowid``（二者独立序列），
      改为按 ``checkpoint_id`` 关联删除 writes：删除 checkpoint_id 不在
      保留集合中的 writes

    Args:
        thread_id: 会话 ID。
        checkpoint_id: 回退到的 checkpoint ID（保留该 checkpoint 及之前的所有）。

    Returns:
        ``{"deleted": int, "kept": int, "cutoff_checkpoint_id": str | None}``

    Raises:
        ThreadIdInvalid: thread_id 非法。
        ValueError: checkpoint_id 为空或在该 thread 中找不到。
        sqlite3.Error: 数据库错误。
    """
    _validate_thread_id(thread_id)
    if not checkpoint_id or not isinstance(checkpoint_id, str):
        raise ValueError("checkpoint_id 不能为空")

    db_path = _get_db_path()
    if not db_path.exists():
        return {"deleted": 0, "kept": 0, "cutoff_checkpoint_id": None}

    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            # 1. 定位 cutoff checkpoint 的 rowid（同表内查找，语义有效）
            cur = await conn.execute(
                "SELECT rowid FROM checkpoints "
                "WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                (thread_id, _MAIN_NS, checkpoint_id),
            )
            row = await cur.fetchone()
            await cur.close()

            if row is None:
                raise ValueError(
                    f"checkpoint_id {checkpoint_id} 不存在于 thread {thread_id}"
                )
            cutoff_rowid = row[0]

            # 2. 统计总数（用于返回 kept）
            cur = await conn.execute(
                "SELECT COUNT(*) FROM checkpoints "
                "WHERE thread_id = ? AND checkpoint_ns = ?",
                (thread_id, _MAIN_NS),
            )
            total_row = await cur.fetchone()
            await cur.close()
            total = total_row[0] if total_row else 0

            # 3. 删除 cutoff 之后的 writes（按 checkpoint_id 关联，不按 rowid 跨表比较）
            #    保留的 checkpoint_id 集合 = rowid <= cutoff_rowid 的那些
            await conn.execute(
                "DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? "
                "AND checkpoint_id NOT IN ("
                "  SELECT checkpoint_id FROM checkpoints "
                "  WHERE thread_id = ? AND checkpoint_ns = ? AND rowid <= ?"
                ")",
                (thread_id, _MAIN_NS, thread_id, _MAIN_NS, cutoff_rowid),
            )

            # 4. 删除 cutoff 之后的 checkpoints（同表 rowid 比较，语义有效）
            del_cur = await conn.execute(
                "DELETE FROM checkpoints "
                "WHERE thread_id = ? AND checkpoint_ns = ? AND rowid > ?",
                (thread_id, _MAIN_NS, cutoff_rowid),
            )
            deleted = del_cur.rowcount or 0
            await del_cur.close()
            await conn.commit()

            logger.info(
                "thread checkpoint 已回退",
                thread_id=thread_id,
                cutoff_checkpoint_id=checkpoint_id,
                cutoff_rowid=cutoff_rowid,
                total_checkpoints=total,
                deleted=deleted,
            )
            return {
                "deleted": deleted,
                "kept": total - deleted,
                "cutoff_checkpoint_id": checkpoint_id,
            }
    except sqlite3.Error as exc:
        logger.warning(
            "rewind_thread DB 错误",
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise


async def _ensure_thread_meta_table(conn: aiosqlite.Connection) -> None:
    """确保 thread_meta 表存在。"""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_meta (
            thread_id       TEXT PRIMARY KEY,
            last_active_at  TEXT NOT NULL
        )
        """
    )
    await conn.commit()


async def touch_thread(thread_id: str) -> None:
    """更新 thread 的最后活跃时间（在 chat 请求入口调用）。

    使用 ``INSERT OR REPLACE`` upsert；失败不阻塞主流程。
    """
    try:
        _validate_thread_id(thread_id)
    except ThreadIdInvalid:
        return
    db_path = _get_db_path()
    if not db_path.exists():
        return
    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            await _ensure_thread_meta_table(conn)
            now = datetime.now(tz=timezone.utc).isoformat()
            await conn.execute(
                "INSERT OR REPLACE INTO thread_meta (thread_id, last_active_at) VALUES (?, ?)",
                (thread_id, now),
            )
            await conn.commit()
    except sqlite3.Error as exc:
        logger.warning("touch_thread failed", thread_id=thread_id, error=str(exc))


async def cleanup_expired_checkpoints(ttl_days: int | None = None) -> int:
    """清理超过 TTL 未活动的 thread checkpoint。

    - 从 ``thread_meta`` 表获取每个 thread 的 ``last_active_at``
    - 未在 ``thread_meta`` 中的旧 thread：opportunistic 补录（grace period，本轮不删）
    - ``last_active_at`` 早于 cutoff 的 thread：删除其 checkpoints + writes + meta

    Args:
        ttl_days: TTL 天数；None 时从 ``settings.checkpoint_ttl_days`` 读取。

    Returns:
        实际删除的 thread 数量。
    """
    days = ttl_days
    if days is None:
        days = getattr(get_settings(), "checkpoint_ttl_days", 30)
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()

    db_path = _get_db_path()
    if not db_path.exists():
        return 0
    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            await _ensure_thread_meta_table(conn)

            # 1. 对未在 meta 中的旧 thread 补录（grace period，本轮不删）
            await conn.execute(
                """
                INSERT OR IGNORE INTO thread_meta (thread_id, last_active_at)
                SELECT DISTINCT thread_id, ? FROM checkpoints WHERE checkpoint_ns = ?
                """,
                (datetime.now(tz=timezone.utc).isoformat(), _MAIN_NS),
            )
            await conn.commit()

            # 2. 查询待删除的 thread_id 列表
            cur = await conn.execute(
                "SELECT thread_id FROM thread_meta WHERE last_active_at < ?",
                (cutoff,),
            )
            rows = await cur.fetchall()
            await cur.close()
            expired_ids = [r[0] for r in rows]
            if not expired_ids:
                return 0

            # 3. 逐个删除（先 writes → checkpoints → meta）
            deleted = 0
            for tid in expired_ids:
                try:
                    await conn.execute(
                        "DELETE FROM writes WHERE thread_id = ?", (tid,)
                    )
                    await conn.execute(
                        "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ?",
                        (tid, _MAIN_NS),
                    )
                    await conn.execute(
                        "DELETE FROM thread_meta WHERE thread_id = ?", (tid,)
                    )
                    deleted += 1
                except sqlite3.Error as exc:
                    logger.warning("cleanup_thread failed", thread_id=tid, error=str(exc))
            await conn.commit()
            logger.info("checkpoint cleanup removed {} expired threads", deleted)
            return deleted
    except sqlite3.Error as exc:
        logger.warning("cleanup_expired_checkpoints failed", error=str(exc))
        return 0


def start_checkpoint_reaper(interval_hours: float = 6.0) -> asyncio.Task:
    """启动后台周期清理任务（每 6h 执行一次 ``cleanup_expired_checkpoints``）。

    lifespan 关闭时 cancel。
    """

    async def _reaper_loop() -> None:
        while True:
            await asyncio.sleep(interval_hours * 3600)
            try:
                await cleanup_expired_checkpoints()
            except Exception as exc:  # noqa: BLE001
                logger.warning("checkpoint reaper error: {}", exc)

    return asyncio.create_task(_reaper_loop())


__all__ = [
    "ThreadIdInvalid",
    "cleanup_expired_checkpoints",
    "delete_thread",
    "get_db_size",
    "list_checkpoints",
    "list_threads",
    "rewind_thread",
    "start_checkpoint_reaper",
    "touch_thread",
]
