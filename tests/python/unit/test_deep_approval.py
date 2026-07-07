"""DeepAgent 审批辅助函数单元测试。

覆盖：
1. _extract_paths_from_tool_call 对各类工具的路径提取
2. cli_execute 未指定 cwd 时回退到 workspace_path，避免已授权工作区仍被误判为危险
"""

from __future__ import annotations

import pytest

from app.deep.approval import _extract_paths_from_tool_call


def test_extract_paths_read_file() -> None:
    tc = {"name": "read_file", "args": {"path": "/tmp/a.txt"}}
    assert _extract_paths_from_tool_call(tc) == ["/tmp/a.txt"]


def test_extract_paths_glob_files() -> None:
    tc = {"name": "glob_files", "args": {"pattern": "d:/proj/**/*.py"}}
    assert _extract_paths_from_tool_call(tc) == ["d:/proj"]


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
