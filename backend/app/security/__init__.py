"""安全策略与审批包。

聚合导出：
- 危险工具分类：``DANGEROUS_TOOLS`` / ``FORBIDDEN_SUBAGENT_TOOLS`` / ``compute_runtime_dangerous``
- 命令过滤与脱敏：``DEFAULT_BLOCKLIST`` / ``FORBIDDEN_ARG_PATTERN`` / ``effective_blocklist``
  / ``is_command_blocked`` / ``has_forbidden_args`` / ``is_git_write_command`` / ``redact_args``
- 审批决策与状态：``ApprovalDecision`` / ``ApprovalResult`` / ``submit_approval`` /
  ``pop_approval`` / ``set_abort`` / ``is_aborted`` / ``clear_abort`` / ``wait_for_abort`` /
  ``set_pause`` / ``clear_pause`` / ``wait_for_resume`` / ``start_reaper``

与 ``app.approval`` / ``app.deepagent.tool_assembly`` / ``app.tools.cli`` / ``app.config.subagents``
平行存在，Phase 5 统一迁移 import 后旧定义可删除。
"""

from app.security.sandbox_escalation import (
    SandboxFailureAnalysis,
    analyze_sandbox_failure,
)
from app.security.approval import (
    ApprovalDecision,
    ApprovalResult,
    clear_abort,
    clear_pause,
    get_abort_event,
    get_pause_event,
    is_aborted,
    is_paused,
    pop_approval,
    set_abort,
    set_pause,
    start_reaper,
    submit_approval,
    wait_for_abort,
    wait_for_resume,
)
from app.security.command_filter import (
    DEFAULT_BLOCKLIST,
    FORBIDDEN_ARG_PATTERN,
    effective_blocklist,
    has_forbidden_args,
    is_command_blocked,
    is_git_write_command,
    redact_args,
)
from app.security.dangerous_tools import (
    DANGEROUS_TOOLS,
    FORBIDDEN_SUBAGENT_TOOLS,
    compute_runtime_dangerous,
)

__all__ = [
    # dangerous_tools
    "DANGEROUS_TOOLS",
    "FORBIDDEN_SUBAGENT_TOOLS",
    "compute_runtime_dangerous",
    # command_filter
    "DEFAULT_BLOCKLIST",
    "FORBIDDEN_ARG_PATTERN",
    "effective_blocklist",
    "is_command_blocked",
    "has_forbidden_args",
    "is_git_write_command",
    "redact_args",
    # sandbox_escalation
    "SandboxFailureAnalysis",
    "analyze_sandbox_failure",
    # approval
    "ApprovalDecision",
    "ApprovalResult",
    "submit_approval",
    "pop_approval",
    "set_abort",
    "is_aborted",
    "clear_abort",
    "wait_for_abort",
    "get_abort_event",
    "set_pause",
    "clear_pause",
    "is_paused",
    "get_pause_event",
    "wait_for_resume",
    "start_reaper",
]
