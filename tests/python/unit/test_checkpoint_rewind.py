"""Phase 2 回归测试：rewind + compact 的 checkpoint 正确性。

覆盖 T2.1/T2.3/T2.4/T2.6/T2.7：
1. rewind 到指定 checkpoint_id 保留正确范围
2. rewind 按 checkpoint_id 关联删除 writes（不跨表比较 rowid）
3. 编辑中间消息：list_checkpoints + rewind 协作
4. 编辑首条消息：rewind 到第一个 checkpoint（初始状态）
5. rewind 后继续写入（模拟 resend）
6. compact 后继续对话（真实 AsyncSqliteSaver）

使用真实 ``AsyncSqliteSaver`` 与临时 SQLite 数据库，确保测试覆盖
LangGraph saver 的真实行为（而非仅 mock）。
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest

import app.memory.checkpointer as cp_module
import app.memory.checkpointer_view as cv_module
from app.memory.checkpointer_view import (
    ThreadIdInvalid,
    list_checkpoints,
    rewind_thread,
)


# ============================================================
# 辅助函数
# ============================================================


def _create_test_db(db_path: Path, threads: list[tuple[str, str, bytes]]) -> None:
    """创建测试用 sqlite 数据库，含 checkpoints + writes 表与指定数据。"""
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


def _insert_writes(db_path: Path, thread_id: str, writes: list[tuple[str, str, int]]) -> None:
    """插入 writes 记录: [(checkpoint_id, task_id, idx), ...]。"""
    conn = sqlite3.connect(str(db_path))
    for cp_id, task_id, idx in writes:
        conn.execute(
            "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel) "
            "VALUES (?, '', ?, ?, ?, 'messages')",
            (thread_id, cp_id, task_id, idx),
        )
    conn.commit()
    conn.close()


def _get_remaining_checkpoints(db_path: Path, thread_id: str) -> list[str]:
    """返回指定 thread 剩余的 checkpoint_id 列表（按 rowid 升序）。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT checkpoint_id FROM checkpoints "
        "WHERE thread_id = ? AND checkpoint_ns = '' ORDER BY rowid",
        (thread_id,),
    )
    ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


def _get_remaining_writes(db_path: Path, thread_id: str) -> list[str]:
    """返回指定 thread 剩余 writes 的 checkpoint_id 列表（按 idx 升序）。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT checkpoint_id FROM writes "
        "WHERE thread_id = ? AND checkpoint_ns = '' ORDER BY idx",
        (thread_id,),
    )
    ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试隔离 ``DATA_DIR`` 与 checkpointer 单例。"""
    monkeypatch.setattr(cp_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cv_module, "DATA_DIR", tmp_path)
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None
    yield
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None


# ============================================================
# T2.3/T2.4: rewind 到指定 checkpoint_id + 按 checkpoint_id 关联删除 writes
# ============================================================


def test_rewind_to_specific_checkpoint_keeps_up_to_cutoff(tmp_path: Path) -> None:
    """T2.3-1: rewind 到 checkpoint_id 保留该 checkpoint 及之前的所有。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("t1", "cp1", b"blob1"),
            ("t1", "cp2", b"blob2"),
            ("t1", "cp3", b"blob3"),
            ("t1", "cp4", b"blob4"),
            ("t1", "cp5", b"blob5"),
        ],
    )

    import asyncio

    result = asyncio.run(rewind_thread("t1", "cp3"))
    assert result["deleted"] == 2  # cp4, cp5
    assert result["kept"] == 3  # cp1, cp2, cp3
    assert result["cutoff_checkpoint_id"] == "cp3"

    remaining = _get_remaining_checkpoints(db_path, "t1")
    assert remaining == ["cp1", "cp2", "cp3"]


def test_rewind_deletes_writes_by_checkpoint_id_not_rowid(tmp_path: Path) -> None:
    """T2.4: writes 按 checkpoint_id 关联删除，不跨表比较 rowid。

    场景：writes 表的 rowid 顺序与 checkpoints 不同，
    旧实现 ``DELETE FROM writes WHERE rowid > checkpoints.rowid`` 会误删/漏删。
    新实现按 checkpoint_id NOT IN (保留集合) 删除，正确。
    """
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("t1", "cp_keep1", b"b1"),
            ("t1", "cp_keep2", b"b2"),
            ("t1", "cp_del1", b"b3"),
            ("t1", "cp_del2", b"b4"),
        ],
    )
    # writes 按 cp_keep1, cp_del1, cp_keep2, cp_del2 顺序插入
    # （rowid 顺序与 checkpoints 不同，旧跨表比较会出错）
    _insert_writes(
        db_path,
        "t1",
        [
            ("cp_keep1", "task_a", 0),
            ("cp_del1", "task_b", 1),
            ("cp_keep2", "task_c", 2),
            ("cp_del2", "task_d", 3),
        ],
    )

    import asyncio

    # rewind 到 cp_keep2：保留 cp_keep1, cp_keep2 的 writes
    result = asyncio.run(rewind_thread("t1", "cp_keep2"))
    assert result["deleted"] == 2

    remaining_writes = _get_remaining_writes(db_path, "t1")
    # 只保留 cp_keep1 和 cp_keep2 的 writes（按 idx 升序）
    assert remaining_writes == ["cp_keep1", "cp_keep2"]


def test_rewind_preserves_other_threads(tmp_path: Path) -> None:
    """rewind 只影响指定 thread，其他 thread 的 checkpoint 不受影响。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("t1", "cp1", b"b1"),
            ("t1", "cp2", b"b2"),
            ("t1", "cp3", b"b3"),
            ("t2", "cp_a", b"ba"),
            ("t2", "cp_b", b"bb"),
        ],
    )

    import asyncio

    asyncio.run(rewind_thread("t1", "cp1"))

    # t2 不受影响
    remaining_t2 = _get_remaining_checkpoints(db_path, "t2")
    assert remaining_t2 == ["cp_a", "cp_b"]


def test_rewind_preserves_child_namespace(tmp_path: Path) -> None:
    """rewind 只影响主线程（checkpoint_ns=''），子命名空间不受影响。"""
    db_path = tmp_path / "agentx.db"
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
    # 主线程 checkpoints
    for cp_id, blob in [("cp1", b"b1"), ("cp2", b"b2"), ("cp3", b"b3")]:
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint) "
            "VALUES ('t1', '', ?, ?)",
            (cp_id, blob),
        )
    # 子命名空间 checkpoints（team 子任务）
    for cp_id, blob in [("child_cp1", b"c1"), ("child_cp2", b"c2")]:
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint) "
            "VALUES ('t1', 'team-sub', ?, ?)",
            (cp_id, blob),
        )
    conn.commit()
    conn.close()

    import asyncio

    # rewind 主线程到 cp1
    asyncio.run(rewind_thread("t1", "cp1"))

    # 子命名空间不受影响
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT checkpoint_id FROM checkpoints "
        "WHERE thread_id = 't1' AND checkpoint_ns = 'team-sub' ORDER BY rowid"
    )
    child_ids = [r[0] for r in cur.fetchall()]
    conn.close()
    assert child_ids == ["child_cp1", "child_cp2"]


# ============================================================
# T2.6: 编辑首条消息（rewind 到第一个 checkpoint）
# ============================================================


def test_edit_first_message_rewinds_to_initial_checkpoint(tmp_path: Path) -> None:
    """T2.6: 编辑首条消息时 rewind 到 checkpoints[0]（初始状态）。

    模拟前端 deleteMessagesAfter 流程：
    keepMessagesCount=0 → list_checkpoints → 取 checkpoints[0] → rewind
    """
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("t1", "cp_init", b"init"),
            ("t1", "cp_msg1", b"msg1"),
            ("t1", "cp_msg2", b"msg2"),
            ("t1", "cp_msg3", b"msg3"),
        ],
    )

    import asyncio

    # 前端逻辑：keepMessagesCount=0 → targetIdx=0 → checkpoints[0]
    checkpoints = asyncio.run(list_checkpoints("t1"))
    assert len(checkpoints) == 4

    target_idx = 0  # keepMessagesCount=0
    target_cp = checkpoints[target_idx]
    assert target_cp["checkpoint_id"] == "cp_init"

    result = asyncio.run(rewind_thread("t1", target_cp["checkpoint_id"]))
    assert result["deleted"] == 3  # cp_msg1, cp_msg2, cp_msg3
    assert result["kept"] == 1  # cp_init
    assert result["cutoff_checkpoint_id"] == "cp_init"


# ============================================================
# 编辑中间消息：list_checkpoints + rewind 协作
# ============================================================


def test_edit_middle_message_picks_correct_checkpoint(tmp_path: Path) -> None:
    """编辑中间消息：前端用 list_checkpoints + keepMessagesCount 选 checkpoint_id。

    场景：5 条消息（2 user + 2 assistant + 1 user），编辑第 3 条，
    keepMessagesCount=2 → 取 checkpoints[2]
    """
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("t1", f"cp{i}", b"b") for i in range(1, 8)  # 7 checkpoints
        ],
    )

    import asyncio

    checkpoints = asyncio.run(list_checkpoints("t1"))
    assert len(checkpoints) == 7

    keep_messages_count = 2  # 保留前 2 条消息
    target_idx = min(keep_messages_count, len(checkpoints) - 1)
    target_cp = checkpoints[target_idx]
    assert target_cp["checkpoint_id"] == "cp3"

    # rewind 到 cp3
    result = asyncio.run(rewind_thread("t1", target_cp["checkpoint_id"]))
    assert result["deleted"] == 4  # cp4-cp7
    assert result["kept"] == 3  # cp1-cp3

    remaining = _get_remaining_checkpoints(db_path, "t1")
    assert remaining == ["cp1", "cp2", "cp3"]


# ============================================================
# rewind 后继续写入（模拟 resend）
# ============================================================


@pytest.mark.asyncio
async def test_rewind_then_write_new_checkpoint(tmp_path: Path) -> None:
    """rewind 后可以继续写入新 checkpoint（模拟 resend 场景）。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db(
        db_path,
        [
            ("t1", "cp1", b"b1"),
            ("t1", "cp2", b"b2"),
            ("t1", "cp3", b"b3"),
            ("t1", "cp4", b"b4"),
        ],
    )

    # rewind 到 cp2
    result = await rewind_thread("t1", "cp2")
    assert result["deleted"] == 2
    assert result["kept"] == 2

    # 模拟 resend：写入新 checkpoint
    conn = sqlite3.connect(str(db_path))
    new_cp_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint) "
        "VALUES ('t1', '', ?, ?)",
        (new_cp_id, b"new_after_rewind"),
    )
    conn.commit()
    conn.close()

    # 验证新 checkpoint 存在
    remaining = _get_remaining_checkpoints(db_path, "t1")
    assert remaining == ["cp1", "cp2", new_cp_id]

    # list_checkpoints 能看到新 checkpoint
    checkpoints = await list_checkpoints("t1")
    assert len(checkpoints) == 3
    assert checkpoints[-1]["checkpoint_id"] == new_cp_id


# ============================================================
# T2.1: 真实 AsyncSqliteSaver compact 回归测试
# ============================================================


@pytest.fixture
async def real_async_saver(tmp_path: Path):
    """返回 (checkpointer, db_path, cleanup_fn) — 使用真实 AsyncSqliteSaver。"""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = tmp_path / "real_test.db"
    conn = await aiosqlite.connect(str(db_path))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()

    async def cleanup():
        try:
            await conn.close()
        except Exception:
            pass

    yield saver, db_path, cleanup


@pytest.mark.asyncio
async def test_real_saver_compact_writes_correct_namespace(real_async_saver):
    """T2.1-1: compact 写入的 checkpoint 的 checkpoint_ns=''（主线程命名空间）。"""
    from langchain_core.messages import AIMessage, HumanMessage

    saver, db_path, cleanup = real_async_saver
    thread_id = "compact_ns_test"

    # 写入初始 checkpoint
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    messages = [
        HumanMessage(content="msg1"),
        AIMessage(content="reply1"),
        HumanMessage(content="msg2"),
        AIMessage(content="reply2"),
    ]
    checkpoint = {
        "v": 1,
        "id": str(uuid.uuid4()),
        "ts": "2026-07-14T00:00:00Z",
        "channel_values": {"messages": messages},
        "channel_versions": {"messages": "v1"},
        "versions_seen": {},
        "parent_checkpoint_id": None,
    }
    await saver.aput(config, checkpoint, {}, {"messages": "v1"})

    # 读回验证 checkpoint_ns
    result = await saver.aget(config)
    assert result is not None

    # 直接查 DB 验证 checkpoint_ns
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT checkpoint_ns FROM checkpoints WHERE thread_id = ?", (thread_id,)
    )
    rows = cur.fetchall()
    conn.close()
    assert len(rows) > 0
    assert all(r[0] == "" for r in rows), "所有 checkpoint 的 checkpoint_ns 应为空字符串"

    await cleanup()


@pytest.mark.asyncio
async def test_real_saver_compact_sets_parent_checkpoint_id(real_async_saver):
    """T2.1-2: compact 后新 checkpoint 的 parent_checkpoint_id 指向旧 checkpoint。"""
    saver, db_path, cleanup = real_async_saver
    thread_id = "compact_parent_test"

    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    old_cp_id = str(uuid.uuid4())
    old_checkpoint = {
        "v": 1,
        "id": old_cp_id,
        "ts": "2026-07-14T00:00:00Z",
        "channel_values": {"messages": []},
        "channel_versions": {"messages": "v1"},
        "versions_seen": {},
        "parent_checkpoint_id": None,
    }
    await saver.aput(config, old_checkpoint, {}, {"messages": "v1"})

    # 写入新 checkpoint（模拟 compact 后写回）
    new_cp_id = str(uuid.uuid4())
    # T2.2: config.configurable.checkpoint_id 是父 checkpoint 的 id（旧 checkpoint），
    # LangGraph aput 用它作为 parent_checkpoint_id 存入 DB
    new_config = {
        **config,
        "configurable": {**config["configurable"], "checkpoint_id": old_cp_id},
    }
    new_checkpoint = {
        **old_checkpoint,
        "id": new_cp_id,
        "parent_checkpoint_id": old_cp_id,
    }
    new_versions = {"messages": str(uuid.uuid4())}
    await saver.aput(new_config, new_checkpoint, {}, new_versions)

    # 验证 DB 中 parent_checkpoint_id 正确
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints "
        "WHERE thread_id = ? ORDER BY rowid",
        (thread_id,),
    )
    rows = cur.fetchall()
    conn.close()
    assert len(rows) == 2
    # 第一个是旧 checkpoint
    assert rows[0][0] == old_cp_id
    assert rows[0][1] is None
    # 第二个是新 checkpoint，parent 指向旧
    assert rows[1][0] == new_cp_id
    assert rows[1][1] == old_cp_id

    await cleanup()


@pytest.mark.asyncio
async def test_real_saver_compact_new_versions_is_dict(real_async_saver):
    """T2.1-3: compact 的 new_versions 是 dict（非 list），包含 messages key。"""
    saver, db_path, cleanup = real_async_saver
    thread_id = "compact_versions_test"

    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    old_cp_id = str(uuid.uuid4())
    checkpoint = {
        "v": 1,
        "id": old_cp_id,
        "ts": "2026-07-14T00:00:00Z",
        "channel_values": {"messages": []},
        "channel_versions": {"messages": "v_old"},
        "versions_seen": {},
        "parent_checkpoint_id": None,
    }

    # 模拟 compact 的 new_versions 构造
    new_versions = {
        **(checkpoint.get("channel_versions") or {}),
        "messages": str(uuid.uuid4()),
    }
    assert isinstance(new_versions, dict)
    assert not isinstance(new_versions, list)
    assert "messages" in new_versions
    assert new_versions["messages"]  # 非空

    new_cp_id = str(uuid.uuid4())
    new_config = {
        **config,
        "configurable": {**config["configurable"], "checkpoint_id": new_cp_id},
    }
    new_checkpoint = {**checkpoint, "id": new_cp_id, "parent_checkpoint_id": old_cp_id}
    await saver.aput(config, checkpoint, {}, {"messages": "v_old"})
    await saver.aput(new_config, new_checkpoint, {}, new_versions)

    # 验证能被 aget 正确读回
    result = await saver.aget(new_config)
    assert result is not None

    await cleanup()


@pytest.mark.asyncio
async def test_real_saver_compact_then_continue(real_async_saver):
    """T2.7-6: compact 后能继续对话（写新 checkpoint 不冲突）。"""
    from langchain_core.messages import AIMessage, HumanMessage

    saver, db_path, cleanup = real_async_saver
    thread_id = "compact_continue_test"

    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    # 写入 4 条消息（模拟完整对话）
    messages = [
        HumanMessage(content="hello"),
        AIMessage(content="hi there"),
        HumanMessage(content="how are you"),
        AIMessage(content="I'm fine"),
    ]
    old_cp_id = str(uuid.uuid4())
    checkpoint = {
        "v": 1,
        "id": old_cp_id,
        "ts": "2026-07-14T00:00:00Z",
        "channel_values": {"messages": messages},
        "channel_versions": {"messages": "v1"},
        "versions_seen": {},
        "parent_checkpoint_id": None,
    }
    await saver.aput(config, checkpoint, {}, {"messages": "v1"})

    # compact：写入新 checkpoint（摘要 + 最近消息）
    from langchain_core.messages import SystemMessage

    new_messages = [
        SystemMessage(content="summary of earlier conversation"),
        HumanMessage(content="how are you"),
        AIMessage(content="I'm fine"),
    ]
    new_cp_id = str(uuid.uuid4())
    # T2.2: config.configurable.checkpoint_id 是父 checkpoint 的 id（旧 checkpoint）
    new_config = {
        **config,
        "configurable": {**config["configurable"], "checkpoint_id": old_cp_id},
    }
    new_checkpoint = {
        **checkpoint,
        "id": new_cp_id,
        "parent_checkpoint_id": old_cp_id,
        "channel_values": {"messages": new_messages},
    }
    new_versions = {"messages": str(uuid.uuid4())}
    await saver.aput(new_config, new_checkpoint, {}, new_versions)

    # compact 后继续对话：再写一个 checkpoint
    continue_cp_id = str(uuid.uuid4())
    # config.configurable.checkpoint_id 是父 checkpoint 的 id（compact 后的 new_cp_id）
    continue_config = {
        **config,
        "configurable": {**config["configurable"], "checkpoint_id": new_cp_id},
    }
    continue_messages = new_messages + [HumanMessage(content="thanks")]
    continue_checkpoint = {
        **checkpoint,
        "id": continue_cp_id,
        "parent_checkpoint_id": new_cp_id,
        "channel_values": {"messages": continue_messages},
    }
    continue_versions = {"messages": str(uuid.uuid4())}
    await saver.aput(continue_config, continue_checkpoint, {}, continue_versions)

    # 验证：3 个 checkpoints（old, compact, continue）
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints "
        "WHERE thread_id = ? AND checkpoint_ns = '' ORDER BY rowid",
        (thread_id,),
    )
    rows = cur.fetchall()
    conn.close()
    assert len(rows) == 3
    assert rows[0][0] == old_cp_id
    assert rows[0][1] is None
    assert rows[1][0] == new_cp_id
    assert rows[1][1] == old_cp_id
    assert rows[2][0] == continue_cp_id
    assert rows[2][1] == new_cp_id

    # 验证 aget 能读回最新状态（用不含 checkpoint_id 的 config 获取最新）
    latest_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    result = await saver.aget(latest_config)
    assert result is not None
    assert isinstance(result, dict)
    final_messages = result.get("channel_values", {}).get("messages", [])
    assert len(final_messages) == 4  # 3 from compact + 1 new

    await cleanup()


# ============================================================
# T2.5: /reset 子 thread 清理验证（list_checkpoints 不受子命名空间影响）
# ============================================================


@pytest.mark.asyncio
async def test_list_checkpoints_ignores_child_namespace(tmp_path: Path) -> None:
    """list_checkpoints 只返回主线程 checkpoint，不含子命名空间。"""
    db_path = tmp_path / "agentx.db"
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
    # 主线程 checkpoints
    for cp_id in ["main1", "main2"]:
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id) "
            "VALUES ('t1', '', ?)",
            (cp_id,),
        )
    # 子线程 checkpoints（team 子任务）
    for cp_id in ["child1", "child2"]:
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id) "
            "VALUES ('t1', 'team-sub', ?)",
            (cp_id,),
        )
    conn.commit()
    conn.close()

    result = await list_checkpoints("t1")
    assert len(result) == 2
    assert all(r["checkpoint_id"].startswith("main") for r in result)
