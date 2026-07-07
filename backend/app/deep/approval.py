"""DeepAgent 危险工具审批 + 目录扩展授权。

从 ``app.deep.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责:
- ``wait_for_approval``：单次非阻塞查询审批决策（公共 API，前端调用）
- ``_await_approval``：阻塞轮询审批决策（用于 ``run_deep_path`` 中断恢复）
- ``_make_approval_event``：构造 ``approval_request`` SSE 事件
- ``_handle_directory_extension``：处理只读 fs 工具越界目录扩展授权
- 辅助函数：``_redact_args`` / ``_extract_paths_from_tool_call`` / ``_is_read_only_fs_tool``
- 常量：``_READ_ONLY_FS_TOOLS`` / ``_APPROVAL_POLL_INTERVAL``
- 数据类：``_ExtensionResult``

审批数据流（Phase 2.1 起）:
- 决策来源：``app.approval.pop_approval`` / ``app.approval.is_aborted``
- 决策生产：``POST /api/chat/approve`` → ``app.approval.submit_approval``
- ``wait_for_approval`` 和 ``_await_approval`` 都从 ``app.approval`` 拉取，不再持有
  模块级 ``_pending_approvals`` dict。

导入方向：``agent.py`` → ``approval.py``（单向，无循环）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.observability.logger import logger
from app.utils.security import ApprovalDecision
from app.utils.sse_events import make_approval_event

__all__ = [
    "wait_for_approval",
    "_await_approval",
    "_make_approval_event",
    "_handle_directory_extension",
    "_redact_args",
    "_extract_paths_from_tool_call",
    "_is_read_only_fs_tool",
    "_READ_ONLY_FS_TOOLS",
    "_ExtensionResult",
    "_APPROVAL_POLL_INTERVAL",
]

# 审批轮询参数
_APPROVAL_POLL_INTERVAL = 0.3

# 只读 fs 工具名集合（用于 directory_extension 预检查）
_READ_ONLY_FS_TOOLS: frozenset[str] = frozenset(
    {"read_file", "list_dir", "glob_files", "grep_files", "glob", "grep"}
)


def _redact_args(tool_name: str, args: dict) -> dict:
    """对危险工具的参数做 redaction（隐藏文件内容等敏感字段）。"""
    if not isinstance(args, dict):
        return {}
    redacted = dict(args)
    # 写文件 / 编辑文件：隐藏 content / new_text
    if tool_name in ("write_file", "edit_file"):
        if "content" in redacted:
            redacted["content"] = "<redacted>"
        if "new_text" in redacted:
            redacted["new_text"] = "<redacted>"
        if "old_text" in redacted:
            redacted["old_text"] = "<redacted>"
    return redacted


def _make_approval_event(
    tool_call: dict,
    thread_id: str,
    kind: str = "dangerous_tool",
    requested_path: str | None = None,
    writable: bool = False,
) -> dict[str, str]:
    """构造 approval_request SSE 事件。

    MUST 包含 ``thread_id``：前端 ApprovalDialog 据此调
    ``POST /api/chat/approve {thread_id, approval}``，后端 ``_pending_approvals``
    按 thread_id 索引。缺失 thread_id 会导致审批提交后无法被 DeepAgent 消费，
    危险操作链路彻底断开。

    Args:
        tool_call: 工具调用 dict（含 name/args）。
        thread_id: 会话 ID。
        kind: 审批类型，"dangerous_tool"（默认）或 "directory_extension"。
        requested_path: directory_extension 时填，目标路径。
        writable: directory_extension 时填，是否需要写入。
    """
    name = tool_call.get("name", "unknown")
    args = tool_call.get("args", {})
    redacted_args = _redact_args(name, args if isinstance(args, dict) else {})

    # 生成预览描述
    if kind == "directory_extension":
        preview = f"AI 想访问目录: {requested_path}"
    elif name == "write_file":
        path = args.get("path", "?") if isinstance(args, dict) else "?"
        preview = f"将写入文件: {path}"
    elif name == "edit_file":
        path = args.get("path", "?") if isinstance(args, dict) else "?"
        preview = f"将编辑文件: {path}"
    elif name == "shell_exec":
        preview = "将执行系统命令"
    elif name == "cli_execute":
        preview = f"将执行 CLI 命令: {args.get('command')} {' '.join(args.get('arguments') or [])}"
    elif name in ("git_clone", "git_pull", "git_checkout", "git_stage", "git_commit"):
        preview = f"将执行 Git 写操作: {name}"
    else:
        preview = f"将执行工具: {name}"

    data: dict[str, Any] = {
        "thread_id": thread_id,
        "tool_name": name,
        "args": redacted_args,
        "preview": preview,
        "kind": kind,
    }
    if kind == "directory_extension":
        data["requestedPath"] = requested_path or ""
        data["writable"] = writable

    return make_approval_event(data)


def _extract_paths_from_tool_call(
    tool_call: dict, workspace_path: str | None = None
) -> list[str]:
    """从工具调用参数中提取路径字符串（用于 directory_extension / 危险工具预检查）。

    支持的工具：
    - read_file / write_file / edit_file / list_dir / grep: args["path"]
    - glob / glob_files: args["pattern"] → 取 _glob_base
    - cli_execute: args["cwd"]；未指定时若已选择 workspace，回退到 workspace_path
      作为默认工作目录，避免已授权工作区仍被误标为危险操作。
    """
    from app.tools.filesystem import _glob_base

    name = tool_call.get("name", "")
    args = tool_call.get("args", {})
    if not isinstance(args, dict):
        return []
    if name in ("read_file", "write_file", "edit_file", "list_dir", "grep", "grep_files"):
        p = args.get("path")
        return [str(p)] if p else []
    if name in ("glob", "glob_files"):
        pattern = args.get("pattern", "")
        if not pattern:
            return []
        base = _glob_base(str(pattern))
        return [base] if base else []
    if name == "cli_execute":
        p = args.get("cwd")
        if p:
            return [str(p)]
        if workspace_path:
            return [workspace_path]
        return []
    # Git 工具：repo_path/target_path 参与授权判断
    if name in (
        "git_status",
        "git_diff",
        "git_log",
        "git_branches",
        "git_pull",
        "git_checkout",
        "git_stage",
        "git_commit",
    ):
        p = args.get("repo_path")
        return [str(p)] if p else []
    if name == "git_clone":
        p = args.get("target_path")
        return [str(p)] if p else []
    return []


def _is_read_only_fs_tool(name: str) -> bool:
    """是否为只读 fs 工具（用于 directory_extension 预检查）。"""
    return name in _READ_ONLY_FS_TOOLS


async def wait_for_approval(thread_id: str, timeout: float = 0.5) -> ApprovalDecision | None:
    """轮询 ``app.approval`` 中的审批决策（单次非阻塞查询）。

    Args:
        thread_id: 会话 ID。
        timeout: 保留参数（单次查询不阻塞）。

    Returns:
        - ``ApprovalDecision``：用户已决策。
        - ``None``：尚未决定。
    """
    from app.approval import pop_approval

    return pop_approval(thread_id)


async def _await_approval(
    thread_id: str,
    poll_interval: float = 0.3,
    max_wait: float = 300.0,
) -> ApprovalDecision | None:
    """阻塞轮询直至收到审批决策或达到 max_wait。

    - 收到决策 → 返回 ``ApprovalDecision``。
    - 达到 max_wait 仍未决定 → 返回 None（调用方按"中断"处理，安全失败不放行）。
    - 检测到 abort 标志（``app.approval.is_aborted``）→ 返回 None。
    """
    import asyncio

    from app.approval import is_aborted, pop_approval

    elapsed = 0.0
    while elapsed < max_wait:
        # abort 检查
        if is_aborted(thread_id):
            return None
        decision = pop_approval(thread_id)
        if decision is not None:
            return decision
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return None


@dataclass
class _ExtensionResult:
    """``_handle_directory_extension`` 的返回值。"""

    events: list[dict[str, str]]
    denied: bool = False
    timed_out: bool = False


async def _handle_directory_extension(
    pending_calls: list[dict],
    thread_id: str,
    sandbox: Any,
) -> _ExtensionResult:
    """处理只读 fs 工具的目录越界扩展授权。

    遍历 pending_calls，对每个只读 fs 工具提取路径，检查是否已授权。
    未授权的工具调用 yield approval_request(kind=directory_extension)，等待用户决策：
    - once：``sandbox.authorize_temp`` 临时授权
    - session：``sandbox.authorize`` 持久授权
    - deny：返回 denied=True

    Args:
        pending_calls: 待执行的工具调用列表。
        thread_id: 会话 ID。
        sandbox: SessionSandbox 实例。

    Returns:
        _ExtensionResult：含 events（需 yield 的 SSE 事件）+ denied/timed_out 标志。
    """
    events: list[dict[str, str]] = []
    for tc in pending_calls:
        name = tc.get("name", "")
        if not _is_read_only_fs_tool(name):
            continue
        paths = _extract_paths_from_tool_call(tc)
        if not paths:
            continue
        for path in paths:
            if sandbox.is_path_authorized(thread_id, path, writable=False):
                continue
            # 越界 → 弹扩展授权
            events.append(
                _make_approval_event(
                    tc,
                    thread_id,
                    kind="directory_extension",
                    requested_path=path,
                    writable=False,
                )
            )
            decision = await _await_approval(
                thread_id,
                poll_interval=_APPROVAL_POLL_INTERVAL,
                max_wait=float("inf")
                if get_settings().approval_max_wait == 0
                else get_settings().approval_max_wait,
            )
            if decision is None:
                return _ExtensionResult(events=events, timed_out=True)
            if decision.decision == "deny" or not decision.approved:
                return _ExtensionResult(events=events, denied=True)
            if decision.decision == "once":
                try:
                    sandbox.authorize_temp(thread_id, path, writable=False)
                except ValueError as exc:
                    logger.warning("authorize_temp failed", path=path, error=str(exc))
                    return _ExtensionResult(events=events, denied=True)
            elif decision.decision == "session":
                try:
                    sandbox.authorize(thread_id, path, writable=False)
                except ValueError as exc:
                    logger.warning("authorize session failed", path=path, error=str(exc))
                    return _ExtensionResult(events=events, denied=True)
            # approve（旧 dangerous_tool 决策类型）不应当出现在 directory_extension，
            # 防御性按 once 处理
            elif decision.decision == "approve":
                try:
                    sandbox.authorize_temp(thread_id, path, writable=False)
                except ValueError:
                    pass
    return _ExtensionResult(events=events)
