"""沙箱路径授权管理包。

聚合导出：
- ``SessionSandbox`` / ``get_sandbox``：会话级授权状态（async + Lock）
- ``PathNotAuthorized``：路径未授权异常
- ``SandboxStore`` / ``get_sandbox_store`` / ``SandboxEntry``：SQLite 持久化
- ``AuthorizeRequest`` / ``RevokeRequest``：API 请求体
"""

from __future__ import annotations

from app.sandbox.path_guard import (
    CRITICAL_DIRS,
    DEFAULT_WHITELIST,
    PathNotAuthorized,
    is_critical,
    is_under,
    normalize_path,
)
from app.sandbox.schemas import AuthorizeRequest, RevokeRequest
from app.sandbox.session_sandbox import SessionSandbox, get_sandbox
from app.sandbox.store import SandboxEntry, SandboxStore, get_sandbox_store

__all__ = [
    # path_guard
    "PathNotAuthorized",
    "normalize_path",
    "is_under",
    "is_critical",
    "CRITICAL_DIRS",
    "DEFAULT_WHITELIST",
    # store
    "SandboxStore",
    "get_sandbox_store",
    "SandboxEntry",
    # session_sandbox
    "SessionSandbox",
    "get_sandbox",
    # schemas
    "AuthorizeRequest",
    "RevokeRequest",
]
