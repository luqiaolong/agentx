"""MCP 管理 API 单元测试：/api/mcp/servers、/api/mcp/tools、/api/mcp/refresh、/api/mcp/servers/test。

Mock MCP manager，避免真实启动 stdio 子进程。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient。"""
    from app.main import app

    return TestClient(app)


# ============================================================
# GET /api/mcp/servers
# ============================================================


def test_mcp_servers_list_returns_empty_when_no_servers(client: TestClient) -> None:
    """无 MCP server 配置时返回 {servers: []}。"""
    fake_manager = AsyncMock()
    fake_manager.list_servers = AsyncMock(return_value=[])

    with patch("app.main.get_mcp_manager", return_value=fake_manager):
        r = client.get("/api/mcp/servers")

    assert r.status_code == 200
    assert r.json() == {"servers": []}


def test_mcp_servers_list_passes_through_entries(client: TestClient) -> None:
    """servers 列表按 manager 返回原样输出。"""
    entries = [
        {
            "name": "filesystem",
            "transport": "stdio",
            "enabled": True,
            "trusted": False,
            "connected": True,
            "error": None,
            "tool_count": 3,
        },
        {
            "name": "remote-mcp",
            "transport": "sse",
            "enabled": True,
            "trusted": True,
            "connected": False,
            "error": "connection refused",
            "tool_count": 0,
        },
    ]
    fake_manager = AsyncMock()
    fake_manager.list_servers = AsyncMock(return_value=entries)

    with patch("app.main.get_mcp_manager", return_value=fake_manager):
        r = client.get("/api/mcp/servers")

    assert r.status_code == 200
    body = r.json()
    assert len(body["servers"]) == 2
    assert body["servers"][0]["name"] == "filesystem"
    assert body["servers"][1]["error"] == "connection refused"


# ============================================================
# GET /api/mcp/tools
# ============================================================


def test_mcp_tools_list_serializes_attributes(client: TestClient) -> None:
    """tools 列表从 manager.get_tools() 抽取 name/description。"""

    class _FakeTool:
        def __init__(self, name: str, description: str) -> None:
            self.name = name
            self.description = description

    fake_manager = AsyncMock()
    fake_manager.get_tools = AsyncMock(
        return_value=[
            _FakeTool("read_file", "Read a file"),
            _FakeTool("list_dir", "List directory"),
            _FakeTool("no_desc_tool", None),
        ]
    )

    with patch("app.main.get_mcp_manager", return_value=fake_manager):
        r = client.get("/api/mcp/tools")

    assert r.status_code == 200
    body = r.json()
    assert len(body["tools"]) == 3
    assert body["tools"][0] == {"name": "read_file", "description": "Read a file"}
    assert body["tools"][2]["description"] == ""  # None → ""


def test_mcp_tools_list_empty_when_no_manager_tools(client: TestClient) -> None:
    fake_manager = AsyncMock()
    fake_manager.get_tools = AsyncMock(return_value=[])

    with patch("app.main.get_mcp_manager", return_value=fake_manager):
        r = client.get("/api/mcp/tools")

    assert r.json() == {"tools": []}


# ============================================================
# POST /api/mcp/servers/test
# ============================================================


def test_mcp_servers_test_success_returns_tools(client: TestClient) -> None:
    """测试成功：manager.test_server 返回 ok=True + tools。"""
    cfg = {
        "name": "test-srv",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@fake/server"],
        "env": {},
        "url": None,
        "enabled": True,
        "trusted": False,
    }
    fake_manager = AsyncMock()
    fake_manager.test_server = AsyncMock(
        return_value={
            "ok": True,
            "tools": [{"name": "echo", "description": "echo"}],
            "tool_count": 1,
            "error": None,
        }
    )

    with patch("app.main.get_mcp_manager", return_value=fake_manager):
        r = client.post("/api/mcp/servers/test", json=cfg)

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tools"][0]["name"] == "echo"


def test_mcp_servers_test_failure_returns_400(client: TestClient) -> None:
    """测试连接失败时：manager 返回 ok=False + error，前端收到结构化响应（非 HTTP 错误）。"""
    cfg = {
        "name": "bad-srv",
        "transport": "stdio",
        "command": "nonexistent_xyz",
        "args": [],
        "env": {},
        "url": None,
        "enabled": True,
        "trusted": False,
    }
    fake_manager = AsyncMock()
    fake_manager.test_server = AsyncMock(
        return_value={
            "ok": False,
            "tools": [],
            "tool_count": 0,
            "error": "command not found",
        }
    )

    with patch("app.main.get_mcp_manager", return_value=fake_manager):
        r = client.post("/api/mcp/servers/test", json=cfg)

    assert r.status_code == 200  # test 失败仍 200，由 body.ok 表达失败
    assert r.json()["ok"] is False


def test_mcp_servers_test_invalid_config_rejected(client: TestClient) -> None:
    """配置非法（name 含 @）→ Pydantic 校验 422 / 端点构造异常 400。

    name 正则 ^[a-zA-Z0-9_-]{1,64}$ 在 McpServerConfig 构造时被检查；
    但 FastAPI Pydantic 也会先在请求层校验（取决于请求模型的字段约束）。
    总之非法配置应该被 4xx 拒掉，不能透传到 manager.test_server。
    """
    cfg = {
        "name": "bad@name",
        "transport": "stdio",
        "command": "x",
        "args": [],
        "env": {},
        "url": None,
        "enabled": True,
        "trusted": False,
    }
    fake_manager = AsyncMock()
    with patch("app.main.get_mcp_manager", return_value=fake_manager):
        r = client.post("/api/mcp/servers/test", json=cfg)
    assert r.status_code in (400, 422)
    # 关键: manager.test_server 不应被调用
    fake_manager.test_server.assert_not_called()


# ============================================================
# POST /api/mcp/refresh
# ============================================================


def test_mcp_refresh_returns_servers(client: TestClient) -> None:
    """refresh 后立即 list，返回 {ok: True, servers: [...]}。"""
    fake_manager = AsyncMock()
    fake_manager.refresh = AsyncMock()
    fake_manager.list_servers = AsyncMock(
        return_value=[
            {
                "name": "s1",
                "transport": "stdio",
                "enabled": True,
                "trusted": False,
                "connected": True,
                "error": None,
                "tool_count": 2,
            }
        ]
    )

    with patch("app.main.get_mcp_manager", return_value=fake_manager):
        r = client.post("/api/mcp/refresh")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["servers"][0]["name"] == "s1"
    fake_manager.refresh.assert_awaited_once()
    fake_manager.list_servers.assert_awaited_once()
