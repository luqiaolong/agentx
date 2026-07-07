"""沙箱授权路由。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from app.api.schemas import AuthorizeRequest, RevokeRequest
from app.observability.logger import logger
from app.utils.security import PathNotAuthorized, get_sandbox


def register_sandbox_routes(app: FastAPI) -> None:
    """注册沙箱授权路由（``/api/sandbox/*``）。"""

    @app.post("/api/sandbox/authorize")
    async def sandbox_authorize(req: AuthorizeRequest) -> dict[str, Any]:
        """授权目录。系统关键目录或非法路径 → 400。"""
        sandbox = get_sandbox()
        try:
            resolved = sandbox.authorize(
                req.thread_id, req.path, writable=req.writable, source=req.source
            )
        except ValueError as exc:
            # 系统关键目录
            logger.warning(
                "sandbox.authorize rejected",
                thread_id=req.thread_id,
                path=req.path,
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc))
        except PathNotAuthorized as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "authorized": True,
            "path": str(resolved),
            "writable": req.writable,
        }

    @app.post("/api/sandbox/revoke")
    async def sandbox_revoke(req: RevokeRequest) -> dict[str, Any]:
        """撤销授权。"""
        sandbox = get_sandbox()
        revoked = sandbox.revoke(req.thread_id, req.path)
        return {"revoked": revoked}

    @app.get("/api/sandbox/authorized/{thread_id}")
    async def sandbox_authorized(thread_id: str) -> dict[str, Any]:
        """列出会话已授权目录。"""
        sandbox = get_sandbox()
        entries = sandbox.list_authorized(thread_id)
        return {"dirs": [{"path": str(p), "writable": w} for (p, w) in entries]}
