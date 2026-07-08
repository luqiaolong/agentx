"""公共审批执行流：提取 Supervisor / Coding Expert 的 interrupt_before 循环。

从 ``work_supervisor.py`` / ``coding.py`` / ``deep/agent.py`` 提取公共的
``interrupt_before=["tools"]`` 审批循环逻辑，集中修复 6 个 bug：

1. **路径基准不一致**：所有 ``is_path_authorized`` 调用传 ``base=workspace_path``，
   使审批层与执行层路径基准一致。
2. **Team 模式 thread_id 不继承**：``run_approval_loop`` 接受 ``parent_thread_id``，
   授权检查时同时查询子 / 父 thread 的授权。
3. **cli_execute 无 cwd 自动免审批**：``cli_execute`` 始终需要审批（让用户审查命令内容），
   不再因 workspace 已授权就自动放行。
4. **directory_extension 工作区免审批死代码**：``_handle_directory_extension``
   传 ``base=workspace_path`` + ``parent_thread_id``，并修正
   ``all_under_workspace`` 用 ``writable=False`` 检查（只读工具仅需读权限）。
5. **full_trust 仍弹审批框**：``full_trust`` 模式下跳过所有审批（含
   ``directory_extension`` 预检查），直接恢复执行。
6. **approval_max_wait=0 无限阻塞**：``approval_max_wait=0`` 时上限改为 3600s
   而非 ``float("inf")``。

审批状态 API（``pop_approval`` / ``is_aborted`` / ``is_paused`` / ``get_pause_event``）
使用 ``app.security.approval``，与 ``/api/chat/approve`` /
``/api/chat/abort`` / ``/api/chat/pause`` 端点共用同一审批状态模块。

沙箱操作使用新的 async ``app.sandbox.SessionSandbox``。参数脱敏使用新的
``app.security.command_filter.redact_args``（支持 cli_execute 命令脱敏）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from app.security.approval import (
    get_pause_event,
    is_aborted,
    is_paused,
    pop_approval,
)
from app.config import get_settings
from app.observability.logger import logger
from app.sandbox import SessionSandbox
from app.security.command_filter import redact_args
from app.utils.sse_events import make_approval_event, make_sse_event

__all__ = [
    "run_approval_loop",
    "_extract_paths_from_tool_call",
    "_make_approval_event",
    "_await_approval",
    "_handle_directory_extension",
    "_ExtensionResult",
    "_READ_ONLY_FS_TOOLS",
    "_APPROVAL_POLL_INTERVAL",
    "_ABSOLUTE_MAX_WAIT",
]

# 审批轮询间隔（秒）
_APPROVAL_POLL_INTERVAL = 0.3

# approval_max_wait=0 时的绝对上限（秒），避免无限阻塞
_ABSOLUTE_MAX_WAIT = 3600.0

# 只读 fs 工具名集合（用于 directory_extension 预检查）
_READ_ONLY_FS_TOOLS: frozenset[str] = frozenset(
    {"read_file", "list_dir", "glob_files", "grep_files", "glob", "grep"}
)

# 只读工具集合（用于循环保护检测，与 coding.py 保持一致）
_READONLY_TOOLS: frozenset[str] = frozenset(
    {"read_file", "list_dir", "glob", "glob_files", "grep", "grep_files"}
)


# ============================================================
# 辅助函数（从 deep/approval.py 迁移 + bug 修复）
# ============================================================


def _extract_paths_from_tool_call(
    tool_call: dict, workspace_path: str | None = None
) -> list[str]:
    """从工具调用参数中提取路径字符串（用于 directory_extension / 危险工具预检查）。

    支持的工具：
    - read_file / write_file / edit_file / list_dir / grep: args["path"]
    - glob / glob_files: args["pattern"] → 取 _glob_base
    - cli_execute: args["cwd"]；未指定时若已选择 workspace，回退到 workspace_path
      作为默认工作目录，避免已授权工作区仍被误标为危险操作。

    注意：``cli_execute`` 的路径提取仅用于 directory_extension 预检查；
    bug #3 修复后 ``cli_execute`` 在 dangerous_tool 判定时始终需要审批，
    不因路径已授权而自动放行。
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


def _make_approval_event(
    tool_call: dict,
    thread_id: str,
    kind: str = "dangerous_tool",
    requested_path: str | None = None,
    writable: bool = False,
) -> dict[str, str]:
    """构造 approval_request SSE 事件。

    MUST 包含 ``thread_id``：前端 ApprovalDialog 据此调
    ``POST /api/chat/approve {thread_id, approval}``。

    使用 ``app.security.command_filter.redact_args`` 做参数脱敏（支持
    write_file / edit_file 内容隐藏 + cli_execute 命令凭证脱敏）。
    """
    name = tool_call.get("name", "unknown")
    args = tool_call.get("args", {})
    redacted_args = redact_args(name, args if isinstance(args, dict) else {})

    # 生成预览描述
    if kind == "directory_extension":
        preview = f"AI 想访问目录: {requested_path}"
    elif name == "write_file":
        path = args.get("path", "?") if isinstance(args, dict) else "?"
        preview = f"将写入文件: {path}"
    elif name == "edit_file":
        path = args.get("path", "?") if isinstance(args, dict) else "?"
        preview = f"将编辑文件: {path}"
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


async def _await_approval(
    thread_id: str,
    poll_interval: float = _APPROVAL_POLL_INTERVAL,
    max_wait: float = 300.0,
) -> Any:
    """阻塞轮询直至收到审批决策或达到 max_wait。

    - 收到决策 → 返回 ``ApprovalDecision``。
    - 达到 max_wait 仍未决定 → 返回 None（调用方按"中断"处理，安全失败不放行）。
    - 检测到 abort 标志 → 返回 None。

    bug #6 修复：``max_wait`` 不再接受 ``float("inf")``；调用方应传入有限值。
    ``approval_max_wait=0`` 时由调用方转为 ``_ABSOLUTE_MAX_WAIT``（3600s）。
    """
    elapsed = 0.0
    while elapsed < max_wait:
        # abort 检查
        if await is_aborted(thread_id):
            return None
        decision = await pop_approval(thread_id)
        if decision is not None:
            return decision
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return None


def _resolve_max_wait() -> float:
    """解析 approval_max_wait 配置，修复 bug #6（0 不再无限阻塞）。

    - ``approval_max_wait == 0`` → ``_ABSOLUTE_MAX_WAIT``（3600s）
    - ``approval_max_wait > _ABSOLUTE_MAX_WAIT`` → 截断到 ``_ABSOLUTE_MAX_WAIT``
    - 其他 → 使用配置值
    """
    configured = get_settings().approval_max_wait
    if configured == 0:
        return _ABSOLUTE_MAX_WAIT
    return min(float(configured), _ABSOLUTE_MAX_WAIT)


@dataclass
class _ExtensionResult:
    """``_handle_directory_extension`` 的返回值。"""

    events: list[dict[str, str]]
    denied: bool = False
    timed_out: bool = False


async def _handle_directory_extension(
    pending_calls: list[dict],
    thread_id: str,
    sandbox: SessionSandbox,
    workspace_path: str | None = None,
    parent_thread_id: str | None = None,
) -> _ExtensionResult:
    """处理只读 fs 工具的目录越界扩展授权。

    遍历 pending_calls，收集所有越界路径，**一次性批量 yield** 所有
    approval_request(kind=directory_extension) 事件，然后等待用户统一决策。

    决策映射：
    - once / approve：``sandbox.authorize_temp`` 临时授权（所有越界路径）
    - session：``sandbox.authorize`` 持久授权（所有越界路径）
    - deny：返回 denied=True

    bug #1 修复：所有 ``is_path_authorized`` 调用传 ``base=workspace_path``，
    使审批层与执行层路径基准一致。
    bug #2 修复：传 ``parent_thread_id``，Team 模式子任务继承父 thread 授权。
    bug #4 修复：``all_under_workspace`` 用 ``writable=False`` 检查（只读工具
    仅需读权限），且传 ``base=workspace_path``。收集越界路径后进行第二轮
    复核：若所有越界路径在并发期间已被授权（如其他协程已 authorize），
    自动放行，避免不必要的审批弹窗。

    Args:
        pending_calls: 待执行的工具调用列表。
        thread_id: 会话 ID。
        sandbox: SessionSandbox 实例（async）。
        workspace_path: 当前工作区路径（bug #1 修复：传给 is_path_authorized 的 base）。
        parent_thread_id: 父 thread_id（bug #2 修复：Team 模式继承父授权）。

    Returns:
        _ExtensionResult：含 events（需 yield 的 SSE 事件）+ denied/timed_out 标志。
    """
    # 第一步：收集所有越界路径（去重）
    # bug #1 修复：传 base=workspace_path 使相对路径基于 workspace 解析
    unauthorized_paths: list[str] = []
    seen: set[str] = set()
    for tc in pending_calls:
        name = tc.get("name", "")
        if name not in _READ_ONLY_FS_TOOLS:
            continue
        paths = _extract_paths_from_tool_call(tc, workspace_path)
        if not paths:
            continue
        for path in paths:
            if await sandbox.is_path_authorized(
                thread_id,
                path,
                writable=False,
                base=workspace_path,
                parent_thread_id=parent_thread_id,
            ):
                continue
            if path not in seen:
                seen.add(path)
                unauthorized_paths.append(path)

    if not unauthorized_paths:
        return _ExtensionResult(events=[])

    # bug #4 修复：第二轮复核（all_under_workspace 自动放行）
    # 并发授权场景：在收集越界路径与弹出审批框之间，可能有其他协程已授权
    # 这些路径。用 writable=False（只读工具仅需读权限）重新检查所有越界路径，
    # 若全部已授权则自动放行，避免不必要的审批弹窗。
    still_unauthorized: list[str] = []
    for path in unauthorized_paths:
        if await sandbox.is_path_authorized(
            thread_id,
            path,
            writable=False,
            base=workspace_path,
            parent_thread_id=parent_thread_id,
        ):
            continue
        still_unauthorized.append(path)

    if not still_unauthorized:
        # 所有越界路径已在并发期间被授权 → 自动放行
        return _ExtensionResult(events=[])

    unauthorized_paths = still_unauthorized

    # 第二步：批量 yield 所有越界审批请求（前端可展示为批量审批对话框）
    events: list[dict[str, str]] = []
    for path in unauthorized_paths:
        # 用第一个涉及该路径的 tool_call 构造审批事件
        representative_tc = next(
            (
                tc
                for tc in pending_calls
                if tc.get("name", "") in _READ_ONLY_FS_TOOLS
                and path in _extract_paths_from_tool_call(tc, workspace_path)
            ),
            {},
        )
        events.append(
            _make_approval_event(
                representative_tc,
                thread_id,
                kind="directory_extension",
                requested_path=path,
                writable=False,
            )
        )

    # 第三步：等待一次统一审批决策（覆盖所有越界路径）
    # bug #6 修复：max_wait 不再为 float("inf")
    decision = await _await_approval(
        thread_id,
        poll_interval=_APPROVAL_POLL_INTERVAL,
        max_wait=_resolve_max_wait(),
    )
    if decision is None:
        return _ExtensionResult(events=events, timed_out=True)
    if decision.decision == "deny" or not decision.approved:
        return _ExtensionResult(events=events, denied=True)

    # 第四步：统一应用决策到所有越界路径
    if decision.decision in ("once", "approve"):
        for path in unauthorized_paths:
            try:
                await sandbox.authorize_temp(thread_id, path, writable=False)
            except ValueError as exc:
                logger.warning("authorize_temp failed", path=path, error=str(exc))
                return _ExtensionResult(events=events, denied=True)
    elif decision.decision == "session":
        for path in unauthorized_paths:
            try:
                await sandbox.authorize(thread_id, path, writable=False)
            except ValueError as exc:
                logger.warning("authorize session failed", path=path, error=str(exc))
                return _ExtensionResult(events=events, denied=True)

    return _ExtensionResult(events=events)


# ============================================================
# 公共审批循环
# ============================================================


async def _get_pending_tool_calls(agent: Any, config: dict) -> list[dict]:
    """从 agent 状态中提取待执行的工具调用列表。"""
    state = await agent.aget_state(config)
    if not state or not state.values:
        return []
    messages = state.values.get("messages", [])
    if not messages:
        return []
    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []
    return list(tool_calls)


async def _is_interrupted(agent: Any, config: dict) -> bool:
    """检查 agent 是否在 interrupt 处暂停。"""
    state = await agent.aget_state(config)
    if not state or not state.next:
        return False
    return "tools" in state.next


async def _inject_tool_error_for_call(
    agent: Any, config: dict, tool_call: dict, error_text: str
) -> None:
    """为单个 tool_call 注入 ToolMessage 错误。"""
    from langchain_core.messages import ToolMessage
    from uuid import uuid4

    tc_id = tool_call.get("id") or str(uuid4())
    tool_msg = ToolMessage(content=error_text, tool_call_id=tc_id)
    try:
        await agent.aupdate_state(config, {"messages": [tool_msg]})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "approval_flow.inject_tool_error_for_call failed",
            thread_id=config.get("configurable", {}).get("thread_id", ""),
            tool=tool_call.get("name"),
            error=str(exc),
        )


async def run_approval_loop(
    graph,
    config: dict,
    thread_id: str,
    workspace_path: str | None,
    permission_mode: str,
    runtime_dangerous: frozenset[str],
    agent_tools: list,
    yield_event: Callable[[dict], Awaitable[None]] | None,
    sandbox: SessionSandbox,
    parent_thread_id: str | None = None,
    *,
    source: str = "deep",
    inputs: dict | None = None,
    stream_fn: Callable[..., AsyncIterator[dict[str, str]]] | None = None,
    is_interrupted_fn: Callable[[Any, dict], Awaitable[bool]] | None = None,
    get_pending_calls_fn: Callable[[Any, dict], Awaitable[list[dict]]] | None = None,
    inject_tool_error_for_call_fn: Callable[[Any, dict, dict, str], Awaitable[None]] | None = None,
    inject_tool_error_messages_fn: Callable[[Any, dict, str], Awaitable[None]] | None = None,
    readonly_streak_threshold: int = 0,
) -> AsyncIterator[dict[str, str]]:
    """统一的 interrupt_before 审批循环。

    从 ``work_supervisor.py`` / ``coding.py`` 提取的公共逻辑，修复 6 个 bug。
    作为 async generator yield SSE 事件。

    流程:
    1. ``stream_fn(graph, inputs, config, source)`` 消费初始事件，yield 非 interrupt 事件
    2. while 循环：
       a. 暂停/恢复检查（``is_paused`` + ``get_pause_event``）
       b. ``is_interrupted_fn`` 检测 → 非中断则 break
       c. ``get_pending_calls_fn`` 提取 pending tool_calls
       d. **full_trust 模式**（bug #5）→ 跳过所有审批（含 directory_extension 预检查）
       e. readonly_streak 循环保护（可选，threshold > 0 时启用）
       f. dangerous_calls 判定（bug #1 #2 #3）：
          - ``cli_execute`` 始终需审批（bug #3）
          - 其他危险工具：路径检查传 ``base=workspace_path`` + ``parent_thread_id``（bug #1 #2）
       g. dangerous_tool 审批：yield approval_request → ``_await_approval``
          - max_wait 上限 3600s（bug #6）
       h. directory_extension 审批（bug #1 #2 #4）：
          - ``_handle_directory_extension`` 传 ``workspace_path`` + ``parent_thread_id``
       i. 恢复执行（``stream_fn(graph, None, config, source)``）或注入错误
       j. ``await sandbox.clear_temp(thread_id)``

    Args:
        graph: 编译后的 LangGraph agent。
        config: 含 ``configurable.thread_id`` 的运行配置。
        thread_id: 会话 ID。
        workspace_path: 当前工作区路径（bug #1：传给 is_path_authorized 的 base）。
        permission_mode: ``"standard"`` 或 ``"full_trust"``。
        runtime_dangerous: 运行时危险工具集合（``compute_runtime_dangerous`` 结果）。
        agent_tools: agent 工具列表（保留参数，向后兼容）。
        yield_event: 可选的异步回调，每 yield 一个事件时同步调用（用于日志/观察）。
        sandbox: ``SessionSandbox`` 实例（async）。
        parent_thread_id: 父 thread_id（bug #2：Team 模式继承父授权）。
        source: SSE 事件 source 标识（``"work"`` / ``"coding"`` / ``"deep"``）。
        inputs: 初始 inputs；None 表示从 interrupt 处恢复。
        stream_fn: ``_stream_agent_events`` 引用（调用方传模块级引用以便测试 patch）。
        is_interrupted_fn: ``_is_interrupted`` 引用。
        get_pending_calls_fn: ``_get_pending_tool_calls`` 引用。
        inject_tool_error_for_call_fn: ``_inject_tool_error_for_call`` 引用。
        inject_tool_error_messages_fn: ``_inject_tool_error_messages`` 引用。
        readonly_streak_threshold: 只读工具连续调用阈值（>0 启用循环保护）。

    Yields:
        SSE 事件 dict: ``{event: str, data: str}``
    """
    # 解析 helper 函数：优先用调用方传入的（支持测试 patch），否则用本模块默认
    _stream = stream_fn or _stream_default
    _is_int = is_interrupted_fn or _is_interrupted
    _get_calls = get_pending_calls_fn or _get_pending_tool_calls
    _inject_call = inject_tool_error_for_call_fn or _inject_tool_error_for_call
    _inject_msgs = inject_tool_error_messages_fn or _inject_tool_error_messages_default

    is_full_trust = permission_mode == "full_trust"
    max_iterations = 50
    iteration = 0
    readonly_streak = 0

    # ---- 1. 初始流式执行 ----
    try:
        async for sse in _stream(graph, inputs, config, source):
            if yield_event is not None:
                await yield_event(sse)
            yield sse
    except Exception as exc:  # noqa: BLE001
        logger.exception("approval_loop initial stream failed", thread_id=thread_id)
        await _inject_msgs(graph, config, f"执行失败: {exc}")
        yield make_sse_event("error", f"执行失败: {exc}")
        return

    # ---- 2. 中断/恢复循环 ----
    while iteration < max_iterations:
        iteration += 1

        # 暂停/恢复检查
        if await is_paused(thread_id):
            yield make_sse_event("paused", {})
            pause_event = await get_pause_event(thread_id)
            if await is_paused(thread_id):
                await pause_event.wait()
            yield make_sse_event("resumed", {})

        if not await _is_int(graph, config):
            break

        pending_calls = await _get_calls(graph, config)
        if not pending_calls:
            logger.warning("interrupted but no pending tool calls", thread_id=thread_id)
            break

        # ---- 循环保护：检测连续只读工具过度探索 ----
        if readonly_streak_threshold > 0:
            pending_names = {tc.get("name", "") for tc in pending_calls}
            has_dangerous = bool(pending_names & runtime_dangerous)
            all_readonly = pending_names.issubset(_READONLY_TOOLS)
            if all_readonly and not has_dangerous:
                readonly_streak += 1
            else:
                readonly_streak = 0

            if readonly_streak >= readonly_streak_threshold:
                logger.warning(
                    "approval_loop readonly streak exceeded, forcing stop",
                    thread_id=thread_id,
                    readonly_streak=readonly_streak,
                    pending_tools=list(pending_names),
                )
                for tc in pending_calls:
                    await _inject_call(
                        graph,
                        config,
                        tc,
                        "已获取足够信息，请基于已有结果直接回答用户，不要继续调用工具。",
                    )
                yield make_sse_event(
                    "error",
                    "工具调用次数过多，已强制停止。请简化您的请求或明确指定目标路径。",
                )
                return

        # ---- bug #5: full_trust 模式跳过所有审批（含 directory_extension 预检查）----
        if is_full_trust:
            await sandbox.clear_temp(thread_id)
            try:
                async for sse in _stream(graph, None, config, source):
                    if yield_event is not None:
                        await yield_event(sse)
                    yield sse
            except Exception as exc:  # noqa: BLE001
                logger.exception("approval_loop resume failed", thread_id=thread_id)
                await _inject_msgs(graph, config, f"恢复失败: {exc}")
                yield make_sse_event("error", f"恢复失败: {exc}")
                return
            continue

        # ---- standard 模式：检查危险工具 ----
        dangerous_calls: list[dict] = []
        for tc in pending_calls:
            name = tc.get("name", "")
            if name not in runtime_dangerous:
                continue
            # bug #3: cli_execute 始终需要审批（让用户审查命令内容），
            # 不因 workspace 已授权而自动放行
            if name == "cli_execute":
                dangerous_calls.append(tc)
                continue
            paths = _extract_paths_from_tool_call(tc, workspace_path)
            if not paths:
                # 无路径参数的危险工具：若已选工作区且授权则自动放行
                # bug #1: 传 base=workspace_path；bug #2: 传 parent_thread_id
                if workspace_path and await sandbox.is_path_authorized(
                    thread_id,
                    workspace_path,
                    writable=True,
                    base=workspace_path,
                    parent_thread_id=parent_thread_id,
                ):
                    continue
                dangerous_calls.append(tc)
                continue
            # bug #1: 传 base=workspace_path；bug #2: 传 parent_thread_id
            # 注意：不能用 `all(await ... for ...)`，改用 asyncio.gather（同上）。
            path_checks = await asyncio.gather(
                *[
                    sandbox.is_path_authorized(
                        thread_id,
                        p,
                        writable=True,
                        base=workspace_path,
                        parent_thread_id=parent_thread_id,
                    )
                    for p in paths
                ]
            )
            all_authorized = all(path_checks)
            if not all_authorized:
                dangerous_calls.append(tc)

        if dangerous_calls:
            for tc in dangerous_calls:
                evt = _make_approval_event(tc, thread_id, kind="dangerous_tool")
                if yield_event is not None:
                    await yield_event(evt)
                yield evt

            # bug #6: max_wait 不再为 float("inf")，0 → 3600s
            decision = await _await_approval(
                thread_id,
                poll_interval=_APPROVAL_POLL_INTERVAL,
                max_wait=_resolve_max_wait(),
            )

            if decision is None or not decision.approved:
                for tc in dangerous_calls:
                    await _inject_call(
                        graph, config, tc, "用户拒绝执行危险操作"
                    )
                yield make_sse_event("error", "用户拒绝执行危险操作")
                return

            logger.info(
                "approval_loop approval granted",
                thread_id=thread_id,
                tool_count=len(dangerous_calls),
                tools=[tc.get("name") for tc in dangerous_calls],
            )
        else:
            # 非危险工具：检查只读 fs 工具是否越界
            # bug #1 #2 #4: 传 workspace_path + parent_thread_id
            extension_handled = await _handle_directory_extension(
                pending_calls,
                thread_id,
                sandbox,
                workspace_path=workspace_path,
                parent_thread_id=parent_thread_id,
            )
            for evt in extension_handled.events:
                if yield_event is not None:
                    await yield_event(evt)
                yield evt
            if extension_handled.denied:
                yield make_sse_event("error", "用户拒绝访问该目录")
                await _inject_msgs(graph, config, "用户拒绝访问该目录")
                return
            if extension_handled.timed_out:
                yield make_sse_event("error", "目录授权等待被中断，操作未执行")
                await _inject_msgs(graph, config, "目录授权等待被中断，操作未执行")
                return

        # ---- 恢复执行 ----
        try:
            async for sse in _stream(graph, None, config, source):
                if yield_event is not None:
                    await yield_event(sse)
                yield sse
        except Exception as exc:  # noqa: BLE001
            logger.exception("approval_loop resume failed", thread_id=thread_id)
            await _inject_msgs(graph, config, f"恢复失败: {exc}")
            yield make_sse_event("error", f"恢复失败: {exc}")
            return

        await sandbox.clear_temp(thread_id)

    if iteration >= max_iterations:
        logger.warning("approval_loop hit max iterations", thread_id=thread_id)
        yield make_sse_event("error", "达到最大迭代上限")
        await _inject_msgs(graph, config, "达到最大迭代上限")
        return


# ============================================================
# 默认 helper 实现（当调用方未传入时使用）
# ============================================================


async def _stream_default(
    agent: Any, inputs: Any, config: dict, source: str = "deep"
) -> AsyncIterator[dict[str, str]]:
    """默认 stream_fn：委托到 ``app.deep.streaming._stream_agent_events``。"""
    from app.deep.streaming import _stream_agent_events

    async for sse in _stream_agent_events(agent, inputs, config, source=source):
        yield sse


async def _inject_tool_error_messages_default(
    agent: Any, config: dict, error_text: str
) -> None:
    """默认 inject_tool_error_messages_fn：为所有待执行 tool_call 注入错误。

    deepagents 0.6+ 的 ``PatchToolCallsMiddleware`` 在中间件层自动修复悬空
    tool_calls，此函数仅用于主动告知 LLM 操作被拒绝/失败（语义反馈）。
    逐个调用 ``_inject_tool_error_for_call`` 为 pending tool_calls 注入
    ToolMessage 错误。
    """
    pending = await _get_pending_tool_calls(agent, config)
    for tc in pending:
        await _inject_tool_error_for_call(agent, config, tc, error_text)
