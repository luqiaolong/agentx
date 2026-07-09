"""Workspace 包路由：所有 workspace 相关 REST 端点。

合并自：
- 原 ``app/api/workspace.py`` —— ``/api/workspace/list`` + ``/api/workspace/read``
- 原 ``app/api/project_config.py`` —— ``/api/project-config/init`` + ``/api/project-config``

URL 路径**保持不变**（前端契约）：
- ``GET  /api/workspace/list``        列沙箱白名单/已授权目录条目
- ``GET  /api/workspace/read``        读取白名单/已授权目录内的文本文件
- ``POST /api/project-config/init``   幂等生成 ``.agentx/``
- ``GET  /api/project-config``        读取工作区 ``.agentx/`` 状态

安全约束（project-config 端点）：
- 路径校验：拒绝空 / ``.`` / ``..`` / 系统关键目录（双向：祖先+后代）
- 沙箱授权：必须先通过 ``POST /api/sandbox/authorize`` 授权该路径，
  否则 init/get 端点返回 400（防止未授权目录被读写）

> 延迟 import 约定：``list_workspace`` / ``read_workspace_file`` 通过
> ``from app.main import ...`` 引用，保留为 monkeypatch 测试锚点
> （测试通过 ``monkeypatch app.main.<name>`` 注入 fake）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pathlib import Path

from app.api.schemas import ProjectConfigInitRequest
from app.observability.logger import logger
from app.sandbox import PathNotAuthorized
from app.workspace.config.generator import generate_agentx_dir
from app.workspace.config.loader import load_project_config


# ============================================================
# 私有辅助函数（来自原 project_config.py）
# ============================================================


def _is_critical_path(resolved: Path) -> bool:
    """路径是否为系统关键目录、其祖先或其后代（双向检查）。

    复用 ``app.sandbox.CRITICAL_DIRS`` + ``is_under`` 实现，
    逻辑与 ``SessionSandbox._is_critical`` 一致：

    - 系统目录（``C:/Windows`` 等）：拒绝 resolved 是 crit 本身、其祖先
      （如 ``C:/``）或其后代（如 ``C:/Windows/System32``）
    - 用户主目录：仅拒绝 resolved 是 home 本身或其祖先（如 ``C:/Users``），
      不拒绝 home 的子目录（用户可在 ``C:/Users/me/Projects`` 生成 .agentx）
    """
    from app.sandbox import CRITICAL_DIRS, is_under

    home = Path.home().resolve()
    for crit in CRITICAL_DIRS:
        if crit == home:
            # home：仅拒绝授权 home 本身或其上级
            if is_under(crit, resolved):
                return True
            continue
        # 系统目录：双向拒绝（祖先与后代均不可）
        if is_under(resolved, crit) or is_under(crit, resolved):
            return True
    return False


async def _validate_workspace_path(path_str: str, thread_id: str) -> Path:
    """校验工作区路径：非空 + 非系统关键目录 + 沙箱授权 + 存在 + 是目录。

    Args:
        path_str: 工作区绝对路径字符串。
        thread_id: 会话 ID，用于沙箱授权校验。

    Returns:
        规范化后的 ``Path`` 对象。

    Raises:
        HTTPException 400: 路径为空 / ``.`` / ``..`` / 系统关键目录 / 未授权 / 不存在 / 不是目录。
    """
    # 拒绝空 / 纯空白 / "." / ".."（防止 resolve() 静默解析为 CWD）
    if not path_str or not path_str.strip() or path_str.strip() in (".", ".."):
        raise HTTPException(status_code=400, detail="path 不能为空且不能是 . 或 ..")

    try:
        path = Path(path_str).resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"路径非法: {exc}")

    # 拒绝系统关键目录（双向：祖先+后代）
    if _is_critical_path(path):
        raise HTTPException(
            status_code=400,
            detail=f"不能在系统关键目录生成 .agentx: {path}",
        )

    # 沙箱授权校验：路径必须在已授权范围内（防止未授权目录被读写）
    from app.sandbox import get_sandbox

    sandbox = get_sandbox()
    try:
        await sandbox.check_read(thread_id, path)
    except Exception as exc:  # noqa: BLE001 — 沙箱校验失败统一报 400
        raise HTTPException(
            status_code=400,
            detail=f"路径未授权，请先通过工作区选择授权: {exc}",
        )

    if not path.exists():
        raise HTTPException(status_code=400, detail=f"路径不存在: {path}")
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {path}")

    return path


# ============================================================
# 统一路由注册
# ============================================================


def register_routes(app: FastAPI) -> None:
    """注册所有 workspace 路由（``/api/workspace/*`` + ``/api/project-config/*``）。"""

    # ---- /api/workspace/*（原 api/workspace.py）----

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

    # ---- /api/project-config/*（原 api/project_config.py）----

    @app.post("/api/project-config/init")
    async def project_config_init(req: ProjectConfigInitRequest) -> dict[str, Any]:
        """在工作区生成 ``.agentx/`` 配置目录（幂等）。

        已存在的文件不被覆盖，仅创建缺失文件。
        路径必须先通过沙箱授权（``POST /api/sandbox/authorize``）。
        """
        path = await _validate_workspace_path(req.path, req.thread_id)
        try:
            result = generate_agentx_dir(path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError as exc:
            logger.error("workspace.config.init_failed", path=str(path), error=str(exc))
            raise HTTPException(status_code=500, detail=f"生成失败: {exc}")

        return {
            "ok": True,
            "path": result.path,
            "created": result.created,
            "skipped": result.skipped,
        }

    @app.get("/api/project-config")
    async def project_config_get(
        path: str = Query(..., description="工作区绝对路径"),
        thread_id: str = Query(..., description="会话 ID，用于沙箱授权校验"),
    ) -> dict[str, Any]:
        """读取工作区的 ``.agentx/`` 配置状态。

        路径必须先通过沙箱授权。
        """
        ws_path = await _validate_workspace_path(path, thread_id)
        config = load_project_config(ws_path)

        if not config.exists:
            return {
                "exists": False,
                "files": [],
                "agents_md_preview": None,
            }

        # 构建文件状态列表
        agentx_dir = ws_path / ".agentx"
        file_names = [
            "AGENTS.md",
            "mcp.json",
            "subagents.json",
            "tools.json",
            "system_prompt.md",
            "rules",
        ]
        files = []
        for name in file_names:
            fpath = agentx_dir / name
            if fpath.exists():
                if fpath.is_dir():
                    size = 0
                else:
                    try:
                        size = fpath.stat().st_size
                    except OSError:
                        size = 0
                files.append({"name": name, "exists": True, "size": size})
            else:
                files.append({"name": name, "exists": False, "size": 0})

        # AGENTS.md 预览（前 500 字符）
        agents_md_preview = None
        if config.agents_md:
            agents_md_preview = config.agents_md[:500]
            if len(config.agents_md) > 500:
                agents_md_preview += "\n...[truncated]"

        return {
            "exists": True,
            "files": files,
            "agents_md_preview": agents_md_preview,
        }
