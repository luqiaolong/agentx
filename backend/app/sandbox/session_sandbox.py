"""会话级文件系统沙箱：按 thread_id 隔离授权目录，校验读写权限。

从 ``app.utils.security.SessionSandbox`` 迁移而来，与 ``deep/`` / ``team/`` / ``tools/`` 平行。

改进：
- **并发安全**：所有公共方法改为 ``async``，内部用 ``asyncio.Lock`` 保护
- **DB-first 一致性**：``authorize`` 先写 DB（失败抛异常），再改内存；
  ``revoke`` 先删 DB 获取返回值，再改内存
- **parent_thread_id**：Team 模式子任务用独立 ``thread_id``，但可继承父 thread 的授权
- **check_write 修复**：命中 writable 后立即返回（break），不再继续遍历

白名单 = data/workspace + data/uploads（始终可读可写）+ 会话授权目录集合。
授权目录默认只读，授权时可指定 writable=True。所有路径在比对前均经
``normalize_path`` 规范化（解析 ``..`` / 符号链接 / 大小写）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import get_settings
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.sandbox.path_guard import (
    DEFAULT_WHITELIST,
    PathNotAuthorized,
    is_critical,
    is_under,
    normalize_path,
)
from app.sandbox.store import SandboxStore, get_sandbox_store

__all__ = ["SessionSandbox", "get_sandbox"]


class SessionSandbox:
    """会话级沙箱授权目录管理。按 thread_id 隔离，async + Lock 并发安全。

    所有公共方法均为 ``async``，内部用 ``asyncio.Lock`` 保护共享状态：
    - ``_authorized_dirs``：thread_id → {(path, writable)} 持久授权
    - ``_full_trust_threads``：full_trust 模式的 thread_id 集合
    - ``_temp_authorized``：thread_id → {(path, writable)} 临时授权（不持久化）
    - ``_parent_map``：child_thread_id → parent_thread_id 映射（Team 继承）
    """

    def __init__(self, store: SandboxStore | None = None) -> None:
        self._authorized_dirs: dict[str, set[tuple[Path, bool]]] = {}
        self._full_trust_threads: set[str] = set()
        self._temp_authorized: dict[str, set[tuple[Path, bool]]] = {}
        self._parent_map: dict[str, str] = {}
        self._store: SandboxStore = store or get_sandbox_store()
        self._lock = asyncio.Lock()

    # ---- full_trust 模式 ----

    async def set_full_trust(self, thread_id: str, enabled: bool) -> None:
        """设置/取消 full_trust 模式。"""
        async with self._lock:
            if enabled:
                self._full_trust_threads.add(thread_id)
            else:
                self._full_trust_threads.discard(thread_id)

    async def is_full_trust(self, thread_id: str) -> bool:
        """是否处于 full_trust 模式。"""
        async with self._lock:
            return thread_id in self._full_trust_threads

    # ---- 临时授权 ----

    async def authorize_temp(
        self, thread_id: str, path: str | Path, writable: bool = False
    ) -> Path:
        """临时授权（不持久化到 checkpoint）。用于 directory_extension 的 once 决策。

        与 ``authorize`` 的差异：写入 ``_temp_authorized`` 而非 ``_authorized_dirs``，
        不影响 snapshot/restore，工具调用完成后由 ``clear_temp`` 清理。
        """
        path_str = str(path).strip()
        if not path_str or path_str in (".", ".."):
            raise ValueError(f"路径 {path!r} 无效，请提供具体目录绝对路径")
        resolved = normalize_path(path)
        if is_critical(resolved):
            raise ValueError(f"路径 {path} 是系统关键目录，不可授权")
        async with self._lock:
            entries = self._temp_authorized.setdefault(thread_id, set())
            entries = {(p, w) for (p, w) in entries if p != resolved}
            entries.add((resolved, writable))
            self._temp_authorized[thread_id] = entries
        return resolved

    async def clear_temp(self, thread_id: str) -> None:
        """清空 once 决策的临时授权（每次工具调用完成后调用）。"""
        async with self._lock:
            self._temp_authorized.pop(thread_id, None)

    # ---- parent_thread_id 映射 ----

    async def register_parent(
        self, child_thread_id: str, parent_thread_id: str
    ) -> None:
        """注册 parent_thread_id 映射（Team 模式子任务继承父授权）。"""
        async with self._lock:
            self._parent_map[child_thread_id] = parent_thread_id

    # ---- 内部辅助 ----

    def _get_authorized_set(
        self, thread_id: str, source: str = "_authorized_dirs"
    ) -> set[tuple[Path, bool]]:
        """安全获取授权集合。防御非 set 类型（如 list）导致迭代错误。"""
        container = self._authorized_dirs if source == "_authorized_dirs" else self._temp_authorized
        raw = container.get(thread_id, set())
        if isinstance(raw, set):
            return raw
        logger.warning(
            "{} type mismatch for thread_id={}, got {}. Converting.",
            source, thread_id, type(raw).__name__,
        )
        try:
            return set(raw)  # type: ignore[arg-type]
        except TypeError:
            return set()

    def _resolve_parent(self, thread_id: str, parent_thread_id: str | None) -> str | None:
        """解析父 thread_id：优先参数传入，其次查 _parent_map。"""
        if parent_thread_id is not None:
            return parent_thread_id
        return self._parent_map.get(thread_id)

    def _deny(self, thread_id: str, path: str | Path, action: str) -> None:
        with trace_span("sandbox.deny", thread_id=thread_id, path=str(path), action=action):
            pass

    # ---- 公共 API ----

    async def check_read(
        self,
        thread_id: str,
        path: str | Path,
        base: str | Path | None = None,
        parent_thread_id: str | None = None,
    ) -> None:
        """校验读权限。通过则返回 None；否则 raise PathNotAuthorized。

        full_trust 模式下跳过授权检查（仍拒绝系统关键目录）。
        支持 ``parent_thread_id``：子 thread 未授权时查父 thread。

        ``base`` 用于传入 workspace 上下文：相对路径基于 ``base`` 解析。
        """
        resolved = normalize_path(path, base=base)
        if is_critical(resolved):
            self._deny(thread_id, path, "deny_critical")
            raise PathNotAuthorized(f"路径 {path} 是系统关键目录，不可访问")
        async with self._lock:
            if thread_id in self._full_trust_threads:
                return
            for whitelist_path in DEFAULT_WHITELIST:
                if is_under(resolved, whitelist_path):
                    return
            for auth_path, _writable in self._get_authorized_set(thread_id, "_authorized_dirs"):
                if is_under(resolved, auth_path):
                    return
            for auth_path, _writable in self._get_authorized_set(thread_id, "_temp_authorized"):
                if is_under(resolved, auth_path):
                    return
            # 查父 thread 授权
            parent_tid = self._resolve_parent(thread_id, parent_thread_id)
            if parent_tid is not None and parent_tid != thread_id:
                for auth_path, _writable in self._get_authorized_set(parent_tid, "_authorized_dirs"):
                    if is_under(resolved, auth_path):
                        return
                for auth_path, _writable in self._get_authorized_set(parent_tid, "_temp_authorized"):
                    if is_under(resolved, auth_path):
                        return
        self._deny(thread_id, path, "deny_read")
        raise PathNotAuthorized(f"路径 {path} 未授权，请通过 dialog 选择目录后重试")

    async def check_write(
        self,
        thread_id: str,
        path: str | Path,
        base: str | Path | None = None,
        parent_thread_id: str | None = None,
    ) -> None:
        """校验写权限。授权目录默认只读，写需 writable=True；
        data/workspace 与 data/uploads 始终可写。

        full_trust 模式下跳过授权检查（仍拒绝系统关键目录）。
        支持 ``parent_thread_id``：子 thread 未授权时查父 thread。
        命中 writable 后立即返回（break 修复）。
        """
        resolved = normalize_path(path, base=base)
        if is_critical(resolved):
            self._deny(thread_id, path, "deny_critical")
            raise PathNotAuthorized(f"路径 {path} 是系统关键目录，不可访问")
        matched = False
        async with self._lock:
            if thread_id in self._full_trust_threads:
                return
            for whitelist_path in DEFAULT_WHITELIST:
                if is_under(resolved, whitelist_path):
                    return
            for auth_path, writable in self._get_authorized_set(thread_id, "_authorized_dirs"):
                if is_under(resolved, auth_path):
                    matched = True
                    if writable:
                        return  # 命中 writable 后立即返回（break 修复）
            if not matched:
                for auth_path, writable in self._get_authorized_set(thread_id, "_temp_authorized"):
                    if is_under(resolved, auth_path):
                        matched = True
                        if writable:
                            return  # 命中 writable 后立即返回
            # 查父 thread 授权
            if not matched:
                parent_tid = self._resolve_parent(thread_id, parent_thread_id)
                if parent_tid is not None and parent_tid != thread_id:
                    for auth_path, writable in self._get_authorized_set(parent_tid, "_authorized_dirs"):
                        if is_under(resolved, auth_path):
                            matched = True
                            if writable:
                                return  # 命中 writable 后立即返回
                            break
                    if not matched:
                        for auth_path, writable in self._get_authorized_set(parent_tid, "_temp_authorized"):
                            if is_under(resolved, auth_path):
                                matched = True
                                if writable:
                                    return
                                break
        self._deny(thread_id, path, "deny_write")
        if matched:
            raise PathNotAuthorized(
                f"路径 {path} 仅授权读取，写入请使用 data/workspace 或在授权时勾选允许写入"
            )
        raise PathNotAuthorized(f"路径 {path} 未授权，请通过 dialog 选择目录后重试")

    async def authorize(
        self, thread_id: str, path: str | Path, writable: bool = False, source: str = "manual"
    ) -> Path:
        """授权目录。返回规范化后的 Path。拒绝系统关键目录。

        DB-first：先写 DB，失败抛异常不更新内存；成功再改内存。

        拒绝空字符串 / 纯空白 / "." / ".." —— 否则会被 ``resolve()`` 静默解析为
        CWD（当前目录），授权 CWD 等价于一次性把仓库根目录读权限交给 LLM。

        幂等优化：若内存中已存在完全相同的 (path, writable) 授权，则跳过 DB 双写。
        """
        path_str = str(path).strip()
        if not path_str or path_str in (".", ".."):
            raise ValueError(f"路径 {path!r} 无效，请提供具体目录绝对路径")
        resolved = normalize_path(path)
        if is_critical(resolved):
            raise ValueError(f"路径 {path} 是系统关键目录，不可授权")

        async with self._lock:
            entries = self._authorized_dirs.setdefault(thread_id, set())
            # 幂等检测：已存在完全相同的 (path, writable) 则跳过 DB 写
            if (resolved, writable) in entries:
                return resolved

            # DB-first：先写 DB，失败抛异常不更新内存
            if get_settings().sandbox_persistence_enabled:
                await self._store.upsert(thread_id, str(resolved), writable, source)

            # DB 写成功，更新内存
            with trace_span(
                "sandbox.authorize",
                thread_id=thread_id,
                path=str(path),
                writable=writable,
                action="authorize",
            ):
                entries = {(p, w) for (p, w) in entries if p != resolved}
                entries.add((resolved, writable))
                self._authorized_dirs[thread_id] = entries
        return resolved

    async def revoke(self, thread_id: str, path: str | Path) -> bool:
        """撤销授权。返回是否曾存在。

        DB-first：先删 DB 获取返回值，再改内存。existed 基于 DB 返回值。
        """
        resolved = normalize_path(path)
        async with self._lock:
            # DB-first：先删 DB，获取 existed 返回值
            if get_settings().sandbox_persistence_enabled:
                existed = await self._store.delete_by_path(thread_id, str(resolved))
            else:
                # 持久化禁用时，基于内存判断
                entries = self._authorized_dirs.get(thread_id, set())
                existed = any(p == resolved for (p, _w) in entries)

            with trace_span(
                "sandbox.revoke", thread_id=thread_id, path=str(path), action="revoke"
            ):
                entries = self._authorized_dirs.get(thread_id, set())
                entries = {(p, w) for (p, w) in entries if p != resolved}
                self._authorized_dirs[thread_id] = entries
        return existed

    async def clear(self, thread_id: str) -> None:
        """清空该 thread_id 的所有授权（会话结束时调用）。

        仅清理子 thread 的授权，不影响父 thread（parent_thread_id 机制）。
        """
        async with self._lock:
            self._authorized_dirs.pop(thread_id, None)
            self._temp_authorized.pop(thread_id, None)
            # DB-first：先删 DB
            if get_settings().sandbox_persistence_enabled:
                await self._store.delete_by_thread(thread_id)

    async def bootstrap_from_store(self) -> None:
        """启动时从 DB 全量加载授权到内存。失败仅 log error，不阻塞启动。

        注意：``_full_trust_threads`` 不持久化——重启后自动降级为 workspace 模式，
        这是设计意图（full_trust 是临时调试手段，不应跨重启保留）。
        """
        async with self._lock:
            try:
                loaded = await self._store.bootstrap_all()
                for thread_id, entries in loaded.items():
                    self._authorized_dirs[thread_id] = set(entries)
                logger.info(
                    "sandbox.bootstrap_loaded",
                    threads=len(loaded),
                    total_entries=sum(len(e) for e in loaded.values()),
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("sandbox.bootstrap_failed: {}", exc)

    async def list_authorized(self, thread_id: str) -> list[tuple[Path, bool]]:
        """返回 [(path, writable), ...]（按路径排序，结果确定性）。"""
        async with self._lock:
            return sorted(
                self._get_authorized_set(thread_id, "_authorized_dirs"),
                key=lambda e: str(e[0]),
            )

    async def restore(self, thread_id: str, dirs: list[str]) -> None:
        """从 checkpoint 恢复授权目录列表（dirs 为 path 字符串列表，默认 read-only）。"""
        async with self._lock:
            entries = self._authorized_dirs.setdefault(thread_id, set())
            existing = {p for (p, _w) in entries}
            for d in dirs:
                resolved = normalize_path(d)
                if resolved not in existing:
                    entries.add((resolved, False))
                    existing.add(resolved)

    async def snapshot(self, thread_id: str) -> list[str]:
        """导出授权目录为字符串列表（用于写入 checkpoint authorized_dirs 字段）。"""
        async with self._lock:
            return sorted(
                str(p) for (p, _w) in self._get_authorized_set(thread_id, "_authorized_dirs")
            )

    async def is_path_authorized(
        self,
        thread_id: str,
        path: str | Path,
        writable: bool = False,
        base: str | Path | None = None,
        parent_thread_id: str | None = None,
    ) -> bool:
        """检查路径是否已授权（用于 directory_extension 预检查，不抛异常）。

        - 系统关键目录：永远返回 False
        - full_trust 模式：永远返回 True（除系统关键目录）
        - 白名单 / _authorized_dirs / _temp_authorized：返回 True
        - 支持 ``parent_thread_id``：子 thread 未授权时查父 thread

        ``base`` 用于传入 workspace 上下文：相对路径基于 ``base`` 解析。
        """
        try:
            resolved = normalize_path(path, base=base)
        except Exception:  # noqa: BLE001
            return False
        if is_critical(resolved):
            return False
        async with self._lock:
            if thread_id in self._full_trust_threads:
                return True
            for whitelist_path in DEFAULT_WHITELIST:
                if is_under(resolved, whitelist_path):
                    return True
            for auth_path, w in self._get_authorized_set(thread_id, "_authorized_dirs"):
                if is_under(resolved, auth_path) and (not writable or w):
                    return True
            for auth_path, w in self._get_authorized_set(thread_id, "_temp_authorized"):
                if is_under(resolved, auth_path) and (not writable or w):
                    return True
            # 查父 thread 授权
            parent_tid = self._resolve_parent(thread_id, parent_thread_id)
            if parent_tid is not None and parent_tid != thread_id:
                for auth_path, w in self._get_authorized_set(parent_tid, "_authorized_dirs"):
                    if is_under(resolved, auth_path) and (not writable or w):
                        return True
                for auth_path, w in self._get_authorized_set(parent_tid, "_temp_authorized"):
                    if is_under(resolved, auth_path) and (not writable or w):
                        return True
        return False


_sandbox: SessionSandbox | None = None


def get_sandbox() -> SessionSandbox:
    """返回模块级单例 SessionSandbox。"""
    global _sandbox
    if _sandbox is None:
        _sandbox = SessionSandbox()
    return _sandbox
