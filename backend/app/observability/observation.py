"""Agent 观测中心：SQLite 持久化 + LangChain Callback 自动捕获。

100% 复用现成框架：
- LangChain ``BaseCallbackHandler``（钩子触发时机由框架保证）
- LangGraph ``checkpointer.aget``（state 快照读取，本模块仅做镜像）
- ``app.observability.langsmith.redact``（5 字段黑名单脱敏，不重复实现）

4 张表（独立文件 ``data/agent_observation.db``，WAL + busy_timeout=30000，
与 ``sandbox/store.py`` 同款配置）：
- ``observation_run``      — 一次完整 chat 请求 = 一行（run_id = trace_id）
- ``observation_event``    — 每条 SSE 事件 = 一行，append-only
- ``observation_tool_call``— tool_call 聚合行（便于分析失败率）
- ``observation_feedback`` — 用户反馈（显式 👍/👎 + 隐式信号）

写路径全部 ``asyncio.to_thread`` 异步化，避免阻塞事件循环。Callback 钩子
是同步的（LangChain 约定），直接调 sync 写方法（< 5ms），异常隔离不影响 agent。
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from langchain_core.callbacks import BaseCallbackHandler

from app.config import DATA_DIR, get_settings
from app.observability.langsmith import redact

_DB_FILENAME = "agent_observation.db"

# state snapshot 白名单字段（从 channel_values 提取，避免存全量 state）
_STATE_WHITELIST = ("messages", "authorized_dirs", "_rubric_status", "remaining_steps")
# messages 截断条数（只存最近 N 条，避免存储爆炸）
_STATE_MSG_LIMIT = 20
# tool result / LLM 输入截断
_RESULT_PREVIEW_LIMIT = 2000
_LLM_INPUT_LIMIT = 4000

# FR-7.1: comment 值脱敏正则 — 匹配 ``api_key is xxx`` / ``password: xxx`` / ``sk-xxx`` 等
# 捕获组 1 = 字段名，替换为 ``\1: <redacted>``
_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|password|passwd|secret|token|credential)"
    r"\s*(?:is|=|:)?\s*\S+"
)
# 独立密钥前缀模式（sk-/AKIA/glm-/hf_ 等）
_SECRET_PREFIX_RE = re.compile(
    r"\b(?:sk-[a-zA-Z0-9]+|AKIA[0-9A-Z]{16}|hf_[a-zA-Z0-9]+|glm-[a-zA-Z0-9]+)"
)


def _redact_comment_value(comment: str) -> str:
    """对 comment 文本做值级脱敏（FR-7.1）。

    ``redact()`` 仅按字段名脱敏；但用户可能在反馈正文里粘贴密钥，
    故对 comment 值额外做正则扫描：
    - ``api_key is sk-xxx`` → ``api_key: <redacted>``
    - ``my password: abc123`` → ``my password: <redacted>``
    - 独立 ``sk-xxx`` → ``<redacted>``
    """
    redacted = _SECRET_VALUE_RE.sub(r"\1: <redacted>", comment)
    redacted = _SECRET_PREFIX_RE.sub("<redacted>", redacted)
    return redacted


def _db_path() -> Path:
    return DATA_DIR / _DB_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Protocol
# ============================================================


class ObservationSink(Protocol):
    """观测数据写入协议（5 个核心 async 方法）。"""

    async def append_event(
        self, run_id: str, seq: int, event_type: str, payload: dict[str, Any]
    ) -> None: ...

    async def record_prompt(
        self,
        run_id: str,
        system_prompt: str,
        user_message: str,
        history_preview: str,
    ) -> None: ...

    async def record_state_snapshot(
        self, run_id: str, kind: str, state: dict[str, Any]
    ) -> None: ...

    async def write_feedback(
        self,
        run_id: str,
        kind: str,
        score: float | None = None,
        comment: str | None = None,
        categories: list[str] | None = None,
    ) -> int: ...

    async def close(self) -> None: ...


# ============================================================
# SQLite 实现
# ============================================================


class SqliteObservationSink:
    """``ObservationSink`` 的 SQLite 实现。

    4 张表 + WAL + busy_timeout=30000。async 方法内部用 ``asyncio.to_thread``
    包装 sync 写，保证不阻塞事件循环。同步方法（``_*_sync``）供 Callback 直调。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _db_path()
        self._closed = False
        # 持久写连接（check_same_thread=False + Lock 串行化），
        # 避免 1000+ event 场景每条都 connect+PRAGMA 的开销（NFR-1 性能）
        self._write_lock = threading.Lock()
        self._write_conn = sqlite3.connect(
            str(self._db_path), timeout=30, check_same_thread=False
        )
        self._write_conn.execute("PRAGMA journal_mode=WAL")
        self._write_conn.execute("PRAGMA busy_timeout=30000")
        # synchronous=NORMAL 在 WAL 模式下安全（仅断电丢最后一条，不损坏 DB），
        # 减少 fsync 开销，对高频 append 至关重要（NFR-1）
        self._write_conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_table(self._write_conn)

    def _ensure_table(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS observation_run (
                run_id              TEXT PRIMARY KEY,
                trace_id            TEXT NOT NULL,
                thread_id           TEXT NOT NULL,
                agent_mode          TEXT NOT NULL,
                permission_mode     TEXT,
                user_message        TEXT NOT NULL,
                workspace_path      TEXT,
                final_prompt        TEXT,
                history_preview     TEXT,
                state_snapshots_json TEXT,
                result_text         TEXT,
                result_token_count  INTEGER,
                duration_ms         INTEGER,
                started_at          TIMESTAMP NOT NULL,
                ended_at            TIMESTAMP,
                error_type          TEXT,
                error_message       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_obsrun_thread ON observation_run(thread_id, started_at);

            CREATE TABLE IF NOT EXISTS observation_event (
                event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                ts          TIMESTAMP NOT NULL,
                event_type  TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(run_id, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_obsevent_run ON observation_event(run_id, seq);

            CREATE TABLE IF NOT EXISTS observation_tool_call (
                tool_call_id        TEXT PRIMARY KEY,
                run_id              TEXT NOT NULL,
                tool_name           TEXT NOT NULL,
                call_seq            INTEGER,
                args_json           TEXT,
                result_preview      TEXT,
                result_token_count  INTEGER,
                duration_ms         INTEGER,
                approval_decision   TEXT,
                approved            INTEGER,
                error_message       TEXT,
                started_at          TIMESTAMP,
                ended_at            TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_obstool_run ON observation_tool_call(run_id);

            CREATE TABLE IF NOT EXISTS observation_feedback (
                feedback_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          TEXT NOT NULL,
                kind            TEXT NOT NULL,
                score           REAL,
                comment         TEXT,
                categories_json TEXT,
                created_at      TIMESTAMP NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_obsfb_run ON observation_feedback(run_id);
            """
        )
        conn.commit()

    # ---- sync 写方法（供 Callback + async 包装共用） ----

    @property
    def _write_conn_locked(self) -> sqlite3.Connection:
        """获取持久写连接（调用方需持有 ``self._write_lock``）。"""
        return self._write_conn

    def _connect_read(self) -> sqlite3.Connection:
        """短连接用于读操作（WAL 模式下读不阻塞写）。"""
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def _write_ctx(self) -> Iterator[sqlite3.Connection]:
        """写操作上下文：持有 Lock + 使用持久连接，成功 commit / 异常 rollback。"""
        with self._write_lock:
            conn = self._write_conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def start_run_sync(
        self,
        run_id: str,
        trace_id: str,
        thread_id: str,
        agent_mode: str,
        permission_mode: str | None,
        user_message: str,
        workspace_path: str | None,
    ) -> None:
        with self._write_ctx() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO observation_run
                   (run_id, trace_id, thread_id, agent_mode, permission_mode,
                    user_message, workspace_path, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    trace_id,
                    thread_id,
                    agent_mode,
                    permission_mode,
                    user_message,
                    workspace_path,
                    _now(),
                ),
            )
            conn.commit()

    def end_run_sync(
        self,
        run_id: str,
        result_text: str | None = None,
        result_token_count: int | None = None,
        duration_ms: int | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._write_ctx() as conn:
            conn.execute(
                """UPDATE observation_run SET
                       result_text=?, result_token_count=?, duration_ms=?,
                       ended_at=?, error_type=?, error_message=?
                   WHERE run_id=?""",
                (
                    result_text,
                    result_token_count,
                    duration_ms,
                    _now(),
                    error_type,
                    error_message,
                    run_id,
                ),
            )
            conn.commit()

    def _append_event_sync(
        self, run_id: str, seq: int, event_type: str, payload: dict[str, Any]
    ) -> None:
        # FR-1.5: 复用 redact 做 payload 脱敏
        safe_payload = redact(payload) if isinstance(payload, dict) else payload
        payload_json = json.dumps(safe_payload, ensure_ascii=False, default=str)
        with self._write_ctx() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO observation_event
                   (run_id, seq, ts, event_type, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, seq, _now(), event_type, payload_json),
            )
            conn.commit()

    def _record_prompt_sync(
        self,
        run_id: str,
        system_prompt: str,
        user_message: str,
        history_preview: str,
    ) -> None:
        with self._write_ctx() as conn:
            conn.execute(
                """UPDATE observation_run SET
                       final_prompt=?, history_preview=?
                   WHERE run_id=?""",
                (system_prompt, history_preview, run_id),
            )
            conn.commit()

    def _record_state_snapshot_sync(
        self, run_id: str, kind: str, state: dict[str, Any]
    ) -> None:
        # 白名单字段提取 + messages 截断
        snapshot: dict[str, Any] = {"kind": kind, "ts": _now()}
        for key in _STATE_WHITELIST:
            if key in state:
                val = state[key]
                if key == "messages":
                    # 截断到最近 N 条，每条取 content 文本
                    msgs = val if isinstance(val, list) else []
                    snapshot["messages"] = _truncate_messages(msgs[-_STATE_MSG_LIMIT:])
                else:
                    snapshot[key] = val
        # 读现有 snapshots 数组，追加，写回
        with self._write_ctx() as conn:
            cur = conn.execute(
                "SELECT state_snapshots_json FROM observation_run WHERE run_id=?",
                (run_id,),
            )
            row = cur.fetchone()
            existing: list[dict[str, Any]] = []
            if row and row[0]:
                try:
                    existing = json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    existing = []
            existing.append(snapshot)
            conn.execute(
                "UPDATE observation_run SET state_snapshots_json=? WHERE run_id=?",
                (json.dumps(existing, ensure_ascii=False, default=str), run_id),
            )
            conn.commit()

    def _write_feedback_sync(
        self,
        run_id: str,
        kind: str,
        score: float | None,
        comment: str | None,
        categories: list[str] | None,
    ) -> int:
        # FR-9 / FR-7.1: comment 写入前做值级脱敏（用户可能在正文里粘贴密钥）
        safe_comment = _redact_comment_value(comment) if comment else None
        cats_json = json.dumps(categories, ensure_ascii=False) if categories else None
        with self._write_ctx() as conn:
            cur = conn.execute(
                """INSERT INTO observation_feedback
                   (run_id, kind, score, comment, categories_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, kind, score, safe_comment, cats_json, _now()),
            )
            conn.commit()
            return int(cur.lastrowid)

    def append_tool_call_sync(
        self,
        tool_call_id: str,
        run_id: str,
        tool_name: str,
        call_seq: int | None,
        args: dict[str, Any] | None,
        started_at: str | None = None,
    ) -> None:
        args_json = (
            json.dumps(redact(args), ensure_ascii=False, default=str) if args else None
        )
        with self._write_ctx() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO observation_tool_call
                   (tool_call_id, run_id, tool_name, call_seq, args_json, started_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    tool_call_id,
                    run_id,
                    tool_name,
                    call_seq,
                    args_json,
                    started_at or _now(),
                ),
            )
            conn.commit()

    def end_tool_call_sync(
        self,
        tool_call_id: str,
        result_preview: str | None = None,
        result_token_count: int | None = None,
        duration_ms: int | None = None,
        error_message: str | None = None,
    ) -> None:
        preview = (
            result_preview[:_RESULT_PREVIEW_LIMIT] if result_preview else None
        )
        with self._write_ctx() as conn:
            conn.execute(
                """UPDATE observation_tool_call SET
                       result_preview=?, result_token_count=?, duration_ms=?,
                       error_message=?, ended_at=?
                   WHERE tool_call_id=?""",
                (
                    preview,
                    result_token_count,
                    duration_ms,
                    error_message,
                    _now(),
                    tool_call_id,
                ),
            )
            conn.commit()

    def update_tool_call_approval_sync(
        self, tool_call_id: str, approval_decision: str, approved: bool
    ) -> None:
        with self._write_ctx() as conn:
            conn.execute(
                """UPDATE observation_tool_call SET
                       approval_decision=?, approved=?
                   WHERE tool_call_id=?""",
                (approval_decision, int(approved), tool_call_id),
            )
            conn.commit()

    # ---- 读方法（供 API 端点 T4 使用） ----

    def get_run_sync(self, run_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM observation_run WHERE run_id=?", (run_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def list_runs_sync(self, thread_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM observation_run WHERE thread_id=? ORDER BY started_at DESC LIMIT ?",
                (thread_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def list_events_sync(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM observation_event WHERE run_id=? ORDER BY seq ASC",
                (run_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def list_feedback_sync(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM observation_feedback WHERE run_id=? ORDER BY created_at ASC",
                (run_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def find_pending_approval_tool_call_sync(
        self, run_id: str
    ) -> str | None:
        """查 run 内最近一条 ``approval_decision IS NULL`` 的 tool_call_id（供审批回填）。"""
        with self._connect_read() as conn:
            cur = conn.execute(
                """SELECT tool_call_id FROM observation_tool_call
                   WHERE run_id=? AND approval_decision IS NULL
                   ORDER BY started_at DESC LIMIT 1""",
                (run_id,),
            )
            row = cur.fetchone()
            return str(row[0]) if row else None

    def list_thumb_down_feedback_sync(
        self, days: int = 30
    ) -> list[dict[str, Any]]:
        """查最近 N 天 kind=thumb_down 的 feedback（供 eval export-feedback 使用）。"""
        with self._connect_read() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT f.*, r.user_message, r.agent_mode
                   FROM observation_feedback f
                   JOIN observation_run r ON f.run_id = r.run_id
                   WHERE f.kind='thumb_down'
                     AND f.created_at >= datetime('now', ?)
                   ORDER BY f.created_at DESC""",
                (f"-{days} days",),
            )
            return [dict(r) for r in cur.fetchall()]

    def cleanup_old_sync(self, ttl_days: int = 30) -> int:
        """删除 TTL 外的 run/event/tool_call 行。feedback 不删（永久保留）。"""
        cutoff = datetime.now(timezone.utc).timestamp() - ttl_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        with self._write_ctx() as conn:
            # 先删 event/tool_call，再删 run（feedback 保留，run_id 外键无强制约束）
            cur_e = conn.execute(
                "DELETE FROM observation_event WHERE run_id IN "
                "(SELECT run_id FROM observation_run WHERE started_at < ?)",
                (cutoff_iso,),
            )
            cur_t = conn.execute(
                "DELETE FROM observation_tool_call WHERE run_id IN "
                "(SELECT run_id FROM observation_run WHERE started_at < ?)",
                (cutoff_iso,),
            )
            cur_r = conn.execute(
                "DELETE FROM observation_run WHERE started_at < ?", (cutoff_iso,)
            )
            conn.commit()
            return cur_e.rowcount + cur_t.rowcount + cur_r.rowcount

    # ---- async 包装（Protocol 实现，to_thread 异步化） ----

    async def append_event(
        self, run_id: str, seq: int, event_type: str, payload: dict[str, Any]
    ) -> None:
        # 热路径优化：持久连接 + Lock 下 INSERT < 0.5ms，
        # 直接 inline 执行避免 asyncio.to_thread 每 call ~0.3ms 开销（NFR-1）
        self._append_event_sync(run_id, seq, event_type, payload)

    async def record_prompt(
        self,
        run_id: str,
        system_prompt: str,
        user_message: str,
        history_preview: str,
    ) -> None:
        await asyncio.to_thread(
            self._record_prompt_sync, run_id, system_prompt, user_message, history_preview
        )

    async def record_state_snapshot(
        self, run_id: str, kind: str, state: dict[str, Any]
    ) -> None:
        await asyncio.to_thread(self._record_state_snapshot_sync, run_id, kind, state)

    async def write_feedback(
        self,
        run_id: str,
        kind: str,
        score: float | None = None,
        comment: str | None = None,
        categories: list[str] | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._write_feedback_sync, run_id, kind, score, comment, categories
        )

    async def start_run(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.to_thread(self.start_run_sync, *args, **kwargs)

    async def end_run(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.to_thread(self.end_run_sync, *args, **kwargs)

    async def append_tool_call(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.to_thread(self.append_tool_call_sync, *args, **kwargs)

    async def end_tool_call(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.to_thread(self.end_tool_call_sync, *args, **kwargs)

    async def update_tool_call_approval(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.to_thread(self.update_tool_call_approval_sync, *args, **kwargs)

    async def cleanup_old(self, ttl_days: int = 30) -> int:
        return await asyncio.to_thread(self.cleanup_old_sync, ttl_days)

    async def close(self) -> None:
        self._closed = True
        with self._write_lock:
            try:
                self._write_conn.close()
            except Exception:  # noqa: BLE001
                pass


# ============================================================
# 模块级单例
# ============================================================

_sink: SqliteObservationSink | None = None


def get_observation_sink() -> SqliteObservationSink:
    """返回模块级单例 SqliteObservationSink（lifespan 启动时初始化）。"""
    global _sink
    if _sink is None:
        _sink = SqliteObservationSink()
    return _sink


def reset_observation_sink() -> None:
    """重置单例（测试用）。"""
    global _sink
    _sink = None


# ============================================================
# LangChain Callback
# ============================================================


class ObservationCallback(BaseCallbackHandler):
    """LangChain BaseCallbackHandler 实现，自动捕获 LLM/tool/chain 事件写入 observation 库。

    钩子是同步的（LangChain 约定），直接调 sync 写方法（< 5ms）。
    异常隔离（FR-2.3）：所有写操作 try/except 包裹，不影响 agent 主流程。
    """

    def __init__(self, sink: SqliteObservationSink, run_id: str) -> None:
        self._sink = sink
        self._run_id = run_id
        self._tool_seq: dict[str, int] = {}  # run-local tool call sequence
        self._tool_start_ts: dict[str, float] = {}
        self._seq_counter: int = 0  # run-local seq，避免跨 run 共享
        self._last_tool_call_id: str = ""  # 最近一次 on_tool_start 的 ID（on_tool_end fallback）

    def _safe(self, fn: Any, *args: Any) -> None:
        """异常隔离包装：callback 抛错不影响 agent 主流程。"""
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 — FR-2.3 隔离
            pass

    # ---- LLM ----

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        # 写 LLM 输入（messages 截断 4000 字符）
        inputs = {
            "prompts": [p[:_LLM_INPUT_LIMIT] for p in prompts],
            "serialized_name": serialized.get("name", ""),
        }
        self._safe(
            self._sink._append_event_sync,
            self._run_id,
            self._next_seq(),
            "llm_start",
            inputs,
        )

    def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        output_text = ""
        token_count = None
        try:
            output_text = str(response)
            token_count = getattr(response, "llm_output", {}).get("token_usage", {}).get("total_tokens")
        except Exception:  # noqa: BLE001
            pass
        self._safe(
            self._sink._append_event_sync,
            self._run_id,
            self._next_seq(),
            "llm_end",
            {"output": output_text[:_LLM_INPUT_LIMIT], "token_count": token_count},
        )

    # ---- Tool ----

    def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, *, run_id: Any = None, **kwargs: Any
    ) -> None:
        tool_name = serialized.get("name", "unknown")
        tool_call_id = str(run_id) if run_id else f"{tool_name}_{int(time.time()*1000)}"
        self._last_tool_call_id = tool_call_id
        self._tool_start_ts[tool_call_id] = time.perf_counter()
        seq = self._next_seq()
        self._tool_seq[tool_call_id] = seq
        # 写 tool_call 聚合行（args 后续 on_tool_end 时补）
        args = {"input": input_str[:_LLM_INPUT_LIMIT]}
        self._safe(
            self._sink.append_tool_call_sync,
            tool_call_id,
            self._run_id,
            tool_name,
            seq,
            args,
        )
        self._safe(
            self._sink._append_event_sync,
            self._run_id,
            seq,
            "tool_start",
            {"tool_call_id": tool_call_id, "tool_name": tool_name, "input": input_str[:_LLM_INPUT_LIMIT]},
        )

    def on_tool_end(
        self, output: str, *, run_id: Any = None, **kwargs: Any
    ) -> None:
        tool_call_id = str(run_id) if run_id else self._last_tool_call_id
        start_ts = self._tool_start_ts.pop(tool_call_id, time.perf_counter())
        duration_ms = int((time.perf_counter() - start_ts) * 1000)
        self._safe(
            self._sink.end_tool_call_sync,
            tool_call_id,
            str(output)[:_RESULT_PREVIEW_LIMIT],
            None,
            duration_ms,
            None,
        )
        self._safe(
            self._sink._append_event_sync,
            self._run_id,
            self._next_seq(),
            "tool_end",
            {"tool_call_id": tool_call_id, "output": str(output)[:_RESULT_PREVIEW_LIMIT]},
        )

    def on_tool_error(self, error: BaseException, *, run_id: Any = None, **kwargs: Any) -> None:
        tool_call_id = str(run_id) if run_id else self._last_tool_call_id
        start_ts = self._tool_start_ts.pop(tool_call_id, time.perf_counter())
        duration_ms = int((time.perf_counter() - start_ts) * 1000)
        self._safe(
            self._sink.end_tool_call_sync,
            tool_call_id,
            None,
            None,
            duration_ms,
            str(error),
        )

    # ---- Chain / LangGraph node ----

    def on_chain_start(
        self, serialized: dict[str, Any], inputs: dict[str, Any], *, run_id: Any = None, **kwargs: Any
    ) -> None:
        name = serialized.get("name", "") if isinstance(serialized, dict) else ""
        self._safe(
            self._sink._append_event_sync,
            self._run_id,
            self._next_seq(),
            "chain_start",
            {"name": name, "inputs": redact(inputs) if isinstance(inputs, dict) else str(inputs)[:_LLM_INPUT_LIMIT]},
        )

    def on_chain_end(self, outputs: dict[str, Any], *, run_id: Any = None, **kwargs: Any) -> None:
        self._safe(
            self._sink._append_event_sync,
            self._run_id,
            self._next_seq(),
            "chain_end",
            {"outputs": redact(outputs) if isinstance(outputs, dict) else str(outputs)[:_LLM_INPUT_LIMIT]},
        )

    # ---- seq 自增（run 内） ----

    def _next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter


# ============================================================
# 辅助
# ============================================================


def _truncate_messages(msgs: list[Any]) -> list[dict[str, Any]]:
    """把 LangGraph messages 截断为 [{role, content_preview}]，避免存全量。"""
    result: list[dict[str, Any]] = []
    for m in msgs:
        role = getattr(m, "type", None) or getattr(m, "role", None) or "unknown"
        content = getattr(m, "content", str(m))
        result.append({"role": role, "content": str(content)[:500]})
    return result


async def cleanup_old_observations(ttl_days: int | None = None) -> int:
    """清理 TTL 外的 observation 数据（feedback 永久保留）。"""
    days = ttl_days
    if days is None:
        days = getattr(get_settings(), "observation_ttl_days", 30)
    sink = get_observation_sink()
    return await sink.cleanup_old(days)
