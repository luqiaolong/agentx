"""MCP 客户端单例管理器：懒连接 + 工具缓存 + 优雅降级。

设计要点：
- ``MultiServerMCPClient`` 来自 ``langchain-mcp-adapters``，桥接 MCP 工具到 LangChain
  ``BaseTool``，可直接注入 LangGraph ``ToolNode`` / ``create_react_agent``。
- 懒初始化：首次 ``get_tools()`` 调用时才连接所有启用的 server，避免启动期阻塞。
- 连接失败降级：单个 server 失败不影响其他 server，记 warning 并跳过。
- 工具命名空间：MCP 工具原名可能与内置工具冲突（如 ``read_file``），客户端自动
  加 ``<server>__`` 前缀（``MultiServerMCPClient`` 默认行为）。
- 线程安全：``asyncio.Lock`` 保护初始化与重连。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.mcp.config import McpServerConfig, mcp_servers_from_settings
from app.observability.logger import logger


class McpClientManager:
    """MCP 客户端单例管理器。

    生命周期：
    - ``get_tools()``: 懒初始化 + 缓存。返回所有启用 server 的 LangChain 工具列表。
    - ``refresh()``: 关闭旧连接，重新解析配置并初始化。配置变更后调用。
    - ``close()``: 关闭所有连接，清理状态。应用关闭时调用。
    - ``list_servers()``: 返回配置中的 server 列表 + 连接状态（用于 REST 端点）。
    - ``test_server()``: 测试单个 server 配置连接，返回工具列表或错误。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._client: Any | None = None  # MultiServerMCPClient 实例
        self._servers: list[McpServerConfig] = []
        self._tools: list[Any] = []  # LangChain BaseTool 列表
        self._errors: dict[str, str] = {}  # server_name → 连接错误
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def _initialize_locked(self) -> None:
        """在锁保护下解析配置并连接所有启用的 server。"""
        self._servers = mcp_servers_from_settings()
        enabled = [s for s in self._servers if s.enabled]
        if not enabled:
            logger.info("no enabled MCP servers, skipping client init")
            self._initialized = True
            self._tools = []
            self._errors = {}
            return

        try:
            # 延迟 import 避免模块加载期依赖未安装的库（降级场景）
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError as exc:
            logger.error(
                "langchain-mcp-adapters 未安装，MCP 工具不可用: {}。"
                "请运行 `uv add langchain-mcp-adapters mcp`",
                exc,
            )
            self._initialized = True
            self._tools = []
            self._errors = {s.name: "langchain-mcp-adapters 未安装" for s in enabled}
            return

        spec: dict[str, dict[str, Any]] = {s.name: s.to_client_spec() for s in enabled}
        self._client = MultiServerMCPClient(spec)
        self._errors = {}

        # 逐个 server 探测工具，失败的 server 跳过但不阻塞其他
        all_tools: list[Any] = []
        for server in enabled:
            try:
                # MultiServerMCPClient.get_tools(server_name=...) 返回该 server 的工具
                tools = await self._client.get_tools(server_name=server.name)
                all_tools.extend(tools)
                logger.info(
                    "MCP server '{}' connected, {} tools discovered",
                    server.name,
                    len(tools),
                )
            except Exception as exc:  # noqa: BLE001 — 单 server 失败不影响其他
                logger.warning(
                    "MCP server '{}' 连接失败，跳过: {}",
                    server.name,
                    exc,
                )
                self._errors[server.name] = str(exc)

        self._tools = all_tools
        self._initialized = True

    async def get_tools(self) -> list[Any]:
        """返回所有已连接 MCP server 的 LangChain 工具列表（懒初始化）。

        若所有 server 都失败或无启用 server，返回空列表（不抛异常，调用方按空工具集处理）。
        """
        if not self._initialized:
            async with self._lock:
                if not self._initialized:
                    await self._initialize_locked()
        return list(self._tools)

    async def get_tools_with_trust(self) -> tuple[list[Any], set[str]]:
        """返回 (tools, untrusted_tool_names)。

        - ``tools``: 所有已连接 server 的 LangChain 工具列表。
        - ``untrusted_tool_names``: 所有 MCP 工具名集合（BUG-5 修复：不再区分
          trusted/untrusted，所有 MCP 工具均进入运行时危险集合，触发
          ``interrupt_before`` 审批流）。

        安全模型：MCP 工具可执行任意操作（写文件 / shell / 远程调用），
        无论 server 配置为 trusted=True 还是 trusted=False，所有 MCP 工具
        均须经过用户审批。trusted 标记仅影响审批 UI 的默认行为（未来扩展），
        不用于绕过审批流。
        """
        if not self._initialized:
            async with self._lock:
                if not self._initialized:
                    await self._initialize_locked()

        # 所有 MCP 工具均视为危险工具，统一加入 untrusted_names
        # 避免 trusted=True 配置错误导致安全绕过（BUG-5 修复）
        untrusted_names: set[str] = set()
        if self._client is None:
            return list(self._tools), untrusted_names

        for server in self._servers:
            if not server.enabled or server.name in self._errors:
                continue
            try:
                server_tools = await self._client.get_tools(server_name=server.name)
                for t in server_tools:
                    name = getattr(t, "name", None)
                    if name:
                        untrusted_names.add(name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MCP server '{}' get_tools for trust check failed: {}",
                    server.name,
                    exc,
                )
        return list(self._tools), untrusted_names

    async def refresh(self) -> None:
        """关闭旧连接，重新解析配置并初始化。

        配置变更（如新增 / 启用 / 禁用 server）后调用。线程安全。
        """
        async with self._lock:
            await self._close_locked()
            self._initialized = False
            await self._initialize_locked()

    async def list_servers(self) -> list[dict[str, Any]]:
        """返回 server 列表 + 连接状态（用于 REST ``GET /api/mcp/servers``）。"""
        if not self._initialized:
            async with self._lock:
                if not self._initialized:
                    await self._initialize_locked()

        result: list[dict[str, Any]] = []
        for s in self._servers:
            tool_count = sum(
                1 for t in self._tools
                # MCP 工具名通常含 server 名前缀（MultiServerMCPClient 默认）
                # 或可通过 metadata 查询；此处用工具总数近似
            )
            result.append({
                "name": s.name,
                "transport": s.transport,
                "command": s.command,
                "args": list(s.args),
                "env": dict(s.env),
                "url": s.url,
                "enabled": s.enabled,
                "trusted": s.trusted,
                "connected": s.enabled and s.name not in self._errors,
                "error": self._errors.get(s.name),
                "tool_count": tool_count if (s.enabled and s.name not in self._errors) else 0,
            })
        return result

    async def test_server(self, config: McpServerConfig) -> dict[str, Any]:
        """测试单个 server 配置连接，返回工具列表或错误。

        不影响主客户端状态。用于 REST ``POST /api/mcp/servers/{name}/test``。
        """
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError as exc:
            return {
                "ok": False,
                "error": f"langchain-mcp-adapters 未安装: {exc}",
                "tools": [],
            }

        try:
            client = MultiServerMCPClient({config.name: config.to_client_spec()})
            # 用 session context manager 主动清理 stdio 子进程，
            # 避免探测后子进程残留（langchain-mcp-adapters 0.1.0+ 顶层无 aexit）。
            tools: list[Any] = []
            try:
                async with client.session(config.name) as session:
                    from langchain_mcp_adapters.tools import load_mcp_tools
                    tools = await load_mcp_tools(session)
            except AttributeError:
                # 极旧版本无 session() 时退回顶层 get_tools（不清理子进程，仅探测用）
                tools = await client.get_tools(server_name=config.name)
            return {
                "ok": True,
                "tools": [
                    {"name": getattr(t, "name", ""), "description": getattr(t, "description", "")}
                    for t in tools
                ],
                "tool_count": len(tools),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "tools": []}

    async def _close_locked(self) -> None:
        """在锁保护下关闭客户端连接。

        langchain-mcp-adapters 0.1.0+ 的 ``MultiServerMCPClient`` 不支持顶层
        ``async with`` / ``__aexit__``（会抛 ``AttributeError``）。
        这里仅清内部状态；stdio 子进程由 Python GC + 进程退出时回收。
        真正需要立刻回收子进程时，调用方应使用 ``client.session(name)`` 的
        async context manager（见 ``test_server``）。
        """
        if self._client is None:
            return
        self._client = None
        self._tools = []
        self._errors = {}

    async def close(self) -> None:
        """关闭所有连接，清理状态。应用关闭时调用。"""
        async with self._lock:
            await self._close_locked()
            self._initialized = False
            self._servers = []


# ---- 模块级单例 ----

_manager: McpClientManager | None = None


def get_mcp_manager() -> McpClientManager:
    """返回 ``McpClientManager`` 单例（懒创建）。"""
    global _manager
    if _manager is None:
        _manager = McpClientManager()
    return _manager
