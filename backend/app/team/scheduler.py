"""AgentTeam Scheduler：LangGraph 节点使用的子任务执行 helper。

本模块为 ``app.team.orchestrator`` 提供基于 LangGraph ``Send`` 并行编排的
子任务执行工具：

- ``_run_subtask_stream``: 执行单个子任务 runner，实时透传事件，返回
  ``TeamSubtaskResult``。
- ``_route_event_for_node``: 路由单条子任务事件，收集 token / tool_result，
  返回哨兵结果或 None。
- ``_resolve_subtask_runners``: 解析/懒加载真实子任务 runner 字典。
- ``_get_runner``: 从 ``subtask_runners`` 字典或懒加载真实 runner 中解析。
- ``_inherit_workspace``: 继承父 thread 的 workspace 授权到子任务 thread。
- ``_SUBTASK_DONE_EVENT``: 旧实现及少数兼容场景使用的哨兵事件名。
- ``_PASSTHROUGH_EVENTS``: 需要实时透传到前端的事件类型集合。
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from app.config import get_settings
from app.observability.logger import logger
from app.observability.trace import bind_trace, current_trace_id
from app.sse.events import make_sse_event
from app.team.aggregator import _build_summary
from app.team.blackboard import TeamSubtaskResult
from app.utils.text import extract_chunk_text

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = [
    "_run_subtask_stream",
    "_route_event_for_node",
    "_resolve_subtask_runners",
    "_get_runner",
    "_inherit_workspace",
    "_SUBTASK_DONE_EVENT",
    "_PASSTHROUGH_EVENTS",
]


# 子任务完成哨兵事件类型（旧实现兼容，新实现通过返回值传递结果）
_SUBTASK_DONE_EVENT = "_subtask_done"

# 需要实时透传到前端的事件类型
# approval_request 必须直达前端，否则 DeepAgent 审批流会死锁
# tool_result 必须透传，否则前端 tool_call 配对断裂
# reasoning / token 透传供前端展示子任务执行过程
_PASSTHROUGH_EVENTS: frozenset[str] = frozenset(
    {"approval_request", "todo_update", "delegation", "tool_call", "tool_result", "reasoning", "token"}
)

# L17: 默认 runner 字典缓存，避免每次 _get_runner miss 都重复 import + 构造
_default_runners_cache: dict[str, Any] | None = None


def _resolve_subtask_runners(
    subtask_runners: dict[str, Any] | None,
) -> dict[str, Any]:
    """解析子任务 runner 字典。``None`` 时 lazy-import 真实 runner。"""
    if subtask_runners is not None:
        return subtask_runners
    # L17: 缓存默认 runner 字典，避免每次 _get_runner miss 都重复 import + 构造
    global _default_runners_cache
    if _default_runners_cache is None:
        # Lazy import 避免模块顶部循环依赖
        from app.scenarios.coding.agent import run_coding_expert
        from app.deepagent.agent import run_deep_path
        from app.subagents import run_custom_agent, run_rag_agent, run_web_agent

        _default_runners_cache = {
            "code": run_coding_expert,
            "deep": run_deep_path,
            "rag": run_rag_agent,
            "web": run_web_agent,
            "custom": run_custom_agent,
        }
    return _default_runners_cache


def _get_runner(name: str, subtask_runners: dict[str, Any] | None) -> Any:
    """延迟解析子任务 runner（subtask_runners dict 优先，否则懒加载真实模块）。"""
    if subtask_runners and name in subtask_runners:
        return subtask_runners[name]
    return _resolve_subtask_runners(None).get(name)


async def _inherit_workspace(child_thread_id: str, workspace_path: str | None) -> None:
    """将父 thread 的 workspace 授权继承到子任务 thread。

    子任务使用独立 thread_id（如 ``{parent}-team-{agent}-{idx}``），
    若不继承授权，fs 工具的沙箱检查会失败，触发 directory_extension
    审批死锁（_handle_directory_extension 在返回前等待审批，但审批事件
    在返回后才 yield 到前端）。
    """
    if not workspace_path:
        return
    from app.sandbox import get_sandbox

    sandbox = get_sandbox()
    try:
        await sandbox.authorize(child_thread_id, workspace_path, writable=True, source="team_inherit")
    except Exception as exc:  # noqa: BLE001 — M7: 兜底所有异常（不止 ValueError）
        logger.warning(
            "team subtask workspace inherit failed",
            child_thread_id=child_thread_id,
            workspace=workspace_path,
            error=str(exc),
        )


def _route_event_for_node(
    event: dict,
    collected_text: list[str],
    tool_traces: list[str],
    writer: Callable[[dict], None],
    abort_event: Any,
) -> TeamSubtaskResult | None:
    """单事件路由：passthrough 写入 writer，token/error/done 内部处理。

    同时支持两种事件格式：
    - ``event`` / ``data``（deep / code 子代理）
    - ``type`` / ``content``（rag / web / custom 子代理）

    Returns:
        ``TeamSubtaskResult`` 表示哨兵事件到达（subtask 完成），None 表示中间事件。
    """
    event_type = event.get("event") or event.get("type", "")
    data = event.get("data") if "data" in event else event.get("content", "")
    if event_type == "token":
        collected_text.append(str(data))
        return None
    if event_type == "tool_result":
        if "event" in event:
            # event/data 格式（deep / code）
            try:
                obj = json.loads(data) if isinstance(data, str) else data
                if isinstance(obj, dict):
                    tool_traces.append(
                        f"{obj.get('name', '?')}: {str(obj.get('result', ''))[:200]}"
                    )
            except Exception:  # noqa: BLE001
                pass
            writer(event)
        else:
            # type/content 格式（rag / web / custom）
            name = str(event.get("name", "?"))
            result = str(event.get("result", ""))[:200]
            tool_traces.append(f"{name}: {result}")
            # M4: 转换为 event/data 格式后透传，避免前端 tool_call 配对断裂
            writer(
                make_sse_event(
                    "tool_result",
                    {
                        "id": event.get("id", ""),
                        "name": event.get("name", "?"),
                        "result": event.get("result", ""),
                        "source": event.get("source", ""),
                    },
                )
            )
        return None
    if event_type == "error":
        # error 直接透传（仅 event/data 格式）
        if "event" in event:
            writer(event)
        return None
    if event_type == _SUBTASK_DONE_EVENT:
        try:
            obj = json.loads(data) if isinstance(data, str) else {}
            return TeamSubtaskResult(
                agent=obj.get("agent", ""),
                success=bool(obj.get("success")),
                payload=str(obj.get("payload", "")),
            )
        except Exception:  # noqa: BLE001
            return TeamSubtaskResult(agent="", success=False, payload="哨兵事件解析失败")
    if "event" in event and event_type in _PASSTHROUGH_EVENTS:
        writer(event)
    return None


async def _run_subtask_stream(
    runner: Callable[..., AsyncIterator[dict]],
    runner_args: tuple,
    runner_kwargs: dict,
    agent_name: str,
    abort_event: Any,
    writer: Callable[[dict], None],
) -> TeamSubtaskResult:
    """通用子任务流式执行：调用 runner，路由事件，返回结果。

    处理 abort / error / 异常 / 哨兵事件，passthrough 实时写 writer。
    abort 在每轮事件迭代前检查，确保用户触发中止后子任务立即退出。
    """
    collected_text: list[str] = []
    tool_traces: list[str] = []
    # trace_id 透传：子任务 runner（deep/code/rag/web/custom）由 LangGraph 用
    # asyncio.create_task 调度，ContextVar 不会自动跨协程传播。显式绑定让子任务
    # 内部的 logger / make_sse_event 也能拿到 trace_id，便于排查"卡在哪一步"。
    _trace_id = current_trace_id() or ""
    _trace_cm = bind_trace(_trace_id) if _trace_id else contextlib.nullcontext()
    with _trace_cm:
        try:
            stream = runner(*runner_args, **runner_kwargs)
            async for event in stream:
                if abort_event.is_set():
                    return TeamSubtaskResult(agent=agent_name, success=False, payload="用户中止")
                result = _route_event_for_node(event, collected_text, tool_traces, writer, abort_event)
                if result is not None:
                    return result
        except Exception as exc:  # noqa: BLE001
            return TeamSubtaskResult(agent=agent_name, success=False, payload=f"{agent_name} 子任务异常: {exc}")

    # runner 正常结束但未发 _subtask_done → 视为成功（rag/web/custom 无哨兵事件）
    if collected_text or tool_traces:
        return TeamSubtaskResult(
            agent=agent_name,
            success=True,
            payload=_build_summary(collected_text, tool_traces, agent_name),
        )
    return TeamSubtaskResult(
        agent=agent_name,
        success=False,
        payload=_build_summary(collected_text, tool_traces, agent_name),
    )


async def _run_team_role_subtask(
    task: Any,
    thread_id: str,
    history: list | None,
    permission_mode: str,
    profile_prompt: str,
    task_index: int,
    workspace_path: str | None,
    chat_model: BaseChatModel | None,
    subtask_runners: dict[str, Any] | None,
    abort_event: Any,
    writer: Callable[[dict], None],
) -> TeamSubtaskResult:
    """软件开发团队角色子任务（frontend_dev / backend_dev / tester / ...）。

    优先用 ``build_custom_agent`` 构建专属 agent（astream_events v2）；
    无配置时降级到 coding Expert（走 ``_run_subtask_stream`` 统一路由）。
    """
    from app.team.blackboard import TeamPlanTask

    if not isinstance(task, TeamPlanTask):
        task = TeamPlanTask(**task)

    cfg = get_settings().team_subagents.get(task.agent)
    collected_text: list[str] = []
    tool_traces: list[str] = []

    def _done(success: bool, payload: str) -> TeamSubtaskResult:
        return TeamSubtaskResult(agent=task.agent, success=success, payload=payload)

    if not cfg or not cfg.system_prompt:
        # 降级到 coding Expert
        fallback_thread_id = f"{thread_id}-team-fallback-{task_index}"
        await _inherit_workspace(fallback_thread_id, workspace_path)
        runner = _get_runner("code", subtask_runners)
        return await _run_subtask_stream(
            runner,
            runner_args=(task.input, fallback_thread_id),
            runner_kwargs={
                "profile_prompt": profile_prompt,
                "history": history,
                "permission_mode": permission_mode,
                "workspace_path": workspace_path,
                "parent_thread_id": thread_id,
                "chat_model": chat_model,
            },
            agent_name=task.agent,
            abort_event=abort_event,
            writer=writer,
        )

    # 有专属配置：build_custom_agent + astream_events v2
    from app.subagents.custom_agent import build_custom_agent

    # H11: 使用隔离的 child_thread_id，避免与父 thread 或同类型并行子任务冲突
    child_thread_id = f"{thread_id}-team-role-{task.agent}-{task_index}"
    await _inherit_workspace(child_thread_id, workspace_path)

    agent_obj = build_custom_agent(
        key=task.agent,
        thread_id=child_thread_id,
        system_prompt=cfg.system_prompt,
        tools=cfg.tools,
        temperature=cfg.temperature,
        workspace_path=workspace_path,
    )
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": task.input}]}
    config = {"configurable": {"thread_id": child_thread_id}}
    # trace_id 透传：build_custom_agent 内部走 astream_events v2，
    # 回调中创建新协程，ContextVar 不会自动跨协程传播。
    _trace_id = current_trace_id() or ""
    _trace_cm = bind_trace(_trace_id) if _trace_id else contextlib.nullcontext()
    with _trace_cm:
        try:
            # M25: abort 检查在每个事件回调中执行；LLM 长调用期间无法响应中止，
            # 需要 asyncio.cancel 机制才能根本修复，当前为缓解方案。
            async for event in agent_obj.astream_events(inputs, version="v2", config=config):
                if abort_event.is_set():
                    return _done(False, "用户中止")
                kind = event["event"]
                ename = event.get("name", "")
                edata = event.get("data", {}) or {}
                if kind == "on_chat_model_stream":
                    content = extract_chunk_text(edata.get("chunk"), strip=False)
                    if content:
                        collected_text.append(content)
                        # 中间 token 不透传到前端：与 deep/code 子代理行为一致。
                        # 透传 token 会被前端 appendPartText 创建为 text part，
                        # 破坏 delegation 分组（text 破组导致子代理卡片内 traceItems 为空，
                        # 且最终报告 token 追加到已存在的 text part 导致位置错误）。
                        # 子代理的输出通过 collected_text 收集到 summary 中展示。
                elif kind in ("on_tool_start", "on_tool_end"):
                    trace_data = edata.get("input") if kind == "on_tool_start" else edata.get("output")
                    tool_traces.append(f"{ename}: {str(trace_data)[:200]}")
                    # H12: 透传 tool_call / tool_result 事件，避免前端 tool_call 配对断裂
                    # 用 run_id 作为 tool_call_id，确保 on_tool_start / on_tool_end 配对
                    tc_id = str(event.get("run_id") or "")
                    if kind == "on_tool_start":
                        writer(
                            make_sse_event(
                                "tool_call",
                                {
                                    "id": tc_id,
                                    "name": ename,
                                    "args": trace_data,
                                    "source": task.agent,
                                    "parent_task_id": thread_id,
                                },
                            )
                        )
                    else:
                        writer(
                            make_sse_event(
                                "tool_result",
                                {
                                    "id": tc_id,
                                    "name": ename,
                                    "result": trace_data,
                                    "source": task.agent,
                                    "parent_task_id": thread_id,
                                },
                            )
                        )
        except Exception as exc:  # noqa: BLE001
            return _done(False, f"团队角色 {task.agent} 子任务异常: {exc}")

    return _done(
        bool(collected_text or tool_traces),
        _build_summary(collected_text, tool_traces, task.agent),
    )
