"""E2E MCP 测试: 调用后端内部 McpClientManager,验证端到端集成。"""
import asyncio
import os
import sys

# 设置 dummy 凭证使后端启动配置可加载
os.environ.setdefault("AGENTX_MILVUS_AUTH_ENABLED", "false")
os.environ.setdefault("AGENTX_HOST", "127.0.0.1")
os.environ.setdefault("AGENTX_PORT", "8124")  # 不冲突端口
os.environ.setdefault("AGENTX_OPENAI_BASE_URL", "https://api.minimax.chat/v1")
os.environ.setdefault("AGENTX_OPENAI_API_KEY", "sk-test-dummy")
os.environ.setdefault("AGENTX_DEFAULT_MODEL", "minimax-m3")
os.environ.setdefault("AGENTX_EMBEDDING_TIMEOUT", "2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.config import get_settings  # noqa: E402
from app.mcp.config import McpServerConfig  # noqa: E402
from app.mcp.client import McpClientManager  # noqa: E402


async def main():
    # 1. 验证 McpServerConfig 解析
    cfg = McpServerConfig(
        name="agentx-test-mock",
        transport="stdio",
        command="uv",
        args=["run", "python", "scripts/mcp_mock_server.py"],
        env={},
        enabled=True,
        trusted=False,
    )
    print(f"[1] McpServerConfig parsed OK: {cfg.name} transport={cfg.transport}")

    # 2. 验证 spec 转换
    spec = cfg.to_client_spec()
    print(f"[2] to_client_spec OK: {list(spec.keys())}")

    # 3. 验证 McpClientManager
    mgr = McpClientManager()
    tools = await mgr.test_server(cfg)
    print(f"[3] test_server result: ok={tools.get('ok')} count={tools.get('tool_count')}")
    for t in tools.get("tools", []):
        print(f"    - {t['name']}: {t['description']}")

    return tools.get("ok")


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)