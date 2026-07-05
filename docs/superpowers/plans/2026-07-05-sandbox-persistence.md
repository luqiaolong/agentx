# Sandbox Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `SessionSandbox.authorized_dirs` 从纯内存升级为 SQLite 持久化，并补齐前端 chip 隐式授权，让"用户选过工作区"直接等于"后端已放行"。

**Architecture:** 在 `data/agentx.db` 新建 `sandbox_authorize` 表，`SessionSandbox` 双写（内存 + DB），启动时全量加载回内存。前端 `createSession` / `moveSessionToWorkspace` 入口自动调 `authorize(source="chip")`，手动 `revoke` 后写入 `manuallyRevokedPaths` 阻止 chip 覆盖。

**Tech Stack:** Python 3.11+ / FastAPI / sqlite3 / aiosqlite / React 18 / TypeScript / zustand

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/memory/sandbox_store.py` | Create | `SandboxStore` 类：`sandbox_authorize` 表 DDL + CRUD + bootstrap |
| `backend/app/memory/__init__.py` | Modify | 导出 `SandboxStore` / `get_sandbox_store` |
| `backend/app/config.py` | Modify | 新增 `sandbox_persistence_enabled` 字段 |
| `backend/app/utils/security.py` | Modify | `SessionSandbox` 双写 + `bootstrap_from_store` |
| `backend/app/main.py` | Modify | lifespan bootstrap + DELETE 联动 + `AuthorizeRequest.source` |
| `frontend/shared/api-types.ts` | Modify | `AuthorizeRequest` 加 `source` 字段 |
| `frontend/preload/index.ts` | Modify | `sandbox.authorize` 签名加 `source` |
| `frontend/renderer/stores/chat.ts` | Modify | `manuallyRevokedPaths` + `revokeAndMark` / `authorizeAndUnmark` + 隐式授权 |
| `frontend/renderer/components/chat/ChatComposer.tsx` | Modify | `handleAttachWorkspace` 改调 `authorizeAndUnmark` |
| `tests/python/unit/test_sandbox_store.py` | Create | SandboxStore CRUD + bootstrap 测试 |
| `tests/python/unit/test_session_sandbox.py` | Modify | 追加双写 + source 优先级测试 |

---

### Task 1: Backend — config 开关

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: 加 `sandbox_persistence_enabled` 字段**

在 `backend/app/config.py` 的 `Settings` 类中，找到 `milvus_uri` property 附近，在 `ensure_runtime_dirs` 方法之前添加字段：

```python
    sandbox_persistence_enabled: bool = True
    """沙箱授权持久化开关。关闭时所有双写降级为内存-only（故障注入/调试用）。"""
```

字段会自动从 `AGENTX_SANDBOX_PERSISTENCE_ENABLED` env 读取（pydantic-settings 的 `env_prefix="AGENTX_"` 机制）。

- [ ] **Step 2: 验证字段可读**

Run: `uv run python -c "from app.config import get_settings; s = get_settings(); print(s.sandbox_persistence_enabled)"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "feat(config): add sandbox_persistence_enabled flag"
```

---

### Task 2: Backend — SandboxStore 类

**Files:**
- Create: `backend/app/memory/sandbox_store.py`
- Modify: `backend/app/memory/__init__.py`
- Create: `tests/python/unit/test_sandbox_store.py`

- [ ] **Step 1: 写失败测试**

Create `tests/python/unit/test_sandbox_store.py`:

```python
"""SandboxStore 单元测试：CRUD 幂等 + bootstrap 全量加载 + delete_by_thread。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.memory.sandbox_store import SandboxStore


@pytest.fixture
def store(tmp_path: Path) -> SandboxStore:
    """每个测试用例使用独立 tmp DB 文件。"""
    return SandboxStore(db_path=tmp_path / "test.db")


def test_table_auto_created(store: SandboxStore) -> None:
    """首次调用 upsert 时自动建表，不抛异常。"""
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    # 不抛异常即通过


def test_upsert_idempotent(store: SandboxStore) -> None:
    """同 thread_id + path 二次 upsert 不重复，writable 更新。"""
    store.upsert("t1", "d:/docs", writable=False, source="manual")
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    rows = store.list_by_thread("t1")
    assert len(rows) == 1
    assert rows[0].writable is True


def test_source_manual_not_overwritten_by_chip(store: SandboxStore) -> None:
    """manual 优先级 > chip，chip 调用不覆盖 manual source。"""
    store.upsert("t1", "d:/docs", writable=False, source="manual")
    store.upsert("t1", "d:/docs", writable=True, source="chip")
    rows = store.list_by_thread("t1")
    assert rows[0].source == "manual"
    assert rows[0].writable is True  # writable 仍更新


def test_source_chip_upgraded_to_manual(store: SandboxStore) -> None:
    """chip 记录被 manual 调用升级。"""
    store.upsert("t1", "d:/docs", writable=False, source="chip")
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    rows = store.list_by_thread("t1")
    assert rows[0].source == "manual"


def test_delete_by_path(store: SandboxStore) -> None:
    """删除单条授权。"""
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    store.upsert("t1", "d:/book", writable=False, source="chip")
    deleted = store.delete_by_path("t1", "d:/docs")
    assert deleted is True
    rows = store.list_by_thread("t1")
    assert len(rows) == 1
    assert str(rows[0].resolved_path) == str(Path("d:/book").resolve())


def test_delete_by_thread(store: SandboxStore) -> None:
    """删除 thread 下所有授权。"""
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    store.upsert("t1", "d:/book", writable=False, source="chip")
    store.upsert("t2", "d:/other", writable=True, source="manual")
    count = store.delete_by_thread("t1")
    assert count == 2
    assert len(store.list_by_thread("t1")) == 0
    assert len(store.list_by_thread("t2")) == 1


def test_bootstrap_all(store: SandboxStore) -> None:
    """全量加载，按 thread_id 分组。"""
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    store.upsert("t1", "d:/book", writable=False, source="chip")
    store.upsert("t2", "d:/other", writable=True, source="manual")
    result = store.bootstrap_all()
    assert set(result.keys()) == {"t1", "t2"}
    assert len(result["t1"]) == 2
    assert len(result["t2"]) == 1


def test_bootstrap_all_empty_db(store: SandboxStore) -> None:
    """空表 bootstrap 返回空 dict。"""
    result = store.bootstrap_all()
    assert result == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/python/unit/test_sandbox_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.memory.sandbox_store'`

- [ ] **Step 3: 实现 SandboxStore**

Create `backend/app/memory/sandbox_store.py`:

```python
"""沙箱授权 SQLite 持久化层。

在 ``data/agentx.db`` 中维护 ``sandbox_authorize`` 表，存储所有 thread 的
授权目录记录。与 LangGraph checkpoint 共用同一 DB 文件，但独立表独立管理。

设计要点：
- 使用 ``sqlite3`` 同步连接（``SessionSandbox.authorize/revoke/clear`` 是同步方法）
- 每次操作 ``with sqlite3.connect(...)`` 建立短连接，避免连接生命周期管理
- ``source`` 字段 UPSERT 优先级：``manual`` > ``chip`` > ``legacy``
- 所有写操作 best-effort：DB 失败仅 log warning，不抛异常（内存优先）
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR
from app.observability.logger import logger

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
```

- [ ] **Step 4: 导出**

Modify `backend/app/memory/__init__.py`，在现有导出列表中添加：

```python
from .sandbox_store import SandboxStore, get_sandbox_store
```

并在 `__all__` 列表中添加 `"SandboxStore"` 和 `"get_sandbox_store"`。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/python/unit/test_sandbox_store.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/memory/sandbox_store.py backend/app/memory/__init__.py tests/python/unit/test_sandbox_store.py
git commit -m "feat(sandbox): add SandboxStore SQLite persistence layer"
```

---

### Task 3: Backend — SessionSandbox 双写 + bootstrap

**Files:**
- Modify: `backend/app/utils/security.py`
- Modify: `tests/python/unit/test_session_sandbox.py`

- [ ] **Step 1: 写失败测试**

在 `tests/python/unit/test_session_sandbox.py` 末尾追加：

```python
from app.memory.sandbox_store import SandboxStore


@pytest.fixture
def store_sandbox(tmp_path: Path) -> SessionSandbox:
    """带持久化的 sandbox 实例，使用 tmp DB。"""
    store = SandboxStore(db_path=tmp_path / "test.db")
    return SessionSandbox(store=store)


def test_authorize_persists_to_db(store_sandbox: SessionSandbox) -> None:
    """authorize 写入内存同时写 DB。"""
    store_sandbox.authorize("t1", "d:/docs", writable=True)
    entries = store_sandbox._store.list_by_thread("t1")  # noqa: SLF001
    assert len(entries) == 1
    assert entries[0].source == "manual"


def test_revoke_deletes_from_db(store_sandbox: SessionSandbox) -> None:
    """revoke 内存同时删 DB。"""
    store_sandbox.authorize("t1", "d:/docs", writable=True)
    store_sandbox.revoke("t1", "d:/docs")
    entries = store_sandbox._store.list_by_thread("t1")  # noqa: SLF001
    assert len(entries) == 0


def test_clear_deletes_thread_from_db(store_sandbox: SessionSandbox) -> None:
    """clear 内存同时删 DB 该 thread 所有记录。"""
    store_sandbox.authorize("t1", "d:/docs", writable=True)
    store_sandbox.authorize("t1", "d:/book", writable=False)
    store_sandbox.clear("t1")
    entries = store_sandbox._store.list_by_thread("t1")  # noqa: SLF001
    assert len(entries) == 0


def test_bootstrap_restores_from_db(tmp_path: Path) -> None:
    """bootstrap 从 DB 恢复授权到内存。"""
    store = SandboxStore(db_path=tmp_path / "test.db")
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    store.upsert("t1", "d:/book", writable=False, source="chip")

    sandbox = SessionSandbox(store=store)
    sandbox.bootstrap_from_store()

    listed = sandbox.list_authorized("t1")
    paths = {str(p) for (p, _w) in listed}
    assert any("docs" in p for p in paths)
    assert any("book" in p for p in paths)


def test_persistence_disabled_no_db_write(tmp_path: Path) -> None:
    """sandbox_persistence_enabled=False 时不写 DB。"""
    from app.config import get_settings
    settings = get_settings()
    original = settings.sandbox_persistence_enabled
    settings.sandbox_persistence_enabled = False
    try:
        store = SandboxStore(db_path=tmp_path / "test.db")
        sandbox = SessionSandbox(store=store)
        sandbox.authorize("t1", "d:/docs", writable=True)
        entries = store.list_by_thread("t1")
        assert len(entries) == 0  # DB 未写入
    finally:
        settings.sandbox_persistence_enabled = original
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/python/unit/test_session_sandbox.py::test_authorize_persists_to_db -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'store'`

- [ ] **Step 3: 修改 SessionSandbox**

在 `backend/app/utils/security.py` 中：

1. 在文件顶部添加导入（`_DEFAULT_WHITELIST` 之后）：

```python
from app.config import get_settings
from app.memory.sandbox_store import SandboxStore, get_sandbox_store
```

2. 修改 `SessionSandbox.__init__`（约第 71 行）：

```python
    def __init__(self, store: SandboxStore | None = None) -> None:
        self.authorized_dirs: dict[str, set[tuple[Path, bool]]] = {}
        self.full_trust_threads: set[str] = set()
        self._temp_authorized: dict[str, set[tuple[Path, bool]]] = {}
        self._store: SandboxStore = store or get_sandbox_store()
```

3. 修改 `authorize` 方法签名（约第 209 行），加 `source` 参数 + 双写：

```python
    def authorize(
        self, thread_id: str, path: str | Path, writable: bool = False, source: str = "manual"
    ) -> Path:
        """授权目录。返回规范化后的 Path。拒绝系统关键目录。"""
        path_str = str(path).strip()
        if not path_str or path_str in (".", ".."):
            raise ValueError(f"路径 {path!r} 无效，请提供具体目录绝对路径")
        resolved = self._normalize(path)
        if self._is_critical(resolved):
            raise ValueError(f"路径 {path} 是系统关键目录，不可授权")
        with trace_span(
            "sandbox.authorize",
            thread_id=thread_id,
            path=str(path),
            writable=writable,
            action="authorize",
        ):
            entries = self.authorized_dirs.setdefault(thread_id, set())
            entries = {(p, w) for (p, w) in entries if p != resolved}
            entries.add((resolved, writable))
            self.authorized_dirs[thread_id] = entries
        # 双写到 DB（best-effort，失败不阻塞）
        self._persist_upsert(thread_id, resolved, writable, source)
        return resolved
```

4. 修改 `revoke` 方法，加 DB 删除（约第 235 行）：

```python
    def revoke(self, thread_id: str, path: str | Path) -> bool:
        """撤销授权。返回是否曾存在。"""
        resolved = self._normalize(path)
        with trace_span(
            "sandbox.revoke", thread_id=thread_id, path=str(path), action="revoke"
        ):
            entries = self.authorized_dirs.get(thread_id, set())
            before = len(entries)
            entries = {(p, w) for (p, w) in entries if p != resolved}
            self.authorized_dirs[thread_id] = entries
            existed = len(entries) < before
        # 双写到 DB（best-effort）
        self._persist_delete_path(thread_id, resolved)
        return existed
```

5. 修改 `clear` 方法，加 DB 删除（约第 247 行）：

```python
    def clear(self, thread_id: str) -> None:
        """清空该 thread_id 的所有授权（会话结束时调用）。"""
        self.authorized_dirs.pop(thread_id, None)
        # 双写到 DB（best-effort）
        self._persist_delete_thread(thread_id)
```

6. 在 `clear` 方法之后、`list_authorized` 之前，添加 bootstrap + persist 辅助方法：

```python
    def bootstrap_from_store(self) -> None:
        """启动时从 DB 全量加载授权到内存。失败仅 log error，不阻塞启动。"""
        try:
            loaded = self._store.bootstrap_all()
            for thread_id, entries in loaded.items():
                self.authorized_dirs[thread_id] = set(entries)
            logger.info(
                "sandbox.bootstrap_loaded",
                threads=len(loaded),
                total_entries=sum(len(e) for e in loaded.values()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("sandbox.bootstrap_failed: {}", exc)

    def _persist_upsert(
        self, thread_id: str, resolved: Path, writable: bool, source: str
    ) -> None:
        """best-effort DB upsert。失败仅 warning。"""
        if not get_settings().sandbox_persistence_enabled:
            return
        try:
            self._store.upsert(thread_id, str(resolved), writable, source)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox.db.write_failed: {}", exc)

    def _persist_delete_path(self, thread_id: str, resolved: Path) -> None:
        """best-effort DB delete by path。"""
        if not get_settings().sandbox_persistence_enabled:
            return
        try:
            self._store.delete_by_path(thread_id, str(resolved))
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox.db.write_failed: {}", exc)

    def _persist_delete_thread(self, thread_id: str) -> None:
        """best-effort DB delete by thread。"""
        if not get_settings().sandbox_persistence_enabled:
            return
        try:
            self._store.delete_by_thread(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox.db.write_failed: {}", exc)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/python/unit/test_session_sandbox.py -v`
Expected: all passed（含原有测试 + 5 个新测试）

- [ ] **Step 5: Commit**

```bash
git add backend/app/utils/security.py tests/python/unit/test_session_sandbox.py
git commit -m "feat(sandbox): double-write to SQLite + bootstrap_from_store"
```

---

### Task 4: Backend — main.py lifespan + DELETE 联动 + AuthorizeRequest.source

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: 修改 AuthorizeRequest 加 source 字段**

在 `backend/app/main.py` 约第 174 行：

```python
class AuthorizeRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    path: str = Field(..., description="待授权目录绝对路径")
    writable: bool = Field(False, description="是否允许写入（默认只读）")
    source: str = Field("manual", description="授权来源：manual（用户手动）/ chip（工作区自动同步）")
```

- [ ] **Step 2: 修改 sandbox_authorize 端点透传 source**

在 `sandbox_authorize` 函数中（约第 387 行），把 `sandbox.authorize` 调用改为：

```python
    resolved = sandbox.authorize(req.thread_id, req.path, writable=req.writable, source=req.source)
```

- [ ] **Step 3: lifespan 启动阶段加 bootstrap**

在 `backend/app/main.py` lifespan 函数中，checkpointer 初始化之后（约第 109 行之后），添加：

```python
    # 0.5. 沙箱授权从 DB 恢复
    try:
        get_sandbox().bootstrap_from_store()
        logger.info("sandbox bootstrap completed on startup")
    except Exception as exc:  # noqa: BLE001
        logger.warning("sandbox bootstrap failed on startup: {}", exc)
```

需要在文件顶部的 import 中添加 `get_sandbox`（如果尚未导入）：

```python
from app.utils.security import get_sandbox
```

- [ ] **Step 4: DELETE 端点联动删除 sandbox 记录**

在 `memory_checkpointer_delete` 函数中（约第 819 行），`delete_thread` 之后添加 sandbox 联动删除：

```python
@app.delete("/api/memory/checkpointer/{thread_id}")
async def memory_checkpointer_delete(thread_id: str) -> dict[str, Any]:
    """删除指定 thread 的所有 checkpoint + 沙箱授权记录。"""
    try:
        deleted = await delete_thread(thread_id)
    except ThreadIdInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 联动删除沙箱授权记录
    try:
        get_sandbox_store().delete_by_thread(thread_id)
        get_sandbox().clear(thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sandbox cleanup failed for thread {}: {}", thread_id, exc)
    return {"ok": True, "deleted": deleted}
```

在文件顶部 import 中添加：

```python
from app.memory.sandbox_store import get_sandbox_store
```

- [ ] **Step 5: 运行现有测试确认无回归**

Run: `uv run pytest tests/python/unit -m "not integration" -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(main): bootstrap sandbox on startup + DELETE联动 + source field"
```

---

### Task 5: Frontend — api-types + preload 签名

**Files:**
- Modify: `frontend/shared/api-types.ts`
- Modify: `frontend/preload/index.ts`

- [ ] **Step 1: api-types.ts 加 source 类型**

在 `frontend/shared/api-types.ts` 中 `AuthorizedDir` interface 附近添加：

```typescript
export type SandboxSource = "manual" | "chip";
```

- [ ] **Step 2: preload/index.ts 修改 authorize 签名**

在 `frontend/preload/index.ts` 约第 172 行，修改 `authorize` 方法签名加 `source` 可选参数：

```typescript
    authorize: async (threadId, p, writable, source) => {
      const r = await fetch(`${API_BASE}/api/sandbox/authorize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, path: p, writable: writable ?? false, source: source ?? "manual" }),
      });
      return r.json();
    },
```

- [ ] **Step 3: typecheck 确认无报错**

Run: `npm run typecheck`
Expected: 无新增报错（preload 的 `source` 参数是 optional，现有调用不传不会报错）

- [ ] **Step 4: Commit**

```bash
git add frontend/shared/api-types.ts frontend/preload/index.ts
git commit -m "feat(preload): add source param to sandbox.authorize"
```

---

### Task 6: Frontend — chat store 隐式授权 + 手动优先

**Files:**
- Modify: `frontend/renderer/stores/chat.ts`
- Modify: `frontend/renderer/components/chat/ChatComposer.tsx`

- [ ] **Step 1: Session interface 加 manuallyRevokedPaths**

在 `frontend/renderer/stores/chat.ts` 的 `Session` interface 中（约第 51 行），`workspacePath` 之后添加：

```typescript
export interface Session {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  workspacePath: string | null;
  /**
   * 用户手动 revoke 过的路径集合（用于阻止 chip 隐式授权覆盖）。
   * 用户重新手动 authorize 同一路径时会从此集合移除。
   * 持久化到 localStorage（跨重启保留）。
   */
  manuallyRevokedPaths: string[];
}
```

- [ ] **Step 2: createSessionRecord 初始化新字段**

修改 `createSessionRecord` 函数（约第 144 行）：

```typescript
function createSessionRecord(id: string, workspacePath: string | null = null): Session {
  return {
    id,
    title: DEFAULT_TITLE,
    messages: [],
    createdAt: Date.now(),
    workspacePath,
    manuallyRevokedPaths: [],
  };
}
```

- [ ] **Step 3: 迁移函数补字段**

在 `migrateV0toV1` / `migrateV1toV2`（约第 207-277 行，`workspacePath` 补字段的位置），同步补 `manuallyRevokedPaths`：

找到所有 `workspacePath:` 赋值的地方，在其后添加：

```typescript
        manuallyRevokedPaths:
          Array.isArray(raw.manuallyRevokedPaths) ? raw.manuallyRevokedPaths : [],
```

- [ ] **Step 4: ChatState interface 加新方法**

在 `ChatState` interface 中（约第 66 行），`moveSessionToWorkspace` 之后添加：

```typescript
  /** 手动撤销授权并标记，阻止 chip 隐式授权覆盖。 */
  revokeAndMark: (sessionId: string, path: string) => Promise<void>;
  /** 手动授权并清除 revoked 标记（handleAttachWorkspace 复用）。 */
  authorizeAndUnmark: (sessionId: string, path: string, writable?: boolean) => Promise<void>;
```

- [ ] **Step 5: 实现 createSession / moveSessionToWorkspace 隐式授权**

修改 `createSession`（约第 368 行）：

```typescript
        createSession: (workspacePath = null) => {
          const id = crypto.randomUUID();
          set((s) => {
            const sessions = {
              ...s.sessions,
              [id]: createSessionRecord(id, workspacePath),
            };
            const currentId = id;
            return { sessions, currentId };
          });
          // 隐式授权：workspacePath 非空时自动调 authorize（source=chip）
          if (workspacePath) {
            const sess = get().sessions[id];
            if (sess && !sess.manuallyRevokedPaths.includes(workspacePath)) {
              window.api.sandbox.authorize(id, workspacePath, true, "chip").catch(() => {});
            }
          }
          return id;
        },
```

修改 `moveSessionToWorkspace`（约第 415 行）：

```typescript
        moveSessionToWorkspace: (id, workspacePath) => {
          set((s) => {
            const sess = s.sessions[id];
            if (!sess) return s;
            if (sess.workspacePath === workspacePath) return s;
            const sessions = {
              ...s.sessions,
              [id]: { ...sess, workspacePath },
            };
            return { sessions };
          });
          // 隐式授权：workspacePath 非空且未被 revoke 时自动调 authorize（source=chip）
          if (workspacePath) {
            const sess = get().sessions[id];
            if (sess && !sess.manuallyRevokedPaths.includes(workspacePath)) {
              window.api.sandbox.authorize(id, workspacePath, true, "chip").catch(() => {});
            }
          }
        },
```

- [ ] **Step 6: 实现 revokeAndMark / authorizeAndUnmark**

在 `setHomeWorkspacePath` 之前添加：

```typescript
        revokeAndMark: async (sessionId, path) => {
          // 先调后端 revoke
          await window.api.sandbox.revoke(sessionId, path);
          // 再写入 manuallyRevokedPaths
          set((s) => {
            const sess = s.sessions[sessionId];
            if (!sess) return s;
            if (sess.manuallyRevokedPaths.includes(path)) return s;
            const sessions = {
              ...s.sessions,
              [sessionId]: {
                ...sess,
                manuallyRevokedPaths: [...sess.manuallyRevokedPaths, path],
              },
            };
            return { sessions };
          });
        },

        authorizeAndUnmark: async (sessionId, path, writable = true) => {
          // 先调后端 authorize（source=manual）
          await window.api.sandbox.authorize(sessionId, path, writable, "manual");
          // 再从 manuallyRevokedPaths 移除
          set((s) => {
            const sess = s.sessions[sessionId];
            if (!sess) return s;
            const sessions = {
              ...s.sessions,
              [sessionId]: {
                ...sess,
                manuallyRevokedPaths: sess.manuallyRevokedPaths.filter((p) => p !== path),
              },
            };
            return { sessions };
          });
        },
```

- [ ] **Step 7: ChatComposer.handleAttachWorkspace 改调 authorizeAndUnmark**

在 `frontend/renderer/components/chat/ChatComposer.tsx` 的 `handleAttachWorkspace` 中（约第 236 行），把：

```typescript
      await window.api.sandbox.authorize(tid, dirPath, true);
```

改为：

```typescript
      await useChatStore.getState().authorizeAndUnmark(tid, dirPath, true);
```

- [ ] **Step 8: typecheck + test**

Run: `npm run typecheck && npm test`
Expected: 全绿

- [ ] **Step 9: Commit**

```bash
git add frontend/renderer/stores/chat.ts frontend/renderer/components/chat/ChatComposer.tsx
git commit -m "feat(chat): implicit chip authorization + manuallyRevokedPaths"
```

---

### Task 7: 全量验收

- [ ] **Step 1: 后端全量单测**

Run: `uv run pytest tests/python/unit -m "not integration" -v`
Expected: all passed

- [ ] **Step 2: 前端 typecheck + test**

Run: `npm run typecheck && npm test`
Expected: 全绿

- [ ] **Step 3: 手动验证（按 AGENTS.md §14.7 重启）**

```powershell
taskkill /T /F /IM electron.exe
Get-Process -Name python,uv -ErrorAction SilentlyContinue | Stop-Process -Force
npm run dev
```

等 10s 后验证：
1. 在前端选工作区 `D:\java\book\book` → chat 里让 AI 调 `list_dir(D:\java\book\book)` → 直接放行
2. 重启 backend（`taskkill` 后 `npm run dev`）→ 重新进入同一会话 → `list_dir(D:\java\book\book)` 仍放行
3. 手动 revoke → 重启 → 该路径未恢复

- [ ] **Step 4: Final commit（如有手动验证修复）**

```bash
git add -A
git commit -m "test: sandbox persistence manual verification passed"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ 沙箱授权 SQLite 持久化 → Task 2 (SandboxStore) + Task 3 (双写)
- ✅ 启动时全量加载 → Task 3 (bootstrap_from_store) + Task 4 (lifespan)
- ✅ source 字段优先级 → Task 2 (UPSERT CASE) + Task 3 测试
- ✅ DB 写失败不阻塞内存 → Task 3 (_persist_* try/except)
- ✅ 前端 chip 隐式授权 → Task 6 (createSession/moveSessionToWorkspace)
- ✅ manuallyRevokedPaths → Task 6 (revokeAndMark/authorizeAndUnmark)
- ✅ full_trust 与临时授权不持久化 → Task 3 (仅 authorize/revoke/clear 双写，full_trust 不动)
- ✅ DELETE 联动 → Task 4 (memory_checkpointer_delete)
- ✅ 持久化开关 → Task 1 (config) + Task 3 (get_settings check)

**Placeholder scan:** 无 TBD/TODO。

**Type consistency:** `SandboxStore.upsert(thread_id, resolved_path, writable, source)` 签名在 Task 2/3 一致；`authorizeAndUnmark(sessionId, path, writable)` 在 Task 6 一致。
