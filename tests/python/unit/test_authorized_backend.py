"""AuthorizedLocalShellBackend + check_*_sync 单元测试。

覆盖 Phase A.0 基础设施：
- ``SessionSandbox.check_read_sync`` / ``check_write_sync`` 同步版授权校验
- ``AuthorizedLocalShellBackend`` 6 个 fs 方法注入 thread_id 级动态授权
- ``current_thread_id`` contextvar 传递
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import WORKSPACE_DIR
from app.deepagent.authorized_backend import AuthorizedLocalShellBackend
from app.deepagent.context import current_thread_id
from app.sandbox.path_guard import PathNotAuthorized
from app.sandbox.session_sandbox import get_sandbox


@pytest.fixture(autouse=True)
def reset_sandbox() -> None:
    """每个测试前后重置 sandbox 单例内部状态，避免测试间泄漏。"""
    sandbox = get_sandbox()
    sandbox._authorized_dirs.clear()
    sandbox._temp_authorized.clear()
    sandbox._full_trust_threads.clear()
    sandbox._parent_map.clear()
    yield
    sandbox._authorized_dirs.clear()
    sandbox._temp_authorized.clear()
    sandbox._full_trust_threads.clear()
    sandbox._parent_map.clear()


@pytest.fixture(autouse=True)
def reset_thread_id() -> None:
    """每个测试前后重置 contextvar，避免 thread_id 泄漏到后续测试。"""
    token = current_thread_id.set("")
    yield
    current_thread_id.reset(token)


# ---- check_read_sync ----


def test_check_read_sync_whitelist() -> None:
    """1. 白名单路径（data/workspace 下）读放行。"""
    sandbox = get_sandbox()
    # WORKSPACE_DIR 在 DEFAULT_WHITELIST 中，应放行
    sandbox.check_read_sync("t1", str(WORKSPACE_DIR / "foo.txt"))  # 不抛即通过


def test_check_read_sync_unauthorized_rejected(tmp_path: Path) -> None:
    """2. 未授权路径读拒绝（raise PathNotAuthorized）。"""
    sandbox = get_sandbox()
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read_sync("t1", str(tmp_path / "secret.txt"))


# ---- check_write_sync ----


def test_check_write_sync_whitelist() -> None:
    """3. 白名单路径写放行。"""
    sandbox = get_sandbox()
    sandbox.check_write_sync("t1", str(WORKSPACE_DIR / "out.txt"))  # 不抛即通过


def test_check_write_sync_unauthorized_rejected(tmp_path: Path) -> None:
    """4. 未授权路径写拒绝。"""
    sandbox = get_sandbox()
    with pytest.raises(PathNotAuthorized):
        sandbox.check_write_sync("t1", str(tmp_path / "out.txt"))


def test_check_write_sync_readonly_rejected(tmp_path: Path) -> None:
    """5. 只读授权路径拒绝写入（命中但 writable=False，抛 PathNotAuthorized）。"""
    sandbox = get_sandbox()
    resolved = Path(tmp_path).resolve()
    sandbox._authorized_dirs["t1"] = {(resolved, False)}  # 只读授权
    with pytest.raises(PathNotAuthorized):
        sandbox.check_write_sync("t1", str(tmp_path / "out.txt"))


# ---- AuthorizedLocalShellBackend.read ----


def test_backend_read_unauthorized_returns_error(tmp_path: Path) -> None:
    """6. 未授权路径 read 返回 ReadResult(error=...)，不抛异常。"""
    current_thread_id.set("t1")
    backend = AuthorizedLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
    # 无任何授权，read 相对路径解析到 tmp_path 下，应被拒绝
    result = backend.read("secret.txt")
    assert result.error is not None
    assert result.file_data is None


def test_backend_read_whitelist_returns_content() -> None:
    """7. 白名单路径 read 返回正常 ReadResult（file_data 非 None）。

    用 WORKSPACE_DIR 作为 root_dir（在 DEFAULT_WHITELIST 中），创建临时文件并读取。
    """
    current_thread_id.set("t1")
    test_file = WORKSPACE_DIR / "test_authorized_backend_read.txt"
    test_file.write_text("hello world\n", encoding="utf-8")
    try:
        backend = AuthorizedLocalShellBackend(root_dir=WORKSPACE_DIR, virtual_mode=False)
        result = backend.read("test_authorized_backend_read.txt")
        assert result.error is None
        assert result.file_data is not None
        assert result.file_data["content"] == "hello world\n"
    finally:
        test_file.unlink(missing_ok=True)


def test_backend_write_unauthorized_returns_error(tmp_path: Path) -> None:
    """8. 未授权路径 write 返回 WriteResult(error=...)，不抛异常。"""
    current_thread_id.set("t1")
    backend = AuthorizedLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
    result = backend.write("out.txt", "content")
    assert result.error is not None
    assert result.path is None


# ---- contextvar 传递 ----


def test_check_auth_passes_thread_id_via_contextvar(tmp_path: Path) -> None:
    """9. 设置 contextvar thread_id 后 _check_auth 正确传递给 sandbox。

    通过 _authorized_dirs 中预置 thread_id="ctx-tid" 的授权，验证 _check_auth
    能从 contextvar 取到 "ctx-tid" 并通过 sandbox 校验。
    """
    current_thread_id.set("ctx-tid")
    resolved = Path(tmp_path).resolve()
    # 为 ctx-tid 授权 tmp_path 可写
    get_sandbox()._authorized_dirs["ctx-tid"] = {(resolved, True)}

    backend = AuthorizedLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
    # _check_auth 应从 contextvar 取 "ctx-tid"，sandbox 校验通过（不抛）
    backend._check_auth(str(tmp_path / "any.txt"), write=True)
