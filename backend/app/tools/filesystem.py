"""文件系统工具：前端 API 用的 workspace 查看函数。

历史背景：本模块原含 6 个 LLM 工具函数（read_file/ls/glob/grep/write_file/edit_file），
已在 Phase A.2 迁移至 deepagents 内置 fs 工具（由 ``AuthorizedLocalShellBackend`` 提供）。
现仅保留前端 API 用的 ``list_workspace`` 和 ``read_workspace_file``（非 LLM 工具）。

设计要点：
- ``list_workspace`` / ``read_workspace_file`` 返回结构化数据供前端展示，
  不作为 LLM 工具暴露。
- 路径校验复用 ``SessionSandbox.check_read``（白名单 + 授权目录）。
- ``data/workspace`` 与 ``data/uploads`` 始终可读（沙箱白名单）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, WORKSPACE_DIR
from app.observability.logger import logger
from app.sandbox import PathNotAuthorized, get_sandbox
from app.sandbox.path_guard import normalize_path


async def list_workspace(path: str, thread_id: str | None = None) -> list[dict]:
    """列出沙箱白名单内目录的条目（含 type/size/mtime）。

    允许 ``data/workspace`` 和 ``data/uploads``（白名单），以及通过
    ``SessionSandbox`` 授权给指定 ``thread_id`` 的目录。
    ``path`` 相对路径基于 ``PROJECT_ROOT`` 解析（与 ``normalize_path`` 一致）；
    空或 ``"."`` 表示 ``WORKSPACE_DIR`` 根。

    Returns:
        ``[{"name": str, "type": "file"|"dir", "size": int, "mtime": float}]``。

    Raises:
        ValueError: 路径不在白名单内且未授权。
        FileNotFoundError: 路径不存在。
    """
    # 解析路径
    if not path or path == ".":
        target = WORKSPACE_DIR
    else:
        p = Path(path)
        if not p.is_absolute():
            # 相对路径：若以 data/workspace 或 data/uploads 开头则基于 PROJECT_ROOT，
            # 否则默认基于 WORKSPACE_DIR（支持前端仅传子目录名如 "subdir"）
            norm = str(path).replace("\\", "/")
            if norm.startswith("data/workspace") or norm.startswith("data/uploads"):
                p = PROJECT_ROOT / p
            else:
                p = WORKSPACE_DIR / p
        target = p.resolve()

    # 白名单 + 授权校验：复用 sandbox.check_read（统一入口，含 _temp_authorized）
    sandbox = get_sandbox()
    try:
        await sandbox.check_read(thread_id or "", target)
    except PathNotAuthorized:
        raise ValueError(f"路径不在白名单内且未授权: {path}") from None

    if not target.exists():
        raise FileNotFoundError(f"路径不存在: {path}")

    entries: list[dict] = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: e.name):
            try:
                # 安全：用 lstat 而非 stat，避免 follow symlink 泄露目标元数据
                # symlink 本身跳过不列出（白名单内 symlink 通常无必要，且可能逃逸沙箱）
                if entry.is_symlink():
                    continue
                stat = entry.lstat()
                entries.append(
                    {
                        "name": entry.name,
                        "type": "dir" if entry.is_dir() else "file",
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )
            except OSError as exc:
                logger.warning("list_workspace stat failed", path=str(entry), error=str(exc))
                continue
    except OSError as exc:
        raise FileNotFoundError(f"路径不存在: {path}") from exc
    return entries


def _glob_base(pattern: str) -> str:
    """从 glob pattern 中提取最顶层的非通配前缀目录，用于权限校验。

    例：``d:/docs/**/*.md`` → ``d:/docs``；``docs/*.py`` → ``docs``。
    遇到首个含 ``*`` / ``?`` 的路径段即截断。
    """
    # 标准化分隔符
    norm = pattern.replace("\\", "/")
    parts: list[str] = []
    # Windows 盘符前缀（如 d:）保留
    for i, seg in enumerate(norm.split("/")):
        if seg == "":
            continue
        if any(ch in seg for ch in ("*", "?", "[")):
            break
        parts.append(seg)
    if not parts:
        return pattern
    base = "/".join(parts)
    return base


# 文件查看器最大字节数（默认 2 MiB）。超过该值的文件无法在 CodeViewer 内联展示。
_MAX_VIEW_BYTES = 2 * 1024 * 1024


async def read_workspace_file(path: str, thread_id: str | None = None) -> dict[str, Any]:
    """读取沙箱白名单 / 已授权目录内的文本文件内容（仅供前端查看器使用）。

    复用 ``SessionSandbox.check_read`` 完成白名单 + 授权校验，额外要求：
    - 路径必须是文件（不能是目录）
    - 文件大小超过 ``_MAX_VIEW_BYTES``（默认 2 MiB）→ 拒绝并提示用户
    - 解码失败（binary 文件）→ 返回 ``{"binary": True, "size": ...}``

    返回结构：
        ``{"content": str, "size": int, "encoding": "utf-8"}``
        或  ``{"binary": True, "size": int}``
        或  校验失败时由调用方抛 ``ValueError`` / ``FileNotFoundError``。
    """
    # 1. 复用沙箱读校验（白名单 + 授权目录 + 系统关键目录）
    sandbox = get_sandbox()
    await sandbox.check_read(thread_id or "", path)

    # 2. 解析为实际目标路径
    target = normalize_path(path)

    if not target.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if target.is_dir():
        raise ValueError(f"路径是目录而非文件: {path}")

    stat = target.stat()
    if stat.st_size > _MAX_VIEW_BYTES:
        raise ValueError(
            f"文件过大（{stat.st_size} 字节），超过 {_MAX_VIEW_BYTES} 字节上限，"
            "请使用编辑器或专用工具查看"
        )

    # 用 bytes 读，再用 utf-8 解码；失败则视为 binary。
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise FileNotFoundError(f"读取文件失败: {path} ({exc})") from exc

    try:
        text = data.decode("utf-8")
        return {"content": text, "size": stat.st_size, "encoding": "utf-8"}
    except UnicodeDecodeError:
        return {"binary": True, "size": stat.st_size}


__all__ = [
    "list_workspace",
    "read_workspace_file",
]
