r"""SafeLocalShellBackend: 继承 deepagents LocalShellBackend，添加 blocklist + 元字符过滤。

deepagents 的 ``LocalShellBackend`` 提供 ``execute`` 工具用 ``subprocess.run(shell=True)``
执行命令——无 blocklist、无元字符过滤。本模块继承并 override ``execute`` 方法，
复用 ``app.security.command_filter`` 的安全层：

- **blocklist**: ``DEFAULT_BLOCKLIST``（rm/del/format/shutdown/sudo...）+ 用户配置合并
- **元字符过滤**: ``FORBIDDEN_ARG_PATTERN`` 拦截 ``; & | ` $ < >``，阻断 shell 注入
- **root_dir**: 限制工作目录（由父类 ``LocalShellBackend.__init__`` 处理）
- **interrupt_on**: ``DANGEROUS_TOOLS`` 含 ``execute``，触发审批流

虽然 ``shell=True`` 允许管道/重定向/命令链，但元字符过滤使注入面收窄到与
``shell=False`` 相近——所有 shell 元字符在到达 ``subprocess.run`` 前被拦截。
"""

from __future__ import annotations

from deepagents.backends import LocalShellBackend

from app.config import get_settings
from app.security.command_filter import DEFAULT_BLOCKLIST, has_forbidden_args

__all__ = ["SafeLocalShellBackend"]


def _effective_blocklist() -> frozenset[str]:
    """合并默认黑名单与用户配置黑名单。"""
    cfg = get_settings().cli_tool_blocklist
    if not cfg:
        return DEFAULT_BLOCKLIST
    user_blocked = frozenset(
        cmd.strip().lower() for cmd in cfg if isinstance(cmd, str) and cmd.strip()
    )
    return DEFAULT_BLOCKLIST | user_blocked


def _is_command_blocked(command: str) -> bool:
    """命令名是否在黑名单内（含用户配置合并）。"""
    return command.strip().lower() in _effective_blocklist()


class SafeLocalShellBackend(LocalShellBackend):
    """LocalShellBackend with blocklist + metachar filtering.

    继承 ``LocalShellBackend`` 并 override ``execute`` 方法，在调用父类执行前：
    1. 提取命令名，检查是否在 blocklist 中
    2. 检查整个命令字符串是否包含 shell 元字符

    安全层：
    - blocklist: 复用 ``app.security.command_filter.DEFAULT_BLOCKLIST`` + 用户配置
    - 元字符过滤: 复用 ``has_forbidden_args``，拦截 ``; & | ` $ < >``
    - root_dir: 父类 ``LocalShellBackend`` 限制工作目录
    - interrupt_on: ``DANGEROUS_TOOLS`` 含 ``execute``，触发审批流
    """

    def execute(self, command: str, **kwargs) -> str:  # type: ignore[override]
        """执行 shell 命令，带 blocklist + 元字符过滤。

        Args:
            command: 完整命令字符串（``shell=True`` 模式）。
            **kwargs: 透传给父类 ``LocalShellBackend.execute`` 的额外参数。

        Returns:
            命令输出字符串（含 exit code + stdout + stderr），
            或安全拦截消息（blocklist / 元字符过滤触发时）。
        """
        command = command.strip()
        if not command:
            return "command 不能为空"

        # 1. 提取命令名（shell=True 下 command 是完整命令字符串，取第一个 token）
        cmd_name = command.split()[0] if command else ""

        # 2. blocklist 检查
        if _is_command_blocked(cmd_name):
            return f"命令 '{cmd_name}' 在黑名单中，禁止执行（删除/格式化/提权等极度危险操作）"

        # 3. 元字符过滤（阻断 shell 注入：; & | ` $ < >）
        if has_forbidden_args(command):
            return f"命令包含非法 shell 元字符: {command!r}"

        # 4. 调用父类执行（root_dir 限制工作目录）
        return super().execute(command, **kwargs)
