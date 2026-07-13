"""AgentTeam v2 入口（T7）。

``run_team_path`` 是 Team 路径的统一入口，替代旧 ``orchestrator.run_team_path``。

流程：
1. 简单任务降级检查（``_should_downgrade_to_single``）
2. 解析 subtask runners + semaphore + abort_event
3. 构造 ``TeamState`` 初始状态
4. ``graph.astream(stream_mode=["custom", "values"])`` 消费事件
5. yield SSE 事件给调用方

与旧 ``orchestrator.run_team_path`` 的差异：
- 使用 ``graph_builder.get_team_graph()`` 获取 v2 编译图
- 使用 v2 ``TeamState``（含 ``pending_waves`` / ``findings: dict[str, Finding]`` 等）
- 不在 state 中创建 semaphore（v2 用 ``scheduler._get_team_semaphore`` 全局单例）
- abort_event 通过 state 传递（v2 ``execute_node`` 从 state 读取）
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, AsyncIterator

from app.config import get_settings
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.observability.trace import bind_trace, current_trace_id
from app.security.approval import get_abort_event
from app.sse.events import make_sse_event
from app.team.aggregator import _should_downgrade_to_single
from app.team.graph_builder import get_team_graph
from app.team.scheduler import _resolve_subtask_runners
from app.team.state import TeamState

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.router.state import RouterState

__all__ = ["run_team_path"]


def _resolve_subtask_timeout(settings: Any) -> int:
    """从 settings 解析 subtask_timeout（兼容新旧配置名）。"""
    val = getattr(
        settings,
        "subtask_timeout",
        getattr(settings, "agent_team_subtask_timeout", 300),
    )
    if not isinstance(val, int) or val < 30:
        return 300
    return val


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
    """AgentTeam v2 路径入口。

    1. ``plan_node``: Planner 拆任务 → ``TeamPlan``
    2. ``dispatch_node``: fan-out 当前 wave 到 ``execute`` 节点
    3. ``execute_node``: 统一执行（限流 + abort + retry）
    4. ``barrier_node``: join → 有 wave 回 dispatch / 无 wave → aggregate
    5. ``aggregate_node``: aggregator + 质量门 → 通过 → END / 失败 → replan
    6. ``replan_node``: 迭代式重规划 → 有新任务回 dispatch / 无 → END

    事件流通过 ``stream_mode=["custom"]`` 消费：
    - ``custom`` 模式：节点通过 ``get_stream_writer()`` 写入的 SSE 事件
      （token / delegation / warning / team_init / team_done / error 等）
    """
    # 简单任务降级
    downgrade, reason = _should_downgrade_to_single(message)
    if downgrade:
        logger.info(
            "team downgrade suggest switch mode",
            reason=reason,
            message_len=len(message),
        )
        yield make_sse_event(
            "token",
            f"该任务似乎不需要团队协作（{reason}）。建议切换到 work 模式由 Supervisor 直接处理。",
        )
        yield make_sse_event("done", {})
        return

    resolved_runners = _resolve_subtask_runners(subtask_runners)
    settings = get_settings()
    subtask_timeout = _resolve_subtask_timeout(settings)

    # 获取 abort_event（通过 thread_id 查找或创建）
    abort_event = await get_abort_event(thread_id)

    logger.info(
        "team.run_team_path start",
        thread_id=thread_id,
        subtask_timeout=subtask_timeout,
    )

    # trace_id 透传：LangGraph 内部用 asyncio.create_task 调度子节点，
    # ContextVar 不会自动跨协程传播。显式在入口处绑定。
    _trace_id = current_trace_id() or ""
    _trace_cm = bind_trace(_trace_id) if _trace_id else contextlib.nullcontext()

    with _trace_cm, trace_span(
        "team.run", thread_id=thread_id, message_len=len(message)
    ):
        graph = get_team_graph()
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
            # v2 字段
            "plan": [],
            "pending_waves": [],
            "findings": {},
            "errors": [],
            "warnings": [],
            "completed_task_ids": [],
            "replan_count": 0,
            "quality_gate_passed": True,
            # 运行时对象（不参与 checkpoint 序列化）
            "subtask_timeout": subtask_timeout,
            "abort_event": abort_event,
        }

        # stream_mode=["custom"]：消费节点通过 get_stream_writer() 写入的事件
        async for chunk in graph.astream(
            initial_state,
            stream_mode=["custom"],
        ):
            if not isinstance(chunk, tuple) or len(chunk) != 2:
                continue
            mode, payload = chunk
            if mode == "custom":
                yield payload

        # graph 正常完成后发射 done 事件
        # aggregate_node 已通过 custom stream 发射 team_done，
        # 这里补 done 保证前端 SSE 流终结
        yield make_sse_event("done", {})
