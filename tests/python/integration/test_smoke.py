"""SSE 冒烟测试：覆盖三条路径（A 闲聊 / B 单步工具 / C DeepAgent）。

M1 骨架：断言 ``POST /api/chat`` 返回 200 且 SSE 流中至少收到一个事件。
使用 ``httpx.AsyncClient`` + ``ASGITransport`` 直连 FastAPI app（不触发 lifespan，
不依赖 myserver）。M2 接入 LLM 后扩展为断言各路径的事件序列。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.integration
async def test_path_a_chat_returns_sse_stream() -> None:
    """路径 A（闲聊）：``/api/chat`` 返回 200 + 至少一个 SSE 事件。"""
    transport = ASGITransport(app=app)
    body = {"message": "你好，今天天气怎么样", "thread_id": "smoke-a"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/chat", json=body) as resp:
            assert resp.status_code == 200
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            assert "event:" in text, f"未收到 SSE 事件，实际输出: {text!r}"


@pytest.mark.integration
async def test_path_b_single_tool_returns_sse_stream() -> None:
    """路径 B（单步工具）：``/api/chat`` 返回 200 + 至少一个 SSE 事件。"""
    transport = ASGITransport(app=app)
    body = {"message": "读取 data/workspace/readme.md", "thread_id": "smoke-b"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/chat", json=body) as resp:
            assert resp.status_code == 200
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            assert "event:" in text, f"未收到 SSE 事件，实际输出: {text!r}"


@pytest.mark.integration
async def test_path_c_deep_agent_returns_sse_stream() -> None:
    """路径 C（DeepAgent）：``/api/chat`` 返回 200 + 至少一个 SSE 事件。"""
    transport = ASGITransport(app=app)
    body = {"message": "帮我把项目里所有 markdown 整理成一份索引", "thread_id": "smoke-c"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/chat", json=body) as resp:
            assert resp.status_code == 200
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            assert "event:" in text, f"未收到 SSE 事件，实际输出: {text!r}"


@pytest.mark.integration
async def test_reset_clears_sandbox_when_not_persisted() -> None:
    """/reset 指令返回 200 + SSE 事件（授权目录持久化开关由配置决定）。"""
    transport = ASGITransport(app=app)
    body = {"message": "/reset", "thread_id": "smoke-reset"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/chat", json=body) as resp:
            assert resp.status_code == 200
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            assert "event:" in text, f"未收到 SSE 事件，实际输出: {text!r}"
