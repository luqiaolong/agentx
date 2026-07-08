"""sandbox/session_sandbox.py 单元测试：async + Lock + DB-first + parent_thread_id。

覆盖：
- 所有公共方法为 async
- asyncio.Lock 并发安全（并发 authorize 不交叉）
- DB-first 一致性（DB 写失败 → 内存不更新；revoke 基于 DB 返回值）
- parent_thread_id 继承（子 thread 可访问父 thread 授权目录）
- check_write 修复（命中 writable 后 break）
- authorize_temp / clear_temp / snapshot / restore / bootstrap
- get_sandbox 单例
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import UPLOADS_DIR, WORKSPACE_DIR
from app.sandbox.path_guard import PathNotAuthorized
from app.sandbox.session_sandbox import SessionSandbox, get_sandbox
from app.sandbox.store import SandboxStore


@pytest.fixture
def sandbox(tmp_path: Path) -> SessionSandbox:
    """每个测试用例使用独立实例（注入 tmp store 避免 DATA_DIR 不存在）。"""
    store = SandboxStore(db_path=tmp_path / "test.db")
    return SessionSandbox(store=store)


@pytest.fixture
def store_sandbox(tmp_path: Path) -> SessionSandbox:
    """带持久化的 sandbox 实例，使用 tmp DB。"""
    store = SandboxStore(db_path=tmp_path / "test.db")
    return SessionSandbox(store=store)


# ============================================================
# async 方法验证
# ============================================================


async def test_authorize_is_async(sandbox: SessionSandbox) -> None:
    """authorize 是 async 方法，返回 Path。"""
    result = await sandbox.authorize("t1", "d:/docs", writable=False)
    assert isinstance(result, Path)


async def test_revoke_is_async(sandbox: SessionSandbox) -> None:
    """revoke 是 async 方法，返回 bool。"""
    await sandbox.authorize("t1", "d:/docs")
    result = await sandbox.revoke("t1", "d:/docs")
    assert isinstance(result, bool)


async def test_check_read_is_async(sandbox: SessionSandbox) -> None:
    """check_read 是 async 方法。"""
    await sandbox.authorize("t1", "d:/docs")
    await sandbox.check_read("t1", "d:/docs/x")  # 不抛异常即通过


async def test_check_write_is_async(sandbox: SessionSandbox) -> None:
    """check_write 是 async 方法。"""
    await sandbox.authorize("t1", "d:/docs", writable=True)
    await sandbox.check_write("t1", "d:/docs/x")  # 不抛异常即通过


async def test_is_path_authorized_is_async(sandbox: SessionSandbox) -> None:
    """is_path_authorized 是 async 方法，返回 bool。"""
    result = await sandbox.is_path_authorized("t1", str(WORKSPACE_DIR / "x"))
    assert isinstance(result, bool)


async def test_set_full_trust_is_async(sandbox: SessionSandbox) -> None:
    """set_full_trust 是 async 方法。"""
    await sandbox.set_full_trust("t1", True)
    assert await sandbox.is_full_trust("t1") is True


async def test_clear_is_async(sandbox: SessionSandbox) -> None:
    """clear 是 async 方法。"""
    await sandbox.authorize("t1", "d:/docs")
    await sandbox.clear("t1")
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/docs/x")


async def test_snapshot_is_async(sandbox: SessionSandbox) -> None:
    """snapshot 是 async 方法。"""
    await sandbox.authorize("t1", "d:/docs")
    snap = await sandbox.snapshot("t1")
    assert isinstance(snap, list)


async def test_restore_is_async(sandbox: SessionSandbox) -> None:
    """restore 是 async 方法。"""
    await sandbox.restore("t1", ["d:/docs"])
    await sandbox.check_read("t1", "d:/docs/x")


async def test_bootstrap_from_store_is_async(store_sandbox: SessionSandbox) -> None:
    """bootstrap_from_store 是 async 方法。"""
    await store_sandbox.bootstrap_from_store()  # 不抛异常即通过


# ============================================================
# 基本授权 / 校验行为
# ============================================================


async def test_default_whitelist_readable(sandbox: SessionSandbox) -> None:
    """默认白名单始终可读。"""
    await sandbox.check_read("t1", WORKSPACE_DIR / "foo.txt")


async def test_unauthorized_path_rejected(sandbox: SessionSandbox) -> None:
    """未授权路径拒绝读取。"""
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/secrets/passwords.txt")


async def test_authorized_readonly_not_writable(sandbox: SessionSandbox) -> None:
    """授权目录默认只读，不可写。"""
    await sandbox.authorize("t1", "d:/docs", writable=False)
    await sandbox.check_read("t1", "d:/docs/x")
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_write("t1", "d:/docs/out.txt")


async def test_authorized_writable(sandbox: SessionSandbox) -> None:
    """授权可写目录可写。"""
    await sandbox.authorize("t1", "d:/docs", writable=True)
    await sandbox.check_write("t1", "d:/docs/out.txt")


async def test_session_isolation(sandbox: SessionSandbox) -> None:
    """不同 thread_id 授权互不影响。"""
    await sandbox.authorize("t1", "d:/docs")
    await sandbox.check_read("t1", "d:/docs/x")
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t2", "d:/docs/x")


async def test_revoke(sandbox: SessionSandbox) -> None:
    """revoke 撤销授权，返回是否曾存在。"""
    assert await sandbox.revoke("t1", "d:/docs") is False  # 不存在
    await sandbox.authorize("t1", "d:/docs")
    assert await sandbox.revoke("t1", "d:/docs") is True  # 已存在并移除
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/docs/x")


async def test_system_critical_dir_rejected(sandbox: SessionSandbox) -> None:
    """系统关键目录拒绝授权。"""
    if sys.platform == "win32":
        critical = "C:/Windows/System32"
    else:
        critical = "/etc"
    with pytest.raises(ValueError):
        await sandbox.authorize("t1", critical)


async def test_authorize_rejects_empty_path(sandbox: SessionSandbox) -> None:
    """空字符串路径授权应抛错。"""
    with pytest.raises((ValueError, OSError)):
        await sandbox.authorize("t1", "")


# ============================================================
# asyncio.Lock 并发安全
# ============================================================


async def test_concurrent_authorize_no_race(sandbox: SessionSandbox) -> None:
    """并发 authorize 同一 thread_id 不应产生竞态（Lock 保护）。"""
    await asyncio.gather(
        *[sandbox.authorize("t1", f"d:/docs/{i}", writable=False) for i in range(20)]
    )
    listed = await sandbox.list_authorized("t1")
    assert len(listed) == 20


async def test_concurrent_authorize_same_path_idempotent(sandbox: SessionSandbox) -> None:
    """并发 authorize 同一路径应幂等（最终只有一条记录）。"""
    await asyncio.gather(
        *[sandbox.authorize("t1", "d:/docs", writable=True) for _ in range(10)]
    )
    listed = await sandbox.list_authorized("t1")
    assert len(listed) == 1
    assert listed[0][1] is True  # writable


async def test_concurrent_mixed_authorize_revoke(sandbox: SessionSandbox) -> None:
    """并发 authorize + revoke 不应死锁或崩溃。"""
    async def authorize_task() -> None:
        for i in range(10):
            await sandbox.authorize("t1", f"d:/docs/{i}", writable=False)

    async def revoke_task() -> None:
        for i in range(10):
            await sandbox.revoke("t1", f"d:/docs/{i}")

    await asyncio.gather(authorize_task(), revoke_task())
    # 不死锁、不抛异常即通过


# ============================================================
# DB-first 一致性
# ============================================================


async def test_authorize_db_first_persists(store_sandbox: SessionSandbox) -> None:
    """authorize 先写 DB 再改内存（DB-first）。"""
    await store_sandbox.authorize("t1", "d:/docs", writable=True, source="manual")
    entries = store_sandbox._store.list_by_thread("t1")  # noqa: SLF001
    assert len(entries) == 1
    assert entries[0].source == "manual"


async def test_authorize_db_failure_rolls_back_memory(tmp_path: Path) -> None:
    """DB 写失败时内存不更新（DB-first 一致性）。"""
    # 创建一个会抛异常的 mock store
    failing_store = MagicMock(spec=SandboxStore)
    failing_store.upsert.side_effect = sqlite3_error()
    failing_store.bootstrap_all.return_value = {}

    sandbox = SessionSandbox(store=failing_store)
    # authorize 应抛异常
    with pytest.raises(Exception):
        await sandbox.authorize("t1", "d:/docs", writable=True)
    # 内存不应更新
    listed = await sandbox.list_authorized("t1")
    assert listed == []


async def test_revoke_db_first_returns_db_value(store_sandbox: SessionSandbox) -> None:
    """revoke 的 existed 基于 DB 返回值而非内存。"""
    await store_sandbox.authorize("t1", "d:/docs", writable=True)
    # revoke 应返回 True（DB 中存在）
    existed = await store_sandbox.revoke("t1", "d:/docs")
    assert existed is True
    # 二次 revoke 应返回 False（DB 中已不存在）
    existed2 = await store_sandbox.revoke("t1", "d:/docs")
    assert existed2 is False


async def test_revoke_deletes_from_db(store_sandbox: SessionSandbox) -> None:
    """revoke 内存同时删 DB。"""
    await store_sandbox.authorize("t1", "d:/docs", writable=True)
    await store_sandbox.revoke("t1", "d:/docs")
    entries = store_sandbox._store.list_by_thread("t1")  # noqa: SLF001
    assert len(entries) == 0


async def test_clear_deletes_thread_from_db(store_sandbox: SessionSandbox) -> None:
    """clear 内存同时删 DB 该 thread 所有记录。"""
    await store_sandbox.authorize("t1", "d:/docs", writable=True)
    await store_sandbox.authorize("t1", "d:/book", writable=False)
    await store_sandbox.clear("t1")
    entries = store_sandbox._store.list_by_thread("t1")  # noqa: SLF001
    assert len(entries) == 0


async def test_bootstrap_restores_from_db(tmp_path: Path) -> None:
    """bootstrap 从 DB 恢复授权到内存。"""
    store = SandboxStore(db_path=tmp_path / "test.db")
    store.upsert("t1", "d:/docs", writable=True, source="manual")
    store.upsert("t1", "d:/book", writable=False, source="chip")

    sandbox = SessionSandbox(store=store)
    await sandbox.bootstrap_from_store()

    listed = await sandbox.list_authorized("t1")
    paths = {str(p) for (p, _w) in listed}
    assert any("docs" in p for p in paths)
    assert any("book" in p for p in paths)


# ============================================================
# parent_thread_id 继承
# ============================================================


async def test_register_parent_maps_child_to_parent(sandbox: SessionSandbox) -> None:
    """register_parent 注册 child → parent 映射。"""
    await sandbox.register_parent("child_thread", "parent_thread")
    # 内部映射应存在（通过行为间接验证）
    await sandbox.authorize("parent_thread", "d:/docs", writable=True)
    # child_thread 应能继承 parent_thread 的授权
    await sandbox.check_read("child_thread", "d:/docs/x")


async def test_check_read_inherits_parent_authorization(sandbox: SessionSandbox) -> None:
    """check_read 支持 parent_thread_id 继承。"""
    await sandbox.authorize("parent_thread", "d:/docs", writable=False)
    # child_thread 未授权，但通过 parent_thread_id 继承
    await sandbox.check_read(
        "child_thread", "d:/docs/x", parent_thread_id="parent_thread"
    )


async def test_check_write_inherits_parent_writable(sandbox: SessionSandbox) -> None:
    """check_write 支持 parent_thread_id 继承（writable）。"""
    await sandbox.authorize("parent_thread", "d:/docs", writable=True)
    await sandbox.check_write(
        "child_thread", "d:/docs/out.txt", parent_thread_id="parent_thread"
    )


async def test_check_write_inherits_parent_readonly(sandbox: SessionSandbox) -> None:
    """父授权只读时，子 thread 写应被拒。"""
    await sandbox.authorize("parent_thread", "d:/docs", writable=False)
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_write(
            "child_thread", "d:/docs/out.txt", parent_thread_id="parent_thread"
        )


async def test_is_path_authorized_inherits_parent(sandbox: SessionSandbox) -> None:
    """is_path_authorized 支持 parent_thread_id 继承。"""
    await sandbox.authorize("parent_thread", "d:/docs", writable=False)
    assert await sandbox.is_path_authorized(
        "child_thread", "d:/docs/x", writable=False, parent_thread_id="parent_thread"
    ) is True
    assert await sandbox.is_path_authorized(
        "child_thread", "d:/docs/x", writable=True, parent_thread_id="parent_thread"
    ) is False


async def test_parent_not_authorized_still_rejects(sandbox: SessionSandbox) -> None:
    """父 thread 也未授权时，子 thread 仍被拒。"""
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read(
            "child_thread", "d:/unauthorized/x", parent_thread_id="parent_thread"
        )


async def test_clear_child_does_not_affect_parent(sandbox: SessionSandbox) -> None:
    """clear(child_thread_id) 仅清理子映射，不影响父授权。"""
    await sandbox.authorize("parent_thread", "d:/docs", writable=True)
    await sandbox.register_parent("child_thread", "parent_thread")
    await sandbox.clear("child_thread")
    # 父授权仍在
    await sandbox.check_read("parent_thread", "d:/docs/x")


# ============================================================
# check_write 修复：命中 writable 后 break
# ============================================================


async def test_check_write_writable_match_returns_immediately(sandbox: SessionSandbox) -> None:
    """check_write 命中 writable 授权后立即返回（break），不再检查后续条目。"""
    await sandbox.authorize("t1", "d:/docs", writable=True)
    # 应直接通过，不抛异常
    await sandbox.check_write("t1", "d:/docs/out.txt")


async def test_check_write_multiple_entries_finds_writable(
    sandbox: SessionSandbox,
) -> None:
    """多个授权条目中存在 writable 时，check_write 应找到并放行。

    场景：authorized_dirs 有 d:/docs (readonly) + d:/docs/sub (writable)，
    写 d:/docs/sub/file 应通过（命中 writable 后 break）。
    """
    await sandbox.authorize("t1", "d:/docs", writable=False)
    await sandbox.authorize("t1", "d:/docs/sub", writable=True)
    # d:/docs/sub/file 命中 d:/docs/sub (writable) → 通过
    await sandbox.check_write("t1", "d:/docs/sub/file")


async def test_check_write_readonly_match_sets_matched_error(
    sandbox: SessionSandbox,
) -> None:
    """check_write 命中只读条目时应抛 '仅授权读取' 错误。"""
    await sandbox.authorize("t1", "d:/docs", writable=False)
    with pytest.raises(PathNotAuthorized) as exc_info:
        await sandbox.check_write("t1", "d:/docs/out.txt")
    assert "仅授权读取" in str(exc_info.value) or "未授权" in str(exc_info.value)


# ============================================================
# authorize_temp / clear_temp
# ============================================================


async def test_authorize_temp_grants_read(sandbox: SessionSandbox) -> None:
    """authorize_temp 临时授权读，不持久化。"""
    await sandbox.authorize_temp("t1", "d:/tmp_docs", writable=False)
    await sandbox.check_read("t1", "d:/tmp_docs/x")
    listed = await sandbox.list_authorized("t1")
    assert listed == []


async def test_authorize_temp_writable(sandbox: SessionSandbox) -> None:
    """authorize_temp 支持 writable。"""
    await sandbox.authorize_temp("t1", "d:/tmp_docs", writable=True)
    await sandbox.check_write("t1", "d:/tmp_docs/out.txt")


async def test_clear_temp_removes_temp_authorization(sandbox: SessionSandbox) -> None:
    """clear_temp 清空临时授权。"""
    await sandbox.authorize_temp("t1", "d:/tmp_docs")
    await sandbox.check_read("t1", "d:/tmp_docs/x")
    await sandbox.clear_temp("t1")
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/tmp_docs/x")


# ============================================================
# snapshot / restore
# ============================================================


async def test_snapshot_restore_roundtrip(sandbox: SessionSandbox) -> None:
    """snapshot + restore 往返。"""
    await sandbox.authorize("t1", "d:/docs", writable=True)
    snap = await sandbox.snapshot("t1")
    assert len(snap) >= 1
    await sandbox.clear("t1")
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/docs/x")
    await sandbox.restore("t1", snap)
    await sandbox.check_read("t1", "d:/docs/x")


async def test_snapshot_deterministic_ordering(sandbox: SessionSandbox) -> None:
    """snapshot 输出按路径排序。"""
    await sandbox.authorize("t1", "z:/z")
    await sandbox.authorize("t1", "a:/a")
    await sandbox.authorize("t1", "m:/m")
    snap = await sandbox.snapshot("t1")
    assert snap == sorted(snap)


# ============================================================
# full_trust 模式
# ============================================================


async def test_full_trust_bypasses_read_auth(sandbox: SessionSandbox) -> None:
    """full_trust 模式下未授权目录可读。"""
    await sandbox.set_full_trust("t1", True)
    await sandbox.check_read("t1", "d:/some/random/path/file.txt")


async def test_full_trust_bypasses_write_auth(sandbox: SessionSandbox) -> None:
    """full_trust 模式下未授权目录可写。"""
    await sandbox.set_full_trust("t1", True)
    await sandbox.check_write("t1", "d:/some/random/path/out.txt")


async def test_full_trust_still_rejects_critical_dirs(sandbox: SessionSandbox) -> None:
    """full_trust 模式仍拒绝系统关键目录。"""
    await sandbox.set_full_trust("t1", True)
    if sys.platform == "win32":
        with pytest.raises(PathNotAuthorized):
            await sandbox.check_read("t1", "C:/Windows/System32/drivers/etc/hosts")
    else:
        with pytest.raises(PathNotAuthorized):
            await sandbox.check_read("t1", "/etc/passwd")


async def test_full_trust_toggle_off_restores_auth(sandbox: SessionSandbox) -> None:
    """关闭 full_trust 后恢复 standard 行为。"""
    await sandbox.set_full_trust("t1", True)
    await sandbox.check_read("t1", "d:/secrets/x")
    await sandbox.set_full_trust("t1", False)
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/secrets/x")


# ============================================================
# is_path_authorized
# ============================================================


async def test_is_path_authorized_standard(sandbox: SessionSandbox) -> None:
    """standard 模式下 is_path_authorized 反映白名单与授权目录。"""
    assert await sandbox.is_path_authorized("t1", str(WORKSPACE_DIR / "x")) is True
    assert await sandbox.is_path_authorized("t1", "d:/unauthorized/x") is False
    await sandbox.authorize("t1", "d:/docs", writable=False)
    assert await sandbox.is_path_authorized("t1", "d:/docs/x") is True
    assert await sandbox.is_path_authorized("t1", "d:/docs/x", writable=True) is False


async def test_is_path_authorized_full_trust(sandbox: SessionSandbox) -> None:
    """full_trust 模式下 is_path_authorized 对非关键目录返回 True。"""
    await sandbox.set_full_trust("t1", True)
    assert await sandbox.is_path_authorized("t1", "d:/anywhere/x") is True
    assert await sandbox.is_path_authorized("t1", "d:/anywhere/x", writable=True) is True


# ============================================================
# list_authorized / get_sandbox
# ============================================================


async def test_list_authorized_sorted(sandbox: SessionSandbox) -> None:
    """list_authorized 返回排序后的列表。"""
    await sandbox.authorize("t1", "d:/zzz")
    await sandbox.authorize("t1", "d:/aaa")
    listed = await sandbox.list_authorized("t1")
    paths = [str(p) for (p, _w) in listed]
    assert paths == sorted(paths)


async def test_list_authorized_empty(sandbox: SessionSandbox) -> None:
    """未授权的 thread 返回空列表。"""
    listed = await sandbox.list_authorized("t1")
    assert listed == []


def test_get_sandbox_singleton(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_sandbox 返回模块级单例。"""
    import app.sandbox.session_sandbox as ss_module
    import app.sandbox.store as store_module

    # 重置单例 + 指向 tmp DB 避免 DATA_DIR 不存在
    monkeypatch.setattr(ss_module, "_sandbox", None)
    monkeypatch.setattr(store_module, "_store", None)
    monkeypatch.setattr(store_module, "DATA_DIR", tmp_path)
    s1 = get_sandbox()
    s2 = get_sandbox()
    assert s1 is s2


# ============================================================
# 辅助
# ============================================================


def sqlite3_error() -> Exception:
    """构造一个模拟 sqlite3 异常。"""
    return Exception("simulated DB write failure")
