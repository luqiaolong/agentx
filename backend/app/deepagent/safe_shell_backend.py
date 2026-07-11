r"""SafeLocalShellBackend: 继承 deepagents LocalShellBackend，添加统一风险策略评估。

deepagents 的 ``LocalShellBackend`` 提供 ``execute`` 工具用 ``subprocess.run(shell=True)``
执行命令——无 blocklist、无元字符过滤。本模块继承并 override ``execute`` 方法，
将安全检查统一委托给 :class:`RiskClassifier`：

- **blocklist**: ``BlocklistPolicy`` 递归解析包装器（cmd /c、powershell -Command）
- **元字符过滤**: ``MetacharPolicy`` shell_string 模式，PowerShell 引号感知
- **Git 写操作拦截**: ``GitWritePolicy`` 检测 git commit/push/checkout...
- **路径策略**: ``PathPolicy`` 引号感知路径检测
- **scratch 豁免**: 破坏性命令作用于 ``SCRATCH_DIR`` 时跳过 blocklist
- **沙箱升级**: ``analyze_sandbox_failure`` 在命令失败时附加升级提示
- **root_dir**: 限制工作目录（由父类 ``LocalShellBackend.__init__`` 处理）
- **审批**: ``execute`` 不再属于 ``DANGEROUS_TOOLS``，其审批通过 ``directory_extension``
  机制处理（workspace 之外未授权时触发审批）
"""

from __future__ import annotations

import os

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from app.deepagent.context import current_thread_id
from app.sandbox.session_sandbox import get_sandbox
from app.security.command_filter import is_destructive_target_in_scratch
from app.security.context import build_shell_backend_context
from app.security.reporter import aggregate as aggregate_risk
from app.security.risk import RiskClassifier, RiskLevel
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


class SafeLocalShellBackend(LocalShellBackend):
    """LocalShellBackend with unified risk classifier (blocklist + metachar + git + path).

    继承 ``LocalShellBackend`` 并 override ``execute`` 方法，在调用父类执行前
    通过 :class:`RiskClassifier` 统一评估风险，HIGH 及以上命中返回 exit_code=126。

    安全层（全部委托给 RiskClassifier）：
    - blocklist: ``BlocklistPolicy`` 递归解析包装器
    - 元字符过滤: ``MetacharPolicy`` shell_string 模式（PowerShell 引号感知）
    - Git 写操作: ``GitWritePolicy`` 检测 commit/push/checkout...
    - 路径策略: ``PathPolicy`` 引号感知路径检测
    - scratch 豁免: 破坏性命令作用于 SCRATCH_DIR 时跳过 blocklist
    - root_dir: 父类 ``LocalShellBackend`` 限制工作目录
    - 环境变量: 构造函数注入脱敏后的最小环境变量集合

    契约：
    - ``execute`` 必须返回 ``ExecuteResponse``，下游 ``FilesystemMiddleware.async_execute``
      在 ``result.output`` / ``result.exit_code`` 上直接属性访问。
    """

    def __init__(self, *args, **kwargs) -> None:
        """初始化时注入脱敏环境变量，避免空环境导致 Windows 系统命令找不到。

        若调用方未显式传入 timeout / max_output_bytes，则复用 settings 中
        CLI 工具的同一份配置（用户设置面板中的"CLI 工具超时"与"最大输出字符数"
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
        """执行 shell 命令，通过 RiskClassifier 统一风险评估。

        Args:
            command: 完整命令字符串（``shell=True`` 模式）。
            **kwargs: 透传给父类 ``LocalShellBackend.execute`` 的额外参数。

        Returns:
            ``ExecuteResponse``：父类返回含 stdout/stderr/exit_code/truncated；
            安全拦截返回 exit_code=126 + 聚合错误信息。

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

        # 统一风险策略评估：从 contextvar 取 thread_id 构建 ExecutionContext。
        # thread_id 为空时（非 agent 上下文）传 None，策略仍评估但无 sandbox_mode aliasing。
        thread_id = current_thread_id.get()
        ctx = None
        if thread_id:
            ctx = build_shell_backend_context(thread_id, get_sandbox())
        assessments = RiskClassifier.default().assess(command, ctx)

        # scratch 目录豁免：破坏性命令作用于 SCRATCH_DIR 时跳过 blocklist 命中。
        # 允许 agent 清理自己创建的临时文件，系统路径仍被拦截。
        if assessments and is_destructive_target_in_scratch(command):
            assessments = [a for a in assessments if a.policy_name != "blocklist"]

        if assessments:
            # safe_shell_backend 不走审批流（execute 不在 DANGEROUS_TOOLS 中），
            # 因此拦截阈值降到 MEDIUM（含 git 写 / 元字符 / 路径），由 RiskClassifier
            # 统一把关。cli_execute 走审批流，阈值仍为 HIGH。
            medium_or_above = [a for a in assessments if a.level >= RiskLevel.MEDIUM]
            if medium_or_above:
                return ExecuteResponse(
                    output=aggregate_risk(assessments, command, ctx),
                    exit_code=126,
                    truncated=False,
                )

        # 调用父类执行（root_dir 限制工作目录）
        result = super().execute(command, **kwargs)

        # 沙箱权限升级：若执行失败且疑似沙箱限制，分析并返回带升级提示的结果
        if _SANDBOX_ESCALATION_ENABLED and result.exit_code != 0:
            analysis = analyze_sandbox_failure(command, result.exit_code, result.output)
            if analysis.is_sandbox_limit:
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
