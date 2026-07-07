"""Bug fix 测试：fs 工具接受 workspace_path 作为相对路径解析基准。

Bug 描述：当用户授权 workspace 不在 PROJECT_ROOT 下时，LLM 用相对路径访问
工作区内的文件会被误拒（因 SessionSandbox._normalize 默认基于 PROJECT_ROOT 解析）。

修复：SessionSandbox.check_read/check_write/is_path_authorized 接受 base 参数，
fs 工具（read_file/write_file/edit_file/list_dir/glob/grep）将 workspace_path
作为 base 传入。

覆盖：
1. check_read 带 base：相对路径在工作区内可读
2. check_write 带 base：相对路径在工作区内可写
3. is_path_authorized 带 base：相对路径识别正确
4. read_file 端到端：相对路径工作区内可读
5. write_file 端到端：相对路径工作区内可写
6. base 不传时退化到 PROJECT_ROOT（向后兼容）
7. base 与授权目录不一致时仍拒绝
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import PROJECT_ROOT, UPLOADS_DIR, WORKSPACE_DIR
from app.tools.filesystem import read_file, write_file
from app.utils.security import (
    PathNotAuthorized,
    SessionSandbox,
    get_sandbox,
)


@pytest.fixture
def sandbox() -> SessionSandbox:
    """每个测试用例使用独立实例，避免共享状态。"""
    return SessionSandbox()


# 1. check_read 带 base：相对路径在工作区内可读
def test_check_read_relative_path_in_workspace(sandbox: SessionSandbox) -> None:
    """授权 d:/proj 后，base=d:/proj 解析 'src/foo.py' 应通过。"""
    sandbox.authorize("t1", "d:/proj", writable=True)
    # 不带 base：相对路径解到 PROJECT_ROOT，被误拒
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "src/foo.py")
    # 带 base=d:/proj：相对路径正确解析，通过
    sandbox.check_read("t1", "src/foo.py", base="d:/proj")


# 2. check_write 带 base：相对路径在工作区内可写
def test_check_write_relative_path_in_workspace(sandbox: SessionSandbox) -> None:
    """授权 d:/proj 可写后，base=d:/proj 解析 'src/foo.py' 写应通过。"""
    sandbox.authorize("t1", "d:/proj", writable=True)
    # 不带 base：失败
    with pytest.raises(PathNotAuthorized):
        sandbox.check_write("t1", "src/foo.py")
    # 带 base：成功
    sandbox.check_write("t1", "src/foo.py", base="d:/proj")


# 3. is_path_authorized 带 base
def test_is_path_authorized_with_base(sandbox: SessionSandbox) -> None:
    sandbox.authorize("t1", "d:/proj", writable=True)
    assert sandbox.is_path_authorized("t1", "src/foo.py") is False
    assert sandbox.is_path_authorized("t1", "src/foo.py", base="d:/proj") is True


# 4. read_file 端到端：相对路径工作区内可读
async def test_read_file_relative_path_in_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整流程：workspace + 相对路径读文件。"""
    workspace = tmp_path / "proj"
    workspace.mkdir()
    target = workspace / "src" / "foo.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('hello')", encoding="utf-8")

    sandbox = SessionSandbox()
    sandbox.authorize("t1", str(workspace), writable=True)
    # 替换全局 sandbox
    monkeypatch.setattr("app.utils.security._sandbox", sandbox, raising=True)

    rel_path = "src/foo.py"
    content = await read_file("t1", rel_path, base=str(workspace))
    assert content == "print('hello')"


# 5. write_file 端到端：相对路径工作区内可写
async def test_write_file_relative_path_in_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整流程：workspace + 相对路径写文件。"""
    workspace = tmp_path / "proj"
    workspace.mkdir()

    sandbox = SessionSandbox()
    sandbox.authorize("t1", str(workspace), writable=True)
    monkeypatch.setattr("app.utils.security._sandbox", sandbox, raising=True)

    rel_path = "src/new.py"
    result = await write_file("t1", rel_path, "x = 1\n", base=str(workspace))
    assert "已写入" in result
    assert (workspace / "src" / "new.py").exists()
    assert (workspace / "src" / "new.py").read_text(encoding="utf-8") == "x = 1\n"


# 6. base 不传时退化到 PROJECT_ROOT（向后兼容）
def test_check_read_without_base_uses_project_root(sandbox: SessionSandbox) -> None:
    """不带 base 时，相对路径基于 PROJECT_ROOT 解析（保持旧行为）。"""
    # 在 PROJECT_ROOT 下写一个临时文件
    target = PROJECT_ROOT / "_test_relative_no_base.txt"
    target.write_text("test", encoding="utf-8")
    try:
        # 不带 base：相对路径解析为 PROJECT_ROOT/_test_relative_no_base.txt
        # PROJECT_ROOT/data/workspace 在白名单内，但 PROJECT_ROOT/ 不在白名单
        # 所以默认情况下会被拒（与旧行为一致）
        with pytest.raises(PathNotAuthorized):
            sandbox.check_read("t1", "_test_relative_no_base.txt")
    finally:
        target.unlink()


# 7. base 与授权目录不一致时仍拒绝
def test_check_read_base_mismatch(sandbox: SessionSandbox) -> None:
    """base=d:/proj_b 但授权 d:/proj_a，相对路径应仍被拒。"""
    sandbox.authorize("t1", "d:/proj_a", writable=True)
    # base 指向未授权的 d:/proj_b
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "src/foo.py", base="d:/proj_b")


# 8. base 路径下文件在授权范围之外被拒
def test_check_read_outside_workspace_even_with_base(sandbox: SessionSandbox) -> None:
    """base=d:/proj/sub 已授权 d:/proj，但 ../escape.txt 仍被拒。"""
    sandbox.authorize("t1", "d:/proj", writable=True)
    # base=d:/proj/sub，../escape.txt → d:/proj/escape.txt（仍在授权内）
    sandbox.check_read("t1", "../escape.txt", base="d:/proj/sub")
    # base=d:/proj/sub，../../outside.txt → d:/outside.txt（超出授权）
    with pytest.raises(PathNotAuthorized):
        sandbox.check_read("t1", "../../outside.txt", base="d:/proj/sub")