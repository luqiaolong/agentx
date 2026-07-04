"""MCP server 配置模型与解析。

配置通过 ``AGENT_PY_MCP_SERVERS_CONFIG`` 环境变量注入（JSON 字符串），
与 ``subagents_config`` / ``tools_config`` 一致，由 Electron Main 从
``electron-store`` 读取后通过 ``subprocess.Popen(env=...)`` 注入。

配置 schema（JSON 数组）::

    [
      {
        "name": "filesystem",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "d:/workspace"],
        "env": {},
        "enabled": true,
        "trusted": false
      },
      {
        "name": "remote-api",
        "transport": "streamable_http",
        "url": "http://localhost:8000/mcp",
        "enabled": true,
        "trusted": false
      }
    ]

安全约束：
- ``trusted=false``（默认）时，MCP 工具调用经 DeepAgent ``interrupt_before`` 审批流。
- ``trusted=true`` 时自动放行，仅用于完全可信的 MCP server。
- MCP 工具**仅**暴露给 DeepAgent（路径 C），subagent 不暴露。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# 支持的传输方式
McpTransport = Literal["stdio", "sse", "streamable_http"]


class McpServerConfig(BaseModel):
    """单个 MCP server 的配置。

    Args:
        name: server 唯一名（^[a-zA-Z0-9_-]{1,64}$），用作展示与 tool 命名空间前缀。
        transport: 传输方式。``stdio`` 走子进程；``sse`` / ``streamable_http`` 走 HTTP。
        command: ``stdio`` 传输时的可执行命令（如 ``npx`` / ``python``）。
        args: ``stdio`` 传输时的命令参数列表。
        env: ``stdio`` 传输时的环境变量（合并到子进程 env）。
        url: ``sse`` / ``streamable_http`` 传输时的 server URL。
        enabled: 是否启用。禁用的 server 不会连接，工具不暴露。
        trusted: 是否可信。可信 server 的工具调用跳过审批；默认 false 走审批流。
    """

    name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    transport: McpTransport = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    enabled: bool = True
    trusted: bool = False

    @field_validator("command", mode="after")
    @classmethod
    def _validate_stdio_command(cls, v: str | None, info: Any) -> str | None:
        transport = info.data.get("transport") if info.data else None
        if transport == "stdio" and not v:
            raise ValueError("stdio 传输必须提供 command")
        return v

    @field_validator("url", mode="after")
    @classmethod
    def _validate_http_url(cls, v: str | None, info: Any) -> str | None:
        transport = info.data.get("transport") if info.data else None
        if transport in ("sse", "streamable_http") and not v:
            raise ValueError(f"{transport} 传输必须提供 url")
        return v

    def to_client_spec(self) -> dict[str, Any]:
        """转换为 ``langchain_mcp_adapters.client.MultiServerMCPClient`` 接受的 spec dict。"""
        spec: dict[str, Any] = {"transport": self.transport}
        if self.transport == "stdio":
            spec["command"] = self.command
            spec["args"] = list(self.args)
            if self.env:
                spec["env"] = dict(self.env)
        else:
            spec["url"] = self.url
        return spec


def parse_mcp_servers_config(raw: Any) -> list[McpServerConfig]:
    """解析 ``AGENT_PY_MCP_SERVERS_CONFIG`` 环境变量值（JSON 字符串或已解析 list）。

    容错策略：
    - 字符串 → JSON 解析，失败返回空列表
    - list → 逐项构造 ``McpServerConfig``，跳过无效项并记 warning
    - 其他类型 → 空列表
    """
    from app.observability.logger import logger

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("AGENT_PY_MCP_SERVERS_CONFIG JSON 解析失败，忽略 MCP 配置")
            return []
    if not isinstance(raw, list):
        return []

    result: list[McpServerConfig] = []
    seen_names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            cfg = McpServerConfig(**item)
        except Exception as exc:  # noqa: BLE001 — 配置解析容错
            logger.warning("MCP server 配置无效: {} ({})", item.get("name", "?"), exc)
            continue
        if cfg.name in seen_names:
            logger.warning("MCP server 名重复，跳过: {}", cfg.name)
            continue
        seen_names.add(cfg.name)
        result.append(cfg)
    return result


def mcp_servers_from_settings() -> list[McpServerConfig]:
    """从 ``get_settings()`` 读取并解析 MCP server 配置。"""
    from app.config import get_settings

    return parse_mcp_servers_config(get_settings().mcp_servers_config)
