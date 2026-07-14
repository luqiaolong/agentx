"""危险工具分类与运行时危险集合计算。

从 ``app.deepagent.tool_assembly`` 提取 ``DANGEROUS_TOOLS``，从 ``app.config.subagents``
提取 ``FORBIDDEN_SUBAGENT_TOOLS``，统一迁移至 ``app.security`` 包。

变更：
- ``DANGEROUS_TOOLS`` / ``FORBIDDEN_SUBAGENT_TOOLS`` 改为 ``frozenset``。
- CLI 工具名从 ``cli_execute`` 改为 ``execute``（由 ``SafeLocalShellBackend`` 提供）。
- Phase B.3：移除所有 ``git_*`` 条目。Git 写操作（commit/push/checkout 等）不再通过
  独立工具暴露，而是由 ``SafeLocalShellBackend.execute`` 通过 ``is_git_write_command``
  拦截（返回 exit_code=126 提示走审批流）。

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
# （由 ``SafeLocalShellBackend`` 继承并提供，blocklist + 元字符过滤 + Git 写操作拦截）。
# 注意：execute 不再属于 DANGEROUS_TOOLS，其审批通过 directory_extension
# 机制处理（workspace 之外未授权时触发审批）。
# Git 写操作（commit/push/checkout 等）由 ``is_git_write_command`` 在
# ``SafeLocalShellBackend.execute`` 中拦截，不在此集合中。
CLI_TOOL_NAME = "execute"

# 触发人工审批中断的工具集合：写操作 + 文件删除。
# execute 已移除：shell 命令的审批改为基于工作目录是否授权（directory_extension）。
# Git 写操作已移除：由 ``SafeLocalShellBackend.execute`` 通过 ``is_git_write_command``
# 拦截（返回 exit_code=126），不再通过独立工具 + interrupt_on 审批。
# write_file / edit_file 由 deepagents 内置（AuthorizedLocalShellBackend 提供），
# delete_file 为项目自研工具（tool_assembly.make_deep_tools 闭包构建）。
DANGEROUS_TOOLS: frozenset[str] = frozenset(
    {
        "edit_file",
        "write_file",
        "delete_file",
    }
)

# 自定义子代理禁止绑定的危险工具（与 AGENTS.md §18 安全红线一致）。
# subagent 无 interrupt_on 审批流，暴露写/编辑/shell 操作会绕过审批。
# ``execute`` = SafeLocalShellBackend 内置工具（新名）；``cli_execute`` = 旧名，
# 保留以过滤仍引用旧名的陈旧配置。
# Git 写操作由 ``SafeLocalShellBackend.execute`` 拦截，无需在此禁止 git_* 工具
# （git_* 工具已删除，子代理无法引用）。
FORBIDDEN_SUBAGENT_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "delete_file",
        "execute",
        "cli_execute",
    }
)


def compute_runtime_dangerous(
    enabled_tool_names: set[str],
    mcp_untrusted_names: set[str],
) -> frozenset[str]:
    """计算运行时危险工具集合。

    公式：``(DANGEROUS_TOOLS ∩ enabled_tool_names) ∪ mcp_untrusted_names``

    - ``DANGEROUS_TOOLS`` 是静态危险工具集合（写/git 写）。
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
