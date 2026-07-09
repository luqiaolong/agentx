"""沙箱路径归一化 + 关键目录保护（纯函数）。

从 ``app.utils.security`` 迁移而来，与 ``deep/`` / ``team/`` / ``tools/`` 平行。

公共 API：
- ``PathNotAuthorized``：路径未授权异常
- ``normalize_path``：路径归一化（委托 ``app.utils.paths``）
- ``is_under``：判断 child 是否在 parent 下（用 ``relative_to``，非字符串前缀）
- ``is_critical``：判断是否为系统关键目录
- ``CRITICAL_DIRS``：平台相关关键目录列表
- ``DEFAULT_WHITELIST``：始终可读写白名单（WORKSPACE_DIR + UPLOADS_DIR）

Linux bug 修复：
    原 ``_critical_dirs()`` 在非 win32 平台把 ``Path("/")`` 纳入候选，
    ``_is_under(resolved, "/")`` 对任何绝对路径都成功 → Linux 下沙箱不可用。
    修复：对根目录（``parent == self``）仅拒绝 ``path == root``，不拒绝后代。
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.config import PROJECT_ROOT, UPLOADS_DIR, WORKSPACE_DIR

__all__ = [
    "PathNotAuthorized",
    "normalize_path",
    "is_under",
    "is_critical",
    "CRITICAL_DIRS",
    "DEFAULT_WHITELIST",
]


class PathNotAuthorized(Exception):
    """路径未授权。"""


def is_under(child: Path, base: Path) -> bool:
    """child 是否等于 base 或位于 base 之下（prefix match）。

    使用 ``relative_to`` 而非字符串前缀，避免 ``d:/docs`` 误匹配 ``d:/docs-other``。
    """
    try:
        child.relative_to(base)
        return True
    except ValueError:
        return False


def _build_critical_dirs() -> list[Path]:
    """返回当前 OS 的系统关键目录列表（已规范化）。"""
    home = Path.home().resolve()
    if sys.platform == "win32":
        candidates = [
            Path("C:/Windows"),
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
            home,
        ]
    else:
        candidates = [
            Path("/"),
            Path("/etc"),
            Path("/usr"),
            Path("/bin"),
            Path("/sbin"),
            Path("/var"),
            Path("/boot"),
            Path("/root"),
            home,
        ]
    return [c.resolve() for c in candidates]


# 模块级常量：启动时计算一次
CRITICAL_DIRS: list[Path] = _build_critical_dirs()
DEFAULT_WHITELIST: list[Path] = [WORKSPACE_DIR.resolve(), UPLOADS_DIR.resolve()]


def is_critical(path: Path) -> bool:
    """路径是否为系统关键目录、其祖先或其后代。

    - 根目录（``parent == self``，如 Linux ``/`` 或 Windows ``C:/``）：
      仅拒绝 ``path == root`` 本身，不拒绝后代（修复 Linux 沙箱不可用 bug）。
    - 系统目录（C:/Windows 等）：拒绝 resolved 是 crit 本身、其祖先（如 C:/）或
      其后代（如 C:/Windows/System32），即双向 ``is_under``。
    - 用户主目录：仅拒绝 resolved 是 home 本身或其祖先（如 C:/Users），
      不拒绝 home 的子目录（用户可授权 C:/Users/me/Projects）。
    """
    resolved = path.resolve()
    home = Path.home().resolve()
    for crit in CRITICAL_DIRS:
        # 根目录：仅拒绝本身，不拒绝后代（Linux bug 修复）
        if crit.parent == crit:
            if resolved == crit:
                return True
            continue
        if crit == home:
            # home：仅拒绝授权 home 本身或其上级（防止授权整个用户目录）
            if is_under(crit, resolved):
                return True
            continue
        # 系统目录：双向拒绝（祖先与后代均不可）
        if is_under(resolved, crit) or is_under(crit, resolved):
            return True
    return False


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
