"""MCP (Model Context Protocol) 路由。

MCP server 配置由 Electron Main 从 electron-store 读取后通过
``AGENTX_MCP_SERVERS_CONFIG`` 环境变量注入。后端启动时解析配置，
首次 ``GET /api/mcp/servers`` 或 ``GET /api/mcp/tools`` 时懒连接所有启用的 server。
配置变更（前端增删改）需重启后端生效，``POST /api/mcp/refresh`` 可强制重连
（仅适用于未改配置的重连场景，配置变更必须重启）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from app.api.schemas import McpServerTestRequest


def register_mcp_routes(app: FastAPI) -> None:
    """注册 MCP 路由（``/api/mcp/*``）。"""

    @app.get("/api/mcp/servers")
    async def mcp_servers_list() -> dict[str, Any]:
        """返回 MCP server 列表 + 连接状态。

        首次调用触发懒初始化（连接所有启用的 server）。返回每项含 ``name`` /
        ``transport`` / ``enabled`` / ``trusted`` / ``connected`` / ``error`` /
        ``tool_count``。
        """
        # 延迟 import：测试通过 monkeypatch app.main.get_mcp_manager 注入 fake
        from app.main import get_mcp_manager

        manager = get_mcp_manager()
        servers = await manager.list_servers()
        return {"servers": servers}

    @app.get("/api/mcp/tools")
    async def mcp_tools_list() -> dict[str, Any]:
        """返回当前已发现的 MCP 工具列表（name + description）。"""
        from app.main import get_mcp_manager

        manager = get_mcp_manager()
        tools = await manager.get_tools()
        return {
            "tools": [
                {
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", "") or "",
                }
                for t in tools
            ]
        }

    @app.post("/api/mcp/servers/test")
    async def mcp_servers_test(req: McpServerTestRequest) -> dict[str, Any]:
        """测试单个 server 配置连接，返回工具列表或错误。

        不影响主客户端状态，测试完即关闭。前端「测试连接」按钮调用。
        """
        from app.mcp.config import McpServerConfig

        try:
            cfg = McpServerConfig(
                name=req.name,
                transport=req.transport,  # type: ignore[arg-type]
                command=req.command,
                args=req.args,
                env=req.env,
                url=req.url,
                enabled=req.enabled,
                trusted=req.trusted,
            )
        except Exception as exc:  # noqa: BLE001 — 配置校验失败
            raise HTTPException(status_code=400, detail=str(exc))

        from app.main import get_mcp_manager

        manager = get_mcp_manager()
        result = await manager.test_server(cfg)
        return result

    @app.post("/api/mcp/refresh")
    async def mcp_refresh() -> dict[str, Any]:
        """强制重连所有 server（关闭旧连接 + 重新解析配置 + 初始化）。

        仅适用于「未改配置但需重连」的场景（如远端 server 重启后恢复）。
        配置变更（增删改 server）必须重启后端——env 变量在启动期注入，运行时不可改。
        """
        from app.main import get_mcp_manager

        manager = get_mcp_manager()
        await manager.refresh()
        servers = await manager.list_servers()
        return {"ok": True, "servers": servers}
