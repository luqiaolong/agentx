"""危险工具分类与运行时危险集合计算单元测试。

覆盖 request_permission 工具纳入 DANGEROUS_TOOLS 集合后的行为：
1. DANGEROUS_TOOLS 包含 request_permission
2. compute_runtime_dangerous 结果含 request_permission（当 enabled_tool_names 含它时）
3. build_interrupt_config 从 DANGEROUS_TOOLS 生成 interrupt_on 字典（T1 验证）
"""

from __future__ import annotations

import pytest

from app.security.dangerous_tools import DANGEROUS_TOOLS, compute_runtime_dangerous
from app.deepagent.factory import build_interrupt_config


class TestDangerousToolsSet:
    """DANGEROUS_TOOLS 静态集合验证。"""

    def test_dangerous_tools_contains_request_permission(self) -> None:
        """DANGEROUS_TOOLS 集合包含 request_permission。"""
        assert "request_permission" in DANGEROUS_TOOLS

    def test_dangerous_tools_still_contains_core_tools(self) -> None:
        """新增 request_permission 后，原有危险工具仍在集合中。"""
        assert "edit_file" in DANGEROUS_TOOLS
        assert "write_file" in DANGEROUS_TOOLS
        assert "delete_file" in DANGEROUS_TOOLS


class TestComputeRuntimeDangerous:
    """compute_runtime_dangerous 运行时计算验证。"""

    def test_runtime_dangerous_includes_request_permission_when_enabled(self) -> None:
        """enabled_tool_names 含 request_permission 时，结果含 request_permission。"""
        result = compute_runtime_dangerous(
            enabled_tool_names={"request_permission", "edit_file"},
            mcp_untrusted_names=set(),
        )
        assert "request_permission" in result
        assert "edit_file" in result

    def test_runtime_dangerous_excludes_request_permission_when_disabled(self) -> None:
        """enabled_tool_names 不含 request_permission 时，结果不含 request_permission。"""
        result = compute_runtime_dangerous(
            enabled_tool_names={"edit_file", "write_file"},
            mcp_untrusted_names=set(),
        )
        assert "request_permission" not in result

    def test_runtime_dangerous_includes_mcp_untrusted(self) -> None:
        """MCP 不可信工具名纳入运行时危险集合。"""
        result = compute_runtime_dangerous(
            enabled_tool_names={"request_permission"},
            mcp_untrusted_names={"mcp_dangerous_tool"},
        )
        assert "request_permission" in result
        assert "mcp_dangerous_tool" in result


class TestBuildInterruptConfig:
    """``build_interrupt_config`` 从 ``DANGEROUS_TOOLS`` 生成 ``interrupt_on`` 配置。

    T1 验证：``build_interrupt_config`` 输出含 ``"request_permission": True``。
    """

    def test_build_interrupt_config_includes_request_permission(self) -> None:
        """``DANGEROUS_TOOLS`` 含 ``request_permission`` 时，输出字典含 ``request_permission: True``。"""
        # 前置断言：当前 DANGEROUS_TOOLS 确实包含 request_permission
        assert "request_permission" in DANGEROUS_TOOLS

        config = build_interrupt_config()

        assert "request_permission" in config
        assert config["request_permission"] is True

    def test_build_interrupt_config_excludes_request_permission_when_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``DANGEROUS_TOOLS`` 为空时，输出字典不含 ``request_permission``。

        ``build_interrupt_config`` 读取 factory 模块内导入的 ``DANGEROUS_TOOLS``，
        用 monkeypatch 替换为空 frozenset 模拟"未启用任何危险工具"场景。
        """
        import app.deepagent.factory as factory_module

        monkeypatch.setattr(factory_module, "DANGEROUS_TOOLS", frozenset())

        config = build_interrupt_config()

        assert "request_permission" not in config
        assert config == {}
