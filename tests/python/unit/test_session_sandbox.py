"""SessionSandbox 单元测试：覆盖 filesystem-sandbox spec 全部场景。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.config import UPLOADS_DIR, WORKSPACE_DIR
from app.utils.security import (
    PathNotAuthorized,
    SessionSandbox,
    get_sandbox,
)


@pytest.fixture
def sandbox() -> SessionSandbox:
    """每个测试用例使用独立实例，避免共享状态。"""
    return SessionSandbox()


# 1. 默认白名单始终可读
def test_default_whitelist_readable(sandbox: SessionSandbox) -> None:
    sandbox.check_read("t1", WORKSPACE_DIR / "foo.txt")  # 不抛异常即通过


# 2. 未授权路径拒绝读取
def test_unauthorized_path_rejected(sandbox: SessionSandbox) -> None:
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "d:/secrets/passwords.txt")


# 3. 授权目录可读但不可写（默认 read-only）
def test_authorized_readonly_not_writable(sandbox: SessionSandbox) -> None:
    sandbox.authorize("t1", "d:/docs", writable=False)
    sandbox.check_read("t1", "d:/docs/x")  # 读通过
    with pytest.raises(PathNotAuthorized):
        sandbox.check_write("t1", "d:/docs/out.txt")


# 4. 授权可写目录
def test_authorized_writable(sandbox: SessionSandbox) -> None:
    sandbox.authorize("t1", "d:/docs", writable=True)
    sandbox.check_write("t1", "d:/docs/out.txt")  # 写通过


# 5. 路径规范化（.. 解析、规范化后比对）
def test_path_normalization(sandbox: SessionSandbox) -> None:
    sandbox.authorize("t1", "d:/docs/../secrets")
    listed = sandbox.list_authorized("t1")
    # 规范化后应为 d:/secrets（与原样表达式解析结果一致）
    assert listed == [(Path("d:/secrets").resolve(), False)]
    # 通过含 .. 的路径访问也能通过（先规范化再比对）
    sandbox.check_read("t1", "d:/docs/../secrets/x")


# 6. 系统关键目录拒绝授权，授权列表不变
def test_system_critical_dir_rejected(sandbox: SessionSandbox) -> None:
    if sys.platform == "win32":
        critical = "C:/Windows/System32"
    else:
        critical = "/etc"
    before = sandbox.list_authorized("t1")
    with pytest.raises(ValueError):
        sandbox.authorize("t1", critical)
    after = sandbox.list_authorized("t1")
    assert before == after


# 7. 会话隔离：不同 thread_id 授权互不影响
def test_session_isolation(sandbox: SessionSandbox) -> None:
    sandbox.authorize("t1", "d:/docs")
    sandbox.check_read("t1", "d:/docs/x")  # t1 通过
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t2", "d:/docs/x")  # t2 无授权


# 8. clear 清空会话授权
def test_clear(sandbox: SessionSandbox) -> None:
    sandbox.authorize("t1", "d:/docs")
    sandbox.clear("t1")
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "d:/docs/x")


# 9. revoke 撤销授权，返回是否曾存在
def test_revoke(sandbox: SessionSandbox) -> None:
    assert sandbox.revoke("t1", "d:/docs") is False  # 不存在
    sandbox.authorize("t1", "d:/docs")
    assert sandbox.revoke("t1", "d:/docs") is True  # 已存在并移除
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "d:/docs/x")


# 10. restore / snapshot 往返
def test_restore_snapshot(sandbox: SessionSandbox) -> None:
    sandbox.authorize("t1", "d:/docs")
    snap = sandbox.snapshot("t1")
    assert snap == [str(Path("d:/docs").resolve())]
    sandbox.clear("t1")
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "d:/docs/x")
    sandbox.restore("t1", snap)
    sandbox.check_read("t1", "d:/docs/x")  # 恢复后再次通过


# 11. 前缀匹配：授权目录覆盖其下所有路径，但不误匹配兄弟目录
def test_prefix_match(sandbox: SessionSandbox) -> None:
    sandbox.authorize("t1", "d:/docs")
    sandbox.check_read("t1", "d:/docs/a/b/c.txt")  # 深层子路径通过
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "d:/docs-other/x")  # 兄弟目录拒绝


# 12. 默认白名单始终可写
def test_default_whitelist_writable(sandbox: SessionSandbox) -> None:
    sandbox.check_write("t1", WORKSPACE_DIR / "x")
    sandbox.check_write("t1", UPLOADS_DIR / "x")


# 额外：单例 get_sandbox 返回同一实例
def test_singleton() -> None:
    s1 = get_sandbox()
    s2 = get_sandbox()
    assert s1 is s2


# ============================================================
# 路径安全：边界攻击向量
# ============================================================


def test_relative_path_resolved_against_project_root(sandbox: SessionSandbox) -> None:
    """相对路径按 PROJECT_ROOT 解析（不是 CWD），避免 LLM 工具调用路径错位。"""
    # data/workspace 是白名单，相对路径解析后应命中
    sandbox.check_read("t1", "data/workspace/foo.txt")
    # 相对路径走出 PROJECT_ROOT 仍被拒
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "../../etc/passwd")


def test_path_traversal_in_authorized_dir(sandbox: SessionSandbox) -> None:
    """授权目录内用 .. 跳出：被规范化后应仍位于授权目录下（无逃逸）。"""
    sandbox.authorize("t1", "d:/docs")
    # d:/docs/../secrets/x 规范化为 d:/secrets/x，应被拒
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "d:/docs/../secrets/x")


def test_authorize_rejects_empty_path(sandbox: SessionSandbox) -> None:
    """空字符串路径授权应直接抛错（避免授权到 CWD 根）。"""
    with pytest.raises((ValueError, OSError)):
        sandbox.authorize("t1", "")


def test_writable_upgrade_via_reauthorize(sandbox: SessionSandbox) -> None:
    """同路径重新授权 writable=True 应升级权限（先 remove 再 add）。"""
    sandbox.authorize("t1", "d:/docs", writable=False)
    # 写应被拒
    with pytest.raises(PathNotAuthorized):
        sandbox.check_write("t1", "d:/docs/out.txt")
    # 重新授权为可写
    sandbox.authorize("t1", "d:/docs", writable=True)
    sandbox.check_write("t1", "d:/docs/out.txt")  # 现在通过


def test_writable_downgrade_via_reauthorize(sandbox: SessionSandbox) -> None:
    """从可写降级为只读也应生效。"""
    sandbox.authorize("t1", "d:/docs", writable=True)
    sandbox.check_write("t1", "d:/docs/x")
    sandbox.authorize("t1", "d:/docs", writable=False)
    with pytest.raises(PathNotAuthorized):
        sandbox.check_write("t1", "d:/docs/x")


def test_unauthorized_path_does_not_leak_authorization(sandbox: SessionSandbox) -> None:
    """未授权路径访问时不应被记录为已授权（防 side-effect）。"""
    before = set(sandbox.list_authorized("t1"))
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "d:/some/other/path")
    after = set(sandbox.list_authorized("t1"))
    assert before == after


def test_prefix_match_case_insensitive_on_windows(
    sandbox: SessionSandbox,
) -> None:
    """Windows 路径大小写不敏感：授权 D:/docs，访问 d:/DOCS/x 应通过。"""
    if sys.platform != "win32":
        pytest.skip("Windows-specific")
    sandbox.authorize("t1", "D:/docs")
    sandbox.check_read("t1", "d:/DOCS/x.txt")  # 混合大小写
    sandbox.check_read("t1", "D:/Docs/x.txt")  # 标题大小写


def test_restore_with_empty_list_keeps_existing(sandbox: SessionSandbox) -> None:
    """restore(tid, []) 是 no-op：只往里加，不删已有（避免 checkpoint 空 list 清空授权）。

    调用方需要清空应显式调 clear()，不要靠 restore([])。
    """
    sandbox.authorize("t1", "d:/docs")
    sandbox.restore("t1", [])
    # 仍可读
    sandbox.check_read("t1", "d:/docs/x")


def test_snapshot_deterministic_ordering(sandbox: SessionSandbox) -> None:
    """snapshot 输出按路径排序，结果确定性（便于 checkpoint 校验）。"""
    sandbox.authorize("t1", "z:/z")
    sandbox.authorize("t1", "a:/a")
    sandbox.authorize("t1", "m:/m")
    snap = sandbox.snapshot("t1")
    # 排序断言
    assert snap == sorted(snap)


def test_authorize_normalizes_trailing_slash(sandbox: SessionSandbox) -> None:
    """trailing slash 不影响授权结果（Path 规范化时去掉）。"""
    sandbox.authorize("t1", "d:/docs/")
    listed = sandbox.list_authorized("t1")
    # 不带 trailing slash
    assert listed[0][0] == Path("d:/docs").resolve()
