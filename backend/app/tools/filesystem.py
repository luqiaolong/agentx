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


def _deny_read(path: str | Path) -> str:
    return _UNAUTHORIZED_READ.format(path=path)


def _deny_write(path: str | Path, matched_readonly: bool) -> str:
    if matched_readonly:
        return _UNAUTHORIZED_WRITE_READONLY.format(path=path)
    return _UNAUTHORIZED_WRITE.format(path=path)


async def read_file(thread_id: str, path: str) -> str:
    """读取文本文件内容。未授权时返回错误字符串（不抛异常）。"""
    sandbox = get_sandbox()
    try:
        sandbox.check_read(thread_id, path)
    except PathNotAuthorized:
        logger.warning("fs.read_file denied", thread_id=thread_id, path=str(path))
        return _deny_read(path)
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"文件不存在: {path}"
    except OSError as exc:
        return f"读取文件失败: {path} ({exc})"


async def list_dir(thread_id: str, path: str) -> list[str]:
    """列出目录下的条目名称（不含路径前缀）。未授权时返回单元素错误列表。"""
    sandbox = get_sandbox()
    try:
        sandbox.check_read(thread_id, path)
    except PathNotAuthorized:
        logger.warning("fs.list_dir denied", thread_id=thread_id, path=str(path))
        return [_deny_read(path)]
    p = Path(path)
    if not p.is_dir():
        return [f"不是目录: {path}"]
    try:
        return sorted(entry.name for entry in p.iterdir())
    except OSError as exc:
        return [f"列目录失败: {path} ({exc})"]


async def glob(thread_id: str, pattern: str) -> list[str]:
    """glob 匹配文件路径。校验 pattern 父目录的读权限。

    ``pattern`` 形如 ``d:/docs/**/*.md``，取其最顶层的非通配前缀 ``d:/docs`` 做权限校验，
    避免对含 ``*`` / ``?`` 的字符串直接 ``resolve()``。
    """
    sandbox = get_sandbox()
    base = _glob_base(pattern)
    try:
        sandbox.check_read(thread_id, base)
    except PathNotAuthorized:
        logger.warning(
            "fs.glob denied", thread_id=thread_id, pattern=pattern, base=str(base)
        )
        return [_deny_read(base)]
    try:
        # rglob 风格：将 pattern 视为相对 base 的 glob；绝对路径则剥离 base 前缀后基于 base glob
        p = Path(pattern)
        if p.is_absolute():
            rel = pattern[len(str(base)):].lstrip("\\/")
            matched = sorted(str(x) for x in Path(base).glob(rel))
        else:
            matched = sorted(str(x) for x in Path(base).glob(pattern))
        return matched
    except (OSError, ValueError) as exc:
        return [f"glob 失败: {pattern} ({exc})"]


async def grep(thread_id: str, pattern: str, path: str) -> list[str]:
    """在 ``path`` 目录下递归搜索匹配 ``pattern``（正则）的行。

    返回 ``file:line: matched_text`` 列表；未授权或无匹配时返回相应提示列表。
    """
    sandbox = get_sandbox()
    try:
        sandbox.check_read(thread_id, path)
    except PathNotAuthorized:
        logger.warning("fs.grep denied", thread_id=thread_id, path=str(path))
        return [_deny_read(path)]
    p = Path(path)
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


async def write_file(thread_id: str, path: str, content: str) -> str:
    """写入文本文件（覆盖）。未授权时返回错误字符串。"""
    sandbox = get_sandbox()
    try:
        sandbox.check_write(thread_id, path)
    except PathNotAuthorized as exc:
        # 区分"已授权只读"与"完全未授权"，给出针对性提示
        msg = str(exc)
        matched_readonly = "仅授权读取" in msg
        logger.warning("fs.write_file denied", thread_id=thread_id, path=str(path))
        return _deny_write(path, matched_readonly)
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入: {path} ({len(content)} 字符)"
    except OSError as exc:
        return f"写入文件失败: {path} ({exc})"


async def edit_file(thread_id: str, path: str, old_text: str, new_text: str) -> str:
    """编辑文件：将 ``old_text`` 替换为 ``new_text``（仅首次匹配）。未授权时返回错误字符串。"""
    sandbox = get_sandbox()
    try:
        sandbox.check_write(thread_id, path)
    except PathNotAuthorized as exc:
        msg = str(exc)
        matched_readonly = "仅授权读取" in msg
        logger.warning("fs.edit_file denied", thread_id=thread_id, path=str(path))
        return _deny_write(path, matched_readonly)
    try:
        p = Path(path)
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


__all__ = [
    "read_file",
    "list_dir",
    "glob",
    "grep",
    "write_file",
    "edit_file",
]
