"""MCP (Model Context Protocol) 集成模块。

提供对外部 MCP server 的连接管理、工具发现与 LangChain 工具适配。
- ``config``: MCP server 配置模型与解析
- ``client``: 异步单例 ``McpClientManager``，懒连接 + 工具缓存 + 重连

工具暴露范围：仅 DeepAgent（路径 C）合并 MCP 工具；subagent（路径 B）不暴露，
遵循 claude.md §10 安全红线——避免绕过 ``interrupt_on`` 审批流。
"""

from app.mcp.client import McpClientManager, get_mcp_manager
from app.mcp.config import (
    McpServerConfig,
    McpTransport,
    mcp_servers_from_settings,
    parse_mcp_servers_config,
)

__all__ = [
    "McpClientManager",
    "get_mcp_manager",
    "McpServerConfig",
    "McpTransport",
    "mcp_servers_from_settings",
    "parse_mcp_servers_config",
]
