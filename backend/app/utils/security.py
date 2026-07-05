"""会话级文件系统沙箱：按 thread_id 隔离授权目录，校验读写权限。

白名单 = data/workspace + data/uploads（始终可读可写）+ 会话授权目录集合。
授权目录默认只读，授权时可指定 writable=True。所有路径在比对前均经
``Path.resolve()`` 规范化（解析 ``..`` / 符号链接 / 大小写）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT, UPLOADS_DIR, WORKSPACE_DIR, get_settings
from app.memory.sandbox_store import SandboxStore, get_sandbox_store
from app.observability.langsmith import trace_span
from app.observability.logger import logger


class PathNotAuthorized(Exception):
    """路径未授权。"""


@dataclass
class ApprovalDecision:
    """审批决策（兼容旧 bool 语义 + 扩展授权场景）。

    - dangerous_tool：approved=True/False，decision="approve"/"deny"
    - directory_extension：decision="once"/"session"/"deny"，path/writable 描述目标
    """

    approved: bool
    decision: str = "approve"  # "approve" | "once" | "session" | "deny"
    path: str | None = None
    writable: bool = False


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

    def __init__(self, store: SandboxStore | None = None) -> None:
        self.authorized_dirs: dict[str, set[tuple[Path, bool]]] = {}
        # full_trust 模式：thread_id 集合，模式下调过授权检查（仍拒绝系统关键目录）
        self.full_trust_threads: set[str] = set()
        # once 决策临时授权：thread_id → {(path, writable)}，不写入 checkpoint，工具调用完成后清理
        self._temp_authorized: dict[str, set[tuple[Path, bool]]] = {}
        # 持久化层（默认使用模块级单例，测试可注入 tmp DB）
        self._store: SandboxStore = store or get_sandbox_store()

    def set_full_trust(self, thread_id: str, enabled: bool) -> None:
        """设置/取消 full_trust 模式。"""
        if enabled:
            self.full_trust_threads.add(thread_id)
        else:
            self.full_trust_threads.discard(thread_id)

    def is_full_trust(self, thread_id: str) -> bool:
        """是否处于 full_trust 模式。"""
        return thread_id in self.full_trust_threads

    def authorize_temp(self, thread_id: str, path: str | Path, writable: bool = False) -> Path:
        """临时授权（不持久化到 checkpoint）。用于 directory_extension 的 once 决策。

        与 ``authorize`` 的差异：写入 ``_temp_authorized`` 而非 ``authorized_dirs``，
        不影响 snapshot/restore，工具调用完成后由 ``clear_temp`` 清理。
        """
        path_str = str(path).strip()
        if not path_str or path_str in (".", ".."):
            raise ValueError(f"路径 {path!r} 无效，请提供具体目录绝对路径")
        resolved = self._normalize(path)
        if self._is_critical(resolved):
            raise ValueError(f"路径 {path} 是系统关键目录，不可授权")
        entries = self._temp_authorized.setdefault(thread_id, set())
        entries = {(p, w) for (p, w) in entries if p != resolved}
        entries.add((resolved, writable))
        self._temp_authorized[thread_id] = entries
        return resolved

    def clear_temp(self, thread_id: str) -> None:
        """清空 once 决策的临时授权（每次工具调用完成后调用）。"""
        self._temp_authorized.pop(thread_id, None)

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
        """校验读权限。通过则返回 None；否则 raise PathNotAuthorized。

        full_trust 模式下跳过授权检查（仍拒绝系统关键目录）。
        """
        resolved = self._normalize(path)
        if self._is_critical(resolved):
            self._deny(thread_id, path, "deny_critical")
            raise PathNotAuthorized(f"路径 {path} 是系统关键目录，不可访问")
        if self.is_full_trust(thread_id):
            return
        for base in _DEFAULT_WHITELIST:
            if _is_under(resolved, base):
                return
        for auth_path, _writable in self.authorized_dirs.get(thread_id, set()):
            if _is_under(resolved, auth_path):
                return
        for auth_path, _writable in self._temp_authorized.get(thread_id, set()):
            if _is_under(resolved, auth_path):
                return
        self._deny(thread_id, path, "deny_read")
        raise PathNotAuthorized(f"路径 {path} 未授权，请通过 dialog 选择目录后重试")

    def check_write(self, thread_id: str, path: str | Path) -> None:
        """校验写权限。授权目录默认只读，写需 writable=True；
        data/workspace 与 data/uploads 始终可写。

        full_trust 模式下跳过授权检查（仍拒绝系统关键目录）。
        """
        resolved = self._normalize(path)
        if self._is_critical(resolved):
            self._deny(thread_id, path, "deny_critical")
            raise PathNotAuthorized(f"路径 {path} 是系统关键目录，不可访问")
        if self.is_full_trust(thread_id):
            return
        for base in _DEFAULT_WHITELIST:
            if _is_under(resolved, base):
                return
        matched = False
        for auth_path, writable in self.authorized_dirs.get(thread_id, set()):
            if _is_under(resolved, auth_path):
                matched = True
                if writable:
                    return
        for auth_path, writable in self._temp_authorized.get(thread_id, set()):
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

    def authorize(
        self, thread_id: str, path: str | Path, writable: bool = False, source: str = "manual"
    ) -> Path:
        """授权目录。返回规范化后的 Path。拒绝系统关键目录。

        拒绝空字符串 / 纯空白 / "." / ".." —— 否则会被 ``Path.resolve()`` 静默解析为
        CWD（当前目录），授权 CWD 等价于一次性把仓库根目录读权限交给 LLM。
        """
        path_str = str(path).strip()
        if not path_str or path_str in (".", ".."):
            raise ValueError(f"路径 {path!r} 无效，请提供具体目录绝对路径")
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
        # 双写到 DB（best-effort，失败不阻塞内存操作）
        self._persist_upsert(thread_id, resolved, writable, source)
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
            existed = len(entries) < before
        # 双写到 DB（best-effort，失败不阻塞内存操作）
        self._persist_delete_path(thread_id, resolved)
        return existed

    def clear(self, thread_id: str) -> None:
        """清空该 thread_id 的所有授权（会话结束时调用）。"""
        self.authorized_dirs.pop(thread_id, None)
        # 双写到 DB（best-effort，失败不阻塞内存操作）
        self._persist_delete_thread(thread_id)

    def bootstrap_from_store(self) -> None:
        """启动时从 DB 全量加载授权到内存。失败仅 log error，不阻塞启动。"""
        try:
            loaded = self._store.bootstrap_all()
            for thread_id, entries in loaded.items():
                self.authorized_dirs[thread_id] = set(entries)
            logger.info(
                "sandbox.bootstrap_loaded",
                threads=len(loaded),
                total_entries=sum(len(e) for e in loaded.values()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("sandbox.bootstrap_failed: {}", exc)

    def _persist_upsert(
        self, thread_id: str, resolved: Path, writable: bool, source: str
    ) -> None:
        """best-effort DB upsert。失败仅 warning，不阻塞内存操作。"""
        if not get_settings().sandbox_persistence_enabled:
            return
        try:
            self._store.upsert(thread_id, str(resolved), writable, source)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox.db.write_failed: {}", exc)

    def _persist_delete_path(self, thread_id: str, resolved: Path) -> None:
        """best-effort DB delete by path。失败仅 warning。"""
        if not get_settings().sandbox_persistence_enabled:
            return
        try:
            self._store.delete_by_path(thread_id, str(resolved))
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox.db.write_failed: {}", exc)

    def _persist_delete_thread(self, thread_id: str) -> None:
        """best-effort DB delete by thread。失败仅 warning。"""
        if not get_settings().sandbox_persistence_enabled:
            return
        try:
            self._store.delete_by_thread(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox.db.write_failed: {}", exc)

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

    def is_path_authorized(self, thread_id: str, path: str | Path, writable: bool = False) -> bool:
        """检查路径是否已授权（用于 directory_extension 预检查，不抛异常）。

        - 系统关键目录：永远返回 False
        - full_trust 模式：永远返回 True（除系统关键目录）
        - 白名单 / authorized_dirs / _temp_authorized：返回 True
        """
        try:
            resolved = self._normalize(path)
        except Exception:  # noqa: BLE001
            return False
        if self._is_critical(resolved):
            return False
        if self.is_full_trust(thread_id):
            return True
        for base in _DEFAULT_WHITELIST:
            if _is_under(resolved, base):
                return True
        for auth_path, w in self.authorized_dirs.get(thread_id, set()):
            if _is_under(resolved, auth_path) and (not writable or w):
                return True
        for auth_path, w in self._temp_authorized.get(thread_id, set()):
            if _is_under(resolved, auth_path) and (not writable or w):
                return True
        return False


_sandbox: SessionSandbox | None = None


def get_sandbox() -> SessionSandbox:
    """返回模块级单例 SessionSandbox。"""
    global _sandbox
    if _sandbox is None:
        _sandbox = SessionSandbox()
    return _sandbox
