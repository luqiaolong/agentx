"""沙箱授权路由（``/api/sandbox/*``）。

从 ``app.api.sandbox`` 迁移而来，与 ``deep/`` / ``team/`` / ``tools/`` 平行。

改进：
- authorize / revoke / list 三个端点加 ``logger.info`` 审计日志
- 移除死代码 ``except PathNotAuthorized`` 分支（authorize 不会抛 PathNotAuthorized）
- 由于 SessionSandbox 方法改为 async，端点内调用加 ``await``
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from app.observability.logger import logger
from app.sandbox.schemas import AuthorizeRequest, RevokeRequest
from app.sandbox.session_sandbox import get_sandbox


def register_sandbox_routes(app: FastAPI) -> None:
    """注册沙箱授权路由（``/api/sandbox/*``）。"""

    @app.post("/api/sandbox/authorize")
    async def sandbox_authorize(req: AuthorizeRequest) -> dict[str, Any]:
        """授权目录。系统关键目录或非法路径 → 400。"""
        sandbox = get_sandbox()
        try:
            resolved = await sandbox.authorize(
                req.thread_id, req.path, writable=req.writable, source=req.source
            )
        except ValueError as exc:
            logger.warning(
                "sandbox.authorize rejected",
                thread_id=req.thread_id,
                path=req.path,
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc))
        logger.info(
            "sandbox.authorize ok",
            thread_id=req.thread_id,
            path=str(resolved),
            writable=req.writable,
            source=req.source,
        )
        return {
            "authorized": True,
            "path": str(resolved),
            "writable": req.writable,
        }

    @app.post("/api/sandbox/revoke")
    async def sandbox_revoke(req: RevokeRequest) -> dict[str, Any]:
        """撤销授权。"""
        sandbox = get_sandbox()
        revoked = await sandbox.revoke(req.thread_id, req.path)
        logger.info(
            "sandbox.revoke",
            thread_id=req.thread_id,
            path=req.path,
            revoked=revoked,
        )
        return {"revoked": revoked}

    @app.get("/api/sandbox/authorized/{thread_id}")
    async def sandbox_authorized(thread_id: str) -> dict[str, Any]:
        """列出会话已授权目录。"""
        sandbox = get_sandbox()
        entries = await sandbox.list_authorized(thread_id)
        logger.info(
            "sandbox.list_authorized",
            thread_id=thread_id,
            count=len(entries),
        )
        return {"dirs": [{"path": str(p), "writable": w} for (p, w) in entries]}
