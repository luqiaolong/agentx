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
