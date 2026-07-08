"""路径归一化工具。

提取自 ``utils/security.py:_normalize`` 与 ``tools/filesystem.py:_resolve``，
消除两处完全一致的实现重复。

LLM 工具调用常生成相对路径如 ``data/workspace/foo.txt``，若用
``Path.resolve()`` 默认基于 CWD（可能是 ``backend/``）解析，会导致
``backend/data/workspace/foo.txt`` ≠ 白名单 ``PROJECT_ROOT/data/workspace``，
从而被误拒。强制相对路径基于 PROJECT_ROOT 解析可修复此问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.config import PROJECT_ROOT

__all__ = ["normalize_path"]


def normalize_path(path: str | Path, base: str | Path | None = None) -> Path:
    """规范化路径。相对路径基于 ``base`` 或 PROJECT_ROOT 解析（非 CWD）。

    Windows 平台额外做大小写和分隔符归一化，避免 ``D:/workspace`` 与
    ``d:\workspace`` 被判定为不同路径（BUG-3 修复）。

    Args:
        path: 输入路径（字符串或 Path 对象）。
        base: 可选的基准目录；传入时相对路径基于该目录解析，否则基于 PROJECT_ROOT。

    Returns:
        规范化后的绝对 Path（经 ``resolve()`` 解析 ``..`` / 符号链接 / 大小写）。
    """
    p = Path(path)
    if not p.is_absolute():
        root = Path(base) if base else PROJECT_ROOT
        p = root / p
    resolved = p.resolve()

    # Windows 路径归一化：统一小写 drive letter 和正斜杠分隔符
    if sys.platform == "win32" and resolved.parts:
        drive = resolved.parts[0]
        if len(drive) == 2 and drive[1] == ":":
            normalized = drive.lower() + "/" + "/".join(resolved.parts[1:])
            return Path(normalized)

    return resolved
