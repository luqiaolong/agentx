"""request_permission 工具单元测试。

验证 ``make_deep_tools`` 返回的工具列表包含 ``request_permission`` 工具，
且工具名称、docstring 触发条件说明、参数签名、执行体返回值符合预期。
"""

from __future__ import annotations

import pytest

from app.deepagent.tool_assembly import make_deep_tools


def _find_tool(tools: list, name: str):
    """从工具列表中按名称查找工具。"""
    for t in tools:
        if t.name == name:
            return t
    return None


class TestRequestPermissionTool:
    """request_permission 工具注册与元信息验证。"""

    def test_make_deep_tools_contains_request_permission(self) -> None:
        """make_deep_tools 返回列表包含 request_permission 工具。"""
        tools = make_deep_tools("t1", workspace_path=None)
        tool = _find_tool(tools, "request_permission")
        assert tool is not None, "make_deep_tools 应包含 request_permission 工具"

    def test_tool_name_is_request_permission(self) -> None:
        """工具 name 属性为 'request_permission'。"""
        tools = make_deep_tools("t1", workspace_path=None)
        tool = _find_tool(tools, "request_permission")
        assert tool is not None
        assert tool.name == "request_permission"

    def test_tool_docstring_contains_trigger_conditions(self) -> None:
        """工具 docstring/description 含触发条件说明。

        覆盖关键词：Permission denied / EACCES / PathNotAuthorized / SANDBOX_ESCALATION。
        """
        tools = make_deep_tools("t1", workspace_path=None)
        tool = _find_tool(tools, "request_permission")
        assert tool is not None
        # @tool 装饰器将函数 docstring 写入 description
        description = tool.description or ""
        assert "Permission denied" in description
        assert "EACCES" in description
        assert "PathNotAuthorized" in description
        assert "SANDBOX_ESCALATION" in description

    def test_tool_args_contain_path_writable_reason(self) -> None:
        """工具参数包含 path / writable / reason。"""
        tools = make_deep_tools("t1", workspace_path=None)
        tool = _find_tool(tools, "request_permission")
        assert tool is not None
        # langchain @tool 通过 args_schema 或 args 暴露参数
        args = tool.args
        assert "path" in args, "工具应包含 path 参数"
        assert "writable" in args, "工具应包含 writable 参数"
        assert "reason" in args, "工具应包含 reason 参数"
        # path 为必填参数（无默认值）
        assert args["path"].get("type") == "string"

    @pytest.mark.asyncio
    async def test_tool_execution_returns_confirmation(self) -> None:
        """工具执行体返回确认消息。"""
        tools = make_deep_tools("t1", workspace_path=None)
        tool = _find_tool(tools, "request_permission")
        assert tool is not None
        result = await tool.ainvoke(
            {"path": "/tmp/test_dir", "writable": True, "reason": "测试写入"}
        )
        # 返回值应包含路径和授权确认
        assert "路径已授权" in str(result)
        assert "/tmp/test_dir" in str(result)
        assert "writable=True" in str(result)
