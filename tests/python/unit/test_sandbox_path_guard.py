"""sandbox/path_guard.py 单元测试：路径归一化 + 关键目录保护 + Linux bug 修复。

覆盖：
- ``is_under`` 边界（相等 / 后代 / 兄弟 / 父目录）
- ``is_critical`` 关键目录判定（Windows 真实平台 + Linux 模拟）
- Linux ``Path("/")`` bug 修复：根目录仅拒绝本身，不拒绝后代
- ``normalize_path`` 行为
- ``DEFAULT_WHITELIST`` 内容
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.sandbox.path_guard import (
    CRITICAL_DIRS,
    DEFAULT_WHITELIST,
    PathNotAuthorized,
    is_critical,
    is_under,
    normalize_path,
)


# ============================================================
# is_under 边界测试
# ============================================================


def test_is_under_exact_match() -> None:
    """child == base 时返回 True（授权目录本身可访问）。"""
    assert is_under(Path("d:/docs"), Path("d:/docs")) is True


def test_is_under_descendant() -> None:
    """child 是 base 的后代时返回 True。"""
    assert is_under(Path("d:/docs/a/b/c.txt"), Path("d:/docs")) is True


def test_is_under_sibling_rejected() -> None:
    """兄弟目录（d:/docs-other）不匹配 d:/docs（避免前缀字符串误匹配）。"""
    assert is_under(Path("d:/docs-other/x"), Path("d:/docs")) is False


def test_is_under_parent_rejected() -> None:
    """base 的父目录不算在 base 之下。"""
    assert is_under(Path("d:/"), Path("d:/docs")) is False


def test_is_under_case_insensitive_on_windows() -> None:
    """Windows 大小写不敏感：D:/docs 与 d:/DOCS 视为同一路径。"""
    if sys.platform != "win32":
        pytest.skip("Windows-specific")
    assert is_under(Path("d:/DOCS/x.txt"), Path("D:/docs")) is True


# ============================================================
# Linux Path("/") bug 修复验证
# ============================================================


def test_linux_root_only_rejects_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux ``Path("/")`` bug 修复：根目录仅拒绝本身，不拒绝后代。

    原始 bug：``_critical_dirs()`` 在 Linux 把 ``Path("/")`` 纳入候选，
    ``_is_under(resolved, "/")`` 对任何绝对路径都成功 → Linux 下沙箱不可用。

    修复：对根目录（``parent == self``）仅拒绝 ``path == root``，不拒绝后代。
    """
    from app.sandbox import path_guard

    # 模拟 Linux 关键目录：仅包含根目录
    root = Path("/").resolve() if sys.platform != "win32" else Path("C:/")
    monkeypatch.setattr(path_guard, "CRITICAL_DIRS", [root])

    # 根目录本身是关键目录
    assert is_critical(root) is True
    # 根目录的后代 NOT 关键（仅因 root 在列表中）
    descendant = root / "home" / "user" / "project"
    assert is_critical(descendant) is False


def test_linux_root_descendant_not_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux 下 /home/user/project 不应因 ``/`` 在 CRITICAL_DIRS 中而被误拒。"""
    from app.sandbox import path_guard

    root = Path("/").resolve() if sys.platform != "win32" else Path("C:/")
    # 模拟 Linux 完整关键目录列表
    monkeypatch.setattr(
        path_guard,
        "CRITICAL_DIRS",
        [root, Path("/etc").resolve(), Path("/usr").resolve()]
        if sys.platform != "win32"
        else [root, Path("C:/etc"), Path("C:/usr")],
    )

    # /home/user/project 不在 /etc 或 /usr 下，且 / 仅拒绝本身
    project = root / "home" / "user" / "project"
    assert is_critical(project) is False


def test_critical_dirs_is_list() -> None:
    """CRITICAL_DIRS 是 list[Path]（平台相关）。"""
    assert isinstance(CRITICAL_DIRS, list)
    assert len(CRITICAL_DIRS) > 0
    for d in CRITICAL_DIRS:
        assert isinstance(d, Path)


# ============================================================
# is_critical 关键目录判定（Windows 真实平台）
# ============================================================


def test_is_critical_windows_system_dir() -> None:
    """Windows 系统目录 C:/Windows 是关键目录。"""
    if sys.platform != "win32":
        pytest.skip("Windows-specific")
    assert is_critical(Path("C:/Windows")) is True
    assert is_critical(Path("C:/Windows/System32")) is True  # 后代


def test_is_critical_windows_program_files() -> None:
    """C:/Program Files 是关键目录。"""
    if sys.platform != "win32":
        pytest.skip("Windows-specific")
    assert is_critical(Path("C:/Program Files")) is True


def test_is_critical_windows_drive_root_is_critical_as_ancestor() -> None:
    """Windows C:/ 是系统目录（C:/Windows）的祖先 → 关键目录（双向 is_under）。

    C:/ 本身不在 CRITICAL_DIRS 中，但作为 C:/Windows 的祖先仍被拒绝，
    防止授权整个盘根导致 C:/Windows 可访问。
    """
    if sys.platform != "win32":
        pytest.skip("Windows-specific")
    # C:/ 是 C:/Windows 的祖先 → 双向 is_under → 关键目录
    assert is_critical(Path("C:/")) is True


def test_is_critical_rejects_ancestor_of_system_dir() -> None:
    """系统目录的祖先（如 C:/）也拒绝——双向 _is_under。

    但 Windows 下 C:/ 不在 CRITICAL_DIRS，所以这条主要验证
    如果有关键目录 C:/Windows，则 C:/ 作为其祖先也会被拒。
    """
    if sys.platform != "win32":
        pytest.skip("Windows-specific")
    from app.sandbox import path_guard

    # C:/ 是 C:/Windows 的祖先 → 应被拒绝
    # 但默认 CRITICAL_DIRS 不含 C:/，需模拟
    monkeypatch_target = path_guard
    original = monkeypatch_target.CRITICAL_DIRS
    monkeypatch_target.CRITICAL_DIRS = [Path("C:/Windows").resolve()]
    try:
        assert is_critical(Path("C:/")) is True  # 祖先拒绝
        assert is_critical(Path("C:/Windows/System32")) is True  # 后代拒绝
    finally:
        monkeypatch_target.CRITICAL_DIRS = original


def test_is_critical_home_dir_itself() -> None:
    """用户主目录本身是关键目录（拒绝授权整个 home）。"""
    home = Path.home().resolve()
    assert is_critical(home) is True


def test_is_critical_home_ancestor_rejected() -> None:
    """home 的祖先（如 C:/Users）也拒绝——防止授权整个用户目录。"""
    if sys.platform != "win32":
        pytest.skip("Windows-specific")
    home = Path.home().resolve()
    # home 的父目录（如 C:/Users）应被拒绝
    parent = home.parent
    assert is_critical(parent) is True


def test_is_critical_home_descendant_allowed() -> None:
    """home 的子目录不拒绝（用户可授权 C:/Users/me/Projects）。"""
    home = Path.home().resolve()
    project = home / "Projects" / "myapp"
    assert is_critical(project) is False


def test_is_critical_normal_path_not_critical() -> None:
    """普通路径（d:/docs）不是关键目录。"""
    assert is_critical(Path("d:/docs")) is False
    assert is_critical(Path("d:/docs/file.txt")) is False


# ============================================================
# normalize_path 行为
# ============================================================


def test_normalize_path_absolute() -> None:
    """绝对路径直接 resolve。"""
    p = normalize_path("d:/docs/file.txt")
    assert p.is_absolute()


def test_normalize_path_relative_uses_project_root() -> None:
    """相对路径基于 PROJECT_ROOT 解析（非 CWD）。"""
    p = normalize_path("data/workspace/foo.txt")
    assert p.is_absolute()
    assert "data" in p.parts
    assert "workspace" in p.parts


def test_normalize_path_relative_with_base() -> None:
    """相对路径 + base 参数：基于 base 解析。"""
    p = normalize_path("foo.txt", base="d:/mybase")
    assert p == Path("d:/mybase/foo.txt").resolve()


def test_normalize_path_traversal() -> None:
    """.. 被正确解析。"""
    p = normalize_path("d:/docs/../secrets/x.txt")
    # 规范化后不含 docs
    assert "docs" not in p.parts
    assert "secrets" in p.parts


def test_normalize_path_windows_returns_absolute() -> None:
    """Windows 下 normalize_path 返回绝对路径（drive 大小写由 resolve 统一）。"""
    if sys.platform != "win32":
        pytest.skip("Windows-specific")
    p = normalize_path("D:/Docs")
    assert p.is_absolute()
    # resolve() 在 Windows 上统一 drive 为大写，保证不同大小写输入得到相同路径
    p2 = normalize_path("d:/docs")
    assert str(p).lower() == str(p2).lower()


# ============================================================
# DEFAULT_WHITELIST
# ============================================================


def test_default_whitelist_contains_workspace_and_uploads() -> None:
    """DEFAULT_WHITELIST 包含 WORKSPACE_DIR 和 UPLOADS_DIR。"""
    from app.config import UPLOADS_DIR, WORKSPACE_DIR

    whitelist_strs = [str(p) for p in DEFAULT_WHITELIST]
    assert str(WORKSPACE_DIR.resolve()) in whitelist_strs
    assert str(UPLOADS_DIR.resolve()) in whitelist_strs


# ============================================================
# PathNotAuthorized 异常
# ============================================================


def test_path_not_authorized_is_exception() -> None:
    """PathNotAuthorized 是 Exception 子类。"""
    assert issubclass(PathNotAuthorized, Exception)
    with pytest.raises(PathNotAuthorized):
        raise PathNotAuthorized("test")
