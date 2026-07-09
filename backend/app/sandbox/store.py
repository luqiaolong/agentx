"""沙箱授权 SQLite 持久化层（WAL 模式 + busy_timeout）。

从 ``app.memory.sandbox_store`` 迁移而来，与 ``deep/`` / ``team/`` / ``tools/`` 平行。

改进：
- ``PRAGMA journal_mode=WAL``：写前日志模式，并发读不阻塞写
- ``PRAGMA busy_timeout=30000``：锁等待 30 秒，避免短时竞争报错
- ``sqlite3.connect(timeout=30)``：连接级超时 30 秒

设计要点：
- 使用 ``sqlite3`` 同步连接，公共方法通过 ``asyncio.to_thread`` 在线程池执行
  （方案 B），避免阻塞 ``SessionSandbox`` 所在事件循环
- 每次操作 ``with sqlite3.connect(...)`` 建立短连接，避免连接生命周期管理
- ``source`` 字段 UPSERT 优先级：``manual`` > ``chip`` > ``legacy``
- 本层为纯 CRUD，异常直接抛给调用方
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR

_DB_FILENAME = "agentx.db"


def _db_path() -> Path:
    return DATA_DIR / _DB_FILENAME


@dataclass(frozen=True)
class SandboxEntry:
    """授权记录（从 DB 读取后的内存表示）。"""

    thread_id: str
    resolved_path: Path
    writable: bool
    source: str


class SandboxStore:
    """``sandbox_authorize`` 表的 CRUD 封装。

    所有公共方法均为 ``async``：实际 sqlite3 同步逻辑放在 ``_sync_*`` 私有方法，
    通过 ``asyncio.to_thread`` 在线程池执行（方案 B），避免阻塞事件循环。
    每次操作建立短连接，WAL 模式 + busy_timeout 保证并发写不锁。
    SQLite 本地文件 IO 足够快（< 5ms），无需连接池。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _db_path()
        # 建表在初始化时一次性完成，并设置 WAL 模式
        with sqlite3.connect(str(self._db_path), timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._ensure_table(conn)

    def _ensure_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sandbox_authorize (
                thread_id     TEXT    NOT NULL,
                resolved_path TEXT    NOT NULL,
                writable      INTEGER NOT NULL,
                source        TEXT    NOT NULL,
                created_at    TEXT    NOT NULL,
                PRIMARY KEY (thread_id, resolved_path)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sandbox_thread ON sandbox_authorize(thread_id)"
        )
        conn.commit()

    async def upsert(
        self, thread_id: str, resolved_path: str, writable: bool, source: str
    ) -> None:
        """UPSERT 一条授权记录。manual source 不被 chip 覆盖。"""
        await asyncio.to_thread(
            self._sync_upsert, thread_id, resolved_path, writable, source
        )

    def _sync_upsert(
        self, thread_id: str, resolved_path: str, writable: bool, source: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(str(self._db_path), timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute(
                """
                INSERT INTO sandbox_authorize (thread_id, resolved_path, writable, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(thread_id, resolved_path) DO UPDATE SET
                    writable = excluded.writable,
                    source = CASE
                        WHEN sandbox_authorize.source = 'manual' THEN 'manual'
                        ELSE excluded.source
                    END
                """,
                (thread_id, str(resolved_path), int(writable), source, now),
            )
            conn.commit()

    async def delete_by_path(self, thread_id: str, resolved_path: str) -> bool:
        """删除单条授权记录。返回是否曾存在。"""
        return await asyncio.to_thread(
            self._sync_delete_by_path, thread_id, resolved_path
        )

    def _sync_delete_by_path(self, thread_id: str, resolved_path: str) -> bool:
        with sqlite3.connect(str(self._db_path), timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            cur = conn.execute(
                "DELETE FROM sandbox_authorize WHERE thread_id=? AND resolved_path=?",
                (thread_id, str(resolved_path)),
            )
            conn.commit()
            return cur.rowcount > 0

    async def delete_by_thread(self, thread_id: str) -> int:
        """删除 thread 下所有授权记录。返回删除行数。"""
        return await asyncio.to_thread(self._sync_delete_by_thread, thread_id)

    def _sync_delete_by_thread(self, thread_id: str) -> int:
        with sqlite3.connect(str(self._db_path), timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            cur = conn.execute(
                "DELETE FROM sandbox_authorize WHERE thread_id=?",
                (thread_id,),
            )
            conn.commit()
            return cur.rowcount

    async def list_by_thread(self, thread_id: str) -> list[SandboxEntry]:
        """列出 thread 的所有授权记录。"""
        return await asyncio.to_thread(self._sync_list_by_thread, thread_id)

    def _sync_list_by_thread(self, thread_id: str) -> list[SandboxEntry]:
        with sqlite3.connect(str(self._db_path), timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            cur = conn.execute(
                "SELECT thread_id, resolved_path, writable, source FROM sandbox_authorize WHERE thread_id=?",
                (thread_id,),
            )
            return [
                SandboxEntry(
                    thread_id=row[0],
                    resolved_path=Path(row[1]),
                    writable=bool(row[2]),
                    source=row[3],
                )
                for row in cur.fetchall()
            ]

    async def bootstrap_all(self) -> dict[str, set[tuple[Path, bool]]]:
        """全量加载，按 thread_id 分组返回 {(path, writable), ...}。"""
        return await asyncio.to_thread(self._sync_bootstrap_all)

    def _sync_bootstrap_all(self) -> dict[str, set[tuple[Path, bool]]]:
        result: dict[str, set[tuple[Path, bool]]] = {}
        with sqlite3.connect(str(self._db_path), timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            cur = conn.execute(
                "SELECT thread_id, resolved_path, writable FROM sandbox_authorize"
            )
            for row in cur.fetchall():
                tid, path_str, writable = row[0], row[1], bool(row[2])
                result.setdefault(tid, set()).add((Path(path_str), writable))
        return result


_store: SandboxStore | None = None


def get_sandbox_store() -> SandboxStore:
    """返回模块级单例 SandboxStore。"""
    global _store
    if _store is None:
        _store = SandboxStore()
    return _store
