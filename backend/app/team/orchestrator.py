"""AgentTeam 路径：LangGraph StateGraph + Send API 编排多代理协作。

本模块用 LangGraph 原生 ``Send`` API 替代 ``asyncio.Queue`` + ``Semaphore``
手工并行，实现可检查点、可调试的 Map-Reduce 子任务编排：

- ``_plan_node``: Orchestrator 直接 ``llm.ainvoke`` 调用 LLM，让其在回复正文
  输出 ``[agent:xxx]`` 任务行，``_parse_todos_from_text`` 解析为 deepagents
  原生 Todo schema，``_todos_to_team_tasks`` 转换为 ``TeamPlanTask`` 列表。
  （旧方案用 ``create_deep_agent`` + ``TodoListMiddleware`` 依赖 LLM 调用
  ``write_todos`` 工具，但 ``WRITE_TODOS_SYSTEM_PROMPT`` 主动建议不调用，
  导致 99% 失败率。）
- ``_dispatch_node``: 把每个子任务 fan-out 到对应执行节点，同时传递 ``todos``
  快照供子任务节点构造 ``todo_update`` 事件。
- ``_deep_node`` / ``_code_node`` / ``_builtin_node`` / ``_team_role_node`` /
  ``_custom_node`` / ``_default_node``: 按 agent 类型执行，返回部分 todo 状态
  更新（``{"_index": idx, "status": "completed"}``），由 ``_merge_todos``
  reducer 自动归并到全局 ``state.todos``。
- ``_aggregate_node``: 汇总 findings。

状态归并：``TeamState`` 使用 ``_merge_dict`` reducer（findings/errors/
subtask_results）和 ``_merge_todos`` reducer（todos），并行子任务节点返回的
部分 dict/list 自动归并。

事件流：
- ``todo_update`` 事件从 ``stream_mode="values"`` 的 state.todos diff 产出
  （reducer 归并后的全局视图，避免并行子任务竞态）。
- 各子任务节点通过 ``get_stream_writer()`` 写入 custom stream（token /
  tool_result / approval_request 等），由 ``run_team_path`` 通过
  ``graph.astream(stream_mode=["custom", "values"])`` 消费后 yield 给调用方。

安全：危险任务（含写入/编辑/shell 关键词）由 ``_todos_to_team_tasks``
强制改写为 deep，由 ``_deep_node`` 走 ``run_deep_path`` 的 interrupt_on 审批。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.config import get_settings
from app.llm import get_chat_model
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.observability.trace import bind_trace, current_trace_id
from app.security.approval import get_abort_event
from app.sse.events import make_sse_event, make_todo_update_event

from app.team.blackboard import (
    Blackboard,
    SubtaskState,
    TeamPlanTask,
    TeamState,
    TeamSubtaskResult,
    _merge_dict,  # noqa: F401
    _merge_todos,  # noqa: F401
    _serialize_blackboard,  # noqa: F401
)
from app.team.planner import (
    _BASE_EXPERTS,
    _ORCHESTRATOR_SYSTEM_PROMPT,
    _build_project_context,
    _build_team_experts_description,
    _parse_todos_from_text,
    _strip_agent_prefix_from_todos,
    _todos_to_team_tasks,
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

# H2: 跟踪并行子任务的 in_progress 状态，避免 values-mode todo_update 把
# 已标记 in_progress 的 todo 回退为 pending。key=parent_thread_id，value=已启动
# 子任务索引集合。在 _emit_todo_in_progress 中添加，在 _aggregate_node 中清理。
_in_progress_tasks: dict[str, set[int]] = {}

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.router.state import RouterState


# ============================================================
# StateGraph 节点
# ============================================================


async def _plan_node(state: TeamState) -> dict:
    """Orchestrator 拆任务节点：直接 ``llm.ainvoke`` + 文本解析。

    旧方案使用 ``create_deep_agent`` + ``TodoListMiddleware`` 依赖 LLM 自主调用
    ``write_todos`` 工具，但 ``TodoListMiddleware`` 注入的 ``WRITE_TODOS_SYSTEM_PROMPT``
    主动建议 LLM "简单任务不要用 write_todos"，导致 99% 情况下 ``write_todos``
    未被调用 → ``result.get("todos", [])`` 为空 → 无输出。

    新方案直接用 ``llm.ainvoke`` 调用 LLM，让其在回复正文输出 ``[agent:xxx]``
    任务行，``_parse_todos_from_text`` 逐行解析为 deepagents 原生 Todo schema，
    再由 ``_todos_to_team_tasks`` 转换为 ``TeamPlanTask`` 列表。
    """
    writer = get_stream_writer()
    settings = get_settings()

    chat_model = state.get("chat_model")
    try:
        llm = chat_model if chat_model is not None else get_chat_model(
            temperature=settings.llm_temperature_orchestrator, streaming=False
        )
    except ValueError as exc:
        writer(make_sse_event("error", {"message": f"LLM 不可用: {exc}"}))
        return {"plan": [], "errors": {}, "findings": {}, "subtask_results": {}, "todos": []}

    # 构建 Orchestrator system_prompt（fill experts/max_tasks/context）
    experts = _BASE_EXPERTS
    team_desc = _build_team_experts_description(settings)
    if team_desc:
        experts = experts + "\n" + team_desc
    system_prompt = _ORCHESTRATOR_SYSTEM_PROMPT.format(
        experts=experts,
        max_tasks=settings.agent_team_max_tasks,
        context=_build_project_context(),
    )

    # 直接 llm.ainvoke（不依赖 create_deep_agent + write_todos 工具）
    try:
        response = await llm.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": state["message"]},
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("team orchestrator invoke failed", error=str(exc))
        writer(make_sse_event("error", {"message": f"Orchestrator 调用失败: {exc}"}))
        return {"plan": [], "errors": {}, "findings": {}, "subtask_results": {}, "todos": []}

    # 从回复正文解析 [agent:xxx] 前缀任务行
    # response.content 可能是 str 或 list（多模态/工具调用模型返回 content blocks）
    raw_content = response.content if hasattr(response, "content") else str(response)
    if isinstance(raw_content, list):
        text = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in raw_content
        )
    else:
        text = str(raw_content)
    todos = _parse_todos_from_text(text)
    tasks, reasoning = _todos_to_team_tasks(todos, settings)
    if not tasks:
        # 诊断日志：记录 LLM 响应预览，便于排查"未生成有效计划"根因
        # （常见原因：LLM 输出格式不匹配 [agent:xxx] 正则、返回空回复等）
        logger.warning(
            "team orchestrator no valid tasks parsed",
            todos_count=len(todos),
            response_preview=text[:500],
            response_len=len(text),
        )
        writer(make_sse_event("error", {"message": "Orchestrator 未生成有效计划"}))
        return {"plan": [], "errors": {}, "findings": {}, "subtask_results": {}, "todos": []}

    # 剥离 [agent:xxx] 前缀后存入 state.todos，确保 SSE todo_update 透传到前端的是纯文本
    clean_todos = _strip_agent_prefix_from_todos(todos)
    # 同步截断 clean_todos 到与 tasks 相同数量（_todos_to_team_tasks 可能已按 max_tasks 截断）
    if len(clean_todos) > len(tasks):
        clean_todos = clean_todos[:len(tasks)]

    # 发射 team_init 事件：前端据此在消息顶部创建 TeamNodeCard（含 plan + agents）
    writer(make_sse_event("team_init", {
        "plan": [{"agent": t.agent, "input": t.input, "purpose": t.purpose} for t in tasks],
        "agents": [{"agent": t.agent, "purpose": t.purpose, "status": "pending"} for t in tasks],
        "reasoning": reasoning,
    }))

    return {"plan": tasks, "todos": clean_todos, "reasoning": reasoning}


def _dispatch_node(state: TeamState) -> list[Send]:
    """dispatch 条件边：把每个子任务 fan-out 到对应类型的执行节点（LangGraph Send API）。

    传递 ``todos`` 快照到子任务节点，供其构造 ``todo_update`` 事件时引用完整列表。

    Returns:
        每个子任务一个 ``Send(node_name, SubtaskState)``，LangGraph 并行执行。
        空计划时返回 ``[Send("aggregate", {})]``，确保 graph 路由到 aggregate 节点
        发射 team_done（C1 修复：避免空计划导致 graph 终止无 team_done）。
    """
    plan = state.get("plan", [])
    if not plan:
        return [Send("aggregate", {})]
    thread_id = state.get("thread_id", "")
    todos = state.get("todos", [])
    settings = get_settings()
    sends: list[Send] = []
    for idx, task in enumerate(plan):
        node_name = _route_node_for_task(task.agent, settings)
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
                    "todos": todos,
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


def _route_node_for_task(agent: str, settings: Any) -> str:
    """把 ``TeamPlanTask.agent`` 映射到对应的执行节点名。"""
    if agent == "deep":
        return "deep_node"
    if agent == "code":
        return "code_node"
    if agent in ("rag", "web"):
        return "builtin_node"
    if agent.startswith("custom-"):
        return "custom_node"
    if agent in (settings.team_subagents or {}):
        return "team_role_node"
    return "default_node"


def _make_subtask_state_update(result: TeamSubtaskResult, task_index: int) -> dict:
    """把 ``TeamSubtaskResult`` 转换为 StateGraph reducer 兼容的 state update。

    包含 ``todos`` 部分更新（``{"_index": task_index, "status": "completed"}``），
    由 ``_merge_todos`` reducer 归并到全局 ``state.todos``。

    findings/errors/subtask_results 的 key 使用 ``f"{agent}-{task_index}"``，
    避免多个同类型并行子任务（如两个 code 子任务）结果互相覆盖。
    """
    todo_update = [{"_index": task_index, "status": "completed"}]
    key = f"{result.agent}-{task_index}"
    if result.success:
        return {
            "findings": {key: result.payload},
            "subtask_results": {
                key: {
                    "success": True,
                    "payload": result.payload,
                },
            },
            "todos": todo_update,
        }
    return {
        "errors": {key: result.payload},
        "subtask_results": {
            key: {
                "success": False,
                "payload": result.payload,
            },
        },
        "todos": todo_update,
    }


# AGENTS.md §13: source 旧值 code/deep/agent 已废弃，映射为新值
_SOURCE_MAP: dict[str, str] = {"code": "coding", "deep": "work", "agent": "work"}


def _emit_delegation(
    writer: Callable,
    agent: str,
    purpose: str,
) -> None:
    """子任务开始前发射 ``delegation`` 事件，前端据此创建 SubAgentGroup 容器。

    前端 ``AssistantMessageParts`` 的分组逻辑依赖 ``delegation`` part 来开启新的
    子代理卡片（DelegationCard + 可折叠执行轨迹容器）。若不发射此事件，
    子代理的 tool_call / tool_result 会散落为独立卡片，无法聚合。

    Args:
        writer: LangGraph stream writer。
        agent: 子代理角色名（原始值，如 ``"code"`` / ``"rag"`` / ``"frontend_dev"``）。
        purpose: 任务简述（展示在 DelegationCard 副标题）。
    """
    writer(make_sse_event("delegation", {
        "target": agent,
        "source": "team",
        "message": purpose,
    }))


def _emit_todo_in_progress(
    writer: Callable,
    todos: list[dict],
    task_index: int,
    parent_thread_id: str,
    agent_role: str,
) -> None:
    """子任务开始时标记对应 todo 为 in_progress 并发送 ``todo_update`` 事件。

    构造局部 todos 副本（仅当前 index 改为 in_progress），供前端展示进度。
    并行子任务各自发送的 todo_update 可能短暂竞态，但 ``stream_mode="values"``
    会在 reducer 归并后发送最终正确的 todo_update。

    Args:
        writer: LangGraph stream writer。
        todos: 当前 todos 快照。
        task_index: 当前子任务在 todos 中的索引。
        parent_thread_id: 父 thread_id，作为 ``parent_task_id`` 注入 payload，
                         前端据此把子任务 todo 嵌套到父任务卡片下。
        agent_role: 子任务角色（如 ``"deep"`` / ``"code"`` / ``"rag"`` / ``"web"``
                   / ``"frontend_dev"`` / ``"backend_dev"`` 等），作为 ``source`` 注入 payload，
                   前端据此按角色分组渲染子任务。旧值 ``code``/``deep``/``agent``
                   自动映射为 ``coding``/``work``/``work``（AGENTS.md §13）。
    """
    mapped_source = _SOURCE_MAP.get(agent_role, agent_role)
    updated = list(todos)
    if 0 <= task_index < len(updated):
        updated[task_index] = {**updated[task_index], "status": "in_progress"}
    # H2: 记录已启动子任务索引，供 values-mode todo_update 合并 in_progress 状态，
    # 避免 reducer 归并后的 pending 状态覆盖前端已渲染的 in_progress。
    _in_progress_tasks.setdefault(parent_thread_id, set()).add(task_index)
    writer(
        make_todo_update_event(
            updated,
            task_id=parent_thread_id,
            source=mapped_source,
            parent_task_id=parent_thread_id,
        )
    )


async def _deep_node(state: SubtaskState) -> dict:
    """deep 子任务节点：执行 DeepAgent 路径（审批 + 写文件 + 命令）。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    task_index = state["task_index"]
    todos = state.get("todos", [])

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"), task_index)

    child_id = f"{parent_thread_id}-team-deep-{task_index}"
    await _inherit_workspace(child_id, state.get("workspace_path"))
    _emit_delegation(writer, task.agent, task.purpose)
    _emit_todo_in_progress(writer, todos, task_index, parent_thread_id, task.agent)

    deep_state: dict = {
        "thread_id": child_id,
        "messages": [{"role": "user", "content": task.input}],
    }
    runner = _get_runner("deep", state.get("subtask_runners"))
    try:
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
    except asyncio.CancelledError:  # noqa: B904 — H4: 断连取消，返回部分状态以便聚合
        logger.info("team subtask cancelled", agent=task.agent, task_index=task_index)
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="子任务已取消"),
            task_index,
        )
    return _make_subtask_state_update(result, task_index)


async def _code_node(state: SubtaskState) -> dict:
    """code 子任务节点：执行 coding Expert。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    task_index = state["task_index"]
    todos = state.get("todos", [])

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"), task_index)

    child_id = f"{parent_thread_id}-team-code-{task_index}"
    await _inherit_workspace(child_id, state.get("workspace_path"))
    _emit_delegation(writer, task.agent, task.purpose)
    _emit_todo_in_progress(writer, todos, task_index, parent_thread_id, task.agent)

    runner = _get_runner("code", state.get("subtask_runners"))
    try:
        result = await _run_subtask_stream(
            runner,
            runner_args=(task.input, child_id),
            runner_kwargs={
                "profile_prompt": state.get("profile_prompt", ""),
                "permission_mode": state.get("permission_mode", "standard"),
                "workspace_path": state.get("workspace_path"),
                "parent_thread_id": parent_thread_id,
                "chat_model": state.get("chat_model"),
            },
            agent_name=task.agent,
            abort_event=abort_event,
            writer=writer,
        )
    except asyncio.CancelledError:  # noqa: B904 — H4: 断连取消，返回部分状态以便聚合
        logger.info("team subtask cancelled", agent=task.agent, task_index=task_index)
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="子任务已取消"),
            task_index,
        )
    return _make_subtask_state_update(result, task_index)


async def _builtin_node(state: SubtaskState) -> dict:
    """builtin（rag / web）子任务节点。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    task_index = state["task_index"]
    todos = state.get("todos", [])

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"), task_index)

    _emit_delegation(writer, task.agent, task.purpose)
    _emit_todo_in_progress(writer, todos, task_index, parent_thread_id, task.agent)

    runner = _get_runner(task.agent, state.get("subtask_runners"))
    try:
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
    except asyncio.CancelledError:  # noqa: B904 — H4: 断连取消，返回部分状态以便聚合
        logger.info("team subtask cancelled", agent=task.agent, task_index=task_index)
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="子任务已取消"),
            task_index,
        )
    return _make_subtask_state_update(result, task_index)


async def _team_role_node(state: SubtaskState) -> dict:
    """团队角色子任务节点（frontend_dev / backend_dev / tester 等）。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    task_index = state["task_index"]
    todos = state.get("todos", [])

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"), task_index)

    _emit_delegation(writer, task.agent, task.purpose)
    _emit_todo_in_progress(writer, todos, task_index, parent_thread_id, task.agent)

    try:
        result = await _run_team_role_subtask(
            task=task,
            thread_id=parent_thread_id,
            history=state.get("history"),
            permission_mode=state.get("permission_mode", "standard"),
            profile_prompt=state.get("profile_prompt", ""),
            task_index=task_index,
            workspace_path=state.get("workspace_path"),
            chat_model=state.get("chat_model"),
            subtask_runners=state.get("subtask_runners"),
            abort_event=abort_event,
            writer=writer,
        )
    except asyncio.CancelledError:  # noqa: B904 — H4: 断连取消，返回部分状态以便聚合
        logger.info("team subtask cancelled", agent=task.agent, task_index=task_index)
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="子任务已取消"),
            task_index,
        )
    return _make_subtask_state_update(result, task_index)


async def _custom_node(state: SubtaskState) -> dict:
    """custom-* 子任务节点。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    task_index = state["task_index"]
    todos = state.get("todos", [])

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"), task_index)

    _emit_delegation(writer, task.agent, task.purpose)
    _emit_todo_in_progress(writer, todos, task_index, parent_thread_id, task.agent)

    key = task.agent[len("custom-"):]
    runner = _get_runner("custom", state.get("subtask_runners"))
    try:
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
    except asyncio.CancelledError:  # noqa: B904 — H4: 断连取消，返回部分状态以便聚合
        logger.info("team subtask cancelled", agent=task.agent, task_index=task_index)
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="子任务已取消"),
            task_index,
        )
    return _make_subtask_state_update(result, task_index)


async def _default_node(state: SubtaskState) -> dict:
    """未知 agent 类型的 fallback 节点。"""
    task = TeamPlanTask(**state["task"])
    task_index = state["task_index"]
    err_msg = f"未知 agent: {task.agent}"
    return _make_subtask_state_update(
        TeamSubtaskResult(agent=task.agent, success=False, payload=err_msg),
        task_index,
    )


async def _aggregate_node(state: TeamState) -> dict:
    """Aggregator 汇总节点：综合黑板内容生成最终回复。"""
    writer = get_stream_writer()
    thread_id = state.get("thread_id", "")
    # H2: 清理本 thread 的 in_progress 跟踪集合，避免泄漏到下次会话
    _in_progress_tasks.pop(thread_id, None)

    try:
        findings: dict[str, str] = state.get("findings", {})
        errors: dict[str, str] = state.get("errors", {})
        plan: list = state.get("plan", [])

        if not findings:
            if not plan:
                writer(make_sse_event("team_done", {"status": "error"}))
                return {}
            writer(make_sse_event("error", {"message": "所有专家任务均失败"}))
            writer(make_sse_event("team_done", {"status": "error"}))
            return {}

        # H3: aggregator 调用前检查 abort_event，已中止则提前返回 team_done
        abort_event = await get_abort_event(thread_id)
        if abort_event.is_set():
            writer(make_sse_event("team_done", {"status": "error", "error": "用户中止"}))
            return {}

        blackboard = Blackboard(findings=dict(findings), errors=dict(errors))
        message = state["message"]
        chat_model = state.get("chat_model")

        async for sse in _run_aggregator(message, blackboard, chat_model=chat_model, abort_event=abort_event):
            writer(sse)

        has_error = bool(errors)
        # 把每个子任务的 summary 随 team_done 回传给前端，回填 TeamNodeCard 的 agent 输出
        agent_summaries = [
            {"agent": task.agent, "summary": findings.get(f"{task.agent}-{idx}", "")}
            for idx, task in enumerate(plan)
            if f"{task.agent}-{idx}" in findings
        ]
        writer(
            make_sse_event(
                "team_done",
                {"status": "error" if has_error else "done", "agents": agent_summaries},
            )
        )
        logger.info(
            "team aggregate_node completed",
            has_error=has_error,
            findings_count=len(findings),
            errors_count=len(errors),
        )
        return {}
    except Exception as exc:  # noqa: BLE001 — C2: 异常时仍发射 team_done，并 re-raise 让 chat.py 也能感知错误
        logger.exception("team aggregate_node failed")
        writer(make_sse_event("team_done", {"status": "error", "error": str(exc)}))
        raise


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
    permission_mode: str = "standard",
    scene_prompt: str | None = None,
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
    subtask_runners: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """AgentTeam 路径入口（LangGraph StateGraph + Send API 编排）。

    1. _plan_node: Orchestrator 用 ``llm.ainvoke`` 拆任务 → ``state.todos``
    2. _dispatch_node: 并行 fan-out 子任务到执行节点
    3. _{deep,code,builtin,team_role,custom}_node: 执行子任务 → todos 状态更新
    4. _aggregate_node: 汇总 → token / reasoning 事件

    事件流通过 ``stream_mode=["custom", "values"]`` 消费：
    - ``custom`` 模式：子任务节点通过 ``get_stream_writer()`` 写入的 passthrough
      事件（approval_request / token / tool_result 等）实时透传。
    - ``values`` 模式：每次节点返回后 state.todos diff 检测，产出 ``todo_update``
      事件（reducer 归并后的全局视图，避免并行竞态）。

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
        yield make_sse_event("team_done", {"status": "done"})
        return

    resolved_runners = _resolve_subtask_runners(subtask_runners)

    # trace_id 透传：LangGraph 内部用 asyncio.create_task 调度子节点，
    # ContextVar 不会自动跨协程传播。显式在入口处绑定，让 LangGraph 子节点
    # （_plan_node / _deep_node / _aggregate_node 等）的 logger / make_sse_event
    # 也能拿到 trace_id，便于用户报问题时通过 grep data/logs/backend.log 排查链路。
    _trace_id = current_trace_id() or ""
    _trace_cm = bind_trace(_trace_id) if _trace_id else contextlib.nullcontext()
    with _trace_cm, trace_span("team.run", thread_id=thread_id, message_len=len(message)):
        # H2: 进入新会话前清理本 thread 的 in_progress 跟踪集合，避免上次残留
        _in_progress_tasks.pop(thread_id, None)
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
            "todos": [],
        }

        # stream_mode=["custom", "values"]：
        # - custom: 子任务节点 writer 写入的 passthrough 事件
        # - values: 每次节点返回后的完整 state（用于 todos diff → todo_update）
        last_todos: list[dict] = []
        async for chunk in graph.astream(
            initial_state,
            stream_mode=["custom", "values"],
        ):
            if not isinstance(chunk, tuple) or len(chunk) != 2:
                continue
            mode, payload = chunk
            if mode == "custom":
                yield payload
            elif mode == "values":
                current_todos = payload.get("todos", []) if isinstance(payload, dict) else []
                if current_todos != last_todos:
                    # H2: 合并 in_progress 状态——reducer 归并后的 state.todos 只有
                    # pending/completed，但前端已通过 custom-stream 收到 in_progress；
                    # 这里把 _in_progress_tasks 中索引对应的 pending 改为 in_progress，
                    # 避免 values-mode 覆盖导致 in_progress → pending 回退。
                    in_progress = _in_progress_tasks.get(thread_id)
                    if in_progress:
                        current_todos = [
                            {**t, "status": "in_progress"}
                            if isinstance(t, dict) and t.get("status") == "pending" and i in in_progress
                            else t
                            for i, t in enumerate(current_todos)
                        ]
                    # L19: 与 _emit_todo_in_progress 保持一致的字段（task_id/source/parent_task_id）
                    yield make_todo_update_event(
                        current_todos,
                        task_id=thread_id,
                        source="team",
                        parent_task_id=thread_id,
                    )
                    last_todos = list(current_todos)


__all__ = [
    "run_team_path",
    "Blackboard",
    "TeamPlanTask",
    "TeamSubtaskResult",
    "TeamState",
    "SubtaskState",
    "_run_subtask_stream",
    "_run_team_role_subtask",
    "_should_downgrade_to_single",
    "_validate_task",
    "_todos_to_team_tasks",
    "_build_project_context",
    "_resolve_subtask_runners",
    "_SUBTASK_DONE_EVENT",
    "_PASSTHROUGH_EVENTS",
    "_BASE_EXPERTS",
    "_ORCHESTRATOR_SYSTEM_PROMPT",
    "_parse_todos_from_text",
]
