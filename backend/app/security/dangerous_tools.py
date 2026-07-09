"""危险工具分类与运行时危险集合计算。

从 ``app.deep.tools`` 提取 ``DANGEROUS_TOOLS``，从 ``app.config.subagents``
提取 ``FORBIDDEN_SUBAGENT_TOOLS``，统一迁移至 ``app.security`` 包。

变更：
- ``DANGEROUS_TOOLS`` / ``FORBIDDEN_SUBAGENT_TOOLS`` 改为 ``frozenset``。
- CLI 工具名从 ``cli_execute`` 改为 ``execute``（由 ``SafeLocalShellBackend`` 提供）。

新增 ``compute_runtime_dangerous``：根据已启用工具名 + MCP 不可信工具名，
计算运行时实际触发 ``interrupt_on`` 审批的工具集合。
"""

from __future__ import annotations

__all__ = [
    "DANGEROUS_TOOLS",
    "FORBIDDEN_SUBAGENT_TOOLS",
    "compute_runtime_dangerous",
]

# CLI 工具名：deepagents ``LocalShellBackend`` 内置的 ``execute`` 工具
# （由 ``SafeLocalShellBackend`` 继承并提供，blocklist + 元字符过滤）。
CLI_TOOL_NAME = "execute"

# 触发人工审批中断的工具集合：写操作 + CLI + Git 写操作。
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
# subagent 无 interrupt_on 审批流，暴露写/编辑/git 写/shell 操作会绕过审批。
# ``execute`` = SafeLocalShellBackend 内置工具（新名）；``cli_execute`` = 旧名，
# 保留以过滤仍引用旧名的陈旧配置。
FORBIDDEN_SUBAGENT_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "execute",
        "cli_execute",
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
        运行时危险工具名 ``frozenset``，供 ``interrupt_on`` 配置使用。
    """
    return frozenset((DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names)
