"""Workspace 列表 + 文件查看路由。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from app.sandbox import PathNotAuthorized


def register_workspace_routes(app: FastAPI) -> None:
    """注册 workspace 路由（``GET /api/workspace/list`` + ``GET /api/workspace/read``）。"""

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

    @app.get("/api/workspace/read")
    async def workspace_read(
        path: str,
        thread_id: str = "",
    ) -> dict[str, Any]:
        """读取沙箱白名单 / 已授权目录内的文本文件内容（供 CodeViewer 使用）。

        - 文本文件：返回 ``{"content": str, "size": int, "encoding": "utf-8"}``
        - 二进制文件：返回 ``{"binary": True, "size": int}``
        - 路径不在白名单或未授权 → 400
        - 文件不存在 → 404
        - 路径是目录或超过 2 MiB → 400
        """
        # 延迟 import：测试通过 monkeypatch app.main.read_workspace_file 注入 fake
        from app.main import read_workspace_file

        try:
            return await read_workspace_file(path, thread_id or None)
        except (ValueError, PathNotAuthorized) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))