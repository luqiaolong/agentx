"""CLI 工具：在授权目录下执行系统命令。

安全模型（与前端 PermissionToggle 对齐）：
- ``workspace`` 模式：命令只能在已授权目录（cwd）下执行，且 ``cli_execute``
  作为危险工具走 DeepAgent ``interrupt_on`` 审批。
- ``full_trust`` 模式：跳过路径授权检查，但仍受以下约束保护：
  - 命令黑名单（删除/格式化/关机等极度危险命令直接拒绝）
  - 沙箱模式 off 时跳过全部策略检查
  - 拒绝系统关键目录
  - 超时与输出长度限制

子代理也可使用此工具（走各自审批流）。

风险策略统一委托给 :class:`RiskClassifier`：argv 模式下元字符策略自动
短路（参数不经 shell 解析），黑名单 / git 写 / 路径策略仍生效。
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
import subprocess
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.sandbox import PathNotAuthorized, get_sandbox, is_critical
from app.sandbox.path_guard import normalize_path
from app.security.context import build_cli_execute_context
from app.security.reporter import aggregate as aggregate_risk
from app.security.risk import RiskClassifier, RiskLevel

__all__ = ["LLM_CLI_TOOL_NAME", "cli_execute"]

LLM_CLI_TOOL_NAME: str = "cli_execute"

# 系统关键目录校验委托给 app.sandbox.is_critical()（与 session_sandbox.py 统一），
# 不再维护第二套正则模式——此前 cli.py 的 Windows 正则 `^C:\\\\Windows` 因
# 反斜杠转义错误（匹配 2 个字面反斜杠，实际路径只有 1 个）导致形同虚设。


def _is_critical_dir(path: Path) -> bool:
    """路径是否为系统关键目录。委托给 app.sandbox.is_critical() 统一实现。"""
    return is_critical(path)


def _resolve_cwd(cwd: str | None, workspace_path: str | None = None) -> Path:
    """解析 cwd；为空时回退到 workspace_path，再空则使用 PROJECT_ROOT。"""
    if not cwd:
        if workspace_path:
            return normalize_path(workspace_path)
        return PROJECT_ROOT
    if workspace_path:
        return normalize_path(cwd, base=workspace_path)
    return normalize_path(cwd)


def _format_output(exit_code: int, stdout: str, stderr: str, max_chars: int) -> str:
    """格式化命令输出，超长时智能截取（保留首尾，中间折叠）。"""
    combined = f"[exit={exit_code}]\n"
    if stdout.strip():
        combined += f"--- stdout ---\n{stdout}\n"
    if stderr.strip():
        combined += f"--- stderr ---\n{stderr}\n"
    text = combined.rstrip()
    if len(text) <= max_chars:
        return text

    # 智能截取：保留开头和结尾，中间折叠
    # 预留折叠提示的字符空间
    ellipsis = f"\n...（共 {len(text)} 字符，中间 {len(text) - max_chars} 字符已折叠）...\n"
    reserve = max_chars - len(ellipsis)
    if reserve < 200:
        # 空间不足，直接截断尾部
        return text[:max_chars] + f"\n...（输出已截断至 {max_chars} 字符）"

    head_len = reserve // 2
    tail_len = reserve - head_len
    return text[:head_len] + ellipsis + text[-tail_len:]


async def _check_cwd_authorization(thread_id: str, cwd: Path) -> str | None:
    """校验 cwd 是否可执行命令（workspace 模式）。

    full_trust 模式下 sandbox.check_write 内部跳过检查，直接通过。
    """
    sandbox = get_sandbox()
    try:
        await sandbox.check_write(thread_id, str(cwd))
    except PathNotAuthorized as exc:
        msg = str(exc)
        matched_readonly = "仅授权读取" in msg
        logger.warning(
            "cli_execute.cwd_denied",
            thread_id=thread_id,
            cwd=str(cwd),
            reason="readonly" if matched_readonly else "unauthorized",
        )
        if matched_readonly:
            return f"目录 {cwd} 仅授权读取，CLI 执行需要写入权限，请重新授权或切换到可写 workspace"
        return f"目录 {cwd} 未授权，请通过 PermissionToggle 选择 workspace 并授权目录"
    return None


async def cli_execute(
    thread_id: str,
    command: str,
    arguments: list[str] | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    workspace_path: str | None = None,
) -> str:
    """在受限环境下执行一个系统命令。

    Args:
        thread_id: 会话 ID，用于沙箱授权校验。
        command: 命令名（如 ``git``、``npm``、``python``）。
        arguments: 命令参数列表（每个参数独立，不经过 shell 解析）。
        cwd: 工作目录；为空时回退到 workspace_path，再空则使用项目根目录。
        timeout: 超时秒数；为空时使用配置 ``cli_tool_timeout``。
        workspace_path: 当前会话绑定的 workspace 绝对路径，作为相对路径解析基准。

    Returns:
        包含 exit code、stdout、stderr 的字符串；出错时返回错误说明。
    """
    settings = get_settings()
    if not settings.cli_tool_enabled:
        return "CLI 工具未启用，请在设置面板开启 cli_execute"

    command = command.strip()
    if not command:
        return "command 不能为空"

    arguments = list(arguments) if arguments else []

    # 统一风险策略评估：argv 模式下元字符策略短路，黑名单 / git 写 / 路径仍生效。
    # 沙箱 off 模式在 classifier.assess 内部短路（is_path_unrestricted → 空列表）。
    sandbox = get_sandbox()
    ctx = build_cli_execute_context(thread_id, sandbox)
    # 拼接 command + arguments 成单字符串供策略扫描（PathPolicy 需要扫描参数中的路径）。
    command_str = command if not arguments else f"{command} {shlex.join(arguments)}"
    assessments = RiskClassifier.default().assess(command_str, ctx)
    if assessments:
        high_or_above = [a for a in assessments if a.level >= RiskLevel.HIGH]
        if high_or_above:
            logger.warning(
                "cli_execute.blocked",
                thread_id=thread_id,
                command=command,
                reason="risk_classifier",
                levels=[a.level.name for a in assessments],
            )
            return aggregate_risk(assessments, command_str, ctx)

    resolved_cwd = _resolve_cwd(cwd, workspace_path)
    if _is_critical_dir(resolved_cwd):
        return f"拒绝在系统关键目录执行: {resolved_cwd}"

    if err := await _check_cwd_authorization(thread_id, resolved_cwd):
        return err

    exe = shutil.which(command)
    if not exe:
        return f"系统中未找到命令: {command}"

    effective_timeout = timeout or settings.cli_tool_timeout or 300
    max_output = settings.cli_tool_max_output_chars or 50000

    logger.info(
        "cli_execute.start",
        thread_id=thread_id,
        command=command,
        arguments=arguments,
        cwd=str(resolved_cwd),
        timeout=effective_timeout,
    )

    def _run() -> tuple[int, str, str]:
        try:
            result = subprocess.run(
                [exe, *arguments],
                cwd=resolved_cwd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                encoding="utf-8",
                errors="replace",
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"命令执行超时（>{effective_timeout}s）"
        except OSError as exc:
            return -1, "", f"命令启动失败: {exc}"

    # trace_span 包 subprocess.run：单独记录子进程执行耗时（不包含前置检查）。
    # 进入 span 时把关键 metadata 注入，span dict 可在 with 块内被修改（追加
    # exit_code / output_len）。loguru patcher 会自动从 ContextVar 注入 trace_id
    # 到 logger，stderr / 文件 sink 日志行尾带 ``| trace=xxxxxxxxxxxxxxxx``。
    with trace_span(
        "cli_execute.run",
        thread_id=thread_id,
        command=command,
        cwd=str(resolved_cwd),
        timeout=effective_timeout,
    ) as span:
        exit_code, stdout, stderr = await asyncio.to_thread(_run)
        span["exit_code"] = exit_code
        span["stdout_len"] = len(stdout)
        span["stderr_len"] = len(stderr)
        logger.info(
            "cli_execute.done",
            thread_id=thread_id,
            command=command,
            exit_code=exit_code,
        )
    return _format_output(exit_code, stdout, stderr, max_output)
