"""直接测试 langchain-mcp-adapters 与 mock server 的连接。"""
import asyncio
import sys
import traceback

async def test():
    from langchain_mcp_adapters.client import MultiServerMCPClient

    spec = {
        "agentx-test-mock": {
            "transport": "stdio",
            "command": "uv",
            "args": ["run", "python", "scripts/mcp_mock_server.py"],
            "env": {},
        }
    }
    client = MultiServerMCPClient(spec)
    print("[1] client created", flush=True)
    try:
        tools = await client.get_tools(server_name="agentx-test-mock")
        print(f"[2] get_tools OK, count={len(tools)}", flush=True)
        for t in tools:
            print(f"  - {t.name}: {t.description}", flush=True)
    except Exception:
        print("[2] get_tools FAILED:", flush=True)
        traceback.print_exc()
    finally:
        try:
            await client.__aexit__(None, None, None)
        except Exception:
            pass

asyncio.run(test())