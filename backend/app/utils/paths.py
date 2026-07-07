"""路径归一化工具。

提取自 ``utils/security.py:_normalize`` 与 ``tools/filesystem.py:_resolve``，
消除两处完全一致的实现重复。

LLM 工具调用常生成相对路径如 ``data/workspace/foo.txt``，若用
``Path.resolve()`` 默认基于 CWD（可能是 ``backend/``）解析，会导致
``backend/data/workspace/foo.txt`` ≠ 白名单 ``PROJECT_ROOT/data/workspace``，
从而被误拒。强制相对路径基于 PROJECT_ROOT 解析可修复此问题。
"""

from __future__ import annotations

from pathlib import Path

from app.config import PROJECT_ROOT

__all__ = ["normalize_path"]


def normalize_path(path: str | Path) -> Path:
    """规范化路径。相对路径基于 PROJECT_ROOT 解析（非 CWD）。

    Args:
        path: 输入路径（字符串或 Path 对象）。

    Returns:
        规范化后的绝对 Path（经 ``resolve()`` 解析 ``..`` / 符号链接 / 大小写）。
    """
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()
