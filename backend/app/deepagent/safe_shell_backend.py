r"""SafeLocalShellBackend: 继承 deepagents LocalShellBackend，添加 blocklist + 元字符过滤 + Git 写操作拦截。

deepagents 的 ``LocalShellBackend`` 提供 ``execute`` 工具用 ``subprocess.run(shell=True)``
执行命令——无 blocklist、无元字符过滤。本模块继承并 override ``execute`` 方法，
复用 ``app.security.command_filter`` 的安全层：

- **blocklist**: ``DEFAULT_BLOCKLIST``（rm/del/format/shutdown/sudo...）+ 用户配置合并
- **元字符过滤**: ``FORBIDDEN_ARG_PATTERN`` 拦截 ``; & | ` $ < >``，阻断 shell 注入
- **Git 写操作拦截**: ``is_git_write_command`` 检测 ``git commit/push/checkout...`` 等写操作，
  返回 exit_code=126 提示走审批流（Phase B.2）
- **root_dir**: 限制工作目录（由父类 ``LocalShellBackend.__init__`` 处理）
- **审批**: ``execute`` 不再属于 ``DANGEROUS_TOOLS``，其审批通过 ``directory_extension``
  机制处理（workspace 之外未授权时触发审批）

虽然 ``shell=True`` 允许管道/重定向/命令链，但元字符过滤使注入面收窄到与
``shell=False`` 相近——所有 shell 元字符在到达 ``subprocess.run`` 前被拦截。
"""

from __future__ import annotations

import os
import shlex

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from app.security.command_filter import get_forbidden_chars, has_forbidden_args, is_command_blocked, is_git_write_command
from app.security.sandbox_escalation import analyze_sandbox_failure

__all__ = ["SafeLocalShellBackend"]


# 模块级标志：控制沙箱权限升级功能是否启用（可通过环境变量或配置覆盖）
_SANDBOX_ESCALATION_ENABLED = True

# Windows 系统命令必需的环境变量白名单（始终保留，不受过滤影响）
_REQUIRED_ENV_KEYS = frozenset(
    {"PATH", "PATHEXT", "SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"}
)
# 敏感前缀：这些前缀的变量一律排除（如 AGENTX_OPENAI_API_KEY）
_SENSITIVE_ENV_PREFIXES = ("AGENTX_",)
# 敏感子串：变量名包含任一即排除（高置信度敏感标识）
_SENSITIVE_ENV_SUBSTRINGS = ("api_key", "token", "password", "secret", "credential")
# 需要两个及以上敏感子串才排除的宽松子串（避免误杀如 "keychain"、"tokenize"）
# 多元素使 loose_hits >= 2 条件可达（单元素时永远为 False，属死代码）
_SENSITIVE_LOOSE_SUBSTRINGS = ("auth", "credential", "passwd", "pwd", "apikey")


def _build_safe_env() -> dict[str, str]:
    """构建脱敏后的最小环境变量字典，供 SafeLocalShellBackend 使用。

    继承父进程 PATH / SystemRoot / WINDIR / COMSPEC 等 Windows 必需变量，
    同时过滤掉敏感凭证（AGENTX_* / token / password / key 等）。
    """
    safe: dict[str, str] = {}
    for key, value in os.environ.items():
        # 白名单变量始终保留（大小写不敏感匹配）
        if key.upper() in _REQUIRED_ENV_KEYS:
            safe[key] = value
            continue
        # 跳过敏感前缀（如 AGENTX_*）
        if any(key.upper().startswith(p) for p in _SENSITIVE_ENV_PREFIXES):
            continue
        # 跳过包含高置信度敏感子串的变量名（如 api_key / token / password / secret / credential）
        key_lower = key.lower()
        if any(sub in key_lower for sub in _SENSITIVE_ENV_SUBSTRINGS):
            continue
        # 跳过包含两个及以上宽松敏感子串的变量名（如 "auth_token" 同时含 auth + token）
        loose_hits = sum(1 for sub in _SENSITIVE_LOOSE_SUBSTRINGS if sub in key_lower)
        if loose_hits >= 2:
            continue
        # 保留其他非敏感变量
        safe[key] = value
    return safe


# 命令包装器前缀：这些命令通过 /c、-c 等 flag 执行子命令，
# 需递归解析提取实际子命令再做黑名单检查，防止绕过（如 cmd /c del file.txt）
_WRAPPER_PREFIXES: dict[str, list[str]] = {
    "cmd": ["/c", "/k", "-c"],
    "powershell": ["-command", "-c", "/c"],
    "pwsh": ["-command", "-c"],
    "sh": ["-c"],
    "bash": ["-c"],
    "python": ["-c"],
    "python3": ["-c"],
}


def _extract_cmd_name(command: str) -> str:
    """从命令字符串提取命令名（basename + 去除 Windows 扩展名）。

    用 ``shlex.split`` 解析命令，取第一个 token 的 basename 并去除扩展名。
    例如 ``C:\\Windows\\System32\\format.com`` → ``format``，``del.exe`` → ``del``。

    ``shlex.split`` 解析失败时降级为 ``command.split()[0]``。
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command.split()[0] if command else ""
    if not tokens:
        return ""
    cmd_name = os.path.basename(tokens[0])
    cmd_name = os.path.splitext(cmd_name)[0]
    return cmd_name


def _extract_inner_command(command: str) -> str:
    """递归解析包装器命令，提取实际执行的子命令。

    对 ``cmd /c``、``powershell -Command``、``python -c``、``sh -c``、``bash -c``
    等包装器，找到 flag 后的子命令并递归解析，直到不再命中包装器前缀。

    Args:
        command: 完整命令字符串。

    Returns:
        提取到的最内层子命令字符串；若不是包装器则返回原命令。
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command
    if not tokens:
        return command
    cmd = os.path.basename(tokens[0])
    cmd = os.path.splitext(cmd)[0].lower()
    if cmd in _WRAPPER_PREFIXES and len(tokens) >= 3:
        flags = _WRAPPER_PREFIXES[cmd]
        for i, t in enumerate(tokens[1:], 1):
            if t.lower() in flags and i + 1 < len(tokens):
                inner = " ".join(tokens[i + 1:])
                return _extract_inner_command(inner)
    return command


class SafeLocalShellBackend(LocalShellBackend):
    """LocalShellBackend with blocklist + metachar filtering + git write interception.

    继承 ``LocalShellBackend`` 并 override ``execute`` 方法，在调用父类执行前：
    1. 提取命令名，检查是否在 blocklist 中
    2. 检查整个命令字符串是否包含 shell 元字符
    3. 检查是否为 Git 写操作（commit/push/checkout 等）

    安全层：
    - blocklist: 复用 ``app.security.command_filter.DEFAULT_BLOCKLIST`` + 用户配置
    - 元字符过滤: 复用 ``has_forbidden_args``，拦截 ``; & | ` $ < >``
    - Git 写操作拦截: 复用 ``is_git_write_command``，拦截 ``git commit/push/...``
    - root_dir: 父类 ``LocalShellBackend`` 限制工作目录
    - 审批: execute 不再属于 DANGEROUS_TOOLS；其审批通过 directory_extension
      机制处理（workspace 之外未授权时触发审批）。
    - 环境变量: 构造函数注入脱敏后的最小环境变量集合（保留 PATH/SystemRoot 等
      Windows 系统命令必需变量，过滤 AGENTX_*/token/password/key 等敏感凭证）。

    契约：
    - ``execute`` 必须返回 ``ExecuteResponse``，下游 ``FilesystemMiddleware.async_execute``
      在 ``result.output`` / ``result.exit_code`` 上直接属性访问（参见
      ``deepagents/middleware/filesystem.py:1839``）。安全拦截分支不能返回 ``str``，
      否则触发 ``AttributeError: 'str' object has no attribute 'output'``，并导致
      ``run_agent_with_approval`` 的初始流崩溃（trace 案例 ``ace5a9740dd543bd``）。
    """

    def __init__(self, *args, **kwargs) -> None:
        """初始化时注入脱敏环境变量，避免空环境导致 Windows 系统命令找不到。

        若调用方未显式传入 timeout / max_output_bytes，则复用 settings 中
        CLI 工具的同一份配置（用户设置面板中的“CLI 工具超时”与“最大输出字符数”
        同时作用于 deepagents 内置 ``execute`` 工具）。
        """
        # 若调用方未显式传入 env，则注入脱敏后的最小环境变量
        if "env" not in kwargs:
            kwargs["env"] = _build_safe_env()
        # 未显式指定 timeout / max_output_bytes 时，从 settings 读取
        if "timeout" not in kwargs or "max_output_bytes" not in kwargs:
            # 延迟导入避免循环依赖：app.config 初始化链会间接 import security
            from app.config import get_settings

            settings = get_settings()
            kwargs.setdefault("timeout", settings.cli_tool_timeout)
            kwargs.setdefault("max_output_bytes", settings.cli_tool_max_output_chars)
        super().__init__(*args, **kwargs)

    def execute(self, command: str, **kwargs) -> ExecuteResponse:
        """执行 shell 命令，带 blocklist + 元字符过滤 + Git 写操作拦截。

        Args:
            command: 完整命令字符串（``shell=True`` 模式）。
            **kwargs: 透传给父类 ``LocalShellBackend.execute`` 的额外参数。

        Returns:
            父类或安全拦截均返回 ``ExecuteResponse``：
            - 父类返回：含 stdout/stderr/exit_code/truncated。
            - 空命令：``ExecuteResponse(output="command 不能为空", exit_code=1, truncated=False)``
            - blocklist 命中：``ExecuteResponse(output=..., exit_code=126, truncated=False)``
            - 元字符过滤命中：``ExecuteResponse(output=..., exit_code=126, truncated=False)``
            - Git 写操作命中：``ExecuteResponse(output=..., exit_code=126, truncated=False)``

        Raises:
            ValueError: 透传父类在非法 timeout 时的异常。
        """
        command = command.strip()
        if not command:
            return ExecuteResponse(
                output="command 不能为空",
                exit_code=1,
                truncated=False,
            )

        # 1. 提取命令名（处理完整路径 + Windows 扩展名，如 C:\Windows\System32\format.com → format）
        cmd_name = _extract_cmd_name(command)

        # 1.5 递归解析包装器命令（cmd /c, powershell -Command, python -c 等），
        # 提取实际子命令再做黑名单检查，防止通过包装器绕过（如 cmd /c del file.txt）
        inner_command = _extract_inner_command(command)
        inner_cmd_name = _extract_cmd_name(inner_command)

        # 2. blocklist 检查（exit_code=126 沿用 shell "command cannot execute" 语义）
        # 对原始命令名和包装器内部命令名都做检查
        if is_command_blocked(cmd_name) or is_command_blocked(inner_cmd_name):
            blocked_name = cmd_name if is_command_blocked(cmd_name) else inner_cmd_name
            return ExecuteResponse(
                output=(
                    f"命令 '{blocked_name}' 在黑名单中，禁止执行"
                    "（删除/格式化/提权等极度危险操作）"
                ),
                exit_code=126,
                truncated=False,
            )

        # 3. 元字符过滤（阻断 shell 注入：; & | ` $ < >）
        # execute 使用 shell=True，引号内字符对 shell 是字面值，因此使用引号感知模式，
        # 避免 Python 字符串字面量中的 ; 等字符被误拦截。
        if has_forbidden_args(command, respect_quotes=True):
            forbidden = get_forbidden_chars(command, respect_quotes=True)
            return ExecuteResponse(
                output=(
                    f"命令包含非法 shell 元字符: {command!r}\n"
                    f"被拦截字符: {', '.join(repr(c) for c in forbidden)}\n"
                    f"提示: 沙箱禁止管道(|)、重定向(<>)、变量($)、命令链(;&`)等元字符，"
                    f"请拆分复杂命令为多个简单命令，或使用 Python 标准库替代"
                ),
                exit_code=126,
                truncated=False,
            )

        # 4. Git 写操作拦截（commit/push/checkout/clone/pull/add/merge/rebase/reset/stash）
        # Git 写操作会改变仓库状态，需通过审批流执行。
        if is_git_write_command(command):
            return ExecuteResponse(
                output="git 写操作需审批，请通过审批流执行",
                exit_code=126,
                truncated=False,
            )

        # 5. 调用父类执行（root_dir 限制工作目录）
        # 父类 LocalShellBackend.execute 已始终返回 ExecuteResponse，无需再做包装
        result = super().execute(command, **kwargs)

        # 6. 沙箱权限升级：若执行失败且疑似沙箱限制，分析并返回带升级提示的结果
        if _SANDBOX_ESCALATION_ENABLED and result.exit_code != 0:
            analysis = analyze_sandbox_failure(command, result.exit_code, result.output)
            if analysis.is_sandbox_limit:
                # 在输出中附加沙箱升级提示，供上层审批流识别
                upgrade_hint = (
                    f"\n\n[SANDBOX_ESCALATION]"
                    f"\nreason: {analysis.reason}"
                    f"\nsuggested_action: {analysis.suggested_action}"
                )
                if analysis.suggested_path:
                    upgrade_hint += f"\nsuggested_path: {analysis.suggested_path}"
                return ExecuteResponse(
                    output=result.output + upgrade_hint,
                    exit_code=result.exit_code,
                    truncated=result.truncated,
                )

        return result
