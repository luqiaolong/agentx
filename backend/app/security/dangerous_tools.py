"""危险工具分类与运行时危险集合计算。

从 ``app.deep.tools`` 提取 ``DANGEROUS_TOOLS``，从 ``app.config.subagents``
提取 ``FORBIDDEN_SUBAGENT_TOOLS``，统一迁移至 ``app.security`` 包。

变更：
- ``DANGEROUS_TOOLS`` / ``FORBIDDEN_SUBAGENT_TOOLS`` 改为 ``frozenset``。
- **移除 ``shell_exec`` 死代码**：``shell_exec`` 已被 ``cli_execute`` 取代
  （见 ``app.tools.cli``），旧常量中保留 ``shell_exec`` 仅为历史兼容，
  实际工具集已不含此名称。新包不再保留死代码。

新增 ``compute_runtime_dangerous``：根据已启用工具名 + MCP 不可信工具名，
计算运行时实际触发 ``interrupt_before`` 审批的工具集合。
"""

from __future__ import annotations

__all__ = [
    "DANGEROUS_TOOLS",
    "FORBIDDEN_SUBAGENT_TOOLS",
    "compute_runtime_dangerous",
]

# CLI 工具名（与 ``app.tools.cli.CLI_TOOL_NAME`` 一致，硬编码避免循环导入）
CLI_TOOL_NAME = "cli_execute"

# 触发人工审批中断的工具集合：写操作 + CLI + Git 写操作。
# 移除 ``shell_exec``（已被 ``cli_execute`` 取代，旧常量保留仅为历史兼容，实际工具集无此名称）。
DANGEROUS_TOOLS: frozenset[str] = frozenset(
    {
        "edit_file",
        "write_file",
        CLI_TOOL_NAME,
        "git_clone",
        "git_pull",
        "git_checkout",
        "git_stage",
        "git_commit",
    }
)

# 自定义子代理禁止绑定的危险工具（与 AGENTS.md §18 安全红线一致）。
# subagent 无 interrupt_before 审批流，暴露写/编辑/git 写操作会绕过审批。
# 移除 ``shell_exec``（同上，死代码）。
# 注意：``cli_execute`` 允许子代理使用（黑名单 + 沙箱授权 + 元字符过滤已足够安全）。
FORBIDDEN_SUBAGENT_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "git_clone",
        "git_pull",
        "git_checkout",
        "git_stage",
        "git_commit",
    }
)


def compute_runtime_dangerous(
    enabled_tool_names: set[str],
    mcp_untrusted_names: set[str],
) -> frozenset[str]:
    """计算运行时危险工具集合。

    公式：``(DANGEROUS_TOOLS ∩ enabled_tool_names) ∪ mcp_untrusted_names``

    - ``DANGEROUS_TOOLS`` 是静态危险工具集合（写/shell/git 写）。
    - ``enabled_tool_names`` 是当前会话已启用的工具名集合（来自 settings.tools_enabled）。
      未启用的工具不会暴露给 LLM，故无需审批。
    - ``mcp_untrusted_names`` 是来自 ``trusted=False`` MCP server 的工具名集合，
      调用方应将其加入审批流。

    Args:
        enabled_tool_names: 已启用的内置工具名集合。
        mcp_untrusted_names: 不可信 MCP 工具名集合。

    Returns:
        运行时危险工具名 ``frozenset``，供 ``interrupt_before`` 配置使用。
    """
    return frozenset((DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names)
