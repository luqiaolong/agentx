"""文件系统工具集：所有读写操作经 ``SessionSandbox`` 校验授权。

设计要点：
- 工具函数返回字符串 / 列表，**不抛 ``PathNotAuthorized``**：DeepAgent 期望工具错误
  以字符串形式回流到 state（与 LangGraph tool node 契约一致），由路径层决定是否中止。
- 读操作调用 ``check_read``，写操作调用 ``check_write``；二者均在未授权时抛
  ``PathNotAuthorized``，本模块捕获后转为中文错误字符串。
- 路径用 ``pathlib.Path`` 规范化，``glob``/``grep`` 校验 pattern 的父目录读权限。
- ``data/workspace`` 与 ``data/uploads`` 始终可读可写（沙箱白名单），无需显式授权。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, UPLOADS_DIR, WORKSPACE_DIR
from app.observability.logger import logger
from app.utils.security import PathNotAuthorized, get_sandbox

# 读权限缺失时的统一错误信息（与 SessionSandbox.check_read 一致）
_UNAUTHORIZED_READ = "路径 {path} 未授权，请通过 dialog 选择目录后重试"
# 写权限缺失：路径已授权只读时的提示
_UNAUTHORIZED_WRITE_READONLY = (
    "路径 {path} 仅授权读取，写入请使用 data/workspace 或在授权时勾选允许写入"
)
# 写权限缺失：路径完全未授权时的提示
_UNAUTHORIZED_WRITE = "路径 {path} 未授权，请通过 dialog 选择目录后重试"


def _resolve(path: str | Path, base: str | Path | None = None) -> Path:
    """规范化路径，相对路径基于 ``base`` 或 PROJECT_ROOT 解析。

    委托给 ``app.utils.paths.normalize_path``。``base`` 用于将相对路径基于
    workspace 解析，避免 fs 工具用相对路径时被解到 PROJECT_ROOT。
    """
    from app.utils.paths import normalize_path

    return normalize_path(path, base=base)


def _deny_read(path: str | Path) -> str:
    return _UNAUTHORIZED_READ.format(path=path)


def _deny_write(path: str | Path, matched_readonly: bool) -> str:
    if matched_readonly:
        return _UNAUTHORIZED_WRITE_READONLY.format(path=path)
    return _UNAUTHORIZED_WRITE.format(path=path)


async def read_file(thread_id: str, path: str, base: str | Path | None = None) -> str:
    """读取文本文件内容。未授权时返回错误字符串（不抛异常）。

    ``base`` 用于沙箱授权校验时解析相对路径的基准（通常为 workspace 路径）。
    """
    sandbox = get_sandbox()
    try:
        sandbox.check_read(thread_id, path, base=base)
    except PathNotAuthorized:
        logger.warning("fs.read_file denied", thread_id=thread_id, path=str(path))
        return _deny_read(path)
    try:
        return _resolve(path, base=base).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"文件不存在: {path}"
    except OSError as exc:
        return f"读取文件失败: {path} ({exc})"


async def list_dir(thread_id: str, path: str, base: str | Path | None = None) -> list[str]:
    """列出目录下的条目名称（不含路径前缀）。未授权时返回单元素错误列表。

    ``base`` 用于沙箱授权校验时解析相对路径的基准。
    """
    sandbox = get_sandbox()
    try:
        sandbox.check_read(thread_id, path, base=base)
    except PathNotAuthorized:
        logger.warning("fs.list_dir denied", thread_id=thread_id, path=str(path))
        return [_deny_read(path)]
    p = _resolve(path, base=base)
    if not p.is_dir():
        return [f"不是目录: {path}"]
    try:
        return sorted(entry.name for entry in p.iterdir())
    except OSError as exc:
        return [f"列目录失败: {path} ({exc})"]


async def glob(thread_id: str, pattern: str, base: str | Path | None = None) -> list[str]:
    """glob 匹配文件路径。校验 pattern 父目录的读权限。

    ``pattern`` 形如 ``d:/docs/**/*.md``，取其最顶层的非通配前缀 ``d:/docs`` 做权限校验，
    避免对含 ``*`` / ``?`` 的字符串直接 ``resolve()``。

    ``base`` 用于沙箱授权校验时解析相对路径的基准。
    """
    sandbox = get_sandbox()
    base_dir = _glob_base(pattern)
    try:
        sandbox.check_read(thread_id, base_dir, base=base)
    except PathNotAuthorized:
        logger.warning(
            "fs.glob denied", thread_id=thread_id, pattern=pattern, base=str(base_dir)
        )
        return [_deny_read(base_dir)]
    try:
        # rglob 风格：将 pattern 视为相对 base 的 glob；绝对路径则剥离 base 前缀后基于 base glob
        p = Path(pattern)
        if p.is_absolute():
            rel = pattern[len(str(base_dir)):].lstrip("\\/")
            matched = sorted(str(x) for x in _resolve(base_dir, base=base).glob(rel))
        else:
            matched = sorted(str(x) for x in _resolve(base_dir, base=base).glob(pattern))
        return matched
    except (OSError, ValueError) as exc:
        return [f"glob 失败: {pattern} ({exc})"]


async def grep(thread_id: str, pattern: str, path: str, base: str | Path | None = None) -> list[str]:
    """在 ``path`` 目录下递归搜索匹配 ``pattern``（正则）的行。

    返回 ``file:line: matched_text`` 列表；未授权或无匹配时返回相应提示列表。

    ``base`` 用于沙箱授权校验时解析相对路径的基准。
    """
    sandbox = get_sandbox()
    try:
        sandbox.check_read(thread_id, path, base=base)
    except PathNotAuthorized:
        logger.warning("fs.grep denied", thread_id=thread_id, path=str(path))
        return [_deny_read(path)]
    p = _resolve(path, base=base)
    if not p.exists():
        return [f"路径不存在: {path}"]
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return [f"正则表达式非法: {pattern} ({exc})"]

    results: list[str] = []
    try:
        files = p.rglob("*") if p.is_dir() else [p]
        for fp in files:
            if not fp.is_file():
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{fp}:{lineno}: {line}")
                    if len(results) >= 200:
                        results.append("... (结果过多，已截断至 200 条)")
                        return results
    except OSError as exc:
        return [f"grep 失败: {path} ({exc})"]
    if not results:
        return [f"未找到匹配: {pattern}"]
    return results


# 模块级：记录最近写入操作，用于幂等性检查（防重复写入）
_recent_writes: dict[str, tuple[str, int, str, float]] = {}
"""thread_id → (path, content_len, content_hash, timestamp) 最近成功写入记录"""


def _content_hash(content: str) -> str:
    """计算内容短哈希，用于幂等性比对。"""
    import hashlib
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


async def write_file(thread_id: str, path: str, content: str, base: str | Path | None = None) -> str:
    """写入文本文件（覆盖）。未授权时返回错误字符串。

    幂等性保护：
    - 若同一 thread_id 在 60 秒内写入相同路径 + 相同内容，直接返回上次结果，
      防止 LLM 因重试机制导致重复写入。
    - 若文件已存在且内容相同，跳过写入并提示。

    ``base`` 用于沙箱授权校验时解析相对路径的基准。
    """
    from time import time

    sandbox = get_sandbox()
    resolved = _resolve(path, base=base)
    content_len = len(content)
    content_h = _content_hash(content)

    # ---- 1. 幂等性检查：同一 thread 近期是否已写入相同内容 ----
    now = time()
    recent = _recent_writes.get(thread_id)
    if recent is not None:
        recent_path, recent_len, recent_hash, recent_ts = recent
        if (recent_path == str(resolved) and recent_len == content_len
                and recent_hash == content_h and now - recent_ts < 60):
            logger.info(
                "fs.write_file.idempotent_skip",
                thread_id=thread_id,
                path=str(path),
                reason="duplicate_within_60s",
            )
            return f"已写入(重复请求已跳过): {path} ({content_len} 字符)"

    # ---- 2. 权限校验 ----
    try:
        sandbox.check_write(thread_id, path, base=base)
    except PathNotAuthorized as exc:
        msg = str(exc)
        matched_readonly = "仅授权读取" in msg
        logger.warning(
            "fs.write_file.denied",
            thread_id=thread_id,
            path=str(path),
            resolved=str(resolved),
            reason="readonly" if matched_readonly else "unauthorized",
        )
        return _deny_write(path, matched_readonly)

    # ---- 3. 文件已存在且内容相同：跳过写入 ----
    if resolved.exists() and resolved.is_file():
        try:
            existing = resolved.read_text(encoding="utf-8")
            if existing == content:
                logger.info(
                    "fs.write_file.identical_skip",
                    thread_id=thread_id,
                    path=str(path),
                    reason="content_identical",
                )
                return f"文件已存在且内容相同，跳过写入: {path} ({content_len} 字符)"
        except OSError as exc:
            logger.warning(
                "fs.write_file.read_existing_failed",
                thread_id=thread_id,
                path=str(path),
                error=str(exc),
            )

    # ---- 4. 执行写入 ----
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        _recent_writes[thread_id] = (str(resolved), content_len, content_h, now)
        logger.info(
            "fs.write_file.success",
            thread_id=thread_id,
            path=str(path),
            resolved=str(resolved),
            size=content_len,
        )
        return f"已写入: {path} ({content_len} 字符)"
    except OSError as exc:
        logger.error(
            "fs.write_file.os_error",
            thread_id=thread_id,
            path=str(path),
            error=str(exc),
        )
        return f"写入文件失败: {path} ({exc})"


async def edit_file(thread_id: str, path: str, old_text: str, new_text: str, base: str | Path | None = None) -> str:
    """编辑文件：将 ``old_text`` 替换为 ``new_text``（仅首次匹配）。未授权时返回错误字符串。

    ``base`` 用于沙箱授权校验时解析相对路径的基准。
    """
    sandbox = get_sandbox()
    try:
        sandbox.check_write(thread_id, path, base=base)
    except PathNotAuthorized as exc:
        msg = str(exc)
        matched_readonly = "仅授权读取" in msg
        logger.warning("fs.edit_file denied", thread_id=thread_id, path=str(path))
        return _deny_write(path, matched_readonly)
    try:
        p = _resolve(path, base=base)
        if not p.exists():
            return f"文件不存在: {path}"
        text = p.read_text(encoding="utf-8")
        if old_text not in text:
            return f"未找到要替换的文本: {path}"
        new = text.replace(old_text, new_text, 1)
        p.write_text(new, encoding="utf-8")
        return f"已编辑: {path}"
    except OSError as exc:
        return f"编辑文件失败: {path} ({exc})"


async def list_workspace(path: str, thread_id: str | None = None) -> list[dict]:
    """列出沙箱白名单内目录的条目（含 type/size/mtime）。

    允许 ``data/workspace`` 和 ``data/uploads``（白名单），以及通过
    ``SessionSandbox`` 授权给指定 ``thread_id`` 的目录。
    ``path`` 相对路径基于 ``PROJECT_ROOT`` 解析（与 ``_resolve`` 一致）；
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

    # 白名单校验
    whitelist = [WORKSPACE_DIR.resolve(), UPLOADS_DIR.resolve()]
    in_whitelist = any(target == w or w in target.parents for w in whitelist)

    # 若不在白名单，检查是否在当前 thread_id 的授权目录内
    if not in_whitelist:
        if thread_id is None:
            raise ValueError(f"路径不在白名单内: {path}")
        sandbox = get_sandbox()
        # 检查目标是否位于该 thread_id 的任一授权目录之下
        raw_authorized = sandbox.authorized_dirs.get(thread_id, set())
        # 防御性：确保 authorized 是可迭代的集合类型（set/list/tuple）
        if isinstance(raw_authorized, (list, tuple)):
            logger.warning(
                "authorized_dirs type mismatch for thread_id={}, expected set got {}. "
                "Converting to set to avoid iteration errors.",
                thread_id,
                type(raw_authorized).__name__,
            )
            authorized = set(raw_authorized)
        elif not isinstance(raw_authorized, set):
            logger.error(
                "authorized_dirs type error for thread_id={}, expected set got {}. "
                "Value: {}. Falling back to empty set.",
                thread_id,
                type(raw_authorized).__name__,
                raw_authorized,
            )
            authorized = set()
        else:
            authorized = raw_authorized
        in_authorized = any(
            target == auth_path or auth_path in target.parents
            for (auth_path, _writable) in authorized
        )
        if not in_authorized:
            raise ValueError(f"路径不在白名单内且未授权: {path}")

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
    sandbox.check_read(thread_id or "", path)

    # 2. 解析为实际目标路径
    target = _resolve(path)

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
    "read_file",
    "list_dir",
    "list_workspace",
    "read_workspace_file",
    "glob",
    "grep",
    "write_file",
    "edit_file",
]
