"""E2E MCP 测试 2: 通过 env var 注入 MCP 配置,验证 Settings 解析 + McpClientManager。"""
import asyncio
import json
import os
import sys

os.environ["AGENTX_MILVUS_AUTH_ENABLED"] = "false"
os.environ["AGENTX_HOST"] = "127.0.0.1"
os.environ["AGENTX_PORT"] = "8124"
os.environ["AGENTX_OPENAI_BASE_URL"] = "https://api.minimax.chat/v1"
os.environ["AGENTX_OPENAI_API_KEY"] = "sk-test-dummy"
os.environ["AGENTX_DEFAULT_MODEL"] = "minimax-m3"
os.environ["AGENTX_EMBEDDING_TIMEOUT"] = "2"

# 注入 MCP 配置(标准 stdio server,使用绝对路径避免 cwd 问题)
mcp_cfg = [
    {
        "name": "agentx-test-mock",
        "transport": "stdio",
        "command": sys.executable,
        "args": [os.path.abspath("scripts/mcp_mock_server.py")],
        "env": {},
        "enabled": True,
        "trusted": False,
    }
]
os.environ["AGENTX_MCP_SERVERS_CONFIG"] = json.dumps(mcp_cfg)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.config import get_settings  # noqa: E402
from app.mcp.config import mcp_servers_from_settings  # noqa: E402
from app.mcp.client import get_mcp_manager  # noqa: E402


async def main():
    settings = get_settings()
    print(f"[1] env injected, mcp_servers_config={settings.mcp_servers_config}")

    servers = mcp_servers_from_settings()
    print(f"[2] parsed MCP servers: count={len(servers)}")
    for s in servers:
        print(f"    - {s.name} transport={s.transport} enabled={s.enabled}")

    mgr = get_mcp_manager()
    tools = await mgr.get_tools()
    print(f"[3] MCP manager discovered tools: count={len(tools)}")
    for t in tools:
        print(f"    - {t.name}: {getattr(t, 'description', '')[:60]}")

    tools_untrusted, untrusted = await mgr.get_tools_with_trust()
    print(f"[4] trust check: {len(tools_untrusted)} tools, {len(untrusted)} untrusted")
    print(f"    untrusted names: {untrusted}")

    list_result = await mgr.list_servers()
    print(f"[5] list_servers: {len(list_result)} servers")
    for s in list_result:
        print(f"    - {s['name']}: connected={s['connected']} tools={s['tool_count']}")

    await mgr.close()

    ok = len(tools) >= 1
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)