"""SandboxStore 单元测试：CRUD 幂等 + bootstrap 全量加载 + delete_by_thread。"""

from __future__ import annotations

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
    assert rows[0].resolved_path.resolve() == Path("d:/book").resolve()


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
