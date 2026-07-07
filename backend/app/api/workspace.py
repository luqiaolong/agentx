"""Workspace 列表路由。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException


def register_workspace_routes(app: FastAPI) -> None:
    """注册 workspace 路由（``GET /api/workspace/list``）。"""

    @app.get("/api/workspace/list")
    async def workspace_list(
        path: str = "data/workspace",
        thread_id: str = "",
    ) -> dict[str, Any]:
        """列出沙箱白名单内或已授权目录的条目（含 type/size/mtime）。

        允许 ``data/workspace`` 和 ``data/uploads``（始终可读），
        以及通过 ``POST /api/sandbox/authorize`` 授权给 ``thread_id`` 的目录。
        其他路径 → 400。
        """
        # 延迟 import：测试通过 monkeypatch app.main.list_workspace 注入 fake
        from app.main import list_workspace

        try:
            entries = await list_workspace(path, thread_id or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"entries": entries}
