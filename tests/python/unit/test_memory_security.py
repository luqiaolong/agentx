"""Memory 安全与生命周期单元测试（Phase 5b）。

覆盖 4 个核心场景：
1. **T5.1** — unauthorized workspace_path 被拒绝（``_validate_memory_workspace_path``）
2. **T5.6** — 无工作区时 project 类记忆写入被拒绝（``profile_store.add`` / ``upsert_from_llm``）
3. **T5.4** — 旧格式 ``profile.json`` → 新格式 ``.md`` 迁移（``migrate_legacy_profile``）
4. **T5.7** — ``list_threads`` 返回 ``thread_meta.last_active_at`` 真实时间戳

用 ``tmp_path`` + ``monkeypatch`` 隔离 ``DATA_DIR`` / 沙箱单例，避免污染真实数据。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.memory.profile_store as ps_module
from app.memory.profile_store import (
    ProfileEntry,
    ProjectMemoryWithoutWorkspace,
    add as profile_add,
    upsert_from_llm,
)


# ============================================================
# 共享 fixtures
# ============================================================


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试隔离 ``DATA_DIR`` / ``_PROFILE_FILE`` / 工作区锁缓存 / checkpointer 单例。

    用 ``monkeypatch.setattr`` 确保所有 patch 在测试结束后自动还原，
    避免模块级状态泄漏到其他测试文件（如 ``test_deepagents_integration``）。
    """
    monkeypatch.setattr(ps_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ps_module, "_PROFILE_DIR", tmp_path / "config")
    monkeypatch.setattr(ps_module, "_PROFILE_FILE", tmp_path / "config" / "profile.json")
    import app.workspace.memory_store as ms_module
    monkeypatch.setattr(ms_module, "_workspace_locks", {})
    # 隔离 checkpointer DATA_DIR 与单例（T5.7 测试需要）
    # 用 monkeypatch 而非直接赋值，确保测试结束后自动还原
    import app.memory.checkpointer as cp_module
    import app.memory.checkpointer_view as cv_module
    monkeypatch.setattr(cp_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cv_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cp_module, "_sync_saver", None)
    monkeypatch.setattr(cp_module, "_sync_conn", None)
    monkeypatch.setattr(cp_module, "_async_saver", None)
    monkeypatch.setattr(cp_module, "_async_conn", None)
    yield


def _make_entry(
    key: str = "test_key",
    category: str = "preference",
    content: str = "test content",
    source: str = "manual",
) -> ProfileEntry:
    """构造测试用 ProfileEntry。"""
    return ProfileEntry(
        key=key,
        category=category,
        content=content,
        source=source,
        created_at="",
        updated_at="",
    )


# ============================================================
# T5.1: workspace_path 授权校验
# ============================================================


async def test_validate_workspace_path_empty_passes() -> None:
    """workspace_path 为空时直接通过（操作全局记忆，无需授权）。"""
    from app.api.memory import _validate_memory_workspace_path

    # 不抛异常即通过
    await _validate_memory_workspace_path(None)
    await _validate_memory_workspace_path("")


async def test_validate_workspace_path_dot_rejected() -> None:
    """workspace_path 为 ``.`` / ``..`` 时抛 400。"""
    from fastapi import HTTPException

    from app.api.memory import _validate_memory_workspace_path

    for bad in (".", ".."):
        with pytest.raises(HTTPException) as exc_info:
            await _validate_memory_workspace_path(bad)
        assert exc_info.value.status_code == 400


async def test_validate_workspace_path_critical_dir_rejected(
    tmp_path: Path,
) -> None:
    """workspace_path 为系统关键目录时抛 400。"""
    from fastapi import HTTPException

    from app.api.memory import _validate_memory_workspace_path

    # C:/Windows 是 Windows 关键目录；Linux 下 /etc 等
    import sys

    if sys.platform == "win32":
        critical = "C:/Windows"
    else:
        critical = "/etc"

    with pytest.raises(HTTPException) as exc_info:
        await _validate_memory_workspace_path(critical)
    assert exc_info.value.status_code == 400


async def test_validate_workspace_path_unauthorized_with_thread_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提供 thread_id 但路径未授权时抛 403（T5.1 核心）。"""
    from fastapi import HTTPException

    from app.api.memory import _validate_memory_workspace_path
    from app.sandbox.path_guard import PathNotAuthorized

    # 构造一个未授权的临时目录
    unauth_dir = tmp_path / "unauthorized_workspace"
    unauth_dir.mkdir()

    # mock get_sandbox 返回一个 check_read 抛异常的 sandbox
    mock_sandbox = MagicMock()
    mock_sandbox.check_read = AsyncMock(
        side_effect=PathNotAuthorized(f"路径 {unauth_dir} 未授权")
    )
    monkeypatch.setattr("app.api.memory.get_sandbox", lambda: mock_sandbox)

    with pytest.raises(HTTPException) as exc_info:
        await _validate_memory_workspace_path(
            str(unauth_dir), thread_id="thread_test"
        )
    assert exc_info.value.status_code == 403
    assert "未授权" in exc_info.value.detail


async def test_validate_workspace_path_authorized_with_thread_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提供 thread_id 且路径已授权时通过（T5.1 正向）。"""
    from app.api.memory import _validate_memory_workspace_path

    auth_dir = tmp_path / "authorized_workspace"
    auth_dir.mkdir()

    mock_sandbox = MagicMock()
    mock_sandbox.check_read = AsyncMock(return_value=None)  # 授权通过
    monkeypatch.setattr("app.api.memory.get_sandbox", lambda: mock_sandbox)

    # 不抛异常即通过
    await _validate_memory_workspace_path(str(auth_dir), thread_id="thread_ok")


async def test_validate_workspace_path_without_thread_id_skips_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只提供 workspace_path 不提供 thread_id 时，仅做基础路径校验，不查沙箱。"""
    from app.api.memory import _validate_memory_workspace_path

    safe_dir = tmp_path / "safe_workspace"
    safe_dir.mkdir()

    # mock get_sandbox 确保不被调用
    mock_sandbox = MagicMock()
    mock_sandbox.check_read = AsyncMock(
        side_effect=AssertionError("不应调用 check_read")
    )
    monkeypatch.setattr("app.api.memory.get_sandbox", lambda: mock_sandbox)

    # 不抛异常即通过（未调用沙箱）
    await _validate_memory_workspace_path(str(safe_dir))


# ============================================================
# T5.6: 无工作区时 project 类记忆写入被拒绝
# ============================================================


async def test_project_memory_without_workspace_rejected_in_add() -> None:
    """``add`` 在无 workspace_path 时拒绝 project 类记忆（T5.6 store 层）。"""
    entry = _make_entry(key="proj1", category="project", content="项目信息")
    with pytest.raises(ProjectMemoryWithoutWorkspace, match="project 类记忆"):
        await profile_add(entry, workspace_path=None)


async def test_project_memory_with_workspace_allowed_in_add(
    tmp_path: Path,
) -> None:
    """``add`` 在有 workspace_path 时允许 project 类记忆（T5.6 正向）。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()
    entry = _make_entry(key="proj1", category="project", content="项目信息")
    result = await profile_add(entry, workspace_path=str(ws_path))
    assert result.scope == "workspace"
    assert result.category == "project"


async def test_non_project_category_allowed_without_workspace() -> None:
    """非 project 类记忆（preference/fact/custom）在无工作区时允许写入全局。"""
    for cat in ("preference", "fact", "custom"):
        entry = _make_entry(key=f"global_{cat}", category=cat, content=f"{cat} 内容")
        result = await profile_add(entry, workspace_path=None)
        assert result.scope == "global"
        assert result.category == cat


async def test_project_memory_skipped_in_upsert_from_llm_without_workspace(
    tmp_path: Path,
) -> None:
    """``upsert_from_llm`` 在无 workspace_path 时跳过 project 类条目（T5.6）。"""
    entries = [
        {"key": "proj_skip", "category": "project", "content": "应被跳过"},
        {"key": "fact_ok", "category": "fact", "content": "应被写入"},
        {"key": "pref_ok", "category": "preference", "content": "也应被写入"},
    ]
    written = await upsert_from_llm(entries, workspace_path=None)
    # project 被跳过，只写入 fact + preference
    assert written == 2
    from app.memory.profile_store import get

    assert get("proj_skip") is None  # project 被拒绝
    assert get("fact_ok") is not None
    assert get("pref_ok") is not None


# ============================================================
# T5.4: 旧格式 profile.json → 新格式 .md 迁移
# ============================================================


async def test_migrate_legacy_profile_no_file(tmp_path: Path) -> None:
    """无旧格式文件时返回 migrated=0, skipped=False。"""
    from app.workspace.memory_store import migrate_legacy_profile

    ws_path = tmp_path / "workspace_no_legacy"
    ws_path.mkdir()

    result = await migrate_legacy_profile(str(ws_path))
    assert result == {"migrated": 0, "skipped": False, "backup": None}


async def test_migrate_legacy_profile_migrates_entries(tmp_path: Path) -> None:
    """旧格式 profile.json 存在且 memory 目录为空时执行迁移（T5.4 核心）。"""
    from app.workspace.memory_store import migrate_legacy_profile, list_entries

    ws_path = tmp_path / "workspace_with_legacy"
    agentx_dir = ws_path / ".agentx"
    agentx_dir.mkdir(parents=True)

    # 构造旧格式 profile.json
    legacy_data = {
        "entries": [
            {
                "key": "legacy_proj",
                "category": "project",
                "content": "旧格式项目记忆",
                "source": "manual",
                "created_at": "2026-07-01T00:00:00+00:00",
                "updated_at": "2026-07-01T00:00:00+00:00",
            },
            {
                "key": "legacy_fact",
                "category": "fact",
                "content": "旧格式事实记忆",
                "source": "llm_extracted",
            },
        ]
    }
    legacy_file = agentx_dir / "profile.json"
    legacy_file.write_text(json.dumps(legacy_data, ensure_ascii=False), encoding="utf-8")

    result = await migrate_legacy_profile(str(ws_path))
    assert result["migrated"] == 2
    assert result["skipped"] is False
    assert result["backup"] is not None
    assert result["backup"].endswith("profile.json.bak")

    # 旧文件已重命名为 .bak
    assert not legacy_file.exists()
    assert (agentx_dir / "profile.json.bak").exists()

    # 新格式 .md 文件已生成
    memory_dir = agentx_dir / "memory"
    assert (memory_dir / "legacy_proj.md").exists()
    assert (memory_dir / "legacy_fact.md").exists()

    # list_entries 能读到迁移后的条目
    entries = {e.key: e for e in list_entries(str(ws_path))}
    assert "legacy_proj" in entries
    assert entries["legacy_proj"].category == "project"
    assert entries["legacy_proj"].content == "旧格式项目记忆"
    assert "legacy_fact" in entries
    assert entries["legacy_fact"].source == "llm_extracted"


async def test_migrate_legacy_profile_skips_when_memory_dir_has_md(
    tmp_path: Path,
) -> None:
    """memory 目录已有 .md 文件时跳过迁移（避免覆盖用户已编辑的新格式）。"""
    from app.workspace.memory_store import migrate_legacy_profile

    ws_path = tmp_path / "workspace_already_migrated"
    agentx_dir = ws_path / ".agentx"
    memory_dir = agentx_dir / "memory"
    memory_dir.mkdir(parents=True)

    # memory 目录已有 .md 文件
    (memory_dir / "existing.md").write_text(
        "---\nkey: existing\ncategory: project\nsource: manual\nupdated_at: \"2026-07-10T00:00:00+00:00\"\n---\n\n已存在\n",
        encoding="utf-8",
    )

    # 同时存在旧格式文件（不应被迁移）
    legacy_file = agentx_dir / "profile.json"
    legacy_file.write_text(
        json.dumps(
            {"entries": [{"key": "should_not_migrate", "category": "fact", "content": "x"}]}
        ),
        encoding="utf-8",
    )

    result = await migrate_legacy_profile(str(ws_path))
    assert result == {"migrated": 0, "skipped": True, "backup": None}

    # 旧文件未被重命名
    assert legacy_file.exists()
    # memory 目录仍只有原有的 1 个 .md
    assert len(list(memory_dir.glob("*.md"))) == 1


async def test_migrate_legacy_profile_invalid_json(tmp_path: Path) -> None:
    """旧格式文件 JSON 损坏时返回 migrated=0（不报错）。"""
    from app.workspace.memory_store import migrate_legacy_profile

    ws_path = tmp_path / "workspace_corrupt"
    agentx_dir = ws_path / ".agentx"
    agentx_dir.mkdir(parents=True)

    legacy_file = agentx_dir / "profile.json"
    legacy_file.write_text("{invalid json", encoding="utf-8")

    result = await migrate_legacy_profile(str(ws_path))
    assert result == {"migrated": 0, "skipped": False, "backup": None}
    # 旧文件未被重命名（迁移失败）
    assert legacy_file.exists()


async def test_migrate_legacy_profile_idempotent(tmp_path: Path) -> None:
    """迁移幂等：第二次调用时旧文件已重命名，返回 migrated=0。"""
    from app.workspace.memory_store import migrate_legacy_profile

    ws_path = tmp_path / "workspace_idempotent"
    agentx_dir = ws_path / ".agentx"
    agentx_dir.mkdir(parents=True)

    legacy_file = agentx_dir / "profile.json"
    legacy_file.write_text(
        json.dumps(
            {"entries": [{"key": "k1", "category": "fact", "content": "v1"}]}
        ),
        encoding="utf-8",
    )

    # 第一次迁移
    result1 = await migrate_legacy_profile(str(ws_path))
    assert result1["migrated"] == 1

    # 第二次调用：旧文件已重命名，无东西可迁
    result2 = await migrate_legacy_profile(str(ws_path))
    assert result2 == {"migrated": 0, "skipped": False, "backup": None}


# ============================================================
# T5.7: list_threads 返回真实时间戳
# ============================================================


def _create_test_db_with_meta(
    db_path: Path,
    threads: list[tuple[str, str, bytes]],
    meta: list[tuple[str, str]] | None = None,
) -> None:
    """创建测试用 sqlite 数据库，含 checkpoints + thread_meta 表。

    Args:
        db_path: 数据库文件路径。
        threads: [(thread_id, checkpoint_id, checkpoint_blob), ...]
        meta: [(thread_id, last_active_at), ...] — thread_meta 表数据
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
        CREATE TABLE IF NOT EXISTS thread_meta (
            thread_id TEXT PRIMARY KEY,
            last_active_at TEXT NOT NULL
        );
        """
    )
    for thread_id, checkpoint_id, blob in threads:
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint) "
            "VALUES (?, '', ?, ?)",
            (thread_id, checkpoint_id, blob),
        )
    if meta:
        for thread_id, last_active_at in meta:
            conn.execute(
                "INSERT INTO thread_meta (thread_id, last_active_at) VALUES (?, ?)",
                (thread_id, last_active_at),
            )
    conn.commit()
    conn.close()


async def test_list_threads_returns_real_timestamp(tmp_path: Path) -> None:
    """list_threads 返回 thread_meta.last_active_at 真实时间戳（T5.7 核心）。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db_with_meta(
        db_path,
        threads=[
            ("thread_a", "cp_uuid_1", b"blob_a_1"),
            ("thread_a", "cp_uuid_2", b"blob_a_2"),
            ("thread_b", "cp_uuid_3", b"blob_b_1"),
        ],
        meta=[
            ("thread_a", "2026-07-14T10:30:00+00:00"),
            ("thread_b", "2026-07-14T09:00:00+00:00"),
        ],
    )

    from app.memory.checkpointer_view import list_threads

    result = await list_threads()
    assert len(result) == 2

    by_thread = {r["thread_id"]: r for r in result}

    a = by_thread["thread_a"]
    assert a["checkpoint_count"] == 2
    # T5.7: last_updated 是真实时间戳，不是 checkpoint_id（UUID）
    assert a["last_updated"] == "2026-07-14T10:30:00+00:00"
    assert a["last_updated"] != "cp_uuid_2"  # 确保不是旧的 checkpoint_id
    assert a["size_bytes"] == len(b"blob_a_1") + len(b"blob_a_2")

    b = by_thread["thread_b"]
    assert b["checkpoint_count"] == 1
    assert b["last_updated"] == "2026-07-14T09:00:00+00:00"
    assert b["last_updated"] != "cp_uuid_3"


async def test_list_threads_none_when_no_meta(tmp_path: Path) -> None:
    """thread_meta 中无记录时 last_updated 为 None（旧 thread 兜底）。"""
    db_path = tmp_path / "agentx.db"
    # 不插入 thread_meta 数据
    _create_test_db_with_meta(
        db_path,
        threads=[("thread_old", "cp1", b"blob")],
        meta=None,
    )

    from app.memory.checkpointer_view import list_threads

    result = await list_threads()
    assert len(result) == 1
    assert result[0]["thread_id"] == "thread_old"
    # 无 thread_meta 记录时 last_updated 为 None
    assert result[0]["last_updated"] is None
    assert result[0]["checkpoint_count"] == 1


async def test_list_threads_partial_meta(tmp_path: Path) -> None:
    """部分 thread 有 meta、部分没有时，各自的 last_updated 正确。"""
    db_path = tmp_path / "agentx.db"
    _create_test_db_with_meta(
        db_path,
        threads=[
            ("thread_with_meta", "cp1", b"x"),
            ("thread_without_meta", "cp2", b"y"),
        ],
        meta=[("thread_with_meta", "2026-07-14T12:00:00+00:00")],
    )

    from app.memory.checkpointer_view import list_threads

    result = await list_threads()
    by_thread = {r["thread_id"]: r for r in result}

    assert by_thread["thread_with_meta"]["last_updated"] == "2026-07-14T12:00:00+00:00"
    assert by_thread["thread_without_meta"]["last_updated"] is None
