"""会话级文件系统沙箱：按 thread_id 隔离授权目录，校验读写权限。

白名单 = data/workspace + data/uploads（始终可读可写）+ 会话授权目录集合。
授权目录默认只读，授权时可指定 writable=True。所有路径在比对前均经
``Path.resolve()`` 规范化（解析 ``..`` / 符号链接 / 大小写）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.config import PROJECT_ROOT, UPLOADS_DIR, WORKSPACE_DIR
from app.observability.langsmith import trace_span


class PathNotAuthorized(Exception):
    """路径未授权。"""


# 默认白名单：始终可读可写（已规范化）
_DEFAULT_WHITELIST: tuple[Path, ...] = (WORKSPACE_DIR.resolve(), UPLOADS_DIR.resolve())


def _is_under(child: Path, base: Path) -> bool:
    """child 是否等于 base 或位于 base 之下（prefix match）。

    使用 ``relative_to`` 而非字符串前缀，避免 ``d:/docs`` 误匹配 ``d:/docs-other``。
    """
    try:
        child.relative_to(base)
        return True
    except ValueError:
        return False


def _critical_dirs() -> list[Path]:
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
        candidates = [Path("/"), Path("/etc"), Path("/usr"), Path("/bin"),
                       Path("/sbin"), Path("/var"), Path("/boot"), Path("/root"), home]
    return [c.resolve() for c in candidates]


class SessionSandbox:
    """会话级沙箱授权目录管理。按 thread_id 隔离。"""

    def __init__(self) -> None:
        self.authorized_dirs: dict[str, set[tuple[Path, bool]]] = {}

    # ---- internal helpers ----

    @staticmethod
    def _normalize(path: str | Path) -> Path:
        """规范化路径。相对路径基于 PROJECT_ROOT 解析（非 CWD）。

        LLM 工具调用常生成相对路径如 ``data/workspace/foo.txt``，
        若用 ``Path.resolve()`` 默认基于 CWD（可能是 ``backend/``）解析，
        会导致 ``backend/data/workspace/foo.txt`` ≠ 白名单 ``PROJECT_ROOT/data/workspace``，
        从而被误拒。强制相对路径基于 PROJECT_ROOT 解析可修复此问题。
        """
        p = Path(path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p.resolve()

    def _is_critical(self, resolved: Path) -> bool:
        """路径是否为系统关键目录、其祖先或其后代。

        - 系统目录（C:/Windows 等）：拒绝 resolved 是 crit 本身、其祖先（如 C:/）或其后代
          （如 C:/Windows/System32），即双向 _is_under。
        - 用户主目录：仅拒绝 resolved 是 home 本身或其祖先（如 C:/Users），
          不拒绝 home 的子目录（用户可授权 C:/Users/me/Projects）。
        """
        home = Path.home().resolve()
        for crit in _critical_dirs():
            if crit == home:
                # home：仅拒绝授权 home 本身或其上级（防止授权整个用户目录）
                if _is_under(crit, resolved):
                    return True
                continue
            # 系统目录：双向拒绝（祖先与后代均不可）
            if _is_under(resolved, crit) or _is_under(crit, resolved):
                return True
        return False

    def _deny(self, thread_id: str, path: str | Path, action: str) -> None:
        with trace_span("sandbox.deny", thread_id=thread_id, path=str(path), action=action):
            pass

    # ---- public API ----

    def check_read(self, thread_id: str, path: str | Path) -> None:
        """校验读权限。通过则返回 None；否则 raise PathNotAuthorized。"""
        resolved = self._normalize(path)
        for base in _DEFAULT_WHITELIST:
            if _is_under(resolved, base):
                return
        for auth_path, _writable in self.authorized_dirs.get(thread_id, set()):
            if _is_under(resolved, auth_path):
                return
        self._deny(thread_id, path, "deny_read")
        raise PathNotAuthorized(f"路径 {path} 未授权，请通过 dialog 选择目录后重试")

    def check_write(self, thread_id: str, path: str | Path) -> None:
        """校验写权限。授权目录默认只读，写需 writable=True；
        data/workspace 与 data/uploads 始终可写。"""
        resolved = self._normalize(path)
        for base in _DEFAULT_WHITELIST:
            if _is_under(resolved, base):
                return
        matched = False
        for auth_path, writable in self.authorized_dirs.get(thread_id, set()):
            if _is_under(resolved, auth_path):
                matched = True
                if writable:
                    return
        self._deny(thread_id, path, "deny_write")
        if matched:
            raise PathNotAuthorized(
                f"路径 {path} 仅授权读取，写入请使用 data/workspace 或在授权时勾选允许写入"
            )
        raise PathNotAuthorized(f"路径 {path} 未授权，请通过 dialog 选择目录后重试")

    def authorize(self, thread_id: str, path: str | Path, writable: bool = False) -> Path:
        """授权目录。返回规范化后的 Path。拒绝系统关键目录。"""
        resolved = self._normalize(path)
        if self._is_critical(resolved):
            raise ValueError(f"路径 {path} 是系统关键目录，不可授权")
        with trace_span(
            "sandbox.authorize",
            thread_id=thread_id,
            path=str(path),
            writable=writable,
            action="authorize",
        ):
            entries = self.authorized_dirs.setdefault(thread_id, set())
            # 同路径重新授权时更新 writable（先移除旧条目再添加）
            entries = {(p, w) for (p, w) in entries if p != resolved}
            entries.add((resolved, writable))
            self.authorized_dirs[thread_id] = entries
        return resolved

    def revoke(self, thread_id: str, path: str | Path) -> bool:
        """撤销授权。返回是否曾存在。"""
        resolved = self._normalize(path)
        with trace_span(
            "sandbox.revoke", thread_id=thread_id, path=str(path), action="revoke"
        ):
            entries = self.authorized_dirs.get(thread_id, set())
            before = len(entries)
            entries = {(p, w) for (p, w) in entries if p != resolved}
            self.authorized_dirs[thread_id] = entries
            return len(entries) < before

    def clear(self, thread_id: str) -> None:
        """清空该 thread_id 的所有授权（会话结束时调用）。"""
        self.authorized_dirs.pop(thread_id, None)

    def list_authorized(self, thread_id: str) -> list[tuple[Path, bool]]:
        """返回 [(path, writable), ...]（按路径排序，结果确定性）。"""
        return sorted(
            self.authorized_dirs.get(thread_id, set()),
            key=lambda e: str(e[0]),
        )

    def restore(self, thread_id: str, dirs: list[str]) -> None:
        """从 checkpoint 恢复授权目录列表（dirs 为 path 字符串列表，默认 read-only）。"""
        entries = self.authorized_dirs.setdefault(thread_id, set())
        existing = {p for (p, _w) in entries}
        for d in dirs:
            resolved = self._normalize(d)
            if resolved not in existing:
                entries.add((resolved, False))
                existing.add(resolved)

    def snapshot(self, thread_id: str) -> list[str]:
        """导出授权目录为字符串列表（用于写入 checkpoint authorized_dirs 字段）。"""
        return sorted(str(p) for (p, _w) in self.authorized_dirs.get(thread_id, set()))


_sandbox: SessionSandbox | None = None


def get_sandbox() -> SessionSandbox:
    """返回模块级单例 SessionSandbox。"""
    global _sandbox
    if _sandbox is None:
        _sandbox = SessionSandbox()
    return _sandbox
