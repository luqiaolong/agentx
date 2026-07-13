"""画像抽取持久化队列。

将原本 fire-and-forget 的画像抽取任务落到 SQLite 队列，避免 kill -9 / OOM 等
异常导致已提取的画像丢失。队列与 checkpointer 共用 ``data/agentx.db``，
启动时自动建表。

设计：
- ``enqueue``: 把 (message, assistant_reply, workspace_path) 写入队列表。
- ``dequeue``: 取出最老的一条任务；处理成功后再删除。
- 工作器崩溃时任务仍留在队列中，重启后会被重新消费（upsert 幂等，允许重复）。
- ``start_worker``: 后台循环消费队列。
- ``drain_queue``: 关闭前等待队列消费完毕（带超时）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.config import DATA_DIR
from app.memory.checkpointer import _DB_FILENAME
from app.observability.logger import logger

_QUEUE_TABLE = "profile_extract_queue"


@dataclass
class ExtractJob:
    """单条画像抽取任务。"""

    id: int
    message: str
    assistant_reply: str
    workspace_path: str | None
    created_at: str


async def _ensure_table(conn: aiosqlite.Connection) -> None:
    """确保队列表存在。"""
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_QUEUE_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            assistant_reply TEXT NOT NULL,
            workspace_path TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()


def _db_path() -> Path:
    """返回队列 SQLite 数据库路径（与 checkpointer 共享）。"""
    return DATA_DIR / _DB_FILENAME


async def enqueue(
    message: str,
    assistant_reply: str,
    workspace_path: str | None = None,
) -> None:
    """将画像抽取任务持久化到队列。

    Args:
        message: 用户原始消息。
        assistant_reply: 助手最后一轮回复文本。
        workspace_path: 工作区绝对路径；None 表示全局画像。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _db_path()
    async with aiosqlite.connect(str(db_path)) as conn:
        await _ensure_table(conn)
        await conn.execute(
            f"""
            INSERT INTO {_QUEUE_TABLE}
            (message, assistant_reply, workspace_path, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                message,
                assistant_reply,
                workspace_path,
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )
        await conn.commit()
    logger.info(
        "profile extract job enqueued",
        workspace_path=workspace_path,
        assistant_reply_len=len(assistant_reply),
    )


async def dequeue() -> ExtractJob | None:
    """原子取出最老的一条任务（peek）。

    读取后并不删除；工作器在 ``_process_job`` 成功后调用 ``_delete_job`` 删除。
    这种"先读再处理"模式保证：即使处理中途崩溃，任务也仍留在队列中会被重试；
    upsert 是幂等的，因此允许重复处理。
    """
    db_path = _db_path()
    if not db_path.exists():
        return None
    async with aiosqlite.connect(str(db_path)) as conn:
        await _ensure_table(conn)
        async with conn.execute(
            f"""
            SELECT id, message, assistant_reply, workspace_path, created_at
            FROM {_QUEUE_TABLE}
            ORDER BY id ASC
            LIMIT 1
            """
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return ExtractJob(
                id=row[0],
                message=row[1],
                assistant_reply=row[2],
                workspace_path=row[3],
                created_at=row[4],
            )


async def _delete_job(job_id: int) -> None:
    """删除已完成的任务。"""
    db_path = _db_path()
    async with aiosqlite.connect(str(db_path)) as conn:
        await _ensure_table(conn)
        await conn.execute(f"DELETE FROM {_QUEUE_TABLE} WHERE id = ?", (job_id,))
        await conn.commit()


async def _process_job(job: ExtractJob) -> None:
    """执行单条画像抽取任务。"""
    from app.memory.profile_extractor import extract_profile_via_llm
    from app.memory.profile_store import upsert_from_llm

    entries = await extract_profile_via_llm(job.message, job.assistant_reply)
    if not entries:
        return

    if job.workspace_path:
        from app.workspace.memory_store import upsert_from_llm as workspace_upsert_from_llm

        written = await workspace_upsert_from_llm(job.workspace_path, entries)
        logger.info(
            "workspace_memory.auto_extracted",
            count=written,
            workspace=job.workspace_path,
        )
    else:
        await upsert_from_llm(entries, workspace_path=None)
        logger.info(
            "profile auto extracted",
            count=len(entries),
            scope="global",
        )


async def _run_worker(poll_interval: float = 1.0) -> None:
    """后台工作器：循环消费队列。

    Args:
        poll_interval: 队列为空时的轮询间隔（秒）。
    """
    while True:
        try:
            job = await dequeue()
            if job is None:
                await asyncio.sleep(poll_interval)
                continue

            logger.info("profile extract worker processing job", job_id=job.id)
            try:
                await _process_job(job)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"profile extract job failed (job_id={job.id}), "
                    f"will retry on next poll: {type(exc).__name__}: {exc}"
                )
                # 不删除，留在队列中下次重试
                await asyncio.sleep(poll_interval)
                continue

            await _delete_job(job.id)
            logger.info("profile extract job completed", job_id=job.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("profile extract worker loop error", error=str(exc))
            await asyncio.sleep(poll_interval)


def start_worker(poll_interval: float = 1.0) -> asyncio.Task[None]:
    """启动后台画像抽取工作器。

    Returns:
        可用于取消的工作器 Task。
    """
    return asyncio.create_task(_run_worker(poll_interval=poll_interval))


async def drain_queue(timeout: float = 5.0) -> None:
    """关闭前等待队列清空（最多等待 timeout 秒）。

    注意：正在处理中的任务可能因取消而未能成功，此时未删除的任务会保留在队列中，
    下次启动时重新消费。
    """
    start = asyncio.get_event_loop().time()
    while True:
        job = await dequeue()
        if job is None:
            return
        if asyncio.get_event_loop().time() - start >= timeout:
            logger.warning(
                "profile extract queue drain timed out with pending jobs",
                remaining_job_id=job.id,
            )
            return
        # 关闭阶段同步处理，不依赖后台工作器
        try:
            await _process_job(job)
            await _delete_job(job.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"profile extract job failed during drain (job_id={job.id}): "
                f"{type(exc).__name__}: {exc}"
            )


async def queue_size() -> int:
    """返回队列中待处理任务数量（主要用于测试）。"""
    db_path = _db_path()
    if not db_path.exists():
        return 0
    async with aiosqlite.connect(str(db_path)) as conn:
        await _ensure_table(conn)
        async with conn.execute(
            f"SELECT COUNT(*) FROM {_QUEUE_TABLE}"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


__all__ = [
    "ExtractJob",
    "dequeue",
    "drain_queue",
    "enqueue",
    "queue_size",
    "start_worker",
]
