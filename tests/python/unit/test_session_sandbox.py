"""SessionSandbox 单元测试：覆盖 filesystem-sandbox spec 全部场景。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.config import UPLOADS_DIR, WORKSPACE_DIR
from app.sandbox import (
    PathNotAuthorized,
    SessionSandbox,
    get_sandbox,
)
from app.sandbox.store import SandboxStore
from app.security.approval import ApprovalDecision, ApprovalResult


@pytest.fixture
def sandbox() -> SessionSandbox:
    """每个测试用例使用独立实例，避免共享状态。"""
    return SessionSandbox()


# 1. 默认白名单始终可读
@pytest.mark.asyncio
async def test_default_whitelist_readable(sandbox: SessionSandbox) -> None:
    await sandbox.check_read("t1", WORKSPACE_DIR / "foo.txt")  # 不抛异常即通过


# 2. 未授权路径拒绝读取
@pytest.mark.asyncio
async def test_unauthorized_path_rejected(sandbox: SessionSandbox) -> None:
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/secrets/passwords.txt")


# 3. 授权目录可读但不可写（默认 read-only）
@pytest.mark.asyncio
async def test_authorized_readonly_not_writable(sandbox: SessionSandbox) -> None:
    await sandbox.authorize("t1", "d:/docs", writable=False)
    await sandbox.check_read("t1", "d:/docs/x")  # 读通过
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_write("t1", "d:/docs/out.txt")


# 4. 授权可写目录
@pytest.mark.asyncio
async def test_authorized_writable(sandbox: SessionSandbox) -> None:
    await sandbox.authorize("t1", "d:/docs", writable=True)
    await sandbox.check_write("t1", "d:/docs/out.txt")  # 写通过


# 5. 路径规范化（.. 解析、规范化后比对）
@pytest.mark.asyncio
async def test_path_normalization(sandbox: SessionSandbox) -> None:
    await sandbox.authorize("t1", "d:/docs/../secrets")
    listed = await sandbox.list_authorized("t1")
    # 规范化后应为 d:/secrets（与原样表达式解析结果一致）
    assert listed == [(Path("d:/secrets").resolve(), False)]
    # 通过含 .. 的路径访问也能通过（先规范化再比对）
    await sandbox.check_read("t1", "d:/docs/../secrets/x")


# 6. 系统关键目录拒绝授权，授权列表不变
@pytest.mark.asyncio
async def test_system_critical_dir_rejected(sandbox: SessionSandbox) -> None:
    if sys.platform == "win32":
        critical = "C:/Windows/System32"
    else:
        critical = "/etc"
    before = await sandbox.list_authorized("t1")
    with pytest.raises(ValueError):
        await sandbox.authorize("t1", critical)
    after = await sandbox.list_authorized("t1")
    assert before == after


# 7. 会话隔离：不同 thread_id 授权互不影响
@pytest.mark.asyncio
async def test_session_isolation(sandbox: SessionSandbox) -> None:
    await sandbox.authorize("t1", "d:/docs")
    await sandbox.check_read("t1", "d:/docs/x")  # t1 通过
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t2", "d:/docs/x")  # t2 无授权


# 8. clear 清空会话授权
@pytest.mark.asyncio
async def test_clear(sandbox: SessionSandbox) -> None:
    await sandbox.authorize("t1", "d:/docs")
    await sandbox.clear("t1")
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/docs/x")


# 9. revoke 撤销授权，返回是否曾存在
@pytest.mark.asyncio
async def test_revoke(sandbox: SessionSandbox) -> None:
    assert await sandbox.revoke("t1", "d:/docs") is False  # 不存在
    await sandbox.authorize("t1", "d:/docs")
    assert await sandbox.revoke("t1", "d:/docs") is True  # 已存在并移除
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/docs/x")


# 10. restore / snapshot 往返
@pytest.mark.asyncio
async def test_restore_snapshot(sandbox: SessionSandbox) -> None:
    await sandbox.authorize("t1", "d:/docs")
    snap = await sandbox.snapshot("t1")
    assert snap == [str(Path("d:/docs").resolve())]
    await sandbox.clear("t1")
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/docs/x")
    await sandbox.restore("t1", snap)
    await sandbox.check_read("t1", "d:/docs/x")  # 恢复后再次通过


# 11. 前缀匹配：授权目录覆盖其下所有路径，但不误匹配兄弟目录
@pytest.mark.asyncio
async def test_prefix_match(sandbox: SessionSandbox) -> None:
    await sandbox.authorize("t1", "d:/docs")
    await sandbox.check_read("t1", "d:/docs/a/b/c.txt")  # 深层子路径通过
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/docs-other/x")  # 兄弟目录拒绝


# 12. 默认白名单始终可写
@pytest.mark.asyncio
async def test_default_whitelist_writable(sandbox: SessionSandbox) -> None:
    await sandbox.check_write("t1", WORKSPACE_DIR / "x")
    await sandbox.check_write("t1", UPLOADS_DIR / "x")


# 额外：单例 get_sandbox 返回同一实例
def test_singleton() -> None:
    s1 = get_sandbox()
    s2 = get_sandbox()
    assert s1 is s2


# ============================================================
# 路径安全：边界攻击向量
# ============================================================


@pytest.mark.asyncio
async def test_relative_path_resolved_against_project_root(sandbox: SessionSandbox) -> None:
    """相对路径按 PROJECT_ROOT 解析（不是 CWD），避免 LLM 工具调用路径错位。"""
    # data/workspace 是白名单，相对路径解析后应命中
    await sandbox.check_read("t1", "data/workspace/foo.txt")
    # 相对路径走出 PROJECT_ROOT 仍被拒
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "../../etc/passwd")


@pytest.mark.asyncio
async def test_path_traversal_in_authorized_dir(sandbox: SessionSandbox) -> None:
    """授权目录内用 .. 跳出：被规范化后应仍位于授权目录下（无逃逸）。"""
    await sandbox.authorize("t1", "d:/docs")
    # d:/docs/../secrets/x 规范化为 d:/secrets/x，应被拒
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/docs/../secrets/x")


@pytest.mark.asyncio
async def test_authorize_rejects_empty_path(sandbox: SessionSandbox) -> None:
    """空字符串路径授权应直接抛错（避免授权到 CWD 根）。"""
    with pytest.raises((ValueError, OSError)):
        await sandbox.authorize("t1", "")


@pytest.mark.asyncio
async def test_writable_upgrade_via_reauthorize(sandbox: SessionSandbox) -> None:
    """同路径重新授权 writable=True 应升级权限（先 remove 再 add）。"""
    await sandbox.authorize("t1", "d:/docs", writable=False)
    # 写应被拒
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_write("t1", "d:/docs/out.txt")
    # 重新授权为可写
    await sandbox.authorize("t1", "d:/docs", writable=True)
    await sandbox.check_write("t1", "d:/docs/out.txt")  # 现在通过


@pytest.mark.asyncio
async def test_writable_downgrade_via_reauthorize(sandbox: SessionSandbox) -> None:
    """从可写降级为只读也应生效。"""
    await sandbox.authorize("t1", "d:/docs", writable=True)
    await sandbox.check_write("t1", "d:/docs/x")
    await sandbox.authorize("t1", "d:/docs", writable=False)
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_write("t1", "d:/docs/x")


@pytest.mark.asyncio
async def test_unauthorized_path_does_not_leak_authorization(sandbox: SessionSandbox) -> None:
    """未授权路径访问时不应被记录为已授权（防 side-effect）。"""
    before = set(await sandbox.list_authorized("t1"))
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/some/other/path")
    after = set(await sandbox.list_authorized("t1"))
    assert before == after


@pytest.mark.asyncio
async def test_prefix_match_case_insensitive_on_windows(
    sandbox: SessionSandbox,
) -> None:
    """Windows 路径大小写不敏感：授权 D:/docs，访问 d:/DOCS/x 应通过。"""
    if sys.platform != "win32":
        pytest.skip("Windows-specific")
    await sandbox.authorize("t1", "D:/docs")
    await sandbox.check_read("t1", "d:/DOCS/x.txt")  # 混合大小写
    await sandbox.check_read("t1", "D:/Docs/x.txt")  # 标题大小写


@pytest.mark.asyncio
async def test_restore_with_empty_list_keeps_existing(sandbox: SessionSandbox) -> None:
    """restore(tid, []) 是 no-op：只往里加，不删已有（避免 checkpoint 空 list 清空授权）。

    调用方需要清空应显式调 clear()，不要靠 restore([])。
    """
    await sandbox.authorize("t1", "d:/docs")
    await sandbox.restore("t1", [])
    # 仍可读
    await sandbox.check_read("t1", "d:/docs/x")


@pytest.mark.asyncio
async def test_snapshot_deterministic_ordering(sandbox: SessionSandbox) -> None:
    """snapshot 输出按路径排序，结果确定性（便于 checkpoint 校验）。"""
    await sandbox.authorize("t1", "z:/z")
    await sandbox.authorize("t1", "a:/a")
    await sandbox.authorize("t1", "m:/m")
    snap = await sandbox.snapshot("t1")
    # 排序断言
    assert snap == sorted(snap)


@pytest.mark.asyncio
async def test_authorize_normalizes_trailing_slash(sandbox: SessionSandbox) -> None:
    """trailing slash 不影响授权结果（Path 规范化时去掉）。"""
    await sandbox.authorize("t1", "d:/docs/")
    listed = await sandbox.list_authorized("t1")
    # 不带 trailing slash
    assert listed[0][0] == Path("d:/docs").resolve()


# ============================================================
# full_trust 模式 + 临时授权 + is_path_authorized
# ============================================================


@pytest.mark.asyncio
async def test_full_trust_bypasses_read_auth(sandbox: SessionSandbox) -> None:
    """full_trust 模式下未授权目录可读。"""
    await sandbox.set_full_trust("t1", True)
    # 未授权路径在 standard 模式会拒绝，full_trust 下放行
    await sandbox.check_read("t1", "d:/some/random/path/file.txt")


@pytest.mark.asyncio
async def test_full_trust_bypasses_write_auth(sandbox: SessionSandbox) -> None:
    """full_trust 模式下未授权目录可写。"""
    await sandbox.set_full_trust("t1", True)
    await sandbox.check_write("t1", "d:/some/random/path/out.txt")


@pytest.mark.asyncio
async def test_full_trust_still_rejects_critical_dirs(sandbox: SessionSandbox) -> None:
    """full_trust 模式仍拒绝系统关键目录。"""
    await sandbox.set_full_trust("t1", True)
    if sys.platform == "win32":
        with pytest.raises(PathNotAuthorized):
            await sandbox.check_read("t1", "C:/Windows/System32/drivers/etc/hosts")
    else:
        with pytest.raises(PathNotAuthorized):
            await sandbox.check_read("t1", "/etc/passwd")


@pytest.mark.asyncio
async def test_full_trust_toggle_off_restores_auth(sandbox: SessionSandbox) -> None:
    """关闭 full_trust 后恢复 standard 行为。"""
    await sandbox.set_full_trust("t1", True)
    await sandbox.check_read("t1", "d:/secrets/x")  # 放行
    await sandbox.set_full_trust("t1", False)
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/secrets/x")  # 恢复拒绝


@pytest.mark.asyncio
async def test_is_path_authorized_standard(sandbox: SessionSandbox) -> None:
    """standard 模式下 is_path_authorized 反映白名单与授权目录。"""
    assert await sandbox.is_path_authorized("t1", str(WORKSPACE_DIR / "x")) is True
    assert await sandbox.is_path_authorized("t1", "d:/unauthorized/x") is False
    await sandbox.authorize("t1", "d:/docs", writable=False)
    assert await sandbox.is_path_authorized("t1", "d:/docs/x") is True
    assert await sandbox.is_path_authorized("t1", "d:/docs/x", writable=True) is False


@pytest.mark.asyncio
async def test_is_path_authorized_full_trust(sandbox: SessionSandbox) -> None:
    """full_trust 模式下 is_path_authorized 对非关键目录返回 True。"""
    await sandbox.set_full_trust("t1", True)
    assert await sandbox.is_path_authorized("t1", "d:/anywhere/x") is True
    assert await sandbox.is_path_authorized("t1", "d:/anywhere/x", writable=True) is True
    # 关键目录仍 False
    if sys.platform == "win32":
        assert await sandbox.is_path_authorized("t1", "C:/Windows/System32/x") is False
    else:
        assert await sandbox.is_path_authorized("t1", "/etc/passwd") is False


@pytest.mark.asyncio
async def test_authorize_temp_grants_read_without_persisting(sandbox: SessionSandbox) -> None:
    """authorize_temp 临时授权读，但不污染 authorized_dirs。"""
    await sandbox.authorize_temp("t1", "d:/tmp_docs", writable=False)
    await sandbox.check_read("t1", "d:/tmp_docs/x")
    # 临时授权不出现在 list_authorized（不持久化）
    assert await sandbox.list_authorized("t1") == []
    # 写仍拒绝（writable=False）
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_write("t1", "d:/tmp_docs/out.txt")


@pytest.mark.asyncio
async def test_clear_temp_removes_temp_authorization(sandbox: SessionSandbox) -> None:
    """clear_temp 清空临时授权。"""
    await sandbox.authorize_temp("t1", "d:/tmp_docs")
    await sandbox.check_read("t1", "d:/tmp_docs/x")
    await sandbox.clear_temp("t1")
    with pytest.raises(PathNotAuthorized):
        await sandbox.check_read("t1", "d:/tmp_docs/x")


@pytest.mark.asyncio
async def test_authorize_temp_rejects_critical_dirs(sandbox: SessionSandbox) -> None:
    """authorize_temp 拒绝系统关键目录。"""
    if sys.platform == "win32":
        with pytest.raises(ValueError):
            await sandbox.authorize_temp("t1", "C:/Windows/System32")
    else:
        with pytest.raises(ValueError):
            await sandbox.authorize_temp("t1", "/etc")


def test_approval_decision_defaults(sandbox: SessionSandbox) -> None:
    """ApprovalResult + ApprovalDecision Enum 语义。"""
    d = ApprovalResult(decision=ApprovalDecision.APPROVE)
    assert d.approved is True
    assert d.decision == ApprovalDecision.APPROVE
    assert d.path is None
    assert d.writable is False
    d2 = ApprovalResult(decision=ApprovalDecision.DENY)
    assert d2.approved is False
    assert d2.decision == ApprovalDecision.DENY


# ============================================================
# 持久化双写 + bootstrap（Task 3）
# ============================================================


@pytest.fixture
def store_sandbox(tmp_path: Path) -> SessionSandbox:
    """带持久化的 sandbox 实例，使用 tmp DB。"""
    store = SandboxStore(db_path=tmp_path / "test.db")
    return SessionSandbox(store=store)


@pytest.mark.asyncio
async def test_authorize_persists_to_db(store_sandbox: SessionSandbox) -> None:
    """authorize 写入内存同时写 DB。"""
    await store_sandbox.authorize("t1", "d:/docs", writable=True)
    entries = store_sandbox._store.list_by_thread("t1")  # noqa: SLF001
    assert len(entries) == 1
    assert entries[0].source == "manual"


@pytest.mark.asyncio
async def test_revoke_deletes_from_db(store_sandbox: SessionSandbox) -> None:
    """revoke 内存同时删 DB。"""
    await store_sandbox.authorize("t1", "d:/docs", writable=True)
    await store_sandbox.revoke("t1", "d:/docs")
    entries = store_sandbox._store.list_by_thread("t1")  # noqa: SLF001
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_clear_deletes_thread_from_db(store_sandbox: SessionSandbox) -> None:
    """clear 内存同时删 DB 该 thread 所有记录。"""
    await store_sandbox.authorize("t1", "d:/docs", writable=True)
    await store_sandbox.authorize("t1", "d:/book", writable=False)
    await store_sandbox.clear("t1")
    entries = store_sandbox._store.list_by_thread("t1")  # noqa: SLF001
    assert len(entries) == 0


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_persistence_disabled_no_db_write(tmp_path: Path) -> None:
    """sandbox_persistence_enabled=False 时不写 DB。"""
    from app.config import get_settings
    settings = get_settings()
    original = settings.sandbox_persistence_enabled
    settings.sandbox_persistence_enabled = False
    try:
        store = SandboxStore(db_path=tmp_path / "test.db")
        sandbox = SessionSandbox(store=store)
        await sandbox.authorize("t1", "d:/docs", writable=True)
        entries = store.list_by_thread("t1")
        assert len(entries) == 0  # DB 未写入
    finally:
        settings.sandbox_persistence_enabled = original
