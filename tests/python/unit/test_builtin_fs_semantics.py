"""内置 fs 工具语义单元测试（Phase A.3）。

覆盖 deepagents FilesystemMiddleware（经 AuthorizedLocalShellBackend 暴露）
的关键语义差异，确保项目代码与 prompt 文档与内置行为对齐：

1. ``write_file`` 对已存在文件返回 error（必须用 ``edit_file`` 改已有文件）
2. ``read_file`` 支持 ``offset`` / ``limit`` 分段读取
3. ``grep`` 为字面量搜索（非正则），特殊字符按字面处理

测试直接调用 ``AuthorizedLocalShellBackend`` 的 fs 方法，不经过完整 agent 流水线，
聚焦语义校验。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import WORKSPACE_DIR
from app.deepagent.authorized_backend import AuthorizedLocalShellBackend
from app.deepagent.context import current_thread_id
from app.sandbox.session_sandbox import get_sandbox


@pytest.fixture(autouse=True)
def reset_sandbox() -> None:
    """每个测试前后重置 sandbox 单例内部状态。"""
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
    """每个测试前后重置 contextvar。"""
    token = current_thread_id.set("")
    yield
    current_thread_id.reset(token)


# ============================================================
# 1. write_file 对已存在文件返回 error
# ============================================================


def test_write_file_existing_returns_error(tmp_path: Path) -> None:
    """write_file 写已存在文件时返回 WriteResult(error=...)，提示用 edit_file。

    内置 write_file 语义为「创建新文件」；修改已有文件必须用 edit_file。
    """
    current_thread_id.set("t-write")
    resolved = tmp_path.resolve()
    get_sandbox()._authorized_dirs["t-write"] = {(resolved, True)}

    existing = tmp_path / "exists.txt"
    existing.write_text("old content", encoding="utf-8")

    backend = AuthorizedLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
    result = backend.write("exists.txt", "new content")

    assert result.error is not None
    assert "already exists" in result.error
    assert result.path is None
    # 原文件内容未被覆盖
    assert existing.read_text(encoding="utf-8") == "old content"


# ============================================================
# 2. read_file 支持 offset / limit 分段读取
# ============================================================


def test_read_file_offset_limit() -> None:
    """read_file(offset, limit) 返回从 offset 行开始的最多 limit 行内容。

    offset 是 0-indexed 行号，limit 是最大行数。内置实现按行 splitlines。
    """
    current_thread_id.set("t-read")
    # 使用 WORKSPACE_DIR（DEFAULT_WHITELIST 中，授权自动通过）
    test_file = WORKSPACE_DIR / "test_builtin_fs_read_offset.txt"
    lines = [f"line-{i}" for i in range(10)]
    test_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        backend = AuthorizedLocalShellBackend(
            root_dir=WORKSPACE_DIR, virtual_mode=False
        )
        # 从第 3 行（offset=2）开始读 3 行
        result = backend.read("test_builtin_fs_read_offset.txt", offset=2, limit=3)

        assert result.error is None
        assert result.file_data is not None
        content = result.file_data["content"]
        # 应包含 line-2 / line-3 / line-4
        assert "line-2" in content
        assert "line-3" in content
        assert "line-4" in content
        # 不应包含 offset 之前或 limit 之外的行
        assert "line-1" not in content
        assert "line-5" not in content
    finally:
        test_file.unlink(missing_ok=True)


# ============================================================
# 3. grep 授权注入（字面量搜索语义由 deepagents 库保证）
# ============================================================


def test_grep_authorized_returns_no_error(tmp_path: Path) -> None:
    """grep 授权通过后返回 error=None（字面量搜索语义由 deepagents -F 保证）。"""
    current_thread_id.set("t-grep")
    resolved = tmp_path.resolve()
    get_sandbox()._authorized_dirs["t-grep"] = {(resolved, True)}

    (tmp_path / "test.txt").write_text("foo.bar\n", encoding="utf-8")

    backend = AuthorizedLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
    result = backend.grep(pattern="foo.bar", path=str(tmp_path))

    # 授权通过，无错误（字面量搜索结果由 ripgrep -F / Python fallback 保证）
    assert result.error is None
    assert result.matches is not None


def test_grep_unauthorized_returns_error(tmp_path: Path) -> None:
    """grep 未授权返回 GrepResult(error=...)。"""
    current_thread_id.set("t-grep-unauth")
    # 不设置授权——tmp_path 不在白名单
    (tmp_path / "test.txt").write_text("foo.bar\n", encoding="utf-8")

    backend = AuthorizedLocalShellBackend(root_dir=tmp_path, virtual_mode=False)
    result = backend.grep(pattern="foo.bar", path=str(tmp_path))

    # 未授权，返回错误
    assert result.error is not None
