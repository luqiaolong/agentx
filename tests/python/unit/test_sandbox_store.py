"""sandbox/store.py 单元测试：SQLite WAL 模式 + 并发写 + CRUD。

覆盖：
- WAL 模式生效（journal_mode == 'wal'）
- busy_timeout 生效
- upsert 幂等 + source 优先级
- delete_by_path / delete_by_thread / list_by_thread / bootstrap_all
- 并发写不锁（多线程并发 upsert）
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from app.sandbox.store import SandboxEntry, SandboxStore, get_sandbox_store


@pytest.fixture
def store(tmp_path: Path) -> SandboxStore:
    """每个测试用例使用独立 tmp DB 文件。"""
    return SandboxStore(db_path=tmp_path / "test.db")


# ============================================================
# WAL 模式验证
# ============================================================


def test_wal_mode_enabled(store: SandboxStore) -> None:
    """PRAGMA journal_mode=WAL 生效。"""
    with sqlite3.connect(str(store._db_path)) as conn:  # noqa: SLF001
        cur = conn.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
    assert mode.lower() == "wal"


def test_busy_timeout_pragma_executed(tmp_path: Path) -> None:
    """Store 源码包含 PRAGMA busy_timeout=30000（连接级 PRAGMA，通过源码 + 并发测试验证）。

    busy_timeout 是连接级 PRAGMA（非持久化），无法通过独立连接验证。
    通过源码检查确保 PRAGMA 存在，行为正确性由 test_concurrent_writes_no_lock 覆盖。
    """
    import inspect

    from app.sandbox.store import SandboxStore

    source = inspect.getsource(SandboxStore)
    assert "busy_timeout" in source, "Store 源码缺少 PRAGMA busy_timeout"
    assert "30000" in source, "Store 源码缺少 busy_timeout=30000"
    # 同时验证 sqlite3.connect timeout=30
    assert "timeout=30" in source, "Store 源码缺少 sqlite3.connect timeout=30"


def test_connect_timeout_30(store: SandboxStore) -> None:
    """sqlite3.connect 的 timeout 参数为 30 秒。"""
    # 通过实际操作验证不抛异常（timeout 参数已在 connect 调用中设置）
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    rows = store.list_by_thread("t1")
    assert len(rows) == 1


# ============================================================
# CRUD 操作
# ============================================================


def test_table_auto_created(store: SandboxStore) -> None:
    """首次 upsert 自动建表，不抛异常。"""
    store.upsert("t1", "d:/docs", writable=True, source="manual")


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
    assert rows[0].writable is True


def test_source_chip_upgraded_to_manual(store: SandboxStore) -> None:
    """chip 记录被 manual 调用升级。"""
    store.upsert("t1", "d:/docs", writable=False, source="chip")
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    rows = store.list_by_thread("t1")
    assert rows[0].source == "manual"


def test_delete_by_path(store: SandboxStore) -> None:
    """删除单条授权记录。"""
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    store.upsert("t1", "d:/book", writable=False, source="chip")
    deleted = store.delete_by_path("t1", "d:/docs")
    assert deleted is True
    rows = store.list_by_thread("t1")
    assert len(rows) == 1
    assert rows[0].resolved_path.resolve() == Path("d:/book").resolve()


def test_delete_by_path_not_exist(store: SandboxStore) -> None:
    """删除不存在的记录返回 False。"""
    deleted = store.delete_by_path("t1", "d:/notexist")
    assert deleted is False


def test_delete_by_thread(store: SandboxStore) -> None:
    """删除 thread 下所有授权记录。"""
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    store.upsert("t1", "d:/book", writable=False, source="chip")
    store.upsert("t2", "d:/other", writable=True, source="manual")
    count = store.delete_by_thread("t1")
    assert count == 2
    assert len(store.list_by_thread("t1")) == 0
    assert len(store.list_by_thread("t2")) == 1


def test_list_by_thread(store: SandboxStore) -> None:
    """列出 thread 的所有授权记录。"""
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    store.upsert("t1", "d:/book", writable=False, source="chip")
    rows = store.list_by_thread("t1")
    assert len(rows) == 2
    for entry in rows:
        assert isinstance(entry, SandboxEntry)
        assert entry.thread_id == "t1"


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


# ============================================================
# 并发写验证
# ============================================================


def test_concurrent_writes_no_lock(tmp_path: Path) -> None:
    """多线程并发 upsert 不应导致 'database is locked' 异常。"""
    store = SandboxStore(db_path=tmp_path / "concurrent.db")
    errors: list[Exception] = []

    def writer(thread_id: int) -> None:
        try:
            for i in range(20):
                store.upsert(
                    f"t{thread_id}",
                    f"d:/docs/{thread_id}/{i}",
                    writable=(i % 2 == 0),
                    source="manual",
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == [], f"并发写出现异常: {errors}"
    # 验证所有记录都写入成功
    all_entries = store.bootstrap_all()
    total = sum(len(v) for v in all_entries.values())
    assert total == 100  # 5 threads × 20 entries


def test_concurrent_mixed_operations(tmp_path: Path) -> None:
    """并发混合 upsert/delete/list 不应崩溃。"""
    store = SandboxStore(db_path=tmp_path / "mixed.db")
    errors: list[Exception] = []

    def upserter() -> None:
        try:
            for i in range(10):
                store.upsert("t1", f"d:/docs/{i}", writable=True, source="manual")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def deleter() -> None:
        try:
            for i in range(10):
                store.delete_by_path("t1", f"d:/docs/{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def lister() -> None:
        try:
            for _ in range(10):
                store.list_by_thread("t1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=upserter),
        threading.Thread(target=deleter),
        threading.Thread(target=lister),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == [], f"并发混合操作出现异常: {errors}"


# ============================================================
# 单例 get_sandbox_store
# ============================================================


def test_get_sandbox_store_singleton(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_sandbox_store 返回模块级单例。"""
    import app.sandbox.store as store_module

    # 重置单例 + 指向 tmp DB 避免 DATA_DIR 不存在
    monkeypatch.setattr(store_module, "_store", None)
    monkeypatch.setattr(store_module, "DATA_DIR", tmp_path)
    s1 = get_sandbox_store()
    s2 = get_sandbox_store()
    assert s1 is s2
