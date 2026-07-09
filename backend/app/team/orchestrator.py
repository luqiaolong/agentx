"""AgentTeam 路径（路径 D）：LangGraph StateGraph 编排多代理协作。

核心流程（StateGraph 三节点）：
1. ``_plan_node``：Orchestrator 拆任务 → team_plan 事件
2. ``_execute_node``：并行执行子任务（asyncio.Queue + Semaphore 内嵌于节点）
   → team_progress / team_result / passthrough 事件
3. ``_aggregate_node``：Aggregator 汇总 → token / reasoning 事件

事件流：节点内部通过 ``get_stream_writer()`` 写入 custom stream，
``run_team_path`` 通过 ``graph.astream(..., stream_mode="custom")`` 消费后
yield 给调用方（``coding_team.py`` / CLI / 测试）。

混合方案说明（task hint #2/#6）：
- StateGraph 管理 plan → execute → aggregate 的节点编排与状态归并
- execute_node 内部仍用 asyncio.Queue + Semaphore 驱动并行子任务
  （保留实时 approval_request 透传 + 超时/中止检查）
- 不使用 LangGraph Send API fan-out（单节点内并行更简单，且避免
  每个子任务一个 Send 节点带来的状态合并复杂度）

安全：
- 普通子代理只调用只读/安全工具；写/编辑/shell 等危险任务必须指定为 deep
  子任务，由 run_deep_path 执行并走 interrupt_on 审批。
- 若 Orchestrator 把危险任务误分配给普通子代理，planner 会强制改写为 deep。

模块拆分（Phase 2.4）：
- ``app.team.blackboard``：Blackboard / TeamPlanTask / TeamSubtaskResult /
  TeamState / _merge_dict / _serialize_blackboard
- ``app.team.planner``：Orchestrator prompt + 计划解析与校验
- ``app.team.scheduler``：_run_subtask + _route_subtask_events + 队列驱动并行调度
- ``app.team.aggregator``：Aggregator prompt + 汇总 + 质量门 + 降级评估
- ``app.team.orchestrator``（本文件）：StateGraph 编排入口 +
  从子模块 re-export 公共符号以保持向后兼容
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, AsyncIterator

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.security.approval import get_abort_event
from app.config import get_settings
from app.llm import get_chat_model
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.utils.sse_events import make_sse_event, make_team_event

# 从子模块 re-export，保持 ``from app.team.orchestrator import X`` 向后兼容
from app.team.blackboard import (  # noqa: F401
    Blackboard,
    TeamPlanTask,
    TeamState,
    TeamSubtaskResult,
    _merge_dict,
    _serialize_blackboard,
)
from app.team.planner import (  # noqa: F401
    _BASE_EXPERTS,
    _ORCHESTRATOR_PROMPT,
    TeamPlan,
    _build_orchestrator_prompt,
    _build_project_context,
    _build_team_experts_description,
    _looks_like_dangerous_task,
    _postprocess_plan,
    _validate_task,
)
from app.team.scheduler import (  # noqa: F401
    _PASSTHROUGH_EVENTS,
    _SUBTASK_DONE_EVENT,
    _collect_event,
    _route_subtask_events,
    _run_subtask,
)
from app.team.aggregator import (  # noqa: F401
    _AGGREGATOR_PROMPT,
    _SIMPLE_TASK_KEYWORDS,
    _build_summary,
    _quality_gate,
    _run_aggregator,
    _should_downgrade_to_single,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.router.state import RouterState

# T-P2-4: 已删除 5 个 monkeypatch-compat noqa re-export（run_deep_path /
# run_coding_expert / run_rag_agent / run_web_agent / run_custom_agent）。
# 测试通过 ``subtask_runners={"code": fake, ...}`` 参数注入 mock；
# 生产路径由 ``_resolve_subtask_runners`` lazy-import 构造默认 dict。


def _resolve_subtask_runners(
    subtask_runners: dict[str, Any] | None,
) -> dict[str, Any]:
    """解析子任务 runner 字典。

    - 调用方显式传入 ``subtask_runners`` 时直接返回（测试注入 mock 路径）。
    - ``None`` 时 lazy-import 真实 runner 构造默认 dict（生产路径）。

    key 使用 ``TeamPlanTask.agent`` 值（"code" / "deep" / "rag" / "web" / "custom"），
    与 ``scheduler._get_runner`` 查找逻辑对齐。
    """
    if subtask_runners is not None:
        return subtask_runners
    # Lazy import 避免模块顶部循环依赖
    from app.agents.expert.coding import run_coding_expert
    from app.deep.agent import run_deep_path
    from app.subagents import (
        run_custom_agent,
        run_rag_agent,
        run_web_agent,
    )

    return {
        "code": run_coding_expert,
        "deep": run_deep_path,
        "rag": run_rag_agent,
        "web": run_web_agent,
        "custom": run_custom_agent,
    }


# ============================================================
# StateGraph 节点
# ============================================================


async def _plan_node(state: TeamState) -> dict:
    """Orchestrator 拆任务节点：调用 LLM 生成 TeamPlan，校验后写入 state。

    通过 ``get_stream_writer()`` 发出：
    - ``team_plan``：子任务计划
    - ``team_progress(error)``：校验失败的子任务
    - ``error``：LLM 不可用 / Orchestrator 调用失败 / 空计划

    返回空 ``plan`` 作为 execute_node / aggregate_node 的跳过信号。
    """
    writer = get_stream_writer()
    settings = get_settings()

    chat_model = state.get("chat_model")
    try:
        llm = chat_model if chat_model is not None else get_chat_model(
            temperature=0.3, streaming=False
        )
    except ValueError as exc:
        writer(make_team_event("error", {"message": f"LLM 不可用: {exc}"}))
        return {"plan": []}

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
        return {"plan": []}

    plan, reasoning = _postprocess_plan(plan_obj, max_tasks)
    if not plan:
        writer(make_team_event("error", {"message": "Orchestrator 未生成有效计划"}))
        return {"plan": []}

    # 校验并标记不可用任务
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


async def _execute_node(state: TeamState) -> dict:
    """并行执行子任务节点（混合方案：asyncio.Queue + Semaphore 内嵌于 StateGraph 节点）。

    通过 ``get_stream_writer()`` 实时发出：
    - ``team_progress(running)``：子任务开始
    - passthrough 事件（``approval_request`` / ``token`` / ``tool_result`` 等）
    - ``team_progress(done/error)``：子任务完成/失败（summary 循环）
    - ``team_result``：成功子任务的结果摘要

    ``approval_request`` 等事件**实时透传**（不经缓冲），保证审批流不死锁。
    """
    writer = get_stream_writer()
    settings = get_settings()

    tasks: list[TeamPlanTask] = state.get("plan", [])
    if not tasks:
        return {}

    thread_id = state["thread_id"]
    history = state.get("history")
    permission_mode = state.get("permission_mode", "standard")
    scene_prompt = state.get("scene_prompt")
    profile_prompt = state.get("profile_prompt", "")
    workspace_path = state.get("workspace_path")
    chat_model = state.get("chat_model")
    subtask_runners = state.get("subtask_runners")

    max_parallel = settings.agent_team_max_parallel
    subtask_timeout = settings.agent_team_subtask_timeout

    # 队列驱动的并行执行：deep 子任务的 approval_request 等事件实时透传。
    # 若用 asyncio.gather 直接收集结果，deep 子任务的审批事件会被吞掉导致死锁。
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    semaphore = asyncio.Semaphore(max_parallel)

    async def _runner(t: TeamPlanTask, idx: int) -> None:
        async with semaphore:
            abort_event = await get_abort_event(thread_id)
            if abort_event.is_set():
                await queue.put(
                    make_team_event(
                        _SUBTASK_DONE_EVENT,
                        {"agent": t.agent, "success": False, "payload": "用户中止"},
                    )
                )
                return
            # 实际开始执行时才发 running
            await queue.put(
                make_team_event(
                    "team_progress",
                    {"agent": t.agent, "status": "running", "message": t.purpose},
                )
            )
            try:
                async for ev in _run_subtask(
                    t, thread_id, history, permission_mode, scene_prompt, None,
                    profile_prompt, task_index=idx, workspace_path=workspace_path,
                    chat_model=chat_model, subtask_runners=subtask_runners,
                ):
                    await queue.put(ev)
            except Exception as exc:  # noqa: BLE001
                await queue.put(
                    make_team_event(
                        _SUBTASK_DONE_EVENT,
                        {
                            "agent": t.agent,
                            "success": False,
                            "payload": f"{t.agent} 子任务异常: {exc}",
                        },
                    )
                )

    runner_tasks = [
        asyncio.create_task(_runner(t, idx)) for idx, t in enumerate(tasks)
    ]

    results: dict[str, TeamSubtaskResult] = {}
    done_count = 0
    total = len(tasks)

    while done_count < total:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=subtask_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "team subtask timeout",
                done_count=done_count,
                total=total,
                timeout=subtask_timeout,
            )
            for rt in runner_tasks:
                if not rt.done():
                    rt.cancel()
            for task in tasks:
                if task.agent not in results:
                    err_msg = f"{task.agent} 子任务超时（{subtask_timeout}s）"
                    results[task.agent] = TeamSubtaskResult(
                        agent=task.agent, success=False, payload=err_msg
                    )
                    writer(
                        make_team_event(
                            "team_progress",
                            {"agent": task.agent, "status": "error", "message": err_msg},
                        )
                    )
            break

        if event["event"] == _SUBTASK_DONE_EVENT:
            done_count += 1
            obj = json.loads(event["data"])
            results[obj["agent"]] = TeamSubtaskResult(
                agent=obj["agent"],
                success=obj["success"],
                payload=obj["payload"],
            )
        else:
            # 透传事件（approval_request / todo_update / delegation / tool_call / token）
            writer(event)

    await asyncio.gather(*runner_tasks, return_exceptions=True)

    # 汇总循环：根据 results 发出 team_progress(done/error) + team_result
    findings: dict[str, str] = {}
    errors: dict[str, str] = dict(state.get("errors", {}))  # 起始含校验错误
    for task in tasks:
        agent_name = task.agent
        res = results.get(agent_name)
        if res is None:
            err_msg = f"{agent_name} 子任务未返回结果"
            errors[agent_name] = err_msg
            writer(
                make_team_event(
                    "team_progress",
                    {"agent": agent_name, "status": "error", "message": err_msg},
                )
            )
        elif res.success:
            findings[agent_name] = res.payload
            writer(
                make_team_event(
                    "team_progress",
                    {"agent": agent_name, "status": "done", "message": task.purpose},
                )
            )
            writer(
                make_team_event(
                    "team_result",
                    {"agent": agent_name, "summary": res.payload},
                )
            )
        else:
            errors[agent_name] = res.payload
            writer(
                make_team_event(
                    "team_progress",
                    {"agent": agent_name, "status": "error", "message": res.payload},
                )
            )

    return {"findings": findings, "errors": errors}


async def _aggregate_node(state: TeamState) -> dict:
    """Aggregator 汇总节点：综合黑板内容生成最终回复。

    通过 ``get_stream_writer()`` 发出：
    - ``error`` + ``team_done(error)``：所有专家任务均失败时
    - ``token`` / ``reasoning``：Aggregator LLM 流式输出
    - ``team_done``：整体结束
    """
    writer = get_stream_writer()

    findings: dict[str, str] = state.get("findings", {})
    errors: dict[str, str] = state.get("errors", {})
    plan: list = state.get("plan", [])

    if not findings:
        # 若 plan 为空，错误已由 _plan_node 发出（"Orchestrator 未生成有效计划"），
        # 此处只发 team_done 收尾，不重复发 "所有专家任务均失败"。
        if not plan:
            writer(make_team_event("team_done", {"status": "error"}))
            return {}
        writer(make_team_event("error", {"message": "所有专家任务均失败"}))
        writer(make_team_event("team_done", {"status": "error"}))
        return {}

    # 构造 Blackboard 供 _run_aggregator（向后兼容 dataclass 入参）
    blackboard = Blackboard(findings=dict(findings), errors=dict(errors))
    message = state["message"]
    chat_model = state.get("chat_model")

    async for sse in _run_aggregator(message, blackboard, chat_model=chat_model):
        writer(sse)

    # team 整体结束
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
    """构建 Team StateGraph：plan → execute → aggregate。

    无 checkpointer（不需要断点恢复），节点函数引用模块级全局变量
    （``_run_subtask`` / ``get_chat_model`` / ``_should_downgrade_to_single``
    等），故 ``monkeypatch.setattr(orch_module, "...")`` 在图已编译后仍生效
    （Python 全局变量在函数调用时按模块 ``__dict__`` 查找）。
    """
    graph: Any = StateGraph(TeamState)
    graph.add_node("plan", _plan_node)
    graph.add_node("execute", _execute_node)
    graph.add_node("aggregate", _aggregate_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "aggregate")
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
    """AgentTeam 路径入口（LangGraph StateGraph 编排）。

    1. plan_node: Orchestrator 拆任务 → team_plan 事件
    2. execute_node: 并行执行子任务 → team_progress / team_result 事件
    3. aggregate_node: Aggregator 汇总 → token / reasoning 事件

    事件通过 ``get_stream_writer()`` 写入 custom stream，由
    ``graph.astream(..., stream_mode="custom")`` 消费后 yield 给调用方。

    Args:
        workspace_path: 当前会话绑定的 workspace 路径，透传到子代理 fs 工具，
            用于解析相对路径（避免被解到 PROJECT_ROOT）。
        chat_model: 可选注入的 ChatModel，透传到 ``_run_subtask`` 与 ``_run_aggregator``。
            None 时使用真实 LLM。
        subtask_runners: 可选 ``{"code": callable, "rag": callable, ...}`` 字典，
            透传到 ``_run_subtask``，测试注入 mock 替代真实 runner。
            ``None`` 时由 ``_resolve_subtask_runners`` lazy-import 真实 runner
            构造默认 dict（生产路径）。key 使用 ``TeamPlanTask.agent`` 值。
    """
    # 简单任务降级：短消息 / 问候 / 翻译等无需 team 协作
    # 场景化架构下不再回退到 CHAT 路径，改为建议用户切换到 work 模式
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

    # 解析 subtask_runners（None 时 lazy-import 真实 runner）
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

        # stream_mode="custom" 消费 get_stream_writer() 写入的事件 payload
        async for event in graph.astream(initial_state, stream_mode="custom"):
            yield event


__all__ = [
    "run_team_path",
    "Blackboard",
    "TeamPlanTask",
    "TeamSubtaskResult",
    "TeamState",
]
