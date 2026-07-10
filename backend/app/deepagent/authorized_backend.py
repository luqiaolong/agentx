"""AuthorizedLocalShellBackend: 在 fs 操作前注入 SessionSandbox 动态授权。"""

from __future__ import annotations

from deepagents.backends.protocol import (
    EditResult,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from app.deepagent.context import current_thread_id
from app.deepagent.safe_shell_backend import SafeLocalShellBackend
from app.sandbox.path_guard import PathNotAuthorized
from app.sandbox.session_sandbox import get_sandbox

__all__ = ["AuthorizedLocalShellBackend"]


class AuthorizedLocalShellBackend(SafeLocalShellBackend):
    """在 fs 操作前注入 SessionSandbox 动态授权。

    继承 ``SafeLocalShellBackend``（保留 execute 的 blocklist + 元字符过滤），
    override 6 个 fs 方法注入 thread_id 级动态授权。
    不传 ``_permissions``（绕过 0.6.12 permissions+sandbox 互斥限制）。
    """

    def _check_auth(self, path: str, write: bool) -> None:
        """从 contextvar 取 thread_id，调 SessionSandbox 同步校验。

        Raises:
            PathNotAuthorized: 路径未通过 sandbox 授权校验。
        """
        thread_id = current_thread_id.get()
        sandbox = get_sandbox()
        if write:
            sandbox.check_write_sync(thread_id, path, base=str(self.cwd))
        else:
            sandbox.check_read_sync(thread_id, path, base=str(self.cwd))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            self._check_auth(file_path, write=False)
        except PathNotAuthorized as exc:
            return ReadResult(error=str(exc))
        return super().read(file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            self._check_auth(file_path, write=True)
        except PathNotAuthorized as exc:
            return WriteResult(error=str(exc))
        return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            self._check_auth(file_path, write=True)
        except PathNotAuthorized as exc:
            return EditResult(error=str(exc))
        return super().edit(file_path, old_string, new_string, replace_all)

    def ls(self, path: str) -> LsResult:
        try:
            self._check_auth(path, write=False)
        except PathNotAuthorized as exc:
            return LsResult(error=str(exc))
        return super().ls(path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        try:
            self._check_auth(path or str(self.cwd), write=False)
        except PathNotAuthorized as exc:
            return GrepResult(error=str(exc))
        return super().grep(pattern, path, glob)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            self._check_auth(path or str(self.cwd), write=False)
        except PathNotAuthorized as exc:
            return GlobResult(error=str(exc))
        return super().glob(pattern, path)

    # execute 不 override：继承 SafeLocalShellBackend.execute
