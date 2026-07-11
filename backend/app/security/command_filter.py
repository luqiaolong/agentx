"""CLI command filter + argument redaction.

Migrated from ``app.tools.cli`` for command blocklist / metachar validation;
extracted ``_redact_args`` from ``app.deepagent.approval_runner`` and extended it
to cover the ``command`` / ``arguments`` redaction of both ``execute``
(deepagents' built-in ``LocalShellBackend`` tool) and ``cli_execute``
(legacy self-built tool, still used in subagent paths).

Adds ``redact_args``: a unified redaction entry point that supports
``dict`` / ``list`` / ``str`` input.

Adds ``is_git_write_command``: detects whether a command is a Git write
operation (``commit`` / ``push`` / ``checkout`` / etc.), consumed by
``SafeLocalShellBackend.execute`` to intercept Git writes and force them
through the approval flow.

O4 improvement (scratch-aware command filter):
    Adds ``is_destructive_target_in_scratch`` so that ``rm`` / ``del`` /
    ``unlink`` / etc. targeting only the scratch directory
    (``data/workspace/.scratch``) are allowed even though those commands
    are on the default blocklist. System paths remain blocked; ``rm -rf /``
    and ``rm -rf ~`` are still rejected because the target does not live
    under the scratch directory.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

__all__ = [
    "DEFAULT_BLOCKLIST",
    "FORBIDDEN_ARG_PATTERN",
    "effective_blocklist",
    "is_command_blocked",
    "has_forbidden_args",
    "get_forbidden_chars",
    "is_git_write_command",
    "is_destructive_target_in_scratch",
    "redact_args",
]

_DESTRUCTIVE_FILE_CMDS: frozenset[str] = frozenset(
    {"rm", "rmdir", "del", "erase", "unlink"}
)

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

# 与 FORBIDDEN_ARG_PATTERN 对应的字符集合，用于引号感知模式的逐字符检查。
FORBIDDEN_CHAR_SET: frozenset[str] = frozenset(";&|`$<>\r\n")

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


def has_forbidden_args(value: str, respect_quotes: bool = False) -> bool:
    """检查字符串是否包含 shell 元字符。

    Args:
        value: 待检查字符串。
        respect_quotes: 为 True 时，忽略单引号包裹区域内的字符，并在双引号
            包裹区域内仅检查 ``$`` 与反引号（其它元字符在双引号中对 shell
            是字面值）。用于 ``shell=True`` 的 ``execute`` 工具，避免
            ``python -c "print('a;b;c')"`` 这类命令被误拦截。
    """
    if not respect_quotes:
        return bool(FORBIDDEN_ARG_PATTERN.search(value))
    return _has_forbidden_respecting_quotes(value)


def _has_forbidden_respecting_quotes(value: str) -> bool:
    """引号感知模式：仅检查未加引号区域，以及双引号内的 ``$`` / 反引号。"""
    in_double = False
    in_single = False
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "\\" and i + 1 < n:
            # 反斜杠转义：跳过被转义字符
            i += 2
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if in_single:
            # 单引号内全部字面化
            pass
        elif in_double:
            # 双引号内仅 $ 与反引号仍可能被 shell 解释
            if ch in ("$", "`"):
                return True
        else:
            if ch in FORBIDDEN_CHAR_SET:
                return True
        i += 1

    # 未闭合引号：安全降级为严格模式
    if in_double or in_single:
        return bool(FORBIDDEN_ARG_PATTERN.search(value))
    return False


def get_forbidden_chars(value: str, respect_quotes: bool = False) -> list[str]:
    """提取字符串中所有被禁止的 shell 元字符（去重，保持出现顺序）。

    Args:
        value: 待检查字符串。
        respect_quotes: 同 ``has_forbidden_args``。
    """
    seen: set[str] = set()
    chars: list[str] = []

    if not respect_quotes:
        for m in FORBIDDEN_ARG_PATTERN.finditer(value):
            ch = m.group(0)
            if ch not in seen:
                seen.add(ch)
                chars.append(ch)
        return chars

    # 引号感知模式
    in_double = False
    in_single = False
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if in_single:
            pass
        elif in_double:
            if ch in ("$", "`") and ch not in seen:
                seen.add(ch)
                chars.append(ch)
        else:
            if ch in FORBIDDEN_CHAR_SET and ch not in seen:
                seen.add(ch)
                chars.append(ch)
        i += 1

    # 未闭合引号：安全降级为严格模式
    if in_double or in_single:
        return get_forbidden_chars(value, respect_quotes=False)
    return chars


# Git 写操作子命令集合：这些子命令会改变仓库状态（commit/push/checkout 等），
# 在 ``SafeLocalShellBackend.execute`` 中被拦截，强制走审批流。
# 只读子命令（status/diff/log/branch/show）不在其中，可正常执行。
_GIT_WRITE_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "commit",
        "push",
        "checkout",
        "clone",
        "pull",
        "add",
        "merge",
        "rebase",
        "reset",
        "stash",
    }
)


def is_git_write_command(command: str) -> bool:
    """检测命令是否为 Git 写操作。

    用 ``shlex.split`` 解析命令，检查第一个 token 是否为 ``git``、
    第二个 token 是否在 ``_GIT_WRITE_SUBCOMMANDS`` 中。

    ``shlex.split`` 解析失败（如不匹配的引号）时返回 True，安全降级为拦截
    （无法确定命令是否安全时，宁可误拦截也不放行）。

    Args:
        command: 完整命令字符串。

    Returns:
        True 表示该命令是 Git 写操作（或无法解析），应被拦截；False 表示不是。
    """
    if not command or not command.strip():
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        # shlex 解析失败（如不匹配的引号）→ 安全降级为拦截
        return True
    if len(tokens) < 2:
        return False
    return tokens[0] == "git" and tokens[1] in _GIT_WRITE_SUBCOMMANDS


def _scratch_root() -> Path | None:
    """Return the scratch directory, or ``None`` if unavailable.

    Imported lazily to avoid an import cycle (path_guard imports from
    ``app.config`` which can transitively reach this module during
    startup).
    """
    try:
        from app.sandbox.path_guard import SCRATCH_DIR
    except Exception:
        return None
    if SCRATCH_DIR is None:
        return None
    return SCRATCH_DIR


def _tokenize_paths(command: str) -> list[str]:
    """Split ``command`` into tokens while preserving backslashes in paths.

    ``shlex.split(posix=True)`` treats backslashes as POSIX escape characters
    so ``D:\\foo`` becomes ``D:foo`` and the scratch check loses the path.
    We pre-escape any backslash that is followed by a non-quote / non-escape
    character, then run ``shlex.split``, then strip our escapes back out.
    The set of ``\\X`` sequences shlex actually consumes as escapes is the
    union of ``\\\\`` and ``\\<quote>``; everything else (e.g. ``\\j``,
    ``\\s``) is NOT a valid POSIX escape so we can safely re-introduce
    ``\\`` before re-splitting.
    """
    placeholder = "\x00BSLASH\x00"
    safe = re.sub(r"\\(?=[^\"'\\])", placeholder, command)
    try:
        tokens = shlex.split(safe)
    except ValueError:
        return []
    return [t.replace(placeholder, "\\") for t in tokens]


def _all_targets_in_scratch(tokens: list[str], scratch: Path) -> bool:
    """Return True iff every path-like token resolves under ``scratch``.

    Pure flags (``-r`` / ``-rf`` / ``/q`` / ``/f`` ...) are ignored.
    Targets that don't look like paths (e.g. ``--interactive``) are also
    skipped. If at least one target is path-like and ALL of them live under
    ``scratch``, the command is safe to allow.

    Relative paths are resolved against ``scratch.parent`` (i.e.
    ``WORKSPACE_DIR``) and the current working directory. The scratch
    directory itself is intentionally NOT used as a base, so a token like
    ``data/workspace/foo.txt`` cannot be smuggled in via
    ``scratch / "data/workspace/foo.txt"``.

    Home-directory expansion (``~`` / ``~user``) is applied via
    ``os.path.expanduser`` so ``rm -rf ~`` does not silently match
    ``scratch``.
    """
    saw_path = False
    cwd = Path.cwd()
    candidates = [scratch.parent, cwd]
    for tok in tokens:
        if not tok or tok.startswith("-"):
            continue
        expanded = os.path.expanduser(tok)
        resolved = None
        for base in candidates:
            try:
                p = (base / expanded).resolve()
            except OSError:
                continue
            if scratch in p.parents or p == scratch:
                resolved = p
                break
        if resolved is None:
            return False
        saw_path = True
    return saw_path


def is_destructive_target_in_scratch(command: str) -> bool:
    """Return True iff ``command`` is a destructive file op targeting only ``SCRATCH_DIR``.

    Designed to be called by ``SafeLocalShellBackend.execute`` when the
    command hits the default blocklist. If the destructively-flagged
    command (``rm`` / ``rmdir`` / ``del`` / ``erase`` / ``unlink``) only
    touches paths inside the scratch directory, it is allowed through;
    anything outside (system paths, ``/``, ``~``, etc.) remains blocked.
    """
    if not command or not command.strip():
        return False
    scratch = _scratch_root()
    if scratch is None:
        return False
    tokens = _tokenize_paths(command)
    if len(tokens) < 2:
        return False
    if tokens[0].lower() not in _DESTRUCTIVE_FILE_CMDS:
        return False
    return _all_targets_in_scratch(tokens[1:], scratch)


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
    """脱敏 write_file / edit_file 的参数 dict（隐藏文件内容）。

    内置 fs 工具参数名（deepagents FilesystemMiddleware）：
    - write_file: ``file_path`` / ``content``
    - edit_file: ``file_path`` / ``old_string`` / ``new_string`` / ``replace_all``
    """
    redacted = dict(args)
    if "content" in redacted:
        redacted["content"] = "<redacted>"
    if "new_string" in redacted:
        redacted["new_string"] = "<redacted>"
    if "old_string" in redacted:
        redacted["old_string"] = "<redacted>"
    return redacted


def redact_args(tool_name: str, args: dict | list | str) -> dict:
    """统一脱敏入口。

    根据工具名选择脱敏策略：
    - ``write_file`` / ``edit_file``：隐藏 ``content`` / ``new_string`` / ``old_string``。
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
