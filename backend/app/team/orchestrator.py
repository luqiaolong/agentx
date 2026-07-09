"""AgentTeam 路径（路径 D）：多代理协作。

核心流程：
1. Orchestrator 把用户任务拆成子任务计划（JSON）。
2. Scheduler 并行调度子任务到对应专家（code / rag / web / custom / deep）。
3. 每个子代理结果写入共享黑板（blackboard）。
4. Aggregator 综合黑板内容生成最终回复。

安全：
- 普通子代理只调用只读/安全工具；写/编辑/shell 等危险任务必须指定为 deep 子任务，
  由 run_deep_path 执行并走 interrupt_before 审批。
- 若 Orchestrator 把危险任务误分配给普通子代理，后端会强制改写为 deep 子任务。

模块拆分（Phase 2.4）：
- ``app.team.blackboard``：Blackboard / TeamPlanTask / TeamSubtaskResult / _serialize_blackboard
- ``app.team.planner``：Orchestrator prompt + 计划解析与校验
- ``app.team.scheduler``：_run_subtask + 队列驱动并行调度相关常量
- ``app.team.aggregator``：Aggregator prompt + 汇总 + 质量门 + 降级评估
- ``app.team.orchestrator``（本文件）：仅保留 ``run_team_path`` 编排入口 +
  从子模块 re-export 公共符号以保持向后兼容（``from app.team.orchestrator
  import Blackboard / _parse_plan / _run_subtask / _run_aggregator`` 等仍可用）。
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, AsyncIterator

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
    TeamSubtaskResult,
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


# T-P2-4: 仍保留 monkeypatch 兼容 re-export（向后兼容给测试用）。
# 后续测试迁移到 subtask_runners dict 后删除这 5 行（见 T-P2-4 follow-up）。
from app.deep.agent import run_deep_path  # noqa: F401
from app.subagents import (  # noqa: F401
    run_custom_agent,
    run_rag_agent,
    run_web_agent,
)
from app.agents.expert.coding import run_coding_expert  # noqa: F401

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
    """AgentTeam 路径入口。

    1. Orchestrator 拆任务 → team_plan 事件
    2. 并行执行子任务 → team_progress / team_result 事件
    3. Aggregator 汇总 → token / reasoning 事件

    Args:
        workspace_path: 当前会话绑定的 workspace 路径，透传到子代理 fs 工具，
            用于解析相对路径（避免被解到 PROJECT_ROOT）。
        chat_model: 可选注入的 ChatModel，透传到 ``_run_subtask`` 与 ``_run_aggregator``。
            None 时使用真实 LLM。
        subtask_runners: 可选 ``{"code": callable, "rag": callable, ...}`` 字典，
            透传到 ``_run_subtask``，测试注入 mock 替代 ``app.team.orchestrator`` 模块属性。
            ``None`` 时使用 ``app.team.orchestrator`` 默认实现（向后兼容）。
    """
    settings = get_settings()

    # 简单任务降级：短消息 / 问候 / 翻译等无需 team 协作
    # 场景化架构下不再回退到 CHAT 路径，改为建议用户切换到 work 模式
    downgrade, reason = _should_downgrade_to_single(message)
    if downgrade:
        logger.info("team downgrade suggest switch mode", reason=reason, message_len=len(message))
        yield make_sse_event(
            "token",
            f"该任务似乎不需要团队协作（{reason}）。建议切换到 work 模式由 Supervisor 直接处理。",
        )
        yield make_team_event("team_done", {"status": "done"})
        return

    max_tasks = settings.agent_team_max_tasks
    max_parallel = settings.agent_team_max_parallel

    # 构建项目上下文：AGENTS.md 文件地图 + 关键目录结构
    context = _build_project_context()

    with trace_span("team.run", thread_id=thread_id, message_len=len(message)):
        # ---- 1. Orchestrator 拆任务 ----
        try:
            llm = chat_model if chat_model is not None else get_chat_model(temperature=0.3, streaming=False)
        except ValueError as exc:
            yield make_team_event("error", {"message": f"LLM 不可用: {exc}"})
            return

        orchestrator_prompt = _build_orchestrator_prompt(
            message, max_tasks, context=context, settings=settings
        )
        try:
            structured_llm = llm.with_structured_output(TeamPlan)
            plan_obj: TeamPlan = await structured_llm.ainvoke(orchestrator_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("team orchestrator invoke failed", error=str(exc))
            yield make_team_event("error", {"message": f"Orchestrator 调用失败: {exc}"})
            return

        plan, reasoning = _postprocess_plan(plan_obj, max_tasks)
        if not plan:
            yield make_team_event("error", {"message": "Orchestrator 未生成有效计划"})
            return

        yield make_team_event(
            "team_plan",
            {
                "plan": [
                    {"agent": t.agent, "input": t.input, "purpose": t.purpose}
                    for t in plan
                ],
                "reasoning": reasoning,
            },
        )

        # ---- 2. 并行执行子任务 ----
        blackboard = Blackboard()
        # 校验并标记不可用任务
        valid_tasks: list[TeamPlanTask] = []
        for task in plan:
            ok, err = _validate_task(task, settings)
            if ok:
                valid_tasks.append(task)
            else:
                blackboard.errors[task.agent] = err
                yield make_team_event(
                    "team_progress",
                    {"agent": task.agent, "status": "error", "message": err},
                )

        # 队列驱动的并行执行：deep 子任务的 approval_request 等事件实时透传
        # 若用 asyncio.gather 直接收集结果，deep 子任务的审批事件会被吞掉导致死锁
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        semaphore = asyncio.Semaphore(max_parallel)

        async def _runner(t: TeamPlanTask, idx: int) -> None:
            async with semaphore:
                abort_event = await get_abort_event(thread_id)
                if abort_event.is_set():
                    await queue.put(
                        make_team_event(
                            _SUBTASK_DONE_EVENT,
                            {
                                "agent": t.agent,
                                "success": False,
                                "payload": "用户中止",
                            },
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
                        t, thread_id, history, permission_mode, scene_prompt, state, profile_prompt,
                        task_index=idx, workspace_path=workspace_path, chat_model=chat_model,
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
            asyncio.create_task(_runner(t, idx)) for idx, t in enumerate(valid_tasks)
        ]

        results: dict[str, TeamSubtaskResult] = {}
        done_count = 0
        total = len(valid_tasks)
        subtask_timeout = settings.agent_team_subtask_timeout
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
                for task in valid_tasks:
                    if task.agent not in results:
                        err_msg = f"{task.agent} 子任务超时（{subtask_timeout}s）"
                        results[task.agent] = TeamSubtaskResult(
                            agent=task.agent, success=False, payload=err_msg,
                        )
                        blackboard.errors[task.agent] = err_msg
                        yield make_team_event(
                            "team_progress",
                            {"agent": task.agent, "status": "error", "message": err_msg},
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
                # 透传事件（approval_request / todo_update / delegation / tool_call）
                yield event

        await asyncio.gather(*runner_tasks, return_exceptions=True)

        for task in valid_tasks:
            agent_name = task.agent
            res = results.get(agent_name)
            if res is None:
                err_msg = f"{agent_name} 子任务未返回结果"
                blackboard.errors[agent_name] = err_msg
                yield make_team_event(
                    "team_progress",
                    {"agent": agent_name, "status": "error", "message": err_msg},
                )
            elif res.success:
                blackboard.findings[agent_name] = res.payload
                yield make_team_event(
                    "team_progress",
                    {"agent": agent_name, "status": "done", "message": task.purpose},
                )
                yield make_team_event(
                    "team_result",
                    {"agent": agent_name, "summary": res.payload},
                )
            else:
                blackboard.errors[agent_name] = res.payload
                yield make_team_event(
                    "team_progress",
                    {"agent": agent_name, "status": "error", "message": res.payload},
                )

        if not blackboard.findings:
            yield make_team_event("error", {"message": "所有专家任务均失败"})
            yield make_team_event("team_done", {"status": "error"})
            return

        # ---- 3. Aggregator 汇总 ----
        async for sse in _run_aggregator(message, blackboard, chat_model=chat_model):
            yield sse

        # team 整体结束
        has_error = bool(blackboard.errors)
        yield make_team_event(
            "team_done",
            {"status": "error" if has_error and not blackboard.findings else "done"},
        )


__all__ = ["run_team_path", "Blackboard", "TeamPlanTask", "TeamSubtaskResult"]
