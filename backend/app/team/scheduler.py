"""AgentTeam v2 Scheduler：Semaphore 限流 + retry + abort cancel。

本模块为 Team 路径提供子任务执行 helper：

- ``_get_team_semaphore``: 全局单例 ``asyncio.Semaphore``，限制并发数（T8）。
- ``register_running_task`` / ``cancel_running_tasks``: 全局 running tasks
  registry，支持按 ``thread_id`` 主动 cancel（替代旧轮询式 abort）。
- ``acquire_and_run``: 限流 + abort cancel 的执行入口（T8）。
  semaphore 在 ``finally`` 块释放（I3 不泄漏），``CancelledError`` 捕获后
  返回 ``TeamSubtaskResult(success=False, payload="用户中止")``。
- ``run_with_retry``: 失败重试 + 指数退避，仅瞬态错误重试（T8）。
  ``asyncio.CancelledError`` 直接 raise 不重试（I4），4xx/ValueError 不重试。
- ``_run_subtask_stream`` / ``_run_team_role_subtask``: 现有 helper，
  保留显式失败逻辑，abort 从轮询式改为事件驱动（去掉 ``timeout=5`` 轮询）。
- ``_route_event_for_node``: 单事件路由，保留 ``logger.warning`` 错误记录。
- ``_inherit_workspace``: workspace 授权继承，保留窄异常 ``(PathNotAuthorized,
  ValueError)``，失败即返回失败结果。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Coroutine, Optional

import httpx

from app.config import get_settings
from app.observability.logger import logger
from app.observability.trace import bind_trace, current_trace_id
from app.sse.events import make_sse_event
from app.team.aggregator import _build_summary
from app.team.blackboard import TeamSubtaskResult
from app.utils.text import extract_chunk_text

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.team.state import TeamTask

__all__ = [
    "acquire_and_run",
    "run_with_retry",
    "register_running_task",
    "cancel_running_tasks",
    "_get_team_semaphore",
    "reset_team_semaphore",
    "IterResult",
    "_run_subtask_stream",
    "_run_team_role_subtask",
    "_route_event_for_node",
    "_resolve_subtask_runners",
    "_get_runner",
    "_inherit_workspace",
    "_SUBTASK_DONE_EVENT",
    "_PASSTHROUGH_EVENTS",
    "TRANSIENT_ERRORS",
    "_running_tasks",
]


# ============================================================
# Constants
# ============================================================

# 子任务完成哨兵事件类型（旧实现兼容，新实现通过返回值传递结果）
_SUBTASK_DONE_EVENT = "_subtask_done"


# BE-D 修复：_iterate 统一返回协议枚举
class IterResult(Enum):
    """``_iterate`` 闭包统一返回协议。

    - ``NORMAL_END``：runner 流自然结束（StopAsyncIteration），未收到 ``_subtask_done`` 哨兵
    - ``ABORTED``：``abort_event`` 触发，循环被中止
    - ``SUBTASK_DONE``：收到 ``_subtask_done`` 哨兵（仅 ``_run_subtask_stream`` 路径，
      ``_run_team_role_subtask`` 自行发射哨兵后归入 ``NORMAL_END``）
    """

    NORMAL_END = "normal_end"
    ABORTED = "aborted"
    SUBTASK_DONE = "subtask_done"

# 需要实时透传到前端的事件类型
# approval_request 必须直达前端，否则 DeepAgent 审批流会死锁
# tool_result 必须透传，否则前端 tool_call 配对断裂
# reasoning / token 透传供前端展示子任务执行过程
_PASSTHROUGH_EVENTS: frozenset[str] = frozenset(
    {"approval_request", "todo_update", "delegation", "tool_call", "tool_result", "reasoning", "token"}
)

# 瞬态错误：网络波动 / 超时，可重试
# 注意：``httpx.HTTPStatusError`` 不在此元组中，5xx 由 ``_is_transient`` 额外判定
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)

# L17: 默认 runner 字典缓存，避免每次 _get_runner miss 都重复 import + 构造
_default_runners_cache: dict[str, Any] | None = None


# ============================================================
# 全局 running tasks registry + Semaphore 单例
# ============================================================

# thread_id -> running asyncio.Task 列表
# 注意：模块级 dict 在多 event loop 场景下需要 event loop 已绑定；
# asyncio.Task 在 create_task 时绑定当前 loop，registry 只持有引用。
_running_tasks: dict[str, list[asyncio.Task[Any]]] = {}

# BE-M 修复：移除 lru_cache，改用模块级变量 + 配置版本比对，支持配置热更新
_team_semaphore: asyncio.Semaphore | None = None
_team_semaphore_concurrency: int | None = None


def _get_team_semaphore() -> asyncio.Semaphore:
    """全局 semaphore，配置变更时自动重建。

    BE-M 修复：移除 ``lru_cache``，比对当前配置与缓存配置，不一致时重建。
    Semaphore 跨多个 event loop 不安全，本应用单 loop 模型（FastAPI + asyncio 主 loop）。
    """
    global _team_semaphore, _team_semaphore_concurrency
    settings = get_settings()
    max_concurrency = settings.team_max_concurrency
    # 防御性校验：None / 0 / 负数降级到默认 5
    if not isinstance(max_concurrency, int) or max_concurrency <= 0:
        max_concurrency = 5
    if _team_semaphore is None or _team_semaphore_concurrency != max_concurrency:
        _team_semaphore = asyncio.Semaphore(max_concurrency)
        _team_semaphore_concurrency = max_concurrency
    return _team_semaphore


def reset_team_semaphore() -> None:
    """配置变更时主动重置 semaphore（供 settings reload 调用）。

    BE-M 修复：与 ``_get_team_semaphore`` 配合，移除 ``lru_cache`` 后提供
    显式重置入口，便于测试隔离与配置热更新场景下强制重建。
    """
    global _team_semaphore, _team_semaphore_concurrency
    _team_semaphore = None
    _team_semaphore_concurrency = None


def register_running_task(thread_id: str, task: asyncio.Task[Any]) -> None:
    """注册 running task 到 registry。

    同一 ``thread_id`` 可注册多个 task（并行 wave 内多个子任务），追加到列表。
    ``cancel_running_tasks(thread_id)`` 后续可批量取消。

    Args:
        thread_id: 父 thread id（Team 路径入口 thread）。
        task: ``asyncio.Task`` 实例（调用方负责创建）。
    """
    tasks = _running_tasks.get(thread_id)
    if tasks is None:
        tasks = []
        _running_tasks[thread_id] = tasks
    tasks.append(task)


def cancel_running_tasks(thread_id: str) -> None:
    """取消指定 thread_id 的所有 running tasks。

    对每个未完成的 task 调用 ``task.cancel()``，触发 ``CancelledError`` 传播到
    coroutine 内部，让 ``acquire_and_run`` 的 ``except CancelledError`` 捕获并
    返回 ``TeamSubtaskResult(success=False, payload="用户中止")``。

    清空 registry 中该 thread_id 的 task 列表（``_running_tasks[thread_id] = []``），
    避免重复 cancel 已 cancelled 的 task 抛 RuntimeError。
    """
    tasks = _running_tasks.get(thread_id)
    if not tasks:
        return
    for t in tasks:
        if not t.done():
            t.cancel()
    # 清空列表（保留 key，便于 _unregister_running_task 安全 remove）
    tasks.clear()


def _unregister_running_task(thread_id: str, task: asyncio.Task[Any]) -> None:
    """从 registry 移除单个 task（acquire_and_run finally 块调用）。"""
    tasks = _running_tasks.get(thread_id)
    if not tasks:
        return
    with contextlib.suppress(ValueError):
        tasks.remove(task)


def _is_transient(exc: BaseException) -> bool:
    """判定异常是否瞬态（可重试）。

    - ``TRANSIENT_ERRORS`` 元组中的异常类型 → True
    - ``httpx.HTTPStatusError`` 5xx → True（5xx 视为瞬态服务端错误）
    - ``httpx.HTTPStatusError`` 4xx → False（客户端错误，不重试）
    - 其他异常 → False
    """
    if isinstance(exc, TRANSIENT_ERRORS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", 0) if response is not None else 0
        return 500 <= status < 600
    return False


# ============================================================
# acquire_and_run：限流 + abort cancel 执行入口
# ============================================================


async def acquire_and_run(
    task: TeamTask,
    runner_coro: Coroutine[Any, Any, TeamSubtaskResult],
    thread_id: str,
    abort_event: Optional[asyncio.Event] = None,
    writer: Optional[Callable[[dict], None]] = None,
) -> TeamSubtaskResult:
    """限流 + abort cancel 的执行入口（T8）。

    执行流程：

    1. ``semaphore.acquire()`` 限流（``finally`` 块释放，I3 不泄漏）
    2. ``asyncio.ensure_future(runner_coro)`` 创建 ``asyncio.Task``
    3. ``register_running_task(thread_id, task_obj)`` 注册到全局 registry
    4. ``abort_event`` 监听：若 ``is_set()`` 立即 cancel task
    5. ``await task_obj`` 等待结果
    6. 捕获 ``CancelledError`` → 返回 ``TeamSubtaskResult(success=False,
       payload="用户中止")``
    7. ``finally`` 块：``semaphore.release()`` + 从 registry 移除 task

    Args:
        task: ``TeamTask`` 实例（用于构造失败 result 的 ``agent`` 字段）。
        runner_coro: 已构造的 coroutine，返回 ``TeamSubtaskResult``。
        thread_id: 父 thread id，用于 registry 索引。
        abort_event: 可选的 ``asyncio.Event``，``is_set()`` 时触发 cancel。
        writer: 可选的 SSE writer，abort 时发射 ``delegation`` 事件让前端 trace 可见。

    Returns:
        ``TeamSubtaskResult``：runner 正常返回的结果，或 abort/异常失败结果。
    """
    semaphore = _get_team_semaphore()
    # BE-I 修复：ensure_future 移入 try 块，失败时 finally 释放 semaphore
    await semaphore.acquire()
    task_obj = None
    try:
        task_obj = asyncio.ensure_future(runner_coro)
        register_running_task(thread_id, task_obj)
        # 若 abort 已触发，立即 cancel
        if abort_event is not None and abort_event.is_set():
            task_obj.cancel()
        else:
            # 监听 abort_event 与 task_obj 竞速（事件驱动，无 timeout 轮询）
            if abort_event is not None:
                abort_wait = asyncio.ensure_future(abort_event.wait())
                try:
                    done, _pending = await asyncio.wait(
                        {task_obj, abort_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    # abort 触发且 task 未完成 → cancel
                    if abort_wait in done and not task_obj.done():
                        task_obj.cancel()
                finally:
                    if not abort_wait.done():
                        abort_wait.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await abort_wait
        # await task：CancelledError / 业务异常均在此抛出
        return await task_obj
    except asyncio.CancelledError:
        # abort 或外部 cancel，返回失败结果（不重试，I4）
        logger.info(
            "team subtask aborted",
            agent=task.agent,
            task_id=getattr(task, "id", ""),
            thread_id=thread_id,
        )
        if writer is not None:
            writer(
                make_sse_event(
                    "delegation",
                    {
                        "target": task.agent,
                        "source": "team",
                        "event": "aborted",
                        "agent": task.agent,
                        "task_id": getattr(task, "id", ""),
                        "message": "用户中止",
                    },
                )
            )
        return TeamSubtaskResult(
            agent=task.agent,
            success=False,
            payload="用户中止",
        )
    except Exception as exc:  # noqa: BLE001
        # 业务异常包装为失败 result（让 run_with_retry 判定是否瞬态重试）
        # BE-E 修复：payload 包含异常类名，便于 run_with_retry 启发式判定瞬态
        return TeamSubtaskResult(
            agent=task.agent,
            success=False,
            payload=f"{task.agent} 子任务异常: {type(exc).__name__}: {exc}",
        )
    finally:
        semaphore.release()
        if task_obj is not None:
            _unregister_running_task(thread_id, task_obj)


# ============================================================
# run_with_retry：失败重试 + 指数退避
# ============================================================


async def run_with_retry(
    task: TeamTask,
    runner_factory: Callable[[], Coroutine[Any, Any, TeamSubtaskResult]],
    max_retries: int = 2,
    *,
    thread_id: str = "",
    abort_event: Optional[asyncio.Event] = None,
    writer: Optional[Callable[[dict], None]] = None,
) -> TeamSubtaskResult:
    """失败重试 + 指数退避，仅瞬态错误重试（T8）。

    重试策略：

    - 瞬态错误（``TRANSIENT_ERRORS`` + 5xx）：重试，退避 1s/2s/4s
    - ``asyncio.CancelledError``：直接 raise（I4，abort 不重试）
    - 4xx / ``ValueError`` / 其他非瞬态：不重试，直接返回失败结果
    - 重试 ``max_retries`` 次后仍失败：返回 ``success=False, retries=max_retries``
      + payload 含最终异常信息

    ``runner_factory`` 是工厂函数（每次调用返回新 coroutine），避免重试时
    复用已耗尽的 coroutine（Python coroutine 一次性）。

    Args:
        task: ``TeamTask`` 实例。
        runner_factory: 工厂函数，每次调用返回新 coroutine，coroutine 返回
            ``TeamSubtaskResult``。异常会传播到本函数判定是否重试。
        max_retries: 最大重试次数（默认 2，共 3 次执行）。
        thread_id: 父 thread id，透传给 ``acquire_and_run``（如 runner_factory
            内部已调用 acquire_and_run，则本函数只负责 retry 包裹）。
        abort_event: 透传给 ``acquire_and_run``。
        writer: 透传给 ``acquire_and_run``。

    Returns:
        ``TeamSubtaskResult``：成功 / 失败 / 重试耗尽结果，``retries`` 字段
        记录实际重试次数。
    """
    last_exc: BaseException | None = None
    retries_done = 0
    for attempt in range(max_retries + 1):  # initial + max_retries
        try:
            # runner_factory 可能内部调用 acquire_and_run（限流 + abort cancel）
            # 也可能直接是业务 coroutine；本函数只负责 retry + backoff
            if thread_id:
                result = await acquire_and_run(
                    task=task,
                    runner_coro=runner_factory(),
                    thread_id=thread_id,
                    abort_event=abort_event,
                    writer=writer,
                )
                if result.success:
                    result.retries = retries_done
                    return result
                # BE-E 修复：失败 result 启发式判定是否瞬态
                # acquire_and_run 已吞异常返回失败 result，通过 payload 中的
                # 异常类名关键词判定是否瞬态（ConnectError/TimeoutError 等）
                payload_str = str(result.payload)
                is_transient_payload = any(
                    kw in payload_str
                    for kw in (
                        "ConnectError",
                        "TimeoutError",
                        "ReadTimeout",
                        "WriteTimeout",
                        "PoolTimeout",
                    )
                )
                if is_transient_payload and attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "team subtask transient error (from payload), retrying",
                        agent=task.agent,
                        task_id=getattr(task, "id", ""),
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        backoff_sec=backoff,
                    )
                    await asyncio.sleep(backoff)
                    retries_done = attempt + 1
                    continue
                # 非瞬态失败：不重试
                result.retries = retries_done
                return result
            # 直接 await coroutine（不经过 acquire_and_run，便于精确异常判定）
            result = await runner_factory()
            if result.success:
                result.retries = retries_done
                return result
            # 失败 result：业务逻辑失败（如 team_role 缺 system_prompt），不重试
            return result
        except asyncio.CancelledError:
            # I4: abort 不重试，直接 raise
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _is_transient(exc) and attempt < max_retries:
                backoff = 2 ** attempt  # 1, 2, 4
                logger.warning(
                    "team subtask transient error, retrying",
                    agent=task.agent,
                    task_id=getattr(task, "id", ""),
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    backoff_sec=backoff,
                    error=str(exc),
                )
                await asyncio.sleep(backoff)
                retries_done = attempt + 1
                continue
            # 非瞬态错误 / 重试耗尽：返回失败 result
            return TeamSubtaskResult(
                agent=task.agent,
                success=False,
                payload=f"子任务异常: {exc}",
                retries=retries_done,
            )
    # 理论不可达（for 循环已覆盖所有路径），防御性兜底
    return TeamSubtaskResult(
        agent=task.agent,
        success=False,
        payload=f"重试 {retries_done} 次后仍失败: {last_exc}",
        retries=retries_done,
    )


# ============================================================
# 现有 helper 迁移：runner 解析 / workspace 授权 / 事件路由
# ============================================================


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


async def _inherit_workspace(
    child_thread_id: str,
    workspace_path: str | None,
    agent_name: str = "",
) -> TeamSubtaskResult | None:
    """将父 thread 的 workspace 授权继承到子任务 thread。

    子任务使用独立 thread_id（如 ``{parent}-team-{uuid4()}``），
    若不继承授权，fs 工具的沙箱检查会失败，触发 directory_extension
    审批死锁（_handle_directory_extension 在返回前等待审批，但审批事件
    在返回后才 yield 到前端）。

    Phase 2 T7：异常收窄为 ``(PathNotAuthorized, ValueError)``（授权类异常），
    返回 ``TeamSubtaskResult`` 失败结果让调用方跳过 runner。非授权类异常
    （如 ``RuntimeError``）不再被宽 ``except Exception`` 吞掉，向上抛出。

    Returns:
        ``None`` 表示授权成功；``TeamSubtaskResult`` 表示授权失败，调用方
        应跳过 runner 直接返回失败状态更新。
    """
    if not workspace_path:
        return None
    from app.sandbox import get_sandbox
    from app.sandbox.path_guard import PathNotAuthorized

    sandbox = get_sandbox()
    try:
        await sandbox.authorize(child_thread_id, workspace_path, writable=True, source="team_inherit")
    except (PathNotAuthorized, ValueError) as exc:
        logger.warning(
            "team subtask workspace inherit failed",
            child_thread_id=child_thread_id,
            workspace=workspace_path,
            error=str(exc),
        )
        return TeamSubtaskResult(
            agent=agent_name,
            success=False,
            payload=f"workspace authorization failed: {exc}",
        )
    return None


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

    Phase 2 R2：原静默吞异常已改为 ``logger.warning`` 记录，便于排查
    「为何某条 tool_result 丢失」。

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
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning(
                    "dropped malformed tool_result event",
                    event_type="tool_result",
                    error=str(exc),
                )
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
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "dropped malformed subtask_done event",
                event_type=_SUBTASK_DONE_EVENT,
                error=str(exc),
            )
            return TeamSubtaskResult(agent="", success=False, payload="哨兵事件解析失败")
    if "event" in event and event_type in _PASSTHROUGH_EVENTS:
        writer(event)
    return None


# ============================================================
# _run_subtask_stream：通用子任务流式执行（事件驱动 abort）
# ============================================================


async def _run_subtask_stream(
    runner: Callable[..., AsyncIterator[dict]],
    runner_args: tuple,
    runner_kwargs: dict,
    agent_name: str,
    abort_event: Any,
    writer: Callable[[dict], None],
    *,
    subtask_timeout: int = 600,
) -> TeamSubtaskResult:
    """通用子任务流式执行：调用 runner，路由事件，返回结果。

    处理 abort / error / 异常 / 哨兵事件，passthrough 实时写 writer。

    Phase 2 T8 改造：abort 从 ``asyncio.wait(timeout=5)`` 轮询式改为事件驱动
    （``asyncio.wait(FIRST_COMPLETED)`` 无 timeout，abort_event 一旦 set 立即
    返回中止结果）。原 5s 轮询会导致 LLM 长调用期间 abort 信号响应延迟最多 5s，
    新方案响应延迟接近 0。

    Phase 1 稳定性硬化：``asyncio.wait_for`` 包裹事件迭代循环，超时后返回失败
    ``TeamSubtaskResult`` 并发射 ``delegation`` 事件让前端 trace 可见。
    timeout 包裹位于本函数内部（而非节点调用处），确保 Phase 2 节点统一重构不会丢失该保护。

    注：本函数保留 ``abort_event`` 参数以兼容现有 orchestrator 调用；新的 v2
    节点应优先使用 ``acquire_and_run``（registry + cancel 模式）。
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

            # BE-D 修复：_subtask_done 哨兵到达时由 _route_event_for_node 返回
            # TeamSubtaskResult，存入 last_result 供外层读取，_iterate 统一返回 IterResult
            last_result: TeamSubtaskResult | None = None

            async def _iterate() -> IterResult:
                """实际事件迭代循环，被 ``asyncio.wait_for`` 包裹。

                Phase 2 T8：``asyncio.wait(FIRST_COMPLETED)`` 无 timeout，
                abort_event 与 next_event 竞速，事件驱动响应 abort 信号。

                BE-D 修复：统一返回 ``IterResult`` 枚举，调用方按枚举处理：
                - ``ABORTED`` → 外层包装 "用户中止" 失败结果
                - ``NORMAL_END`` → runner 流自然结束（未发 ``_subtask_done``）
                - ``SUBTASK_DONE`` → ``_subtask_done`` 哨兵到达，结果存 ``last_result``
                """
                nonlocal last_result
                stream_iter = stream.__aiter__()
                abort_task = asyncio.ensure_future(abort_event.wait())
                next_event_task = asyncio.ensure_future(stream_iter.__anext__())
                try:
                    while True:
                        done, _pending = await asyncio.wait(
                            {next_event_task, abort_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if abort_task in done:
                            next_event_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await next_event_task
                            return IterResult.ABORTED
                        if next_event_task in done:
                            try:
                                event = next_event_task.result()
                            except StopAsyncIteration:
                                return IterResult.NORMAL_END
                            result = _route_event_for_node(
                                event, collected_text, tool_traces, writer, abort_event
                            )
                            if result is not None:
                                last_result = result
                                return IterResult.SUBTASK_DONE
                            next_event_task = asyncio.ensure_future(stream_iter.__anext__())
                finally:
                    for _t in (next_event_task, abort_task):
                        if not _t.done():
                            _t.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await _t

            try:
                iter_result = await asyncio.wait_for(_iterate(), timeout=subtask_timeout)
            except asyncio.TimeoutError:
                # Phase 1 D3：超时分支发射 delegation 事件让前端 trace 可见
                logger.warning(
                    "team subtask timeout",
                    agent=agent_name,
                    subtask_timeout=subtask_timeout,
                    collected_text_len=sum(len(t) for t in collected_text),
                    tool_traces_count=len(tool_traces),
                )
                writer(make_sse_event("delegation", {
                    "target": agent_name,
                    "source": "team",
                    "event": "timeout",
                    "agent": agent_name,
                    "timeout": subtask_timeout,
                    "message": f"子任务超时（{subtask_timeout}s）",
                }))
                return TeamSubtaskResult(
                    agent=agent_name,
                    success=False,
                    payload=f"子任务超时（{subtask_timeout}s）",
                )
            # BE-D 修复：按 IterResult 枚举统一处理
            if iter_result == IterResult.ABORTED:
                return TeamSubtaskResult(
                    agent=agent_name, success=False, payload="用户中止"
                )
            if iter_result == IterResult.SUBTASK_DONE and last_result is not None:
                return last_result
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


# ============================================================
# _run_team_role_subtask：软件开发团队角色子任务
# ============================================================


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
    *,
    subtask_timeout: int = 600,
    scene_prompt: str = "",
) -> TeamSubtaskResult:
    """软件开发团队角色子任务（frontend_dev / backend_dev / tester / ...）。

    优先用 ``build_custom_agent`` 构建专属 agent（astream_events v2）；
    缺少 ``system_prompt`` 配置时显式失败（D7：不再静默降级到 coding Expert）。
    显式失败时发射 ``warning`` SSE 事件（writer 可用时），让前端 trace 可见。

    I4.2（D6 / REQ-TEAM-PROMPT-1）：``chat_model`` 透传给 ``build_custom_agent``，
    ``profile_prompt`` + ``scene_prompt`` 拼入 ``extra_system_prompt``，让 team role
    继承父会话的模型选择和用户画像 / 场景上下文。

    Phase 2 T8：abort 从 ``asyncio.wait(timeout=5)`` 轮询式改为事件驱动
    （``asyncio.wait(FIRST_COMPLETED)`` 无 timeout）。
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
        # D7：显式失败，不静默降级到 coding Expert。
        # 缺少 system_prompt 的团队角色无法构建专属 agent，启动时
        # validate_team_subagents 应已拦截，此处为运行时兜底保护。
        logger.error(
            "team_role missing system_prompt, failing explicitly",
            agent=task.agent,
            has_cfg=cfg is not None,
        )
        # 新增 SSE warning 发射（writer 可用时），让前端 trace 可见
        if writer is not None:
            writer(
                make_sse_event(
                    "warning",
                    {
                        "source": "team",
                        "agent": task.agent,
                        "message": f"团队角色 {task.agent} 配置缺失 system_prompt",
                    },
                )
            )
        return TeamSubtaskResult(
            agent=task.agent,
            success=False,
            payload=f"团队角色 {task.agent} 配置缺失 system_prompt",
        )

    # 有专属配置：build_custom_agent + astream_events v2
    from app.subagents.custom_agent import build_custom_agent

    # T5: child_id UUID 化（{parent}-team-{uuid4()}），不再含 agent 名与 idx
    child_thread_id = f"{thread_id}-team-{uuid.uuid4()}"
    inherit_result = await _inherit_workspace(child_thread_id, workspace_path, agent_name=task.agent)
    if inherit_result is not None:
        return inherit_result

    # I4.2: 拼接 profile_prompt + scene_prompt 作为 extra_system_prompt
    extra_parts: list[str] = []
    if profile_prompt:
        extra_parts.append(profile_prompt)
    if scene_prompt:
        extra_parts.append(scene_prompt)
    extra_system_prompt = "\n".join(extra_parts)

    agent_obj = build_custom_agent(
        key=task.agent,
        thread_id=child_thread_id,
        system_prompt=cfg.system_prompt,
        tools=cfg.tools,
        temperature=cfg.temperature,
        workspace_path=workspace_path,
        chat_model=chat_model,
        extra_system_prompt=extra_system_prompt,
    )
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": task.input}]}
    config = {
        "configurable": {"thread_id": child_thread_id},
        # astream_events 不继承 create_deep_agent 的 .with_config({"recursion_limit": 9999})，
        # 不显式传入时会使用 LangGraph 默认值 25，导致复杂子任务提前触发 GraphRecursionError。
        # ReadonlyLoopGuardMiddleware 已在模型调用前拦截只读工具死循环，此处仅需硬安全网。
        "recursion_limit": 9_999,
    }
    # trace_id 透传：build_custom_agent 内部走 astream_events v2，
    # 回调中创建新协程，ContextVar 不会自动跨协程传播。
    _trace_id = current_trace_id() or ""
    _trace_cm = bind_trace(_trace_id) if _trace_id else contextlib.nullcontext()
    with _trace_cm:
        try:
            # Phase 1 稳定性硬化：asyncio.wait_for 包裹事件迭代循环，超时后返回失败。
            async def _iterate() -> IterResult:
                """``_iterate`` 闭包，返回 ``IterResult`` 枚举（BE-D 修复）。

                Phase 2 T8：``asyncio.wait(FIRST_COMPLETED)`` 无 timeout，
                abort_event 与 next_event 竞速，事件驱动响应 abort 信号。

                BE-D 修复：统一返回 ``IterResult``，与 ``_run_subtask_stream._iterate``
                协议一致。BE-J 修复：runner 流自然结束时发射 ``_subtask_done`` 哨兵，
                与通用 runner 路径对称（build_custom_agent + astream_events 不发哨兵）。
                """
                # T12 评估：langchain_core 1.4.8 支持 version="v3"，但仅限
                # BaseChatModel / CompiledGraph（build_custom_agent 返回 CompiledGraph，
                # 理论支持 v3）。v3 事件 schema 可能与 v2 不同，当前 _iterate 的事件
                # 处理（on_chat_model_stream / on_tool_start / on_tool_end）按 v2 schema
                # 编写。保持 v2 直至 v3 schema 稳定且事件处理代码完成适配验证。
                stream_iter = agent_obj.astream_events(
                    inputs, version="v2", config=config
                ).__aiter__()
                abort_task = asyncio.ensure_future(abort_event.wait())
                next_event_task = asyncio.ensure_future(stream_iter.__anext__())
                try:
                    while True:
                        done, _pending = await asyncio.wait(
                            {next_event_task, abort_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if abort_task in done:
                            next_event_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await next_event_task
                            return IterResult.ABORTED
                        if next_event_task in done:
                            try:
                                event = next_event_task.result()
                            except StopAsyncIteration:
                                # BE-J 修复：runner 流自然结束，发射 _subtask_done
                                # 哨兵事件，与通用 runner 路径对称（deep/code/rag/web
                                # 等子代理 runner 自行发射该哨兵，team_role 路径补齐）
                                writer(
                                    make_sse_event(
                                        _SUBTASK_DONE_EVENT,
                                        {
                                            "agent": task.agent,
                                            "success": True,
                                            "payload": _build_summary(
                                                collected_text, tool_traces, task.agent
                                            ),
                                        },
                                    )
                                )
                                return IterResult.NORMAL_END
                            kind = event["event"]
                            ename = event.get("name", "")
                            edata = event.get("data", {}) or {}
                            if kind == "on_chat_model_stream":
                                content = extract_chunk_text(edata.get("chunk"), strip=False)
                                if content:
                                    collected_text.append(content)
                            elif kind in ("on_tool_start", "on_tool_end"):
                                trace_data = edata.get("input") if kind == "on_tool_start" else edata.get("output")
                                tool_traces.append(f"{ename}: {str(trace_data)[:200]}")
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
                            next_event_task = asyncio.ensure_future(stream_iter.__anext__())
                finally:
                    for _t in (next_event_task, abort_task):
                        if not _t.done():
                            _t.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await _t

            try:
                iter_result = await asyncio.wait_for(_iterate(), timeout=subtask_timeout)
            except asyncio.TimeoutError:
                # Phase 1 D3：超时分支发射 delegation 事件让前端 trace 可见
                logger.warning(
                    "team_role subtask timeout",
                    agent=task.agent,
                    task_id=getattr(task, "id", ""),
                    subtask_timeout=subtask_timeout,
                    collected_text_len=sum(len(t) for t in collected_text),
                    tool_traces_count=len(tool_traces),
                )
                writer(make_sse_event("delegation", {
                    "target": task.agent,
                    "source": "team",
                    "event": "timeout",
                    "agent": task.agent,
                    "timeout": subtask_timeout,
                    "message": f"子任务超时（{subtask_timeout}s）",
                }))
                return _done(False, f"子任务超时（{subtask_timeout}s）")
            # BE-D 修复：按 IterResult 枚举统一处理
            if iter_result == IterResult.ABORTED:
                return _done(False, "用户中止")
        except Exception as exc:  # noqa: BLE001
            return _done(False, f"团队角色 {task.agent} 子任务异常: {exc}")

    return _done(
        bool(collected_text or tool_traces),
        _build_summary(collected_text, tool_traces, task.agent),
    )
