"""沙箱授权 SQLite 持久化层。

在 ``data/agentx.db`` 中维护 ``sandbox_authorize`` 表，存储所有 thread 的
授权目录记录。与 LangGraph checkpoint 共用同一 DB 文件，但独立表独立管理。

设计要点：
- 使用 ``sqlite3`` 同步连接（``SessionSandbox.authorize/revoke/clear`` 是同步方法）
- 每次操作 ``with sqlite3.connect(...)`` 建立短连接，避免连接生命周期管理
- ``source`` 字段 UPSERT 优先级：``manual`` > ``chip`` > ``legacy``
- 本层为纯 CRUD，异常直接抛给调用方；best-effort 双写由 ``SessionSandbox._persist_*``
  承担（详见 design.md D4）
"""

from __future__ import annotations

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

    所有方法均为同步（``SessionSandbox`` 调用方是同步的）。每次操作建立短连接，
    SQLite 本地文件 IO 足够快（< 5ms），无需连接池。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _db_path()

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

    def upsert(
        self, thread_id: str, resolved_path: str, writable: bool, source: str
    ) -> None:
        """UPSERT 一条授权记录。manual source 不被 chip 覆盖。"""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(str(self._db_path)) as conn:
            self._ensure_table(conn)
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

    def delete_by_path(self, thread_id: str, resolved_path: str) -> bool:
        """删除单条授权记录。返回是否曾存在。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            self._ensure_table(conn)
            cur = conn.execute(
                "DELETE FROM sandbox_authorize WHERE thread_id=? AND resolved_path=?",
                (thread_id, str(resolved_path)),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_by_thread(self, thread_id: str) -> int:
        """删除 thread 下所有授权记录。返回删除行数。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            self._ensure_table(conn)
            cur = conn.execute(
                "DELETE FROM sandbox_authorize WHERE thread_id=?",
                (thread_id,),
            )
            conn.commit()
            return cur.rowcount

    def list_by_thread(self, thread_id: str) -> list[SandboxEntry]:
        """列出 thread 的所有授权记录。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            self._ensure_table(conn)
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

    def bootstrap_all(self) -> dict[str, set[tuple[Path, bool]]]:
        """全量加载，按 thread_id 分组返回 {(path, writable), ...}。"""
        result: dict[str, set[tuple[Path, bool]]] = {}
        with sqlite3.connect(str(self._db_path)) as conn:
            self._ensure_table(conn)
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
