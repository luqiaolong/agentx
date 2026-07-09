"""McpClientManager 单元测试：per-server tool_count + stdio 子进程清理。

直接 mock ``langchain_mcp_adapters.client.MultiServerMCPClient`` 与
``mcp_servers_from_settings``，避免真实启动 stdio 子进程。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp.client import McpClientManager
from app.mcp.config import McpServerConfig


class _FakeTool:
    """Minimal LangChain BaseTool substitute（仅需 ``name`` 属性）。"""

    def __init__(self, name: str) -> None:
        self.name = name


def _make_stdio_server(name: str) -> McpServerConfig:
    """构造一个 stdio MCP server 配置（避免重复样板）。"""
    return McpServerConfig(
        name=name,
        transport="stdio",
        command="fake-cmd",
        args=[],
        env={},
        enabled=True,
        trusted=False,
    )


# ============================================================
# Bug 1: list_servers per-server tool_count
# ============================================================


async def test_list_servers_per_server_tool_count() -> None:
    """两个 server 各有 3 / 5 个工具，list_servers 必须分别报 3 和 5。

    Bug：旧代码 ``tool_count = sum(1 for t in self._tools)`` 在 per-server 循环
    里引用全局 ``self._tools``，导致每个 server 都报告总数 8。
    """
    srv_a = _make_stdio_server("srv-a")
    srv_b = _make_stdio_server("srv-b")

    per_server_tools: dict[str, list[_FakeTool]] = {
        "srv-a": [_FakeTool(f"a{i}") for i in range(3)],
        "srv-b": [_FakeTool(f"b{i}") for i in range(5)],
    }

    fake_client = MagicMock()

    async def fake_get_tools(*, server_name: str | None = None) -> list[_FakeTool]:
        if server_name is None:
            return [t for tools in per_server_tools.values() for t in tools]
        return list(per_server_tools.get(server_name, []))

    fake_client.get_tools = fake_get_tools

    manager = McpClientManager()
    with (
        patch(
            "app.mcp.client.mcp_servers_from_settings",
            return_value=[srv_a, srv_b],
        ),
        patch(
            "langchain_mcp_adapters.client.MultiServerMCPClient",
            return_value=fake_client,
        ),
    ):
        await manager.get_tools()  # 触发懒初始化

    servers = await manager.list_servers()
    assert len(servers) == 2
    by_name = {s["name"]: s for s in servers}

    assert by_name["srv-a"]["tool_count"] == 3
    assert by_name["srv-b"]["tool_count"] == 5

    # 关键：不应有任何 server 报告全局总数 8
    assert all(s["tool_count"] != 8 for s in servers), (
        "no server should report the global total of 8 tools"
    )


# ============================================================
# Bug 2: close() terminates stdio subprocesses via per-server session()
# ============================================================


async def test_close_terminates_stdio_subprocesses() -> None:
    """close() 必须对每个已启用 server 调用 ``client.session(name)`` async CM，
    触发其 ``__aexit__`` 清理 stdio 子进程。

    Bug：旧 ``_close_locked`` 直接 ``self._client = None``，不调用任何清理 hook，
    stdio 子进程悬挂到 Python 进程退出才回收。
    """
    srv_a = _make_stdio_server("srv-a")
    srv_b = _make_stdio_server("srv-b")

    # 收集 session() 调用产生的 async context manager 实例，
    # 用于事后断言 __aenter__ / __aexit__ 被触发。
    session_cms: list[tuple[str, AsyncMock]] = []

    def session_factory(name: str) -> AsyncMock:
        cm: AsyncMock = AsyncMock()  # AsyncMock 原生支持 async with
        session_cms.append((name, cm))
        return cm

    fake_client = MagicMock()
    fake_client.session = MagicMock(side_effect=session_factory)

    async def fake_get_tools(*, server_name: str | None = None) -> list[_FakeTool]:
        # 返回空列表足够——init 不应失败，但也不需要触发任何 session()
        return []

    fake_client.get_tools = fake_get_tools

    manager = McpClientManager()
    with (
        patch(
            "app.mcp.client.mcp_servers_from_settings",
            return_value=[srv_a, srv_b],
        ),
        patch(
            "langchain_mcp_adapters.client.MultiServerMCPClient",
            return_value=fake_client,
        ),
    ):
        await manager.get_tools()  # 触发懒初始化

    # 初始化阶段只调用 get_tools(server_name=...)，不应调用 session()
    assert len(session_cms) == 0, "init should not call client.session()"

    await manager.close()

    # session() 必须为每个已启用 server 调用一次（共 2 次）
    assert len(session_cms) == 2, (
        f"expected session() called for 2 enabled servers, got {len(session_cms)}"
    )
    session_names = {name for name, _ in session_cms}
    assert session_names == {"srv-a", "srv-b"}

    # 每个 async CM 的 __aenter__ 与 __aexit__（=close/shutdown hook）都必须被 await
    for name, cm in session_cms:
        cm.__aenter__.assert_awaited_once()
        cm.__aexit__.assert_awaited_once()

    # 清理后内部状态必须重置
    assert manager._client is None
    assert manager._server_tools == {}
    assert manager._tools == []
    assert manager.is_initialized is False


# ============================================================
# 回归：单个 server 关闭失败不应阻塞其他 server 清理
# ============================================================


async def test_close_isolates_per_server_failures() -> None:
    """某一个 server 的 session() 抛异常时，后续 server 仍应被清理。"""
    srv_a = _make_stdio_server("srv-a")
    srv_b = _make_stdio_server("srv-b")

    session_cms: list[tuple[str, AsyncMock]] = []

    def session_factory(name: str) -> AsyncMock:
        if name == "srv-a":
            # srv-a 的 session 入口直接抛异常（模拟 stdio 已死）
            raise RuntimeError("srv-a session crashed")
        cm: AsyncMock = AsyncMock()
        session_cms.append((name, cm))
        return cm

    fake_client = MagicMock()
    fake_client.session = MagicMock(side_effect=session_factory)

    async def fake_get_tools(*, server_name: str | None = None) -> list[_FakeTool]:
        return []

    fake_client.get_tools = fake_get_tools

    manager = McpClientManager()
    with (
        patch(
            "app.mcp.client.mcp_servers_from_settings",
            return_value=[srv_a, srv_b],
        ),
        patch(
            "langchain_mcp_adapters.client.MultiServerMCPClient",
            return_value=fake_client,
        ),
    ):
        await manager.get_tools()

    # close 不应抛异常（srv-a 失败被 try/except 隔离）
    await manager.close()

    # srv-b 仍应被清理（即使 srv-a 失败）
    assert len(session_cms) == 1
    assert session_cms[0][0] == "srv-b"
    session_cms[0][1].__aexit__.assert_awaited_once()

    # 无论如何 _client 都应被清空
    assert manager._client is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
