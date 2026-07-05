"""最小可用 MCP stdio server（用于 AgentX MCP 集成测试）。

实现一个 echo 工具,验证 stdio 传输 + 工具发现链路。
启动方式: uv run python scripts/mcp_mock_server.py
"""
import asyncio
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


server = Server("agentx-test-mock")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="echo",
            description="回显输入内容,用于验证 MCP 集成链路",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "要回显的内容"},
                },
                "required": ["message"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "echo":
        msg = arguments.get("message", "")
        return [TextContent(type="text", text=f"echo: {msg}")]
    raise ValueError(f"Unknown tool: {name}")


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)