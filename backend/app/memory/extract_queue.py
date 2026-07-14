"""画像抽取持久化队列。

将原本 fire-and-forget 的画像抽取任务落到 SQLite 队列，避免 kill -9 / OOM 等
异常导致已提取的画像丢失。队列与 checkpointer 共用 ``data/agentx.db``，
启动时自动建表。

设计：
- ``enqueue``: 把 (message, assistant_reply, workspace_path) 写入队列表。
- ``dequeue``: 原子 claim 最老的一条 pending 任务（设 status='leased'）；
  处理成功后删除；失败则按 attempts 决定重试或进入死信。
- ``_claim_job``: UPDATE…RETURNING 原子领取任务，避免 peek+delete 竞态。
- 工作器崩溃时任务仍留在队列中（status='leased'），重启后可人工恢复或等 lease 过期。
- ``start_worker``: 后台循环消费队列。
- ``drain_queue``: 关闭前等待队列消费完毕（带超时）；不处理已被其他 worker lease 的任务。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from app.config import DATA_DIR
from app.memory.checkpointer import _DB_FILENAME
from app.observability.logger import logger

_QUEUE_TABLE = "profile_extract_queue"

# 最大重试次数；超过后任务进入死信状态
MAX_ATTEMPTS = 3

# lease 有效期（秒）；worker crash 后任务在此时间内不会被其他 worker 重复领取
_LEASE_DURATION_SECONDS = 60

# T3.5：置信度阈值；低于此值的抽取条目不会被自动写入
_CONFIDENCE_THRESHOLD = 0.6


@dataclass
class ExtractJob:
    """单条画像抽取任务。"""

    id: int
    message: str
    assistant_reply: str
    workspace_path: str | None
    created_at: str


async def _ensure_column(conn: aiosqlite.Connection, name: str, definition: str) -> None:
    """若列不存在则添加（SQLite 不支持 ADD COLUMN IF NOT EXISTS）。"""
    async with conn.execute(f"PRAGMA table_info({_QUEUE_TABLE})") as cursor:
        rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    if name not in existing:
        await conn.execute(f"ALTER TABLE {_QUEUE_TABLE} ADD COLUMN {definition}")


async def _ensure_table(conn: aiosqlite.Connection) -> None:
    """确保队列表存在并包含 status / attempts / last_error / leased_until 列。"""
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_QUEUE_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            assistant_reply TEXT NOT NULL,
            workspace_path TEXT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            leased_until TEXT
        )
        """
    )
    # 旧表迁移：为已存在的表补列
    await _ensure_column(conn, "status", "status TEXT NOT NULL DEFAULT 'pending'")
    await _ensure_column(conn, "attempts", "attempts INTEGER NOT NULL DEFAULT 0")
    await _ensure_column(conn, "last_error", "last_error TEXT")
    await _ensure_column(conn, "leased_until", "leased_until TEXT")
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
            (message, assistant_reply, workspace_path, created_at, status, attempts)
            VALUES (?, ?, ?, ?, 'pending', 0)
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


async def _claim_job(conn: aiosqlite.Connection) -> ExtractJob | None:
    """原子领取最老的一条 pending 或租约过期任务（设 status='leased' + leased_until）。

    使用 ``UPDATE … WHERE id = (SELECT … LIMIT 1) RETURNING …`` 单语句完成
    领取，避免 peek+update 之间的竞态。会领取：
    - status='pending' 的任务
    - status='leased' 且 leased_until < 当前 UTC 时间的任务（worker 崩溃后恢复）
    """
    now = datetime.now(tz=timezone.utc)
    lease_until = (now + timedelta(seconds=_LEASE_DURATION_SECONDS)).isoformat()
    now_iso = now.isoformat()
    async with conn.execute(
        f"""
        UPDATE {_QUEUE_TABLE}
        SET status = 'leased', leased_until = ?
        WHERE id = (
            SELECT id FROM {_QUEUE_TABLE}
            WHERE status = 'pending'
               OR (status = 'leased' AND leased_until < ?)
            ORDER BY id ASC
            LIMIT 1
        )
        RETURNING id, message, assistant_reply, workspace_path, created_at
        """,
        (lease_until, now_iso),
    ) as cursor:
        row = await cursor.fetchone()
        if row is None:
            return None
        await conn.commit()
        return ExtractJob(
            id=row[0],
            message=row[1],
            assistant_reply=row[2],
            workspace_path=row[3],
            created_at=row[4],
        )


async def dequeue() -> ExtractJob | None:
    """原子领取最老的一条 pending 任务。

    通过 ``_claim_job`` 原子地将任务标记为 ``leased`` 并返回。
    工作器在 ``_process_job`` 成功后调用 ``_delete_job`` 删除；
    失败则由 ``_handle_job_failure`` 决定重试（status='pending'）或死信（status='dead_letter'）。
    """
    db_path = _db_path()
    if not db_path.exists():
        return None
    async with aiosqlite.connect(str(db_path)) as conn:
        await _ensure_table(conn)
        return await _claim_job(conn)


async def _delete_job(job_id: int) -> None:
    """删除已完成的任务。"""
    db_path = _db_path()
    async with aiosqlite.connect(str(db_path)) as conn:
        await _ensure_table(conn)
        await conn.execute(f"DELETE FROM {_QUEUE_TABLE} WHERE id = ?", (job_id,))
        await conn.commit()


async def _handle_job_failure(job_id: int, error: str) -> None:
    """处理任务失败：原子递增 attempts，记录 last_error，按次数决定重试或死信。

    使用单条 UPDATE 原子地递增 attempts 并根据新值决定 status，避免
    read-then-write 竞态（尤其是 lease 恢复后可能多 worker 并发处理同一任务）。

    - attempts + 1 < MAX_ATTEMPTS：status 置回 'pending'，等待重试
    - attempts + 1 >= MAX_ATTEMPTS：status 置为 'dead_letter'，不再自动重试
    """
    db_path = _db_path()
    async with aiosqlite.connect(str(db_path)) as conn:
        await _ensure_table(conn)
        async with conn.execute(
            f"""
            UPDATE {_QUEUE_TABLE}
            SET attempts = attempts + 1,
                last_error = ?,
                status = CASE WHEN attempts + 1 >= ? THEN 'dead_letter' ELSE 'pending' END,
                leased_until = NULL
            WHERE id = ?
            """,
            (error, MAX_ATTEMPTS, job_id),
        ) as cursor:
            affected = cursor.rowcount or 0
        await conn.commit()

        if affected == 0:
            return

        # 读取新状态用于日志
        async with conn.execute(
            f"SELECT attempts, status FROM {_QUEUE_TABLE} WHERE id = ?",
            (job_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return
            attempts, new_status = row[0], row[1]

        logger.warning(
            "profile extract job failure recorded",
            job_id=job_id,
            attempts=attempts,
            status=new_status,
            error=error,
        )


async def _process_job(job: ExtractJob) -> None:
    """执行单条画像抽取任务。

    - FAILED → 抛异常，由 ``_run_worker`` 捕获并调用 ``_handle_job_failure``
    - SUCCESS_EMPTY → 正常返回（任务完成，无内容可写）
    - SUCCESS_WRITTEN → 过滤低置信度条目后写入画像

    T3.5：低置信度（confidence < 0.6）的条目不会被自动写入，
    仅记录日志后跳过。
    """
    from app.memory.profile_extractor import ExtractStatus, extract_profile_via_llm
    from app.memory.profile_store import upsert_from_llm

    result = await extract_profile_via_llm(job.message, job.assistant_reply)

    if result.status == ExtractStatus.FAILED:
        raise RuntimeError(f"LLM 抽取失败: {result.error}")

    if result.status == ExtractStatus.SUCCESS_EMPTY:
        logger.info("profile extract job success_empty", job_id=job.id)
        return

    # T3.5：过滤低置信度条目（confidence < 0.6 不自动写入）
    entries = result.entries
    qualified = []
    skipped = []
    for entry in entries:
        confidence = float(entry.get("confidence", 0.8))
        if confidence < _CONFIDENCE_THRESHOLD:
            skipped.append(entry)
        else:
            qualified.append(entry)

    if skipped:
        for entry in skipped:
            logger.info(
                "profile extract entry skipped (low confidence)",
                job_id=job.id,
                key=entry.get("key"),
                confidence=entry.get("confidence"),
            )

    if not qualified:
        logger.info(
            "profile extract job completed with no qualified entries",
            job_id=job.id,
            total=len(entries),
            skipped=len(skipped),
        )
        return

    # SUCCESS_WRITTEN：写入过滤后的条目
    if job.workspace_path:
        from app.workspace.memory_store import upsert_from_llm as workspace_upsert_from_llm

        written = await workspace_upsert_from_llm(job.workspace_path, qualified)
        logger.info(
            "workspace_memory.auto_extracted",
            count=written,
            workspace=job.workspace_path,
            skipped_low_confidence=len(skipped),
        )
    else:
        await upsert_from_llm(qualified, workspace_path=None)
        logger.info(
            "profile auto extracted",
            count=len(qualified),
            scope="global",
            skipped_low_confidence=len(skipped),
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
                # 递增 attempts，决定重试或死信；不删除任务
                await _handle_job_failure(job.id, str(exc))
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

    注意：
    - 正在处理中的任务可能因取消而未能成功，此时未删除的任务会保留在队列中，
      下次启动时重新消费。
    - 关闭阶段任务失败时不递增 attempts（不调用 ``_handle_job_failure``），
      避免单次 drain 期间反复 dequeue 同一失败任务而耗尽重试次数。任务保持
      'leased' 状态，下次启动时由 ``_claim_job`` 的 lease 过期恢复机制重新领取。
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
            # 不调用 _handle_job_failure：保持 'leased' 状态，下次启动时由
            # lease 过期恢复机制重新领取，避免单次 drain 耗尽重试次数。
            logger.warning(
                f"profile extract job failed during drain (job_id={job.id}), "
                f"leaving leased for next startup recovery: "
                f"{type(exc).__name__}: {exc}"
            )


async def queue_size() -> int:
    """返回队列中所有任务数量（主要用于测试）。"""
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


async def _get_job(job_id: int) -> dict | None:
    """返回指定任务的完整字段（主要用于测试）。"""
    db_path = _db_path()
    if not db_path.exists():
        return None
    async with aiosqlite.connect(str(db_path)) as conn:
        await _ensure_table(conn)
        async with conn.execute(
            f"""
            SELECT id, message, assistant_reply, workspace_path, created_at,
                   status, attempts, last_error, leased_until
            FROM {_QUEUE_TABLE}
            WHERE id = ?
            """,
            (job_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "message": row[1],
                "assistant_reply": row[2],
                "workspace_path": row[3],
                "created_at": row[4],
                "status": row[5],
                "attempts": row[6],
                "last_error": row[7],
                "leased_until": row[8],
            }


__all__ = [
    "ExtractJob",
    "MAX_ATTEMPTS",
    "dequeue",
    "drain_queue",
    "enqueue",
    "queue_size",
    "start_worker",
]
