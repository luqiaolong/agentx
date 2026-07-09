"""DeepAgent 审批辅助函数单元测试。

覆盖：
1. _extract_paths_from_tool_call 对各类工具的路径提取
2. cli_execute 未指定 cwd 时回退到 workspace_path，避免已授权工作区仍被误判为危险
"""

from __future__ import annotations

from app.security.approval.flow import _extract_paths_from_tool_call


def test_extract_paths_read_file() -> None:
    tc = {"name": "read_file", "args": {"file_path": "/tmp/a.txt"}}
    assert _extract_paths_from_tool_call(tc) == ["/tmp/a.txt"]


def test_extract_paths_write_file() -> None:
    """内置 write_file 用 file_path 参数。"""
    tc = {"name": "write_file", "args": {"file_path": "/tmp/b.txt", "content": "x"}}
    assert _extract_paths_from_tool_call(tc) == ["/tmp/b.txt"]


def test_extract_paths_edit_file() -> None:
    """内置 edit_file 用 file_path 参数。"""
    tc = {"name": "edit_file", "args": {"file_path": "/tmp/c.txt", "old_string": "a", "new_string": "b"}}
    assert _extract_paths_from_tool_call(tc) == ["/tmp/c.txt"]


def test_extract_paths_ls() -> None:
    """内置 ls 用 path 参数。"""
    tc = {"name": "ls", "args": {"path": "/tmp"}}
    assert _extract_paths_from_tool_call(tc) == ["/tmp"]


def test_extract_paths_delete_file() -> None:
    """delete_file 用 path 参数。"""
    tc = {"name": "delete_file", "args": {"path": "/tmp/d.txt"}}
    assert _extract_paths_from_tool_call(tc) == ["/tmp/d.txt"]


def test_extract_paths_glob() -> None:
    """内置 glob 优先用 path；未指定 path 时从 pattern 提取 base。"""
    tc = {"name": "glob", "args": {"pattern": "d:/proj/**/*.py"}}
    assert _extract_paths_from_tool_call(tc) == ["d:/proj"]


def test_extract_paths_glob_with_path() -> None:
    """glob 显式指定 path 时直接返回 path。"""
    tc = {"name": "glob", "args": {"pattern": "**/*.py", "path": "/custom/dir"}}
    assert _extract_paths_from_tool_call(tc) == ["/custom/dir"]


def test_extract_paths_cli_execute_with_cwd() -> None:
    tc = {"name": "cli_execute", "args": {"command": "git", "cwd": "d:/proj"}}
    assert _extract_paths_from_tool_call(tc) == ["d:/proj"]


def test_extract_paths_cli_execute_falls_back_to_workspace() -> None:
    """cli_execute 省略 cwd 但已选工作区时，应回退到 workspace_path 做授权检查。"""
    tc = {"name": "cli_execute", "args": {"command": "git"}}
    assert _extract_paths_from_tool_call(tc, workspace_path="d:/proj") == ["d:/proj"]


def test_extract_paths_cli_execute_no_workspace() -> None:
    tc = {"name": "cli_execute", "args": {"command": "git"}}
    assert _extract_paths_from_tool_call(tc) == []


def test_extract_paths_unknown_tool() -> None:
    tc = {"name": "magic", "args": {"path": "/tmp"}}
    assert _extract_paths_from_tool_call(tc) == []
