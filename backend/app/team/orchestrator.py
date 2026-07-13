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
    _AGENT_PREFIX_RE,
    _BASE_EXPERTS,
    _ORCHESTRATOR_SYSTEM_PROMPT,
    _build_project_context,
    _build_team_experts_description,
    _looks_like_dangerous_task,
    _parse_after_deps,
    _parse_todos_from_text,
    _todos_to_team_tasks,
    _validate_dag,
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
# DAG 依赖编排 helpers
# ============================================================


def _inject_dependency_context(
    task: TeamPlanTask, findings: dict[str, str]
) -> str:
    """把依赖任务的 findings 注入到 ``task.input`` 头部（DAG 依赖编排 D5）。

    无 deps 时原样返回 ``task.input``；有 deps 时构造 ``[依赖任务结果]`` 头部 +
    每个依赖的 ``--- #N [agent-N] ---`` + 内容 + ``[当前任务]`` + 原 input。
    依赖未完成时显示 ``--- #N (未完成或失败) ---``。每个依赖内容截断到
    ``agent_team_result_max_chars``（避免多依赖拼接导致 input 过长）。

    Args:
        task: 当前任务（含 ``deps`` 字段）。
        findings: 已完成任务的 findings dict，key 格式 ``"{agent}-{task_index}"``。

    Returns:
        注入依赖上下文后的 input 字符串。
    """
    if not task.deps:
        return task.input  # 无依赖，原样返回

    settings = get_settings()
    max_chars = settings.agent_team_result_max_chars
    sections: list[str] = ["[依赖任务结果]"]
    for dep_idx in task.deps:
        # 查找 dep_idx 对应的 finding（key 格式 "{agent}-{idx}"）
        # 遍历 findings 找到 key 后缀为 "-{dep_idx}" 的项
        dep_key: str | None = None
        for k in findings:
            if k.endswith(f"-{dep_idx}"):
                dep_key = k
                break
        if not dep_key:
            sections.append(f"--- #{dep_idx} (未完成或失败) ---")
            continue
        dep_content = findings[dep_key]
        if len(dep_content) > max_chars:
            dep_content = dep_content[:max_chars] + "\n[结果已截断]"
        sections.append(f"--- #{dep_idx} [{dep_key}] ---\n{dep_content}")

    sections.append(f"[当前任务]\n{task.input}")
    return "\n\n".join(sections)


# Replan 系统 prompt 模板（D8 迭代式 replan）
_REPLAN_SYSTEM_PROMPT = """你是任务拆解专家。以下是已完成的子任务结果：

{completed_findings}

用户原始请求：{user_message}

请判断是否需要追加新任务来完善最终回答。
- 若需要追加，输出新的任务行，格式：[agent:类型][after:N1,N2] 任务描述
  - after 引用已完成任务的索引（上方已列出）
  - 新任务必须依赖至少一个已完成任务（不能是无依赖的根任务）
- 若无需追加，输出：NO_NEW_TASKS
"""


# ============================================================
# StateGraph 节点
# ============================================================


async def _plan_node(state: TeamState) -> dict:
    """Orchestrator 拆任务节点：直接 ``llm.ainvoke`` + 文本解析。

    旧方案使用 ``create_deep_agent`` + ``TodoListMiddleware`` 依赖 LLM 自主调用
    ``write_todos`` 工具，但 ``TodoListMiddleware`` 注入的 ``WRITE_TODOS_SYSTEM_PROMPT``
    主动建议 LLM "简单任务不要用 write_todos"，导致 99% 情况下 ``write_todos``
    未被调用 → ``result.get("todos", [])`` 为空 → 无输出。

    新方案直接用 ``llm.ainvoke`` 调用 LLM，让其在回复正文输出 ``[agent:xxx][after:N1,N2]``
    任务行，``_parse_todos_from_text`` 逐行解析为 deepagents 原生 Todo schema（含 deps），
    再由 ``_todos_to_team_tasks`` 转换为 ``TeamPlanTask`` 列表。

    DAG 依赖编排：调用 ``_validate_dag(tasks)`` 计算拓扑分层 ``pending_levels``，
    初始化 ``completed_tasks: []`` 与 ``replan_count: 0``。
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
        return {
            "plan": [],
            "errors": {},
            "findings": {},
            "todos": [],
            "pending_levels": [],
            "completed_tasks": [],
            "replan_count": 0,
        }

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
        return {
            "plan": [],
            "errors": {},
            "findings": {},
            "todos": [],
            "pending_levels": [],
            "completed_tasks": [],
            "replan_count": 0,
        }

    # 从回复正文解析 [agent:xxx][after:N1,N2] 前缀任务行
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
        return {
            "plan": [],
            "errors": {},
            "findings": {},
            "todos": [],
            "pending_levels": [],
            "completed_tasks": [],
            "replan_count": 0,
        }

    # tasks 为单一数据源，展示用 todos 由 tasks_to_display_todos 派生（T4.8）
    # 消除旧 clean_todos / tasks 双列表：tasks 已经过 _todos_to_team_tasks 剥离前缀 + 截断
    display_todos = tasks_to_display_todos(tasks)

    # DAG 依赖编排：调用 _validate_dag 计算拓扑分层
    levels, dropped_edges = _validate_dag(tasks)
    if dropped_edges:
        logger.info(
            "team DAG validate dropped edges",
            dropped_count=len(dropped_edges),
            levels_count=len(levels),
        )

    # 发射 team_init 事件：前端据此在消息顶部创建 TeamNodeCard（含 plan + agents）
    # plan items 包含 deps 字段，前端可展示依赖关系
    writer(make_sse_event("team_init", {
        "plan": [
            {"agent": t.agent, "input": t.input, "purpose": t.purpose, "deps": t.deps}
            for t in tasks
        ],
        "agents": [{"agent": t.agent, "purpose": t.purpose, "status": "pending"} for t in tasks],
        "reasoning": reasoning,
    }))

    return {
        "plan": tasks,
        "todos": display_todos,
        "reasoning": reasoning,
        "pending_levels": levels,
        "completed_tasks": [],
        "replan_count": 0,
    }


def _dispatch_batch_node(state: TeamState) -> dict:
    """DAG 分批派发节点（入口）：无状态更新，路由由 ``_dispatch_batch_router`` 完成。

    LangGraph 0.5+ 要求节点返回 ``dict``（状态更新），``list[Send]`` 由
    ``add_conditional_edges`` 的 path function 返回。本节点返回空 dict，
    实际 fan-out 逻辑在 ``_dispatch_batch_router`` 中。
    """
    return {}


def _dispatch_batch_router(state: TeamState) -> list[Send]:
    """DAG 分批派发路由函数：取 ``pending_levels[0]`` 当前层任务 fan-out 到 ``subtask`` 节点。

    DAG 依赖编排（D4）：替代旧 ``_dispatch_node`` 一次性 fan-out 全部任务。
    本函数只派发当前层（层内并行），层间串行由 ``check_next_level`` 条件边回流。

    对每个任务调用 ``_inject_dependency_context`` 注入前置 findings，使依赖任务
    能看到已完成任务的输出。配置解析由 ``_run_subtask_node`` 内部
    ``_resolve_subtask_config`` 完成（未知 agent fallback 到 code runner）。

    传递 ``remaining_levels`` 到子任务节点，第一个完成的子任务通过
    ``_merge_pending_levels`` reducer 设置 ``pending_levels = remaining_levels``，
    其余返回空列表被 reducer 忽略。

    Returns:
        当前层每个任务一个 ``Send("subtask", SubtaskState)``，LangGraph 并行执行。
        空 ``pending_levels`` 时返回 ``[Send("aggregate", {})]``，确保 graph
        路由到 aggregate 节点发射 team_done（C1 修复）。
    """
    pending_levels = state.get("pending_levels", [])
    if not pending_levels:
        return [Send("aggregate", {})]

    current_level = pending_levels[0]
    remaining_levels = pending_levels[1:]

    plan = state.get("plan", [])
    findings = state.get("findings", {})
    todos = state.get("todos", [])
    thread_id = state.get("thread_id", "")

    sends: list[Send] = []
    for idx in current_level:
        if idx >= len(plan):
            continue
        task = plan[idx]
        # D5: 注入依赖上下文（findings 中前置任务的结果）
        injected_input = _inject_dependency_context(task, findings)
        sends.append(
            Send(
                "subtask",
                {
                    "task": {
                        "agent": task.agent,
                        "input": injected_input,
                        "purpose": task.purpose,
                        "deps": task.deps,
                    },
                    "task_index": idx,
                    "parent_thread_id": thread_id,
                    "todos": todos,
                    "remaining_levels": remaining_levels,
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


def _make_subtask_state_update(
    result: TeamSubtaskResult, task_index: int, *, remaining_levels: list[list[int]] | None = None
) -> dict:
    """把 ``TeamSubtaskResult`` 转换为 StateGraph reducer 兼容的 state update。

    包含 ``todos`` 部分更新（``{"_index": task_index, "status": "completed"}``），
    由 ``_merge_todos`` reducer 归并到全局 ``state.todos``。

    findings/errors 的 key 使用 ``f"{agent}-{task_index}"``，
    避免多个同类型并行子任务（如两个 code 子任务）结果互相覆盖。

    DAG 依赖编排：
    - ``pending_levels``: 传入 ``remaining_levels`` 时设置（第一个完成的子任务），
      传 ``None`` 时返回空列表（``_merge_pending_levels`` reducer 忽略空列表）。
    - ``completed_tasks``: 累积已完成任务索引（``_merge_list`` reducer 去重合并）。

    Phase 2 清理：删除 ``subtask_results`` 死字段（与 findings/errors 信息重复）。
    """
    todo_update = [{"_index": task_index, "status": "completed"}]
    key = f"{result.agent}-{task_index}"
    pending_update = remaining_levels if remaining_levels is not None else []
    if result.success:
        return {
            "findings": {key: result.payload},
            "todos": todo_update,
            "pending_levels": pending_update,
            "completed_tasks": [task_index],
        }
    return {
        "errors": {key: result.payload},
        "todos": todo_update,
        "pending_levels": pending_update,
        "completed_tasks": [task_index],
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

    DAG 依赖编排 D6：``pre_run_hook`` 用于未知 agent fallback 到 code 时
    记录 warning 日志（``_log_unknown_agent_fallback``）。
    """

    runner_type: str  # "deep" | "code" | "builtin" | "team_role" | "custom" | "default"
    runner_key: str | None  # _get_runner 的 key；None 表示无 runner（team_role）
    needs_workspace_inherit: bool  # 是否需要构造 child_id 并 _inherit_workspace
    pre_run_hook: Callable[[TeamPlanTask], None] | None = None  # 可选前置钩子（如 fallback 日志）


def _log_unknown_agent_fallback(task: TeamPlanTask) -> None:
    """未知 agent fallback 到 code runner 的前置日志钩子（D6）。

    记录 warning 便于排查：LLM 输出了未声明的 agent 类型，已 fallback 到
    code runner 执行。``_run_subtask_node`` 中 ``agent_name=task.agent``
    保留原始名（前端展示真实意图），但实际 runner 走 code。
    """
    logger.warning(
        "team unknown agent fallback to code",
        agent=task.agent,
    )


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
    # D6: 未知 agent fallback 到 code runner（替代旧 default 返回失败）
    # agent_name 保留原始名（前端展示真实意图），实际 runner 走 code
    "default": SubtaskConfig(
        runner_type="code",
        runner_key="code",
        needs_workspace_inherit=True,
        pre_run_hook=_log_unknown_agent_fallback,
    ),
}

# D6: 未知 agent fallback 配置（alias for _NODE_DISPATCH["default"]）
_DEFAULT_FALLBACK_CONFIG = _NODE_DISPATCH["default"]


def _resolve_subtask_config(agent: str, settings: Any) -> SubtaskConfig:
    """根据 agent 类型解析 SubtaskConfig。

    builtin 类型（rag/web）的 runner_key 在运行时设置为 agent 本身，
    其余类型从 ``_NODE_DISPATCH`` 静态表取。

    Args:
        agent: ``TeamPlanTask.agent`` 字段（deep/code/rag/web/custom-*/team_role）。
        settings: 全局配置（用于判断 team_subagents 成员）。

    Returns:
        对应的 ``SubtaskConfig``；未知 agent 返回 ``_DEFAULT_FALLBACK_CONFIG``
        （D6：fallback 到 code runner）。
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
    return _DEFAULT_FALLBACK_CONFIG


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

    DAG 依赖编排（D6）：未知 agent 经 ``_DEFAULT_FALLBACK_CONFIG`` fallback 到
    code runner（替代旧 default 返回失败）。``agent_name=task.agent`` 保留原始名，
    前端展示真实意图，但实际 runner 走 code。``pre_run_hook`` 记录 warning 日志。

    保留 Phase 1 稳定性保护：``team_semaphore`` 并发限流 + ``subtask_timeout`` 超时 +
    ``abort_event`` 中止检查 + ``asyncio.CancelledError`` 断连处理。

    Args:
        state: ``SubtaskState``，含 task / task_index / parent_thread_id / todos
            / remaining_levels 及透传的 history / permission_mode / chat_model 等。

    Returns:
        StateGraph reducer 兼容的 state update（findings/errors + todos 部分更新
        + pending_levels + completed_tasks）。
    """
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])  # 自动支持 deps 字段
    parent_thread_id = state["parent_thread_id"]
    task_index = state["task_index"]
    todos = state.get("todos", [])
    semaphore = state.get("team_semaphore")
    subtask_timeout = state.get("subtask_timeout", 300)
    # DAG 依赖编排：剩余待执行层，传递给 _make_subtask_state_update 更新 pending_levels
    remaining_levels = state.get("remaining_levels")

    settings = get_settings()
    config = _resolve_subtask_config(task.agent, settings)

    abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"),
            task_index,
            remaining_levels=remaining_levels,
        )

    # D6: 未知 agent fallback 到 code runner，调用 pre_run_hook 记录 warning
    if config.pre_run_hook is not None:
        config.pre_run_hook(task)

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
            return _make_subtask_state_update(
                inherit_result, task_index, remaining_levels=remaining_levels
            )

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
                # deep/code/builtin/custom/default-fallback 走通用 _run_subtask_stream
                # D6: 未知 agent 的 _DEFAULT_FALLBACK_CONFIG.runner_type="code"，
                # agent_name=task.agent 保留原始名（前端展示真实意图）
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
            remaining_levels=remaining_levels,
        )
    return _make_subtask_state_update(
        result, task_index, remaining_levels=remaining_levels
    )


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
# DAG 依赖编排：条件边路由与 replan 节点
# ============================================================


def _route_after_level(state: TeamState) -> str:
    """条件边路由：``check_next_level`` 后的分支决策。

    ``pending_levels`` 非空 → 还有未执行层 → ``dispatch_batch``（下一层派发）。
    ``pending_levels`` 为空 → 所有层执行完 → ``replan_check``（检查是否需要追加任务）。
    """
    pending_levels = state.get("pending_levels", [])
    if pending_levels:
        return "dispatch_batch"
    return "replan_check"


async def _check_next_level_node(state: TeamState) -> dict:
    """检查是否还有未执行的层（DAG 依赖编排 D4）。

    本节点是 ``subtask`` 与条件边之间的 join 节点：同一层所有并行子任务完成后
    汇聚到此节点，再由 ``_route_after_level`` 条件边决定下一步路由。

    返回空 dict（不修改 state）：``pending_levels`` 已由子任务节点的
    ``_make_subtask_state_update`` 通过 ``_merge_pending_levels`` reducer 更新。
    """
    return {}


def _route_after_replan(state: TeamState) -> str:
    """条件边路由：``replan_check`` 后的分支决策。

    ``pending_levels`` 非空 → replan 追加了新任务 → ``dispatch_batch``（派发新任务）。
    ``pending_levels`` 为空 → 无新任务 / 达到 max_replans → ``aggregate``（汇总）。
    """
    pending_levels = state.get("pending_levels", [])
    if pending_levels:
        return "dispatch_batch"
    return "aggregate"


async def _replan_check_node(state: TeamState) -> dict:
    """迭代式 replan 检查节点：判断是否需要追加新任务（DAG 依赖编排 D8）。

    流程：
    1. ``replan_count >= max_replans`` → 不调 LLM，返回空 dict（走 aggregate）
    2. 调 LLM 判定是否追加任务：
       - ``NO_NEW_TASKS`` → 返回空 dict（走 aggregate）
       - 解析新任务（``_parse_todos_from_text`` + ``_validate_task``）
       - 追加到 plan，重算 ``pending_levels``（排除已完成任务）
       - 递增 ``replan_count``，发射 ``team_replan`` SSE 事件
    3. ``pending_levels`` 非空 → 走 ``dispatch_batch``；空 → 走 ``aggregate``
    """
    writer = get_stream_writer()
    settings = get_settings()
    replan_count = state.get("replan_count", 0)
    max_replans = settings.agent_team_max_replans

    if replan_count >= max_replans:
        logger.info(
            "team replan limit reached",
            replan_count=replan_count,
            max=max_replans,
        )
        return {}

    findings = state.get("findings", {})
    plan = state.get("plan", [])
    message = state["message"]
    chat_model = state.get("chat_model")

    try:
        llm = chat_model if chat_model is not None else get_chat_model(
            temperature=settings.llm_temperature_orchestrator, streaming=False
        )
    except ValueError:
        return {}

    # 构造已完成任务摘要（供 LLM 判断是否需要追加）
    completed_findings = "\n\n".join(
        f"#{idx} [{plan[idx].agent}] {plan[idx].input[:100]}\n结果: "
        f"{findings.get(f'{plan[idx].agent}-{idx}', 'N/A')[:500]}"
        for idx in range(len(plan))
        if f"{plan[idx].agent}-{idx}" in findings
    )

    try:
        response = await llm.ainvoke([
            {"role": "system", "content": _REPLAN_SYSTEM_PROMPT.format(
                completed_findings=completed_findings or "(无已完成任务)",
                user_message=message,
            )},
            {"role": "user", "content": "判断是否需要追加任务。"},
        ])
    except Exception as exc:  # noqa: BLE001
        logger.warning("team replan invoke failed", error=str(exc))
        return {}

    text = response.content if hasattr(response, "content") else str(response)
    if isinstance(text, list):
        text = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in text
        )
    else:
        text = str(text)

    if "NO_NEW_TASKS" in text:
        return {}

    # 解析新任务
    new_todos = _parse_todos_from_text(text)
    if not new_todos:
        return {}

    new_tasks: list[TeamPlanTask] = []
    for todo in new_todos:
        content = todo.get("content", "")
        match = _AGENT_PREFIX_RE.match(content)
        if not match:
            continue
        agent = match.group(1).strip().lower()
        after_content = match.group(2)
        input_text = match.group(3).strip()
        if not input_text:
            continue
        deps = _parse_after_deps(after_content)
        # 安全改写：涉及危险工具关键词但非 deep 的任务强制改为 deep
        if agent != "deep" and _looks_like_dangerous_task(input_text):
            agent = "deep"
        ok, err = _validate_task(
            TeamPlanTask(agent=agent, input=input_text, purpose="", deps=deps), settings
        )
        if ok:
            new_tasks.append(TeamPlanTask(agent=agent, input=input_text, purpose="", deps=deps))
        else:
            logger.warning(
                "team replan task validation failed",
                agent=agent,
                error=err,
            )

    if not new_tasks:
        return {}

    # 追加到 plan + 计算新的 pending_levels（排除已完成任务）
    updated_plan = list(plan) + new_tasks
    new_levels, _ = _validate_dag(updated_plan)
    completed_tasks = set(state.get("completed_tasks", []))
    pending_levels = [
        [idx for idx in level if idx not in completed_tasks]
        for level in new_levels
        if any(idx not in completed_tasks for idx in level)
    ]

    # 同步追加新任务的展示用 todos，避免 _make_subtask_state_update 的 todo_update
    # 引用越界索引（新任务在旧 todos 中不存在）。
    existing_todos = state.get("todos", [])
    new_todos = list(existing_todos) + [
        {"content": t.input, "status": "pending"} for t in new_tasks
    ]

    writer(make_sse_event("team_replan", {
        "new_tasks": [{"agent": t.agent, "input": t.input, "deps": t.deps} for t in new_tasks],
        "replan_count": replan_count + 1,
    }))
    logger.info(
        "team replan added new tasks",
        new_task_count=len(new_tasks),
        replan_count=replan_count + 1,
    )

    return {
        "plan": updated_plan,
        "todos": new_todos,
        "pending_levels": pending_levels,
        "replan_count": replan_count + 1,
    }


# ============================================================
# StateGraph 构建（无 checkpointer，stateless，安全缓存）
# ============================================================


def _build_team_graph() -> Any:
    """构建 Team StateGraph：DAG 依赖编排拓扑。

    拓扑（DAG 依赖编排 D4）：
    ``START → plan → dispatch_batch → subtask → check_next_level``
    ``check_next_level`` 条件边 → ``dispatch_batch``（还有层）| ``replan_check``（无层）
    ``replan_check`` 条件边 → ``dispatch_batch``（有新任务）| ``aggregate``（无新任务）
    ``aggregate → END``

    复用 Phase 2 的 ``plan`` / ``subtask`` / ``aggregate`` 三个节点（不重命名、不拆分）。
    新增 ``dispatch_batch`` / ``check_next_level`` / ``replan_check`` 三个 DAG 编排节点。
    不引入 ``subtask_deep`` / ``subtask_code`` 等分类节点。
    """
    graph: Any = StateGraph(TeamState)
    # 复用 Phase 2 节点
    graph.add_node("plan", _plan_node)
    graph.add_node("subtask", _run_subtask_node)
    graph.add_node("aggregate", _aggregate_node)
    # DAG 编排节点
    graph.add_node("dispatch_batch", _dispatch_batch_node)
    graph.add_node("check_next_level", _check_next_level_node)
    graph.add_node("replan_check", _replan_check_node)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "dispatch_batch")
    # dispatch_batch 节点返回 {}（无状态更新），_dispatch_batch_router 返回 list[Send]
    # 触发 LangGraph 原生 fan-out 到 subtask 节点
    graph.add_conditional_edges("dispatch_batch", _dispatch_batch_router)
    # subtask → check_next_level（join 节点，等当前层所有子任务完成）
    graph.add_edge("subtask", "check_next_level")
    # check_next_level 条件边：还有层 → dispatch_batch；无层 → replan_check
    graph.add_conditional_edges("check_next_level", _route_after_level)
    # replan_check 条件边：有新任务 → dispatch_batch；无 → aggregate
    graph.add_conditional_edges("replan_check", _route_after_replan)
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
    # DAG 依赖编排
    "_inject_dependency_context",
    "_dispatch_batch_node",
    "_dispatch_batch_router",
    "_make_subtask_state_update",
    "_log_unknown_agent_fallback",
    "_DEFAULT_FALLBACK_CONFIG",
    "_route_after_level",
    "_check_next_level_node",
    "_route_after_replan",
    "_replan_check_node",
    "_build_team_graph",
    "_REPLAN_SYSTEM_PROMPT",
    "_validate_dag",
    "_parse_after_deps",
    "_looks_like_dangerous_task",
    "_AGENT_PREFIX_RE",
]
