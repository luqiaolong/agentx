"""CLI 命令过滤 + 参数脱敏。

从 ``app.tools.cli`` 提取命令黑名单 / 元字符校验逻辑，
从 ``app.deepagent.approval_runner`` 提取 ``_redact_args`` 并扩展支持 ``execute``
（deepagents LocalShellBackend 内置工具）和 ``cli_execute``（旧自研工具，
子代理路径仍使用）的 ``command`` / ``arguments`` 脱敏
（token / password / user:pass@host）。

新增 ``redact_args``：统一脱敏入口，支持 dict / list / str 输入。
"""

from __future__ import annotations

import re

__all__ = [
    "DEFAULT_BLOCKLIST",
    "FORBIDDEN_ARG_PATTERN",
    "effective_blocklist",
    "is_command_blocked",
    "has_forbidden_args",
    "redact_args",
]

# 默认命令黑名单：极度危险的命令，任何模式下都直接拒绝。
# 从 ``app.tools.cli._DEFAULT_BLOCKLIST`` 迁移，保持一致。
DEFAULT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "rm",
        "rmdir",
        "del",
        "erase",
        "unlink",
        "format",
        "mkfs",
        "dd",
        "fdisk",
        "parted",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "sudo",
        "su",
        "doas",
        "chmod",
        "chown",
        "kill",
        "killall",
        "taskkill",
        "reg",
        "regedit",
    }
)

# 禁止出现在命令参数中的 shell 元字符（一条正则覆盖所有危险字符）。
# 从 ``app.tools.cli._FORBIDDEN_ARG_PATTERN`` 迁移，保持一致。
FORBIDDEN_ARG_PATTERN: re.Pattern[str] = re.compile(r"[;&|`$<>\r\n]")

# ---- cli_execute 脱敏正则 ----

# token=xxx / password=xxx：键值对形式（不区分大小写）
_TOKEN_PATTERN = re.compile(r"(?i)token=\S+")
_PASSWORD_PATTERN = re.compile(r"(?i)password=\S+")
# user:pass@host：URL 凭证形式（非贪婪匹配 user:pass，后跟 @host）
_CREDENTIALS_PATTERN = re.compile(r"\S+?:\S+?@\S+")

_REDACTED = "***REDACTED***"


def effective_blocklist() -> frozenset[str]:
    """合并默认黑名单与用户配置黑名单。"""
    # 延迟导入：app.config → subagents → app.security → 本模块，顶层导入会成环。
    from app.config import get_settings

    cfg = get_settings().cli_tool_blocklist
    if not cfg:
        return DEFAULT_BLOCKLIST
    user_blocked = frozenset(
        cmd.strip().lower() for cmd in cfg if isinstance(cmd, str) and cmd.strip()
    )
    return DEFAULT_BLOCKLIST | user_blocked


def is_command_blocked(command: str) -> bool:
    """命令名是否在黑名单内（含用户配置合并，不区分大小写）。"""
    return command.strip().lower() in effective_blocklist()


def has_forbidden_args(value: str) -> bool:
    """检查字符串是否包含 shell 元字符。"""
    return bool(FORBIDDEN_ARG_PATTERN.search(value))


def _redact_command_string(value: str) -> str:
    """对 cli_execute 的 command 字符串做凭证脱敏。"""
    result = _TOKEN_PATTERN.sub(_REDACTED, value)
    result = _PASSWORD_PATTERN.sub(_REDACTED, result)
    result = _CREDENTIALS_PATTERN.sub(_REDACTED, result)
    return result


def _redact_cli_execute_args(args: dict) -> dict:
    """脱敏 cli_execute 的参数 dict。

    - ``command`` 字段：正则替换 token=xxx / password=xxx / user:pass@host。
    - ``arguments`` 列表：每个元素同样做凭证脱敏。
    """
    redacted = dict(args)
    if "command" in redacted and isinstance(redacted["command"], str):
        redacted["command"] = _redact_command_string(redacted["command"])
    if "arguments" in redacted and isinstance(redacted["arguments"], list):
        redacted["arguments"] = [
            _redact_command_string(arg) if isinstance(arg, str) else arg
            for arg in redacted["arguments"]
        ]
    return redacted


def _redact_fs_args(args: dict) -> dict:
    """脱敏 write_file / edit_file 的参数 dict（隐藏文件内容）。"""
    redacted = dict(args)
    if "content" in redacted:
        redacted["content"] = "<redacted>"
    if "new_text" in redacted:
        redacted["new_text"] = "<redacted>"
    if "old_text" in redacted:
        redacted["old_text"] = "<redacted>"
    return redacted


def redact_args(tool_name: str, args: dict | list | str) -> dict:
    """统一脱敏入口。

    根据工具名选择脱敏策略：
    - ``write_file`` / ``edit_file``：隐藏 ``content`` / ``new_text`` / ``old_text``。
    - ``execute`` / ``cli_execute``：对 ``command`` / ``arguments`` 做凭证脱敏
      （token=xxx / password=xxx / user:pass@host → ``***REDACTED***``）。
      ``execute`` 是 deepagents ``LocalShellBackend`` 内置工具（单 ``command`` 参数）；
      ``cli_execute`` 是旧自研工具（``command`` + ``arguments`` 列表），子代理路径仍使用。
    - 其他工具：原样返回。

    输入类型处理：
    - ``dict``：原地脱敏后返回新 dict。
    - ``list``：包装为 ``{"arguments": [...]}`` 后脱敏（适用于 cli_execute）。
    - ``str``：包装为 ``{"command": "..."}`` 后脱敏（适用于 execute / cli_execute）。
    - ``None`` / 其他：返回 ``{}``。

    Args:
        tool_name: 工具名（如 ``"write_file"`` / ``"execute"`` / ``"cli_execute"``）。
        args: 工具参数，可为 dict / list / str。

    Returns:
        脱敏后的 dict。
    """
    # 字面量集合，与 dangerous_tools.SHELL_CLI_TOOL_NAME ("execute") 和
    # tools.cli.LLM_CLI_TOOL_NAME ("cli_execute") 对应。刻意不 import 那两个
    # 常量以避免循环依赖（dangerous_tools 反向依赖 security 包）。
    _CLI_TOOL_NAMES = frozenset({"execute", "cli_execute"})

    if isinstance(args, dict):
        if tool_name in ("write_file", "edit_file"):
            return _redact_fs_args(args)
        if tool_name in _CLI_TOOL_NAMES:
            return _redact_cli_execute_args(args)
        return dict(args)

    if isinstance(args, list):
        if tool_name in _CLI_TOOL_NAMES:
            return {"arguments": [_redact_command_string(a) if isinstance(a, str) else a for a in args]}
        return {"arguments": list(args)}

    if isinstance(args, str):
        if tool_name in _CLI_TOOL_NAMES:
            return {"command": _redact_command_string(args)}
        return {"input": args}

    return {}
