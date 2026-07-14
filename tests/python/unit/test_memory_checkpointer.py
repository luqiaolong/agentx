"""Checkpointer 视图单元测试：list_threads / get_db_size / delete_thread / rewind_thread + thread_id 校验。

直接用 aiosqlite 在 tmp_path 创建测试数据库，绕过真实 SqliteSaver 单例。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import app.memory.checkpointer as cp_module
import app.memory.checkpointer_view as cv_module
from app.memory.checkpointer_view import (
    ThreadIdInvalid,
    delete_thread,
    get_db_size,
    list_checkpoints,
    list_threads,
    rewind_thread,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试隔离 ``DATA_DIR`` 与 checkpointer 单例。"""
    monkeypatch.setattr(cp_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cv_module, "DATA_DIR", tmp_path)
    # 重置 checkpointer 单例
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None
    yield
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None


def _create_test_db(db_path: Path, threads: list[tuple[str, str, bytes]]) -> None:
    """创建测试用 sqlite 数据库，含 checkpoints 表与指定数据。

    Args:
        db_path: 数据库文件路径。
        threads: [(thread_id, checkpoint_id, checkpoint_blob), ...]
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint BLOB,
            metadata BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        );
        CREATE TABLE IF NOT EXISTS writes (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            channel TEXT NOT NULL,
            type TEXT,
            value BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        );
        """
    )
    for thread_id, checkpoint_id, blob in threads:
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint) "
            "VALUES (?, '', ?, ?)",
            (thread_id, checkpoint_id, blob),
        )
    conn.commit()
    conn.close()


# ============================================================
# list_threads
# ============================================================


def test_list_threads_empty_when_db_not_exists(tmp_path: Path) -> None:
    """数据库不存在时返回空列表。"""
    import asyncio

    result = asyncio.run(list_threads())
    assert result == []


def test_list_threads_returns_aggregated(tmp_path: Path) -> None:
    """list_threads 返回 thread_id + checkpoint_count + last_updated + size_bytes。

    T5.7: ``last_updated`` 取自 ``thread_meta.last_active_at``，无 meta 记录时为 None
    （旧的 checkpoint_id 不再作为 last_updated 返回）。
    """
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("thread_a", "cp_uuid_1", b"blob_a_1"),
            ("thread_a", "cp_uuid_2", b"blob_a_2"),
            ("thread_b", "cp_uuid_3", b"blob_b_1"),
        ],
    )

    import asyncio

    result = asyncio.run(list_threads())
    assert len(result) == 2

    by_thread = {r["thread_id"]: r for r in result}

    a = by_thread["thread_a"]
    assert a["checkpoint_count"] == 2
    # T5.7: 无 thread_meta 记录时 last_updated 为 None（不再是 checkpoint_id）
    assert a["last_updated"] is None
    assert a["size_bytes"] == len(b"blob_a_1") + len(b"blob_a_2")

    b = by_thread["thread_b"]
    assert b["checkpoint_count"] == 1
    assert b["last_updated"] is None
    assert b["size_bytes"] == len(b"blob_b_1")


def test_list_threads_empty_when_no_table(tmp_path: Path) -> None:
    """数据库存在但无 checkpoints 表时返回空列表。"""
    db_path = tmp_path / "agentx.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 创建空数据库（无表）
    conn = sqlite3.connect(str(db_path))
    conn.close()

    import asyncio

    assert asyncio.run(list_threads()) == []


# ============================================================
# get_db_size
# ============================================================


def test_get_db_size_returns_zero_when_not_exists(tmp_path: Path) -> None:
    """数据库不存在时返回 0。"""
    import asyncio

    assert asyncio.run(get_db_size()) == 0


def test_get_db_size_returns_file_size(tmp_path: Path) -> None:
    """返回数据库文件大小。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(db_path, [("t1", "id1", b"blob")])

    import asyncio

    size = asyncio.run(get_db_size())
    assert size > 0
    assert size == db_path.stat().st_size


# ============================================================
# delete_thread
# ============================================================


def test_delete_thread_removes_checkpoints(tmp_path: Path) -> None:
    """删除指定 thread 的所有 checkpoint。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("thread_del", "id1", b"blob1"),
            ("thread_del", "id2", b"blob2"),
            ("thread_keep", "id3", b"blob3"),
        ],
    )

    import asyncio

    deleted = asyncio.run(delete_thread("thread_del"))
    assert deleted == 2

    # 验证 thread_keep 仍在
    result = asyncio.run(list_threads())
    thread_ids = [r["thread_id"] for r in result]
    assert "thread_del" not in thread_ids
    assert "thread_keep" in thread_ids


def test_delete_thread_nonexistent_returns_zero(tmp_path: Path) -> None:
    """删除不存在的 thread 返回 0。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(db_path, [("t1", "id1", b"blob")])

    import asyncio

    assert asyncio.run(delete_thread("nonexistent")) == 0


def test_delete_thread_invalid_id() -> None:
    """thread_id 非法抛 ThreadIdInvalid。"""
    import asyncio

    with pytest.raises(ThreadIdInvalid):
        asyncio.run(delete_thread("../etc"))
    with pytest.raises(ThreadIdInvalid):
        asyncio.run(delete_thread("a b c"))
    with pytest.raises(ThreadIdInvalid):
        asyncio.run(delete_thread("'; DROP TABLE checkpoints; --"))


def test_delete_thread_db_not_exists(tmp_path: Path) -> None:
    """数据库不存在时返回 0（不报错）。"""
    import asyncio

    assert asyncio.run(delete_thread("any_thread")) == 0


def test_delete_thread_db_error_raises(tmp_path: Path) -> None:
    """DB 错误（如 writes 表缺失）应抛 sqlite3.Error，而非静默返回 0。"""
    import asyncio

    db_path = tmp_path / "agentx.db"
    # 只创建 checkpoints 表（不创建 writes），触发 DELETE FROM writes 失败
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint BLOB,
            metadata BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint) "
        "VALUES ('t1', '', 'id1', ?)",
        (b"blob",),
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.Error):
        asyncio.run(delete_thread("t1"))


def test_delete_thread_also_clears_writes(tmp_path: Path) -> None:
    """delete_thread 同时清理 writes 表（外键关联）。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(db_path, [("t1", "id1", b"blob")])

    # 手动插入 writes 记录
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel) "
        "VALUES ('t1', '', 'id1', 'task1', 0, 'chan')"
    )
    conn.commit()
    conn.close()

    import asyncio

    asyncio.run(delete_thread("t1"))

    # 验证 writes 表也已清理
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("SELECT COUNT(*) FROM writes WHERE thread_id = 't1'")
    assert cur.fetchone()[0] == 0
    conn.close()


# ============================================================
# 异步测试用 pytest-asyncio
# ============================================================


async def test_list_threads_async(tmp_path: Path) -> None:
    """asyncio_mode=auto 下直接 await。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(db_path, [("t_async", "id1", b"x")])

    result = await list_threads()
    assert len(result) == 1
    assert result[0]["thread_id"] == "t_async"


async def test_delete_thread_async(tmp_path: Path) -> None:
    """异步删除。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(db_path, [("t_async_del", "id1", b"x")])

    deleted = await delete_thread("t_async_del")
    assert deleted == 1


# ============================================================
# list_checkpoints
# ============================================================


def test_list_checkpoints_returns_ordered(tmp_path: Path) -> None:
    """list_checkpoints 返回按 rowid 升序的 checkpoint 列表。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("thread_lc", "cp1", b"blob1"),
            ("thread_lc", "cp2", b"blob2"),
            ("thread_lc", "cp3", b"blob3"),
        ],
    )

    import asyncio

    result = asyncio.run(list_checkpoints("thread_lc"))
    assert len(result) == 3
    assert result[0]["checkpoint_id"] == "cp1"
    assert result[1]["checkpoint_id"] == "cp2"
    assert result[2]["checkpoint_id"] == "cp3"
    assert result[0]["rowid"] < result[1]["rowid"] < result[2]["rowid"]


def test_list_checkpoints_empty_thread(tmp_path: Path) -> None:
    """不存在的 thread 返回空列表。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(db_path, [("t1", "cp1", b"x")])

    import asyncio

    result = asyncio.run(list_checkpoints("nonexistent"))
    assert result == []


def test_list_checkpoints_invalid_id() -> None:
    """thread_id 非法抛 ThreadIdInvalid。"""
    import asyncio

    with pytest.raises(ThreadIdInvalid):
        asyncio.run(list_checkpoints("../etc"))


# ============================================================
# rewind_thread
# ============================================================


def test_rewind_thread_keeps_early_checkpoints(tmp_path: Path) -> None:
    """回退到指定 checkpoint_id，保留该 checkpoint 及之前的所有。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("thread_rw", "cp1", b"blob1"),
            ("thread_rw", "cp2", b"blob2"),
            ("thread_rw", "cp3", b"blob3"),
            ("thread_rw", "cp4", b"blob4"),
            ("thread_rw", "cp5", b"blob5"),
            ("thread_rw", "cp6", b"blob6"),
        ],
    )

    import asyncio

    # 回退到 cp5：保留 cp1-cp5，删除 cp6
    result = asyncio.run(rewind_thread("thread_rw", "cp5"))
    assert result["deleted"] == 1
    assert result["kept"] == 5
    assert result["cutoff_checkpoint_id"] == "cp5"

    # 验证数据库
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("SELECT checkpoint_id FROM checkpoints WHERE thread_id = 'thread_rw' ORDER BY rowid")
    ids = [r[0] for r in cur.fetchall()]
    assert ids == ["cp1", "cp2", "cp3", "cp4", "cp5"]
    conn.close()


def test_rewind_thread_to_last_checkpoint_noop(tmp_path: Path) -> None:
    """回退到最后一个 checkpoint 时不删除。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("thread_rw", "cp1", b"blob1"),
            ("thread_rw", "cp2", b"blob2"),
        ],
    )

    import asyncio

    result = asyncio.run(rewind_thread("thread_rw", "cp2"))
    assert result["deleted"] == 0
    assert result["kept"] == 2
    assert result["cutoff_checkpoint_id"] == "cp2"


def test_rewind_thread_nonexistent_checkpoint_raises(tmp_path: Path) -> None:
    """checkpoint_id 不存在时抛 ValueError。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(db_path, [("thread_rw", "cp1", b"blob1")])

    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(rewind_thread("thread_rw", "nonexistent_cp"))


def test_rewind_thread_db_not_exists(tmp_path: Path) -> None:
    """数据库不存在时返回零值。"""
    import asyncio

    result = asyncio.run(rewind_thread("nonexistent", "cp1"))
    assert result["deleted"] == 0
    assert result["kept"] == 0
    assert result["cutoff_checkpoint_id"] is None


def test_rewind_thread_invalid_id() -> None:
    """thread_id 非法抛 ThreadIdInvalid。"""
    import asyncio

    with pytest.raises(ThreadIdInvalid):
        asyncio.run(rewind_thread("../etc", "cp1"))


def test_rewind_thread_empty_checkpoint_id(tmp_path: Path) -> None:
    """checkpoint_id 为空时抛 ValueError。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(db_path, [("thread_rw", "cp1", b"blob1")])

    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(rewind_thread("thread_rw", ""))


def test_rewind_thread_to_first_checkpoint(tmp_path: Path) -> None:
    """回退到第一个 checkpoint（编辑首条消息场景）。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("thread_rw", "cp1", b"blob1"),
            ("thread_rw", "cp2", b"blob2"),
            ("thread_rw", "cp3", b"blob3"),
        ],
    )

    import asyncio

    # 回退到 cp1：保留 cp1，删除 cp2, cp3
    result = asyncio.run(rewind_thread("thread_rw", "cp1"))
    assert result["deleted"] == 2
    assert result["kept"] == 1
    assert result["cutoff_checkpoint_id"] == "cp1"


def test_rewind_thread_also_clears_writes(tmp_path: Path) -> None:
    """rewind_thread 按 checkpoint_id 关联清理 writes 表（不跨表比较 rowid）。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("thread_rw", "cp1", b"blob1"),
            ("thread_rw", "cp2", b"blob2"),
            ("thread_rw", "cp3", b"blob3"),
        ],
    )

    # 手动插入 writes 记录（关联不同 checkpoints）
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel) "
        "VALUES ('thread_rw', '', 'cp1', 'task1', 0, 'chan')"
    )
    conn.execute(
        "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel) "
        "VALUES ('thread_rw', '', 'cp2', 'task1', 1, 'chan')"
    )
    conn.execute(
        "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel) "
        "VALUES ('thread_rw', '', 'cp3', 'task1', 2, 'chan')"
    )
    conn.commit()
    conn.close()

    import asyncio

    # 回退到 cp1：保留 cp1 的 writes，删除 cp2, cp3 的 writes
    result = asyncio.run(rewind_thread("thread_rw", "cp1"))
    assert result["deleted"] == 2

    # 验证 writes 表已清理（只保留 cp1 的）
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("SELECT checkpoint_id FROM writes WHERE thread_id = 'thread_rw' ORDER BY idx")
    ids = [r[0] for r in cur.fetchall()]
    assert ids == ["cp1"]
    conn.close()


async def test_rewind_thread_async(tmp_path: Path) -> None:
    """异步回退。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("t_async_rw", "cp1", b"x"),
            ("t_async_rw", "cp2", b"x"),
            ("t_async_rw", "cp3", b"x"),
            ("t_async_rw", "cp4", b"x"),
        ],
    )

    # 回退到 cp3：保留 cp1-cp3，删除 cp4
    result = await rewind_thread("t_async_rw", "cp3")
    assert result["deleted"] == 1
    assert result["kept"] == 3
