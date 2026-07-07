"""健康检查路由。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.embedding.tei_client import healthcheck as embedding_healthcheck
from app.vectorstore import get_milvus_client


async def _health_with_timeout(coro: Any, timeout: float = 1.0) -> dict[str, Any]:
    """带超时的健康检查子项，避免慢依赖拖垮整体响应。"""
    import asyncio

    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "unhealthy", "error": "health check timeout"}
    except Exception as exc:  # noqa: BLE001 — 健康检查兜底
        return {"status": "unhealthy", "error": str(exc)}


def register_health_routes(app: FastAPI) -> None:
    """注册健康检查路由（``GET /`` + ``GET /api/health``）。"""

    @app.get("/")
    async def root() -> dict[str, str]:
        """应用根健康检查。"""
        return {"app": "agentx", "version": "0.1.0", "status": "ok"}

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """聚合健康检查：BGE-M3 + Milvus 子项。整体 200 即使子项 unhealthy。

        每个子项最多等待 1 秒，保证 Electron 启动轮询在 2 秒内拿到响应。
        """
        embedding_status = await _health_with_timeout(embedding_healthcheck(), timeout=1.0)
        milvus_status = await _health_with_timeout(get_milvus_client().healthcheck(), timeout=1.0)

        return {"status": "ok", "embedding": embedding_status, "milvus": milvus_status}
