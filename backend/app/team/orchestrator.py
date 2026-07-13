"""AgentTeam 路径：LangGraph StateGraph + Send API 编排多代理协作。

本模块用 LangGraph 原生 ``Send`` API 替代 ``asyncio.Queue`` + ``Semaphore``
手工并行，实现可检查点、可调试的 Map-Reduce 子任务编排：

- ``_plan_node``: Orchestrator 直接 ``llm.ainvoke`` 调用 LLM，让其在回复正文
  输出 ``[agent:xxx]`` 任务行，``_parse_todos_from_text`` 解析为 deepagents
  原生 Todo schema，``_todos_to_team_tasks`` 转换为 ``TeamPlanTask`` 列表。
  （旧方案用 ``create_deep_agent`` + ``TodoListMiddleware`` 依赖 LLM 调用
  ``write_todos`` 工具，但 ``WRITE_TODOS_SYSTEM_PROMPT`` 主动建议不调用，
  导致 99% 失败率。）
- ``_dispatch_node``: 把每个子任务 fan-out 到统一执行节点 ``subtask``，同时
  传递 ``todos`` 快照供子任务节点构造 ``todo_update`` 事件。
- ``_run_subtask_node``: 单一统一子任务执行节点，按 ``_NODE_DISPATCH`` 派发表 +
  ``SubtaskConfig`` 数据类路由到对应 runner（deep/code/builtin/team_role/custom/
  default）。替代旧方案 6 个独立节点函数。
- ``_aggregate_node``: 汇总 findings。

状态归并：``TeamState`` 使用 ``_merge_dict`` reducer（findings/errors）和
``_merge_todos`` reducer（todos，粘性 in_progress），并行子任务节点返回的
部分 dict/list 自动归并。

事件流：
- ``todo_update`` 事件从 ``stream_mode="values"`` 的 state.todos diff 产出
  （reducer 归并后的全局视图，粘性 in_progress 由 reducer 保证，无需运行时 overlay）。
- ``_run_subtask_node`` 通过 ``get_stream_writer()`` 写入 custom stream（token /
  tool_result / approval_request 等），由 ``run_team_path`` 通过
  ``graph.astream(stream_mode=["custom", "values"])`` 消费后 yield 给调用方。

安全：危险任务（含写入/编辑/shell 关键词）由 ``_todos_to_team_tasks``
强制改写为 deep，由 ``_run_subtask_node`` 走 ``run_deep_path`` 的 interrupt_on 审批。

Phase 2 清理：删除 ``_in_progress_tasks`` 全局（粘性 reducer 替代）、删除
``subtask_results`` 死字段、统一 6 节点为 ``_run_subtask_node``、降级路径不产出
team 生命周期事件。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
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
    _todos_to_team_tasks,
    _validate_task,
    tasks_to_display_todos,
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


def _acquire_semaphore(semaphore: Any) -> Any:
    """获取信号量上下文管理器；``None`` 时返回 no-op（防御性降级）。

    Phase 1 稳定性硬化：信号量在 ``run_team_path`` 入口按 ``max_parallel`` 创建，
    通过 ``TeamState["team_semaphore"]`` 传递到各子任务节点。
    """
    if semaphore is None:
        return contextlib.nullcontext()
    return semaphore


def _resolve_team_settings(settings: Any) -> tuple[int, int]:
    """从 settings 读取 team 并发参数。

    优先级：``settings.agent_team_*`` 顶层字段 → 默认值。
    返回 ``(max_parallel, subtask_timeout)``。

    Phase 1 稳定性硬化：``max_parallel`` 控制 LangGraph ``Send`` fan-out 并行度，
    ``subtask_timeout`` 为单个子任务超时秒数。对值做防御性校验（None/0/负数降级到默认值）。
    """
    try:
        val = settings.agent_team_max_parallel
        max_parallel = val if isinstance(val, int) and val > 0 else 3
    except Exception:  # noqa: BLE001
        max_parallel = 3
    try:
        val = settings.agent_team_subtask_timeout
        subtask_timeout = val if isinstance(val, int) and val >= 30 else 300
    except Exception:  # noqa: BLE001
        subtask_timeout = 300
    return max_parallel, subtask_timeout

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
        return {"plan": [], "errors": {}, "findings": {}, "todos": []}

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
        return {"plan": [], "errors": {}, "findings": {}, "todos": []}

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
        return {"plan": [], "errors": {}, "findings": {}, "todos": []}

    # tasks 为单一数据源，展示用 todos 由 tasks_to_display_todos 派生（T4.8）
    # 消除旧 clean_todos / tasks 双列表：tasks 已经过 _todos_to_team_tasks 剥离前缀 + 截断
    display_todos = tasks_to_display_todos(tasks)

    # 发射 team_init 事件：前端据此在消息顶部创建 TeamNodeCard（含 plan + agents）
    writer(make_sse_event("team_init", {
        "plan": [{"agent": t.agent, "input": t.input, "purpose": t.purpose} for t in tasks],
        "agents": [{"agent": t.agent, "purpose": t.purpose, "status": "pending"} for t in tasks],
        "reasoning": reasoning,
    }))

    return {"plan": tasks, "todos": display_todos, "reasoning": reasoning}


def _dispatch_node(state: TeamState) -> list[Send]:
    """dispatch 条件边：把每个子任务 fan-out 到统一执行节点 ``subtask``（LangGraph Send API）。

    Phase 2 统一：所有 agent 类型都发送到单一 ``subtask`` 节点，由
    ``_run_subtask_node`` 内部按 ``_NODE_DISPATCH`` 派发表路由到对应 runner。
    替代旧方案按 agent 类型发送到 6 个不同节点。

    传递 ``todos`` 快照到子任务节点，供其构造 ``todo_update`` 事件时引用完整列表。

    Returns:
        每个子任务一个 ``Send("subtask", SubtaskState)``，LangGraph 并行执行。
        空计划时返回 ``[Send("aggregate", {})]``，确保 graph 路由到 aggregate 节点
        发射 team_done（C1 修复：避免空计划导致 graph 终止无 team_done）。
    """
    plan = state.get("plan", [])
    if not plan:
        return [Send("aggregate", {})]
    thread_id = state.get("thread_id", "")
    todos = state.get("todos", [])
    sends: list[Send] = []
    for idx, task in enumerate(plan):
        sends.append(
            Send(
                "subtask",
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
                    # Phase 1 稳定性硬化：透传信号量与超时到子任务节点
                    "team_semaphore": state.get("team_semaphore"),
                    "subtask_timeout": state.get("subtask_timeout", 300),
                },
            )
        )
    return sends


def _make_subtask_state_update(result: TeamSubtaskResult, task_index: int) -> dict:
    """把 ``TeamSubtaskResult`` 转换为 StateGraph reducer 兼容的 state update。

    包含 ``todos`` 部分更新（``{"_index": task_index, "status": "completed"}``），
    由 ``_merge_todos`` reducer 归并到全局 ``state.todos``。

    findings/errors 的 key 使用 ``f"{agent}-{task_index}"``，
    避免多个同类型并行子任务（如两个 code 子任务）结果互相覆盖。

    Phase 2 清理：删除 ``subtask_results`` 死字段（与 findings/errors 信息重复）。
    """
    todo_update = [{"_index": task_index, "status": "completed"}]
    key = f"{result.agent}-{task_index}"
    if result.success:
        return {
            "findings": {key: result.payload},
            "todos": todo_update,
        }
    return {
        "errors": {key: result.payload},
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

    Phase 2 清理：删除 ``_in_progress_tasks`` 全局 side effect。粘性 in_progress
    由 ``_merge_todos`` reducer 保证（T3.1）：任一侧 in_progress 且新状态非终态
    则保持 in_progress，无需运行时 overlay。

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
    writer(
        make_todo_update_event(
            updated,
            task_id=parent_thread_id,
            source=mapped_source,
            parent_task_id=parent_thread_id,
        )
    )


@dataclass
class SubtaskConfig:
    """子任务执行配置：统一 6 个子任务节点的派发依据。

    Phase 2 统一：``_run_subtask_node`` 按 ``_NODE_DISPATCH`` 派发表 +
    ``SubtaskConfig`` 数据类路由到对应 runner，替代旧方案 6 个独立节点函数
    （``_deep_node`` / ``_code_node`` / ``_builtin_node`` / ``_team_role_node``
    / ``_custom_node`` / ``_default_node``）。

    Phase 2 T5：``child_id`` UUID 化（``{parent}-team-{uuid4()}``），不再含
    agent 名与 idx，故删除 ``child_id_suffix`` 字段。
    """

    runner_type: str  # "deep" | "code" | "builtin" | "team_role" | "custom" | "default"
    runner_key: str | None  # _get_runner 的 key；None 表示无 runner（default/team_role）
    needs_workspace_inherit: bool  # 是否需要构造 child_id 并 _inherit_workspace


_NODE_DISPATCH: dict[str, SubtaskConfig] = {
    "deep": SubtaskConfig(
        runner_type="deep",
        runner_key="deep",
        needs_workspace_inherit=True,
    ),
    "code": SubtaskConfig(
        runner_type="code",
        runner_key="code",
        needs_workspace_inherit=True,
    ),
    "builtin": SubtaskConfig(
        runner_type="builtin",
        runner_key=None,  # runtime set to task.agent (rag/web)
        needs_workspace_inherit=False,
    ),
    "team_role": SubtaskConfig(
        runner_type="team_role",
        runner_key=None,  # uses _run_team_role_subtask directly
        needs_workspace_inherit=False,
    ),
    "custom": SubtaskConfig(
        runner_type="custom",
        runner_key="custom",
        needs_workspace_inherit=False,
    ),
    "default": SubtaskConfig(
        runner_type="default",
        runner_key=None,
        needs_workspace_inherit=False,
    ),
}

_DEFAULT_CONFIG = _NODE_DISPATCH["default"]


def _resolve_subtask_config(agent: str, settings: Any) -> SubtaskConfig:
    """根据 agent 类型解析 SubtaskConfig。

    builtin 类型（rag/web）的 runner_key 在运行时设置为 agent 本身，
    其余类型从 ``_NODE_DISPATCH`` 静态表取。

    Args:
        agent: ``TeamPlanTask.agent`` 字段（deep/code/rag/web/custom-*/team_role）。
        settings: 全局配置（用于判断 team_subagents 成员）。

    Returns:
        对应的 ``SubtaskConfig``；未知 agent 返回 ``_DEFAULT_CONFIG``。
    """
    if agent == "deep":
        return _NODE_DISPATCH["deep"]
    if agent == "code":
        return _NODE_DISPATCH["code"]
    if agent in ("rag", "web"):
        # builtin: runner_key 运行时设置为 agent 本身（rag/web）
        return SubtaskConfig(
            runner_type="builtin",
            runner_key=agent,
            needs_workspace_inherit=False,
        )
    if agent.startswith("custom-"):
        return _NODE_DISPATCH["custom"]
    if agent in (settings.team_subagents or {}):
        return _NODE_DISPATCH["team_role"]
    return _DEFAULT_CONFIG


def _build_runner_args(
    config: SubtaskConfig,
    task: TeamPlanTask,
    child_id: str | None,
    parent_thread_id: str,
) -> tuple:
    """构造 runner_args（仅 runner_type 为 deep/code/builtin/custom 时调用）。

    各类型的 args 结构与旧方案 6 个节点函数保持一致：
    - deep: ``(deep_state, task.input)``，deep_state 含 thread_id + messages
    - code: ``(task.input, child_id)``
    - builtin: ``(parent_thread_id, task.input)``
    - custom: ``(key, parent_thread_id, task.input)``，key 剥离 ``custom-`` 前缀
    """
    if config.runner_type == "deep":
        deep_state: dict = {
            "thread_id": child_id,
            "messages": [{"role": "user", "content": task.input}],
        }
        return (deep_state, task.input)
    if config.runner_type == "code":
        return (task.input, child_id)
    if config.runner_type == "builtin":
        return (parent_thread_id, task.input)
    if config.runner_type == "custom":
        key = task.agent[len("custom-"):]
        return (key, parent_thread_id, task.input)
    raise ValueError(f"Unsupported runner_type for args: {config.runner_type}")


def _build_runner_kwargs(config: SubtaskConfig, state: SubtaskState, parent_thread_id: str) -> dict:
    """构造 runner_kwargs。

    各类型的 kwargs 与旧方案 6 个节点函数保持一致：
    - deep: profile_prompt/history/permission_mode/scene_prompt/workspace_path/parent_thread_id/chat_model
    - code: profile_prompt/permission_mode/workspace_path/parent_thread_id/chat_model
    - builtin/custom: history/workspace_path
    """
    if config.runner_type == "deep":
        return {
            "profile_prompt": state.get("profile_prompt", ""),
            "history": state.get("history"),
            "permission_mode": state.get("permission_mode", "standard"),
            "scene_prompt": state.get("scene_prompt"),
            "workspace_path": state.get("workspace_path"),
            "parent_thread_id": parent_thread_id,
            "chat_model": state.get("chat_model"),
        }
    if config.runner_type == "code":
        return {
            "profile_prompt": state.get("profile_prompt", ""),
            "permission_mode": state.get("permission_mode", "standard"),
            "workspace_path": state.get("workspace_path"),
            "parent_thread_id": parent_thread_id,
            "chat_model": state.get("chat_model"),
        }
    if config.runner_type in ("builtin", "custom"):
        return {
            "history": state.get("history"),
            "workspace_path": state.get("workspace_path"),
        }
    raise ValueError(f"Unsupported runner_type for kwargs: {config.runner_type}")


async def _run_subtask_node(state: SubtaskState) -> dict:
    """统一子任务执行节点：按 ``_NODE_DISPATCH`` 派发表路由到对应 runner。

    Phase 2 统一：替代旧方案 6 个独立节点函数（``_deep_node`` / ``_code_node`` /
    ``_builtin_node`` / ``_team_role_node`` / ``_custom_node`` / ``_default_node``），
    所有 agent 类型通过单一节点入口，按 ``SubtaskConfig`` 派发到对应 runner。

    保留 Phase 1 稳定性保护：``team_semaphore`` 并发限流 + ``subtask_timeout`` 超时 +
    ``abort_event`` 中止检查 + ``asyncio.CancelledError`` 断连处理。

    Args:
        state: ``SubtaskState``，含 task / task_index / parent_thread_id / todos
            及透传的 history / permission_mode / chat_model 等。

    Returns:
        StateGraph reducer 兼容的 state update（findings/errors + todos 部分更新）。
    """
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    task_index = state["task_index"]
    todos = state.get("todos", [])
    semaphore = state.get("team_semaphore")
    subtask_timeout = state.get("subtask_timeout", 300)

    settings = get_settings()
    config = _resolve_subtask_config(task.agent, settings)

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"),
            task_index,
        )

    # default fallback：未知 agent 类型，先发射 delegation + todo_in_progress 创建
    # 前端 SubAgentGroup 容器（spec: 未知 agent 类型经默认配置统一失败），
    # 再返回失败状态更新（reducer 归并后前端展示 error 状态）
    if config.runner_type == "default":
        _emit_delegation(writer, task.agent, task.purpose)
        _emit_todo_in_progress(writer, todos, task_index, parent_thread_id, task.agent)
        err_msg = f"未知 agent: {task.agent}"
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload=err_msg),
            task_index,
        )

    child_id: str | None = None
    if config.needs_workspace_inherit:
        # T5: child_id UUID 化（{parent}-team-{uuid4()}），不再含 agent 名与 idx，
        # 同一 parent 的 retry 因 UUID 不同不会撞 stale checkpoint
        child_id = f"{parent_thread_id}-team-{uuid.uuid4()}"
        # T7: 授权失败返回失败结果，跳过 runner，避免 directory_extension 审批死锁
        inherit_result = await _inherit_workspace(
            child_id, state.get("workspace_path"), agent_name=task.agent
        )
        if inherit_result is not None:
            return _make_subtask_state_update(inherit_result, task_index)

    _emit_delegation(writer, task.agent, task.purpose)
    _emit_todo_in_progress(writer, todos, task_index, parent_thread_id, task.agent)

    try:
        async with _acquire_semaphore(semaphore):
            if config.runner_type == "team_role":
                # 团队角色走专用 runner（_run_team_role_subtask）
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
                    subtask_timeout=subtask_timeout,
                )
            else:
                # deep/code/builtin/custom 走通用 _run_subtask_stream
                runner = _get_runner(config.runner_key, state.get("subtask_runners"))
                runner_args = _build_runner_args(config, task, child_id, parent_thread_id)
                runner_kwargs = _build_runner_kwargs(config, state, parent_thread_id)
                result = await _run_subtask_stream(
                    runner,
                    runner_args=runner_args,
                    runner_kwargs=runner_kwargs,
                    agent_name=task.agent,
                    abort_event=abort_event,
                    writer=writer,
                    subtask_timeout=subtask_timeout,
                )
    except asyncio.CancelledError:  # noqa: B904 — H4: 断连取消，返回部分状态以便聚合
        logger.info("team subtask cancelled", agent=task.agent, task_index=task_index)
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="子任务已取消"),
            task_index,
        )
    return _make_subtask_state_update(result, task_index)


async def _aggregate_node(state: TeamState) -> dict:
    """Aggregator 汇总节点：综合黑板内容生成最终回复。"""
    writer = get_stream_writer()
    thread_id = state.get("thread_id", "")

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

        # Phase 2 清理：Blackboard dataclass 已删除，直接用 dict 传给 _run_aggregator
        blackboard: dict = {"findings": dict(findings), "errors": dict(errors)}
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
    except Exception:  # noqa: BLE001 — team_done{status:error} 由 run_coding_team 统一发射
        logger.exception("team aggregate_node failed")
        raise


# ============================================================
# StateGraph 构建（无 checkpointer，stateless，安全缓存）
# ============================================================


def _build_team_graph() -> Any:
    """构建 Team StateGraph：plan → dispatch（Send fan-out）→ subtask → aggregate。

    Phase 2 统一：所有 agent 类型共用单一 ``subtask`` 节点（``_run_subtask_node``），
    替代旧方案 6 个独立节点（deep_node/code_node/builtin_node/team_role_node/
    custom_node/default_node）。
    """
    graph: Any = StateGraph(TeamState)
    graph.add_node("plan", _plan_node)
    graph.add_node("subtask", _run_subtask_node)
    graph.add_node("aggregate", _aggregate_node)

    graph.add_edge(START, "plan")
    # dispatch 条件边：返回 list[Send] 触发 LangGraph 原生 fan-out（统一到 subtask 节点）
    graph.add_conditional_edges("plan", _dispatch_node)
    # subtask → aggregate
    graph.add_edge("subtask", "aggregate")
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
    2. _dispatch_node: 并行 fan-out 子任务到统一 ``subtask`` 节点
    3. _run_subtask_node: 按 ``_NODE_DISPATCH`` 派发执行子任务 → todos 状态更新
    4. _aggregate_node: 汇总 → token / reasoning 事件

    事件流通过 ``stream_mode=["custom", "values"]`` 消费：
    - ``custom`` 模式：子任务节点通过 ``get_stream_writer()`` 写入的 passthrough
      事件（approval_request / token / tool_result 等）实时透传。
    - ``values`` 模式：每次节点返回后 state.todos diff 检测，产出 ``todo_update``
      事件（reducer 归并后的全局视图，粘性 in_progress 由 reducer 保证）。

    Args:
        workspace_path: 当前会话绑定的 workspace 路径，透传到子代理 fs 工具。
        chat_model: 可选注入的 ChatModel，透传到子任务节点与 Aggregator。
        subtask_runners: 可选 ``{"code": callable, "rag": callable, ...}`` 字典，
            测试注入 mock 替代真实 runner。
    """
    # 简单任务降级：短消息 / 问候 / 翻译等无需 team 协作
    # T10: 降级路径不产出 team 生命周期事件（team_init / team_done），
    # 只发射 token 提示 + done 终结符。team 生命周期事件仅由真正进入 graph 的路径产出。
    downgrade, reason = _should_downgrade_to_single(message)
    if downgrade:
        logger.info(
            "team downgrade suggest switch mode", reason=reason, message_len=len(message)
        )
        yield make_sse_event(
            "token",
            f"该任务似乎不需要团队协作（{reason}）。建议切换到 work 模式由 Supervisor 直接处理。",
        )
        yield make_sse_event("done", {})
        return

    resolved_runners = _resolve_subtask_runners(subtask_runners)

    # Phase 1 D2：从 settings 解析并发参数，创建 Semaphore 控制 LangGraph Send fan-out 并行度
    settings = get_settings()
    max_parallel, subtask_timeout = _resolve_team_settings(settings)
    team_semaphore = asyncio.Semaphore(max_parallel)
    logger.info(
        "team.run_team_path start",
        thread_id=thread_id,
        max_parallel=max_parallel,
        subtask_timeout=subtask_timeout,
    )

    # trace_id 透传：LangGraph 内部用 asyncio.create_task 调度子节点，
    # ContextVar 不会自动跨协程传播。显式在入口处绑定，让 LangGraph 子节点
    # （_plan_node / _run_subtask_node / _aggregate_node 等）的 logger / make_sse_event
    # 也能拿到 trace_id，便于用户报问题时通过 grep data/logs/backend.log 排查链路。
    _trace_id = current_trace_id() or ""
    _trace_cm = bind_trace(_trace_id) if _trace_id else contextlib.nullcontext()
    with _trace_cm, trace_span("team.run", thread_id=thread_id, message_len=len(message)):
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
            "todos": [],
            # Phase 1 D2：运行时并发控制对象，不参与 checkpoint 序列化（TypedDict total=False）
            "team_semaphore": team_semaphore,
            "subtask_timeout": subtask_timeout,
        }

        # stream_mode=["custom", "values"]：
        # - custom: 子任务节点 writer 写入的 passthrough 事件
        # - values: 每次节点返回后的完整 state（用于 todos diff → todo_update）
        # Phase 2 清理：删除 _in_progress_tasks overlay 逻辑，粘性 in_progress
        # 由 _merge_todos reducer 保证（T3.1），values-mode 直接使用 reducer 归并后的 todos。
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
                    # L19: 与 _emit_todo_in_progress 保持一致的字段（task_id/source/parent_task_id）
                    yield make_todo_update_event(
                        current_todos,
                        task_id=thread_id,
                        source="team",
                        parent_task_id=thread_id,
                    )
                    last_todos = list(current_todos)

        # D4: graph 正常完成后发射 done 事件。
        # _aggregate_node 已通过 custom stream 发射 team_done，
        # 这里补 done 保证前端 SSE 流终结（team_done + done 配对契约）。
        # 异常路径由 run_coding_team catch 后统一发射 done。
        yield make_sse_event("done", {})


__all__ = [
    "run_team_path",
    "TeamPlanTask",
    "TeamSubtaskResult",
    "TeamState",
    "SubtaskState",
    "SubtaskConfig",
    "_run_subtask_stream",
    "_run_team_role_subtask",
    "_run_subtask_node",
    "_NODE_DISPATCH",
    "_resolve_subtask_config",
    "_should_downgrade_to_single",
    "_validate_task",
    "_todos_to_team_tasks",
    "_build_project_context",
    "_resolve_subtask_runners",
    "_resolve_team_settings",
    "_acquire_semaphore",
    "_SUBTASK_DONE_EVENT",
    "_PASSTHROUGH_EVENTS",
    "_BASE_EXPERTS",
    "_ORCHESTRATOR_SYSTEM_PROMPT",
    "_parse_todos_from_text",
]
