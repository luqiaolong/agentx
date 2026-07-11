"""delete_file 工具单元测试（Phase C.1）。

覆盖：
1. 删除文件成功
2. recursive=True 递归删除目录
3. 根目录保护（_GUARDED_ROOTS）拒绝删除沙箱根
4. 未授权路径拒绝（PathNotAuthorized → 返回错误字符串）
5. recursive=False 遇目录返回错误

delete_file 由 ``_make_deep_tools`` 闭包构建，捕获 ``thread_id`` 与
``workspace_path``。测试通过 ``_make_deep_tools`` 取得工具实例后用
``ainvoke`` 调用。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.deepagent.tool_assembly import _make_deep_tools
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


def _find_delete_file(thread_id: str, workspace_path: str):
    """从 _make_deep_tools 返回列表中取出 delete_file 工具实例。"""
    tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
    for t in tools:
        if t.name == "delete_file":
            return t
    raise AssertionError("delete_file tool not found in _make_deep_tools output")


# ============================================================
# 1. 删除文件成功
# ============================================================


@pytest.mark.asyncio
async def test_delete_file_success(tmp_path: Path) -> None:
    """授权路径下删除文件，返回成功消息且文件被物理删除。"""
    thread_id = "t-del-file"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # 授权 workspace 可写
    resolved = workspace.resolve()
    get_sandbox()._authorized_dirs[thread_id] = {(resolved, True)}

    target = workspace / "note.txt"
    target.write_text("hello", encoding="utf-8")
    assert target.exists()

    delete_file = _find_delete_file(thread_id, str(workspace))
    result = await delete_file.ainvoke({"path": "note.txt", "recursive": False})

    assert isinstance(result, str)
    assert "已删除" in result
    assert not target.exists()


# ============================================================
# 2. recursive=True 递归删除目录
# ============================================================


@pytest.mark.asyncio
async def test_delete_file_recursive_dir(tmp_path: Path) -> None:
    """recursive=True 时递归删除非空目录。"""
    thread_id = "t-del-dir"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    resolved = workspace.resolve()
    get_sandbox()._authorized_dirs[thread_id] = {(resolved, True)}

    sub = workspace / "subdir"
    sub.mkdir()
    (sub / "a.txt").write_text("a", encoding="utf-8")
    (sub / "nested").mkdir()
    (sub / "nested" / "b.txt").write_text("b", encoding="utf-8")

    delete_file = _find_delete_file(thread_id, str(workspace))
    result = await delete_file.ainvoke({"path": "subdir", "recursive": True})

    assert isinstance(result, str)
    assert "已递归删除目录" in result
    assert not sub.exists()


# ============================================================
# 3. 根目录保护（_GUARDED_ROOTS）
# ============================================================


@pytest.mark.asyncio
async def test_delete_file_rejects_guarded_root(tmp_path: Path) -> None:
    """删除 data/workspace 根目录本身被拒绝（_GUARDED_ROOTS 保护）。"""
    thread_id = "t-guard-root"
    # 构造一个路径以 /data/workspace 结尾的 workspace，命中 _GUARDED_ROOTS
    workspace = tmp_path / "data" / "workspace"
    workspace.mkdir(parents=True)
    resolved = workspace.resolve()
    # 先授权（让 check_write 通过），触发后续根目录保护
    get_sandbox()._authorized_dirs[thread_id] = {(resolved, True)}

    delete_file = _find_delete_file(thread_id, str(workspace))
    # path="." 解析为 workspace 本身
    result = await delete_file.ainvoke({"path": ".", "recursive": True})

    assert isinstance(result, str)
    assert "禁止删除" in result
    assert "根目录" in result
    # 目录应仍然存在
    assert workspace.exists()


# ============================================================
# 4. 未授权路径拒绝
# ============================================================


@pytest.mark.asyncio
async def test_delete_file_unauthorized_rejected(tmp_path: Path) -> None:
    """未授权路径删除返回未授权错误字符串（不抛异常）。"""
    thread_id = "t-unauth"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # 不为 thread_id 授权任何路径
    target = workspace / "secret.txt"
    target.write_text("x", encoding="utf-8")

    delete_file = _find_delete_file(thread_id, str(workspace))
    result = await delete_file.ainvoke({"path": "secret.txt", "recursive": False})

    assert isinstance(result, str)
    assert "删除失败" in result
    assert "未授权" in result
    # 文件应仍然存在
    assert target.exists()


# ============================================================
# 5. recursive=False 遇目录返回错误
# ============================================================


@pytest.mark.asyncio
async def test_delete_file_dir_without_recursive_errors(tmp_path: Path) -> None:
    """recursive=False 时遇目录返回错误，不删除。"""
    thread_id = "t-no-recursive"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    resolved = workspace.resolve()
    get_sandbox()._authorized_dirs[thread_id] = {(resolved, True)}

    sub = workspace / "dir"
    sub.mkdir()
    (sub / "inner.txt").write_text("x", encoding="utf-8")

    delete_file = _find_delete_file(thread_id, str(workspace))
    result = await delete_file.ainvoke({"path": "dir", "recursive": False})

    assert isinstance(result, str)
    assert "删除失败" in result
    assert "recursive=True" in result
    # 目录及内容应仍然存在
    assert sub.exists()
    assert (sub / "inner.txt").exists()
