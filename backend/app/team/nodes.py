"""AgentTeam v2 节点函数（T5）。

LangGraph StateGraph 的 6 个节点函数 + 3 个条件边路由：

- ``plan_node``: 调用 ``Planner.plan_with_llm`` + ``resolve_waves`` 分层
- ``execute_node``: 统一执行节点（替代旧 6 个分类节点），按 ``_NODE_DISPATCH``
  派发表路由到对应 runner，通过 ``scheduler.acquire_and_run`` 限流 + abort cancel
- ``barrier_node``: wave 屏障 join 节点（无状态更新）
- ``aggregate_node``: 调用 ``_run_aggregator`` + 质量门 + 发射累积 warning SSE
- ``replan_node``: 质量门失败时迭代式重规划（D10/D16）
- ``_route_after_barrier`` / ``_route_after_aggregate`` / ``_route_after_replan``: 条件边路由

v2 与旧 ``orchestrator._run_subtask_node`` 的关键差异：
- 使用 ``TeamTask``（Pydantic，含 ``id`` / ``depends_on`` str 列表）替代 ``TeamPlanTask``
- findings key 格式 ``{agent}:{task_id}:{wave_index}``（D13，替代旧 ``{agent}-{idx}``）
- findings 值为 ``Finding`` 对象（D4，替代旧裸字符串）
- ``_NODE_DISPATCH`` + ``SubtaskConfig`` 从 orchestrator.py 迁移，pre_run_hook 接收 writer
- ``_route_after_aggregate`` 新增：质量门通过 → END / 失败 → replan（D16）
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from langgraph.config import get_stream_writer
from langgraph.graph import END

from app.config import get_settings
from app.observability.logger import logger
from app.security.approval import get_abort_event
from app.sse.events import make_sse_event
from app.team.aggregator import _quality_gate, _run_aggregator
from app.team.blackboard import TeamPlanTask, TeamSubtaskResult
from app.team.dispatcher import _compose_input_with_upstream, resolve_waves
from app.team.planner import Planner
from app.team.scheduler import (
    _get_runner,
    _inherit_workspace,
    _run_subtask_stream,
    _run_team_role_subtask,
    run_with_retry,
)
from app.team.state import Finding, SubtaskState, TeamState, TeamTask

__all__ = [
    "plan_node",
    "execute_node",
    "barrier_node",
    "aggregate_node",
    "replan_node",
    "_route_after_barrier",
    "_route_after_aggregate",
    "_route_after_replan",
    "SubtaskConfig",
    "_NODE_DISPATCH",
    "_resolve_subtask_config",
    "_make_finding_key",
    "_make_subtask_state_update",
]


# ============================================================
# SubtaskConfig + _NODE_DISPATCH（v2，从 orchestrator.py 迁移）
# ============================================================


@dataclass
class SubtaskConfig:
    """子任务执行配置（v2）。

    ``runner_type``: runner 识别码（deep/code/builtin/team_role/custom/default）。
    ``runner_key``: ``_get_runner`` 的 key；``None`` 表示无 runner（team_role/builtin 运行时设置）。
    ``needs_workspace_inherit``: 是否需要构造 ``child_id`` 并 ``_inherit_workspace``。
    ``pre_run_hook``: 可选前置钩子（如 fallback warning）。
    """

    runner_type: str
    runner_key: str | None
    needs_workspace_inherit: bool
    pre_run_hook: Callable[..., None] | None = None


def _log_unknown_agent_fallback(
    task: TeamTask, writer: Callable[[dict], None] | None = None
) -> None:
    """未知 agent fallback 到 code runner 的前置警告钩子（D7）。

    记录 warning 日志 + 发射 SSE warning 事件 + 返回 warning 字符串供 state.warnings。
    """
    msg = f"未知 agent '{task.agent}' fallback 到 code runner"
    logger.warning("team unknown agent fallback to code", agent=task.agent)
    if writer is not None:
        writer(
            make_sse_event(
                "warning", {"task_id": task.id, "message": msg}
            )
        )


_NODE_DISPATCH: dict[str, SubtaskConfig] = {
    "deep": SubtaskConfig(
        runner_type="deep", runner_key="deep", needs_workspace_inherit=True
    ),
    "code": SubtaskConfig(
        runner_type="code", runner_key="code", needs_workspace_inherit=True
    ),
    "builtin": SubtaskConfig(
        runner_type="builtin", runner_key=None, needs_workspace_inherit=False
    ),
    "team_role": SubtaskConfig(
        runner_type="team_role", runner_key=None, needs_workspace_inherit=False
    ),
    "custom": SubtaskConfig(
        runner_type="custom", runner_key="custom", needs_workspace_inherit=False
    ),
    # D7: 未知 agent fallback 到 code runner
    "default": SubtaskConfig(
        runner_type="code",
        runner_key="code",
        needs_workspace_inherit=True,
        pre_run_hook=_log_unknown_agent_fallback,
    ),
}


def _resolve_subtask_config(agent: str, settings: Any) -> SubtaskConfig:
    """根据 agent 类型解析 SubtaskConfig。

    builtin 类型（rag/web）的 runner_key 在运行时设置为 agent 本身。
    未知 agent 返回 ``_NODE_DISPATCH["default"]``（fallback 到 code，D7）。
    """
    if agent == "deep":
        return _NODE_DISPATCH["deep"]
    if agent == "code":
        return _NODE_DISPATCH["code"]
    if agent in ("rag", "web"):
        return SubtaskConfig(
            runner_type="builtin", runner_key=agent, needs_workspace_inherit=False
        )
    if agent.startswith("custom-"):
        return _NODE_DISPATCH["custom"]
    if agent in (settings.team_subagents or {}):
        return _NODE_DISPATCH["team_role"]
    return _NODE_DISPATCH["default"]


# ============================================================
# v1/v2 类型桥接（临时，T35 删除 orchestrator.py 时清理）
# ============================================================


def _to_team_plan_task(task: TeamTask) -> TeamPlanTask:
    """v2 TeamTask → v1 TeamPlanTask 桥接（供 _run_team_role_subtask 使用）。

    临时桥接：scheduler._run_team_role_subtask 仍接受 v1 ``TeamPlanTask``，
    T35 删除 orchestrator.py 时同步迁移 scheduler 到 v2 类型。
    """
    return TeamPlanTask(
        agent=task.agent,
        input=task.description,
        purpose=task.expected_output,
    )


def _build_runner_args(
    config: SubtaskConfig,
    task: TeamTask,
    child_id: str | None,
    parent_thread_id: str,
    composed_input: str | None = None,
) -> tuple:
    """构造 runner_args（与旧 orchestrator._build_runner_args 对齐）。

    BE-P 修复：``composed_input`` 优先于 ``task.description`` 作为子任务输入，
    让上游 findings 真正注入到下游 runner。
    """
    actual_input = composed_input if composed_input is not None else task.description
    if config.runner_type == "deep":
        deep_state: dict = {
            "thread_id": child_id,
            "messages": [{"role": "user", "content": actual_input}],
        }
        return (deep_state, actual_input)
    if config.runner_type == "code":
        return (actual_input, child_id)
    if config.runner_type == "builtin":
        return (parent_thread_id, actual_input)
    if config.runner_type == "custom":
        key = task.agent[len("custom-") :]
        return (key, parent_thread_id, actual_input)
    raise ValueError(f"Unsupported runner_type for args: {config.runner_type}")


def _build_runner_kwargs(
    config: SubtaskConfig, state: SubtaskState, parent_thread_id: str
) -> dict:
    """构造 runner_kwargs（与旧 orchestrator._build_runner_kwargs 对齐）。"""
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


# ============================================================
# Finding key + state update 构造
# ============================================================


def _make_finding_key(agent: str, task: TeamTask, wave_index: int) -> str:
    """D13: findings key = ``{agent}:{task_id}:{wave_index}``。"""
    return f"{agent}:{task.id}:{wave_index}"


def _make_subtask_state_update(
    result: TeamSubtaskResult,
    task: TeamTask,
    wave_index: int,
    remaining_waves: list[list[TeamTask]] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """把 ``TeamSubtaskResult`` 转换为 StateGraph reducer 兼容的 state update。

    - findings key 使用 D13 复合格式 ``{agent}:{task_id}:{wave_index}``
    - 值为 ``Finding`` 对象（D4 结构化）
    - ``pending_waves`` 通过 ``_merge_pending_waves`` reducer 弹出当前 wave
    - ``completed_task_ids`` 按 task id 累积
    - 失败结果追加到 ``errors``（list[str]）
    """
    key = _make_finding_key(result.agent, task, wave_index)
    finding = Finding(
        agent=result.agent,
        task_id=task.id,
        wave_index=wave_index,
        content=result.payload,
        success=result.success,
        retries=result.retries,
    )
    update: dict[str, Any] = {
        "findings": {key: finding},
        "completed_task_ids": [task.id],
        "pending_waves": remaining_waves if remaining_waves is not None else [],
    }
    if not result.success:
        update["errors"] = [f"{key}: {result.payload}"]
    if warnings:
        update["warnings"] = warnings
    return update


def _emit_delegation(writer: Callable[[dict], None], agent: str, purpose: str) -> None:
    """发射 ``delegation`` SSE 事件，前端据此创建 SubAgentGroup 容器。"""
    writer(
        make_sse_event(
            "delegation",
            {"target": agent, "source": "team", "message": purpose},
        )
    )


# ============================================================
# Node: plan_node
# ============================================================


async def plan_node(state: TeamState) -> dict:
    """Plan 节点：调用 ``Planner.plan_with_llm`` + ``resolve_waves`` 分层。

    流程：
    1. 用 ``Planner.plan_with_llm(message)`` 获取 ``TeamPlan``
    2. 调用 ``resolve_waves(plan.tasks)`` 按 ``depends_on`` Kahn 分层
    3. 发射 ``team_init`` SSE 事件（含 plan + agents）
    4. 返回 ``plan`` / ``pending_waves`` / ``replan_count=0``
    """
    writer = get_stream_writer()
    chat_model = state.get("chat_model")
    planner = Planner(chat_model)
    message = state["message"]

    try:
        plan = await planner.plan_with_llm(message)
    except Exception as exc:  # noqa: BLE001
        logger.warning("team plan_node planner failed", error=str(exc))
        writer(make_sse_event("error", {"message": f"Planner 调用失败: {exc}"}))
        return {"plan": [], "pending_waves": [], "replan_count": 0}

    if not plan.tasks:
        writer(make_sse_event("error", {"message": "Orchestrator 未生成有效计划"}))
        return {"plan": [], "pending_waves": [], "replan_count": 0}

    waves = resolve_waves(plan.tasks)

    # 发射 team_init 事件（前端据此创建 TeamNodeCard）
    writer(
        make_sse_event(
            "team_init",
            {
                "plan": [
                    {
                        "agent": t.agent,
                        "description": t.description,
                        "id": t.id,
                        "depends_on": t.depends_on,
                    }
                    for t in plan.tasks
                ],
                "agents": [
                    {"agent": t.agent, "status": "pending"} for t in plan.tasks
                ],
                "summary": plan.summary,
            },
        )
    )

    return {
        "plan": plan.tasks,
        "pending_waves": waves,
        "replan_count": 0,
    }


# ============================================================
# Node: execute_node（统一执行节点）
# ============================================================


async def execute_node(state: SubtaskState) -> dict:
    """统一执行节点，替代旧 6 个分类节点（D7）。

    流程：
    1. 按 ``task.agent`` 查 ``_NODE_DISPATCH`` 派发表
    2. abort 检查（已中止返回失败结果）
    3. pre_run_hook（fallback 时发射 warning SSE）
    4. workspace 授权继承（deep/code/default 需要）
    5. 调用 ``_compose_input_with_upstream`` 注入上游 findings
    6. 发射 ``delegation`` SSE
    7. ``run_with_retry`` + ``acquire_and_run`` 限流 + abort + retry
    8. 构造 ``Finding`` + findings key（``{agent}:{task_id}:{wave_index}``）
    """
    writer = get_stream_writer()
    task: TeamTask = state["task"]
    upstream_findings = state.get("upstream_findings", {})
    wave_index = state.get("wave_index", 0)
    parent_thread_id = state["parent_thread_id"]
    remaining_waves = state.get("remaining_waves", [])

    settings = get_settings()
    config = _resolve_subtask_config(task.agent, settings)

    # abort 检查
    abort_event = state.get("abort_event")
    if abort_event is None:
        abort_event = await get_abort_event(parent_thread_id)
    if abort_event.is_set():
        return _make_subtask_state_update(
            TeamSubtaskResult(agent=task.agent, success=False, payload="用户中止"),
            task,
            wave_index,
            remaining_waves,
        )

    # pre_run_hook：fallback warning
    warnings: list[str] = []
    if config.pre_run_hook is not None:
        config.pre_run_hook(task, writer)
        warnings.append(f"未知 agent '{task.agent}' fallback 到 code runner")

    # workspace 授权继承
    child_id: str | None = None
    if config.needs_workspace_inherit:
        child_id = f"{parent_thread_id}-team-{uuid.uuid4()}"
        inherit_result = await _inherit_workspace(
            child_id, state.get("workspace_path"), task.agent
        )
        if inherit_result is not None:
            return _make_subtask_state_update(
                inherit_result, task, wave_index, remaining_waves, warnings
            )

    # 注入上游 findings（BE-P 修复：实际使用 composed_input 作为子任务输入）
    composed_input = _compose_input_with_upstream(task, upstream_findings)

    # 发射 delegation SSE
    _emit_delegation(writer, task.agent, task.expected_output)

    subtask_timeout = state.get("subtask_timeout", 600)

    # 构造 runner factory（每次调用返回新 coroutine）
    if config.runner_type == "team_role":
        # team_role 走专用 runner（v1 桥接）
        # BE-P 修复：team_role 也注入上游 findings（替代 _to_team_plan_task）
        team_plan_task = TeamPlanTask(
            agent=task.agent,
            input=composed_input,
            purpose=task.expected_output,
        )

        def runner_factory() -> Coroutine[Any, Any, TeamSubtaskResult]:
            return _run_team_role_subtask(
                task=team_plan_task,
                thread_id=parent_thread_id,
                history=state.get("history"),
                permission_mode=state.get("permission_mode", "standard"),
                profile_prompt=state.get("profile_prompt", ""),
                task_index=0,
                workspace_path=state.get("workspace_path"),
                chat_model=state.get("chat_model"),
                subtask_runners=state.get("subtask_runners"),
                abort_event=abort_event,
                writer=writer,
                subtask_timeout=subtask_timeout,
            )
    else:
        # deep/code/builtin/custom/default 走通用 _run_subtask_stream
        def runner_factory() -> Coroutine[Any, Any, TeamSubtaskResult]:
            runner = _get_runner(config.runner_key, state.get("subtask_runners"))
            runner_args = _build_runner_args(
                config, task, child_id, parent_thread_id, composed_input
            )
            runner_kwargs = _build_runner_kwargs(config, state, parent_thread_id)
            return _run_subtask_stream(
                runner,
                runner_args=runner_args,
                runner_kwargs=runner_kwargs,
                agent_name=task.agent,
                abort_event=abort_event,
                writer=writer,
                subtask_timeout=subtask_timeout,
            )

    # 限流 + abort cancel + retry（run_with_retry 内部调用 acquire_and_run）
    try:
        result = await run_with_retry(
            task=task,
            runner_factory=runner_factory,
            thread_id=parent_thread_id,
            abort_event=abort_event,
            writer=writer,
            max_retries=settings.team_max_retries,
        )
    except asyncio.CancelledError:
        logger.info(
            "team execute_node cancelled", agent=task.agent, task_id=task.id
        )
        result = TeamSubtaskResult(
            agent=task.agent, success=False, payload="子任务已取消"
        )

    return _make_subtask_state_update(
        result, task, wave_index, remaining_waves, warnings
    )


# ============================================================
# Node: barrier_node + 路由
# ============================================================


def barrier_node(state: TeamState) -> dict:
    """Wave 屏障 join 节点：同一 wave 的并行 execute 节点完成后汇聚到此。

    无状态更新，仅作为 join 点供 ``_route_after_barrier`` 条件边决策。
    """
    return {}


def _route_after_barrier(state: TeamState) -> str:
    """barrier 条件边：有 wave 剩余 → dispatch；无 → aggregate。"""
    pending_waves = state.get("pending_waves", [])
    if pending_waves:
        return "dispatch"
    return "aggregate"


# ============================================================
# Node: aggregate_node + 路由
# ============================================================


async def aggregate_node(state: TeamState) -> dict:
    """Aggregate 节点：调用 aggregator + 质量门 + 发射累积 warning SSE。

    流程：
    1. 发射 ``state.warnings`` 累积的 warning SSE（D14）
    2. abort 检查
    3. v2 ``Finding`` 对象 → v1 裸字符串（临时桥接 ``_run_aggregator``）
    4. 质量门检查（``_quality_gate``）
    5. 质量门通过 → 调用 ``_run_aggregator`` 流式输出
    6. 发射 ``team_done`` SSE
    7. 设置 ``quality_gate_passed`` 供 ``_route_after_aggregate`` 路由
    """
    writer = get_stream_writer()
    thread_id = state.get("thread_id", "")

    findings = state.get("findings", {})
    errors = state.get("errors", [])

    # D14: 发射累积的 warning SSE
    for warning in state.get("warnings", []):
        writer(make_sse_event("warning", {"message": warning}))

    if not findings:
        writer(make_sse_event("team_done", {"status": "error"}))
        return {"quality_gate_passed": False}

    # abort 检查
    abort_event = state.get("abort_event")
    if abort_event is None:
        abort_event = await get_abort_event(thread_id)
    if abort_event.is_set():
        writer(
            make_sse_event(
                "team_done", {"status": "error", "error": "用户中止"}
            )
        )
        return {"quality_gate_passed": False}

    # v2 Finding → v1 裸字符串（临时桥接 _run_aggregator / _quality_gate）
    blackboard_findings = {key: f.content for key, f in findings.items()}
    blackboard_errors = {f"error_{i}": e for i, e in enumerate(errors)}
    blackboard = {
        "findings": blackboard_findings,
        "errors": blackboard_errors,
    }

    message = state["message"]
    chat_model = state.get("chat_model")

    # 质量门检查
    ok, reason = _quality_gate(blackboard)

    if ok:
        # 流式输出 aggregator 回复
        async for sse in _run_aggregator(
            message,
            blackboard,
            chat_model=chat_model,
            abort_event=abort_event,
        ):
            writer(sse)

    # 发射 team_done：status 反映实际状态
    # - ok=True → "done"
    # - ok=False + has_error → "error"
    # - ok=False + 无 error（质量门失败触发 replan）→ "replanning"
    has_error = bool(errors)
    if ok:
        status = "done"
    elif has_error:
        status = "error"
    else:
        status = "replanning"
    agent_summaries = [
        {"agent": f.agent, "summary": f.content} for f in findings.values()
    ]
    writer(
        make_sse_event(
            "team_done",
            {
                "status": status,
                "agents": agent_summaries,
            },
        )
    )
    logger.info(
        "team aggregate_node completed",
        quality_gate_passed=ok,
        reason=reason,
        findings_count=len(findings),
        errors_count=len(errors),
    )

    return {"quality_gate_passed": ok}


def _route_after_aggregate(state: TeamState) -> str:
    """D16: aggregate 条件边 — 质量门通过 → END；失败 → replan。"""
    quality_gate_passed = state.get("quality_gate_passed", True)
    if quality_gate_passed:
        return END
    return "replan"


# ============================================================
# Node: replan_node + 路由
# ============================================================


async def replan_node(state: TeamState) -> dict:
    """Replan 节点：质量门失败时迭代式重规划（D10/D16）。

    流程：
    1. 检查 ``replan_count`` 是否超过 ``team_max_replan_attempts``
    2. 调用 ``Planner.replan`` 把原 plan + findings + errors 喂回 LLM
    3. 无新任务 → 返回空 ``pending_waves``（路由到 END）
    4. 有新任务 → ``resolve_waves`` 分层 + 追加到 plan
    5. 发射 ``replan`` SSE 事件
    """
    writer = get_stream_writer()
    settings = get_settings()
    replan_count = state.get("replan_count", 0)
    max_replans = settings.team_max_replan_attempts

    if replan_count >= max_replans:
        logger.info(
            "team replan limit reached",
            replan_count=replan_count,
            max=max_replans,
        )
        writer(
            make_sse_event(
                "team_done",
                {"status": "error", "error": "replan limit reached"},
            )
        )
        return {"pending_waves": []}

    findings = state.get("findings", {})
    errors = state.get("errors", [])
    plan = state.get("plan", [])
    message = state["message"]

    chat_model = state.get("chat_model")
    planner = Planner(chat_model)

    try:
        new_plan = await planner.replan(
            original_message=message,
            previous_plan=plan,
            findings=findings,
            errors=errors,
            hint=plan[0].is_dangerous_hint if plan else False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("team replan_node planner failed", error=str(exc))
        return {"pending_waves": []}

    if not new_plan.tasks:
        logger.info("team replan produced no new tasks")
        return {"pending_waves": []}

    waves = resolve_waves(new_plan.tasks)

    # 追加新任务到现有 plan（保留旧任务用于前端展示历史）
    updated_plan = list(plan) + new_plan.tasks

    writer(
        make_sse_event(
            "replan",
            {
                "new_tasks": [t.model_dump() for t in new_plan.tasks],
                "replan_count": replan_count + 1,
                "reason": "quality_gate_failed",
            },
        )
    )

    return {
        "plan": updated_plan,
        "pending_waves": waves,
        "replan_count": replan_count + 1,
    }


def _route_after_replan(state: TeamState) -> str:
    """replan 条件边：有 pending_waves → dispatch；无 → END。"""
    pending_waves = state.get("pending_waves", [])
    if pending_waves:
        return "dispatch"
    return END
