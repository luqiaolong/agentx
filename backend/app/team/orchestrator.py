"""AgentTeam 路径：LangGraph StateGraph + Send API 编排多代理协作。

本模块用 LangGraph 原生 ``Send`` API 替代 ``asyncio.Queue`` + ``Semaphore``
手工并行，实现可检查点、可调试的 Map-Reduce 子任务编排：

- ``_plan_node``: Orchestrator 拆任务
- ``_dispatch_node``: 把每个子任务 fan-out 到对应执行节点
- ``_deep_node`` / ``_code_node`` / ``_builtin_node`` / ``_team_role_node`` /
  ``_custom_node`` / ``_default_node``: 按 agent 类型执行
- ``_aggregate_node``: 汇总 findings

状态归并：``TeamState`` 使用 ``_merge_dict`` reducer，并行子任务节点返回的
部分 ``findings`` / ``errors`` / ``subtask_results`` 自动归并。

事件流：各子任务节点通过 ``get_stream_writer()`` 写入 custom stream，
``run_team_path`` 通过 ``graph.astream(..., stream_mode="custom")`` 消费后
yield 给调用方。``approval_request`` / ``token`` / ``tool_result`` /
``reasoning`` / ``delegation`` 等 passthrough 事件实时透传，不缓冲，避免
审批流死锁。

安全：危险任务（含写入/编辑/shell 关键词）由 planner 强制改写为 deep，由
``_deep_node`` 走 ``run_deep_path`` 的 interrupt_on 审批。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.config import get_settings
from app.llm import get_chat_model
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.security.approval import get_abort_event
from app.sse.events import make_sse_event, make_team_event

from app.team.blackboard import (
    Blackboard,
    SubtaskState,
    TeamPlanTask,
    TeamState,
    TeamSubtaskResult,
    _merge_dict,  # noqa: F401
    _serialize_blackboard,  # noqa: F401
)
from app.team.planner import (
    _BASE_EXPERTS,
    _ORCHESTRATOR_PROMPT,
    TeamPlan,
    TeamPlanItem,
    _build_orchestrator_prompt,
    _build_project_context,
    _postprocess_plan,
    _validate_task,
)
from app.team.scheduler import (
    _PASSTHROUGH_EVENTS,
    _SUBTASK_DONE_EVENT,
    _get_runner,
    _inherit_workspace,
    _resolve_subtask_runners,
    _run_subtask_stream,
    _run_team_role_subtask,
)
from app.team.aggregator import (
    _build_summary,  # noqa: F401
    _run_aggregator,
    _should_downgrade_to_single,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.router.state import RouterState


# ============================================================
# StateGraph 节点
# ============================================================


async def _plan_node(state: TeamState) -> dict:
    """Orchestrator 拆任务节点：生成 TeamPlan，校验后写入 state。"""
    writer = get_stream_writer()
    settings = get_settings()

    chat_model = state.get("chat_model")
    try:
        llm = chat_model if chat_model is not None else get_chat_model(
            temperature=settings.llm_temperature_orchestrator, streaming=False
        )
    except ValueError as exc:
        writer(make_team_event("error", {"message": f"LLM 不可用: {exc}"}))
        return {"plan": [], "errors": {}, "findings": {}, "subtask_results": {}}

    message = state["message"]
    max_tasks = settings.agent_team_max_tasks
    context = _build_project_context()
    orchestrator_prompt = _build_orchestrator_prompt(
        message, max_tasks, context=context, settings=settings
    )
    try:
        structured_llm = llm.with_structured_output(TeamPlan)
        plan_obj: TeamPlan = await structured_llm.ainvoke(orchestrator_prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("team orchestrator invoke failed", error=str(exc))
        writer(make_team_event("error", {"message": f"Orchestrator 调用失败: {exc}"}))
        return {"plan": [], "errors": {}, "findings": {}, "subtask_results": {}}

    plan, reasoning = _postprocess_plan(plan_obj, max_tasks)
    if not plan:
        writer(make_team_event("error", {"message": "Orchestrator 未生成有效计划"}))
        return {"plan": [], "errors": {}, "findings": {}, "subtask_results": {}}

    errors: dict[str, str] = {}
    valid_tasks: list[TeamPlanTask] = []
    for task in plan:
        ok, err = _validate_task(task, settings)
        if ok:
            valid_tasks.append(task)
        else:
            errors[task.agent] = err
            writer(
                make_team_event(
                    "team_progress",
                    {"agent": task.agent, "status": "error", "message": err},
                )
            )

    writer(
        make_team_event(
            "team_plan",
            {
                "plan": [
                    {"agent": t.agent, "input": t.input, "purpose": t.purpose}
                    for t in plan
                ],
                "reasoning": reasoning,
            },
        )
    )

    return {"plan": valid_tasks, "reasoning": reasoning, "errors": errors}


def _dispatch_node(state: TeamState) -> list[Send]:
    """dispatch 条件边：把每个子任务 fan-out 到对应类型的执行节点（LangGraph Send API）。

    Returns:
        每个子任务一个 ``Send(node_name, SubtaskState)``，LangGraph 并行执行。
    """
    plan = state.get("plan", [])
    thread_id = state.get("thread_id", "")
    sends: list[Send] = []
    for idx, task in enumerate(plan):
        node_name = _route_node_for_task(task.agent, state)
        sends.append(
            Send(
                node_name,
                {
                    "task": {
                        "agent": task.agent,
                        "input": task.input,
                        "purpose": task.purpose,
                    },
                    "task_index": idx,
                    "parent_thread_id": thread_id,
                    "history": state.get("history"),
                    "permission_mode": state.get("permission_mode", "standard"),
                    "scene_prompt": state.get("scene_prompt"),
                    "profile_prompt": state.get("profile_prompt", ""),
                    "workspace_path": state.get("workspace_path"),
                    "chat_model": state.get("chat_model"),
                    "subtask_runners": state.get("subtask_runners"),
                },
            )
        )
    return sends


def _route_node_for_task(agent: str, state: TeamState) -> str:
    """把 ``TeamPlanTask.agent`` 映射到对应的执行节点名。"""
    if agent == "deep":
        return "deep_node"
    if agent == "code":
        return "code_node"
    if agent in ("rag", "web"):
        return "builtin_node"
    if agent.startswith("custom-"):
        return "custom_node"
    settings = get_settings()
    if agent in (settings.team_subagents or {}):
        return "team_role_node"
    return "default_node"


def _make_subtask_state_update(result: TeamSubtaskResult) -> dict:
    """把 ``TeamSubtaskResult`` 转换为 StateGraph reducer 兼容的 state update。"""
    if result.success:
        return {
            "findings": {result.agent: result.payload},
            "subtask_results": {
                result.agent: {
                    "success": True,
                    "payload": result.payload,
                },
            },
        }
    return {
        "errors": {result.agent: result.payload},
        "subtask_results": {
            result.agent: {
                "success": False,
                "payload": result.payload,
            },
        },
    }


async def _emit_team_progress(writer: Callable, agent: str, status: str, message: str) -> None:
    writer(make_team_event("team_progress", {"agent": agent, "status": status, "message": message}))


async def _deep_node(state: SubtaskState) -> dict:
    """deep 子任务节点：执行 DeepAgent 路径（审批 + 写文件 + 命令）。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        await _emit_team_progress(writer, task.agent, "error", "用户中止")
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"))

    child_id = f"{parent_thread_id}-team-deep-{state['task_index']}"
    await _inherit_workspace(child_id, state.get("workspace_path"))
    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    deep_state: dict = {
        "thread_id": child_id,
        "messages": [{"role": "user", "content": task.input}],
    }
    runner = _get_runner("deep", state.get("subtask_runners"))
    result = await _run_subtask_stream(
        runner,
        runner_args=(deep_state, task.input),
        runner_kwargs={
            "profile_prompt": state.get("profile_prompt", ""),
            "history": state.get("history"),
            "permission_mode": state.get("permission_mode", "standard"),
            "scene_prompt": state.get("scene_prompt"),
            "workspace_path": state.get("workspace_path"),
            "parent_thread_id": parent_thread_id,
            "chat_model": state.get("chat_model"),
        },
        agent_name=task.agent,
        abort_event=abort_event,
        writer=writer,
    )
    if result.success:
        await _emit_team_progress(writer, task.agent, "done", task.purpose)
        writer(make_team_event("team_result", {"agent": task.agent, "summary": result.payload}))
    else:
        await _emit_team_progress(writer, task.agent, "error", result.payload)
    return _make_subtask_state_update(result)


async def _code_node(state: SubtaskState) -> dict:
    """code 子任务节点：执行 coding Expert。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        await _emit_team_progress(writer, task.agent, "error", "用户中止")
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"))

    child_id = f"{parent_thread_id}-team-code-{state['task_index']}"
    await _inherit_workspace(child_id, state.get("workspace_path"))
    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    runner = _get_runner("code", state.get("subtask_runners"))
    result = await _run_subtask_stream(
        runner,
        runner_args=(task.input, child_id),
        runner_kwargs={
            "profile_prompt": state.get("profile_prompt", ""),
            "history": state.get("history"),
            "permission_mode": state.get("permission_mode", "standard"),
            "workspace_path": state.get("workspace_path"),
            "parent_thread_id": parent_thread_id,
            "chat_model": state.get("chat_model"),
        },
        agent_name=task.agent,
        abort_event=abort_event,
        writer=writer,
    )
    if result.success:
        await _emit_team_progress(writer, task.agent, "done", task.purpose)
        writer(make_team_event("team_result", {"agent": task.agent, "summary": result.payload}))
    else:
        await _emit_team_progress(writer, task.agent, "error", result.payload)
    return _make_subtask_state_update(result)


async def _builtin_node(state: SubtaskState) -> dict:
    """builtin（rag / web）子任务节点。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        await _emit_team_progress(writer, task.agent, "error", "用户中止")
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"))

    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    runner = _get_runner(task.agent, state.get("subtask_runners"))
    result = await _run_subtask_stream(
        runner,
        runner_args=(parent_thread_id, task.input),
        runner_kwargs={
            "history": state.get("history"),
            "workspace_path": state.get("workspace_path"),
        },
        agent_name=task.agent,
        abort_event=abort_event,
        writer=writer,
    )
    if result.success:
        await _emit_team_progress(writer, task.agent, "done", task.purpose)
        writer(make_team_event("team_result", {"agent": task.agent, "summary": result.payload}))
    else:
        await _emit_team_progress(writer, task.agent, "error", result.payload)
    return _make_subtask_state_update(result)


async def _team_role_node(state: SubtaskState) -> dict:
    """团队角色子任务节点（frontend_dev / backend_dev / tester 等）。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        await _emit_team_progress(writer, task.agent, "error", "用户中止")
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"))

    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    result = await _run_team_role_subtask(
        task=task,
        thread_id=parent_thread_id,
        history=state.get("history"),
        permission_mode=state.get("permission_mode", "standard"),
        profile_prompt=state.get("profile_prompt", ""),
        task_index=state["task_index"],
        workspace_path=state.get("workspace_path"),
        chat_model=state.get("chat_model"),
        subtask_runners=state.get("subtask_runners"),
        abort_event=abort_event,
        writer=writer,
    )
    if result.success:
        await _emit_team_progress(writer, task.agent, "done", task.purpose)
        writer(make_team_event("team_result", {"agent": task.agent, "summary": result.payload}))
    else:
        await _emit_team_progress(writer, task.agent, "error", result.payload)
    return _make_subtask_state_update(result)


async def _custom_node(state: SubtaskState) -> dict:
    """custom-* 子任务节点。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        await _emit_team_progress(writer, task.agent, "error", "用户中止")
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"))

    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    key = task.agent[len("custom-"):]
    runner = _get_runner("custom", state.get("subtask_runners"))
    result = await _run_subtask_stream(
        runner,
        runner_args=(key, parent_thread_id, task.input),
        runner_kwargs={
            "history": state.get("history"),
            "workspace_path": state.get("workspace_path"),
        },
        agent_name=task.agent,
        abort_event=abort_event,
        writer=writer,
    )
    if result.success:
        await _emit_team_progress(writer, task.agent, "done", task.purpose)
        writer(make_team_event("team_result", {"agent": task.agent, "summary": result.payload}))
    else:
        await _emit_team_progress(writer, task.agent, "error", result.payload)
    return _make_subtask_state_update(result)


async def _default_node(state: SubtaskState) -> dict:
    """未知 agent 类型的 fallback 节点。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    err_msg = f"未知 agent: {task.agent}"
    await _emit_team_progress(writer, task.agent, "error", err_msg)
    return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload=err_msg))


async def _aggregate_node(state: TeamState) -> dict:
    """Aggregator 汇总节点：综合黑板内容生成最终回复。"""
    writer = get_stream_writer()

    findings: dict[str, str] = state.get("findings", {})
    errors: dict[str, str] = state.get("errors", {})
    plan: list = state.get("plan", [])

    if not findings:
        if not plan:
            writer(make_team_event("team_done", {"status": "error"}))
            return {}
        writer(make_team_event("error", {"message": "所有专家任务均失败"}))
        writer(make_team_event("team_done", {"status": "error"}))
        return {}

    blackboard = Blackboard(findings=dict(findings), errors=dict(errors))
    message = state["message"]
    chat_model = state.get("chat_model")

    async for sse in _run_aggregator(message, blackboard, chat_model=chat_model):
        writer(sse)

    has_error = bool(errors)
    writer(
        make_team_event(
            "team_done",
            {"status": "error" if has_error and not findings else "done"},
        )
    )
    return {}


# ============================================================
# StateGraph 构建（无 checkpointer，stateless，安全缓存）
# ============================================================


def _build_team_graph() -> Any:
    """构建 Team StateGraph：plan → dispatch（Send fan-out）→ per-agent nodes → aggregate。"""
    graph: Any = StateGraph(TeamState)
    graph.add_node("plan", _plan_node)
    graph.add_node("deep_node", _deep_node)
    graph.add_node("code_node", _code_node)
    graph.add_node("builtin_node", _builtin_node)
    graph.add_node("team_role_node", _team_role_node)
    graph.add_node("custom_node", _custom_node)
    graph.add_node("default_node", _default_node)
    graph.add_node("aggregate", _aggregate_node)

    graph.add_edge(START, "plan")
    # dispatch 条件边：返回 list[Send] 触发 LangGraph 原生 fan-out
    graph.add_conditional_edges("plan", _dispatch_node)
    # 所有子任务节点 → aggregate
    for node_name in ("deep_node", "code_node", "builtin_node", "team_role_node", "custom_node", "default_node"):
        graph.add_edge(node_name, "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()


_team_graph: Any = None


def _get_team_graph() -> Any:
    """获取已编译的 Team StateGraph（单例缓存）。"""
    global _team_graph
    if _team_graph is None:
        _team_graph = _build_team_graph()
    return _team_graph


# ============================================================
# 入口
# ============================================================


async def run_team_path(
    message: str,
    thread_id: str,
    state: RouterState,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "workspace",
    scene_prompt: str | None = None,
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
    subtask_runners: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """AgentTeam 路径入口（LangGraph StateGraph + Send API 编排）。

    1. _plan_node: Orchestrator 拆任务 → team_plan 事件
    2. _dispatch_node: 并行 fan-out 子任务到执行节点
    3. _{deep,code,builtin,team_role,custom}_node: 执行子任务 → team_progress 事件
    4. _aggregate_node: 汇总 → token / reasoning 事件

    事件通过 ``get_stream_writer()`` 写入 custom stream，由
    ``graph.astream(..., stream_mode="custom")`` 消费后 yield 给调用方。

    Args:
        workspace_path: 当前会话绑定的 workspace 路径，透传到子代理 fs 工具。
        chat_model: 可选注入的 ChatModel，透传到子任务节点与 Aggregator。
        subtask_runners: 可选 ``{"code": callable, "rag": callable, ...}`` 字典，
            测试注入 mock 替代真实 runner。
    """
    # 简单任务降级：短消息 / 问候 / 翻译等无需 team 协作
    downgrade, reason = _should_downgrade_to_single(message)
    if downgrade:
        logger.info(
            "team downgrade suggest switch mode", reason=reason, message_len=len(message)
        )
        yield make_sse_event(
            "token",
            f"该任务似乎不需要团队协作（{reason}）。建议切换到 work 模式由 Supervisor 直接处理。",
        )
        yield make_team_event("team_done", {"status": "done"})
        return

    resolved_runners = _resolve_subtask_runners(subtask_runners)

    with trace_span("team.run", thread_id=thread_id, message_len=len(message)):
        graph = _get_team_graph()
        initial_state: TeamState = {
            "message": message,
            "thread_id": thread_id,
            "history": history or [],
            "permission_mode": permission_mode,
            "scene_prompt": scene_prompt,
            "profile_prompt": profile_prompt,
            "workspace_path": workspace_path,
            "chat_model": chat_model,
            "subtask_runners": resolved_runners,
            "plan": [],
            "reasoning": "",
            "findings": {},
            "errors": {},
            "subtask_results": {},
        }

        async for event in graph.astream(initial_state, stream_mode="custom"):
            yield event


__all__ = [
    "run_team_path",
    "Blackboard",
    "TeamPlanTask",
    "TeamPlan",
    "TeamPlanItem",
    "TeamSubtaskResult",
    "TeamState",
    "SubtaskState",
    "_run_subtask_stream",
    "_run_team_role_subtask",
    "_should_downgrade_to_single",
    "_validate_task",
    "_postprocess_plan",
    "_build_orchestrator_prompt",
    "_resolve_subtask_runners",
    "_SUBTASK_DONE_EVENT",
    "_PASSTHROUGH_EVENTS",
    "_BASE_EXPERTS",
    "_ORCHESTRATOR_PROMPT",
]
