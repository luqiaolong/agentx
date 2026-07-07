"""agentx E2E 测试 fixtures。

设计决策：放弃 Playwright + Tauri WebDriver（需下载浏览器 + Tauri 桥接，环境成本高），
改为 Python httpx async 脚本直接打后端 FastAPI。这样：
- 不依赖前端构建
- 不需要 Tauri 桌面运行时
- 与 pytest 集成，CI 5 分钟内可跑完
- 真实覆盖后端 SSE 流、checkpointer 写回、审批流、暂停/恢复

使用：
    uv run uvicorn app.main:app --port 8000 &
    AGENTX_E2E_BASE_URL=http://127.0.0.1:8000 uv run pytest tests/python/e2e/ -v -m e2e
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import httpx
import pytest


DEFAULT_PORTS = (8000, 8127, 8130, 8133, 8135, 8140)


def _find_backend() -> str | None:
    """探测常见端口，返回可达的 base URL；都不可达返回 None。"""
    explicit = os.environ.get("AGENTX_E2E_BASE_URL")
    if explicit:
        return explicit if explicit.startswith("http") else f"http://{explicit}"
    for port in DEFAULT_PORTS:
        if _is_port_open("127.0.0.1", port):
            return f"http://127.0.0.1:{port}"
    return None


def _is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def backend_url() -> str:
    """后端 base URL。默认探测常见端口，全失败则 skip 整 suite。"""
    url = _find_backend()
    if url is None:
        pytest.skip(
            "后端不可达：请先运行 'uv run uvicorn app.main:app --port 8000'。"
            "可通过 AGENTX_E2E_BASE_URL 覆盖。"
        )
    return url


@pytest.fixture
def client(backend_url: str) -> httpx.Client:
    """同步 httpx 客户端（非流式端点：approve / pause / resume / sandbox/authorize）。"""
    return httpx.Client(base_url=backend_url, timeout=30.0)


@pytest.fixture
async def async_client(backend_url: str) -> httpx.AsyncClient:
    """异步 httpx 客户端（用于 SSE 流接收：chat 流式响应）。"""
    async with httpx.AsyncClient(base_url=backend_url, timeout=httpx.Timeout(60.0, read=180.0)) as c:
        yield c


# 标记：所有 E2E 测试都可加 -m e2e 选择执行
pytestmark = pytest.mark.e2e