"""AgentTeam Scheduler：并行调度子任务到对应专家。

包含：
- ``_SUBTASK_DONE_EVENT``：子任务完成哨兵事件类型（内部使用，绝不输出到前端）。
- ``_PASSTHROUGH_EVENTS``：deep 子任务中需要实时透传到前端的事件类型集合。
- ``_run_subtask``：执行单个子任务，流式产出透传事件 + 完成哨兵（≤80 行）。
- ``_route_subtask_events``：流式子任务事件路由 helper（消除 deep/code/fallback
  三处重复的 token/tool_result/error/passthrough 分支）。
- ``_run_team_role_subtask``：软件开发团队角色子任务（astream_events + 降级 coding）。
- ``_collect_event``：从 rag/web/custom 子代理事件流中收集 token / tool_result。

注意：
- ``_run_subtask`` 通过 ``subtask_runners`` 参数注入 mock（推荐），
  或回退到 ``app.team.orchestrator`` 模块属性（向后兼容，仅当 T-P2-4 删除 noqa
  imports 后测试仍 monkeypatch orchestrator 模块属性时使用）。
- 所有 runner（run_deep_path / run_coding_expert / run_rag_agent / run_web_agent /
  run_custom_agent）通过 ``_get_runner`` 延迟解析，不在模块顶部 import。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from app.security.approval import get_abort_event
from app.config import get_settings
from app.observability.logger import logger
from app.utils.sse_events import make_team_event
from app.utils.text import extract_chunk_text
from app.team.aggregator import _build_summary
from app.team.blackboard import TeamPlanTask

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.router.state import RouterState

__all__ = [
    "_run_subtask",
    "_route_subtask_events",
    "_run_team_role_subtask",
    "_collect_event",
    "_SUBTASK_DONE_EVENT",
    "_PASSTHROUGH_EVENTS",
]


# 子任务完成哨兵事件类型（内部使用，绝不输出到前端）
_SUBTASK_DONE_EVENT = "_subtask_done"

# deep 子任务中需要实时透传到前端的事件类型
# approval_request 必须直达前端，否则 DeepAgent 审批流会死锁
# tool_result 必须透传，否则前端 tool_call 配对断裂
# reasoning 透传供前端展示 deep 子任务的思考过程
# token 透传让前端能看到 deep 子任务的流式输出
_PASSTHROUGH_EVENTS: frozenset[str] = frozenset(
    {"approval_request", "todo_update", "delegation", "tool_call", "tool_result", "reasoning", "token"}
)


def _get_runner(name: str, subtask_runners: dict[str, Any] | None) -> Any:
    """延迟解析子任务 runner。

    优先级：
    1. ``subtask_runners`` 参数注入（测试推荐路径，T-P2-4 后唯一路径）
    2. ``app.team.orchestrator`` 模块属性（向后兼容，用于 test_interrupt_stream.py
       仍 monkeypatch ``orch_module._run_subtask`` 的场景）

    映射关系（subtask_runners key = TeamPlanTask.agent 值）：
    - "code"                     → run_coding_expert
    - "deep"                     → run_deep_path
    - "rag"                     → run_rag_agent
    - "web"                     → run_web_agent
    - "custom"                  → run_custom_agent

    ``run_team_path`` 在 ``subtask_runners=None`` 时会从真实模块 lazy-import
    构造默认 dict（见 orchestrator.py），故生产路径也走 subtask_runners 查找。
    """
    if subtask_runners and name in subtask_runners:
        return subtask_runners[name]
    # 向后兼容：从 orchestrator 模块属性解析（T-P2-4 删除 noqa imports 后，
    # 这些属性不再被 re-export，仅当外部代码 monkeypatch orchestrator 模块属性时生效）
    from app.team import orchestrator as _orch

    attr_map = {
        "code": "run_coding_expert",
        "coding": "run_coding_expert",
        "coding_expert": "run_coding_expert",
        "deep": "run_deep_path",
        "rag": "run_rag_agent",
        "web": "run_web_agent",
        "custom": "run_custom_agent",
    }
    return getattr(_orch, attr_map.get(name, f"run_{name}"), None)


async def _inherit_workspace(child_thread_id: str, workspace_path: str | None) -> None:
    """将主 thread_id 的 workspace 授权继承到子任务 thread_id。

    子任务使用独立 thread_id（如 ``{thread_id}-team-code-{idx}``），
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
    except ValueError as exc:  # noqa: BLE001
        logger.warning(
            "team subtask workspace inherit failed",
            child_thread_id=child_thread_id,
            workspace=workspace_path,
            error=str(exc),
        )


async def _route_subtask_events(
    event_stream: AsyncIterator[dict[str, str]],
    abort_event: Any,
    error_prefix: str,
    collected_text: list[str],
    tool_traces: list[str],
    done_factory: Callable[[bool, str], dict[str, str]],
) -> AsyncIterator[dict[str, str]]:
    """路由流式子任务（deep/code/fallback coding）的事件流。

    统一处理 token 收集 / tool_result 透传与痕迹收集 / error 提前终止 /
    passthrough 事件透传 / abort 中止，消除三处重复分支。

    - abort 触发：yield ``_done(False, "用户中止")`` 后停止。
    - error 事件：yield ``_done(False, f"{error_prefix}: {data}")`` 后停止。
    - 异常：yield ``_done(False, f"{error_prefix}异常: {exc}")`` 后停止。
    - 正常结束：不 yield _done（由 ``_run_subtask`` 收尾产出 success/fail _done）。
    """
    try:
        async for event in event_stream:
            if abort_event.is_set():
                yield done_factory(False, "用户中止")
                return
            etype = event.get("event", "")
            data = event.get("data", "")
            if etype == "token":
                collected_text.append(str(data))
            elif etype == "tool_result":
                try:
                    obj = json.loads(data) if isinstance(data, str) else data
                    if isinstance(obj, dict):
                        tool_traces.append(
                            f"{obj.get('name', '?')}: {str(obj.get('result', ''))[:200]}"
                        )
                except Exception:  # noqa: BLE001
                    pass
                yield event
            elif etype == "error":
                yield done_factory(False, f"{error_prefix}: {data}")
                return
            elif etype in _PASSTHROUGH_EVENTS:
                yield event
    except Exception as exc:  # noqa: BLE001
        yield done_factory(False, f"{error_prefix}异常: {exc}")


async def _run_team_role_subtask(
    task: TeamPlanTask,
    thread_id: str,
    history: list | None,
    permission_mode: str,
    profile_prompt: str,
    task_index: int,
    workspace_path: str | None,
    chat_model: BaseChatModel | None,
    subtask_runners: dict[str, Any] | None,
    abort_event: Any,
    collected_text: list[str],
    tool_traces: list[str],
    done_factory: Callable[[bool, str], dict[str, str]],
) -> AsyncIterator[dict[str, str]]:
    """软件开发团队角色子任务（frontend_dev / backend_dev / tester / ...）。

    优先用 ``build_custom_agent`` 构建专属 agent（astream_events v2）；
    无配置时降级到 coding Expert（走 ``_route_subtask_events`` 统一路由）。
    """
    cfg = get_settings().team_subagents.get(task.agent)
    if not cfg or not cfg.system_prompt:
        # 降级到 coding Expert
        fallback_thread_id = f"{thread_id}-team-fallback-{task_index}"
        await _inherit_workspace(fallback_thread_id, workspace_path)
        runner = _get_runner("code", subtask_runners)
        event_stream = runner(
            task.input,
            fallback_thread_id,
            profile_prompt=profile_prompt,
            history=history,
            permission_mode=permission_mode,
            workspace_path=workspace_path,
            parent_thread_id=thread_id,
            chat_model=chat_model,
        )
        async for ev in _route_subtask_events(
            event_stream, abort_event, "降级 coding Expert 子任务失败",
            collected_text, tool_traces, done_factory,
        ):
            yield ev
        return

    # 有专属配置：build_custom_agent + astream_events v2
    from app.subagents.custom_agent import build_custom_agent

    agent = build_custom_agent(
        key=task.agent,
        thread_id=thread_id,
        system_prompt=cfg.system_prompt,
        tools=cfg.tools,
        temperature=cfg.temperature,
        workspace_path=workspace_path,
    )
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": task.input}]}
    config = {"configurable": {"thread_id": thread_id}}
    try:
        async for event in agent.astream_events(inputs, version="v2", config=config):
            if abort_event.is_set():
                yield done_factory(False, "用户中止")
                return
            kind = event["event"]
            ename = event.get("name", "")
            edata = event.get("data", {}) or {}
            if kind == "on_chat_model_stream":
                content = extract_chunk_text(edata.get("chunk"), strip=False)
                if content:
                    collected_text.append(content)
            elif kind == "on_tool_start":
                tool_traces.append(f"{ename}: {str(edata.get('input', ''))[:200]}")
            elif kind == "on_tool_end":
                tool_traces.append(f"{ename}: {str(edata.get('output', ''))[:200]}")
    except Exception as exc:  # noqa: BLE001
        yield done_factory(False, f"团队角色 {task.agent} 子任务异常: {exc}")


async def _run_subtask(
    task: TeamPlanTask,
    thread_id: str,
    history: list | None,
    permission_mode: str,
    scene_prompt: str | None,
    state: RouterState,
    profile_prompt: str,
    task_index: int = 0,
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
    subtask_runners: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """执行单个子任务，流式产出透传事件 + ``_subtask_done`` 哨兵。

    deep 子任务复用 DeepAgent 路径，其 approval_request / todo_update /
    delegation / tool_call 事件必须实时透传到前端——否则审批流会死锁。
    其余子代理（code/rag/web/custom）只产出文本与工具痕迹，无需透传。

    Args:
        subtask_runners: 可选 ``{"code": <callable>, "rag": <callable>, ...}``
            字典，测试注入 mock 替代 ``orchestrator.run_xxx``。``None`` 时
            回退到 ``app.team.orchestrator`` 模块属性（向后兼容）。
        task_index: 子任务序号，用于为 deep/code 子任务生成独立 thread_id，
            避免并行子任务共享 checkpoint 与审批流冲突。
        workspace_path: 当前会话绑定的 workspace 路径，透传到 fs 工具。
        chat_model: 可选注入的 ChatModel，透传到 runner。None 时使用真实 LLM。
    """
    agent_name = task.agent
    input_text = task.input
    collected_text: list[str] = []
    tool_traces: list[str] = []
    abort_event = await get_abort_event(thread_id)

    def _done(success: bool, payload: str) -> dict[str, str]:
        return make_team_event(
            _SUBTASK_DONE_EVENT,
            {"agent": agent_name, "success": success, "payload": payload},
        )

    # 分支分发：按 agent_name 选择对应 runner，统一走 _route_subtask_events 路由
    if agent_name == "deep":
        child_id = f"{thread_id}-team-deep-{task_index}"
        await _inherit_workspace(child_id, workspace_path)
        deep_state: RouterState = {
            "thread_id": child_id,
            "messages": [{"role": "user", "content": input_text}],
        }
        stream = _get_runner("deep", subtask_runners)(
            deep_state, input_text, profile_prompt=profile_prompt, history=history,
            permission_mode=permission_mode, scene_prompt=scene_prompt,
            workspace_path=workspace_path, parent_thread_id=thread_id, chat_model=chat_model,
        )
        async for ev in _route_subtask_events(
            stream, abort_event, "deep 子任务失败", collected_text, tool_traces, _done,
        ):
            yield ev
    elif agent_name == "code":
        child_id = f"{thread_id}-team-code-{task_index}"
        await _inherit_workspace(child_id, workspace_path)
        stream = _get_runner("code", subtask_runners)(
            input_text, child_id, profile_prompt=profile_prompt, history=history,
            permission_mode=permission_mode, workspace_path=workspace_path,
            parent_thread_id=thread_id, chat_model=chat_model,
        )
        async for ev in _route_subtask_events(
            stream, abort_event, "coding Expert 子任务失败", collected_text, tool_traces, _done,
        ):
            yield ev
    elif agent_name in ("rag", "web"):
        async for event in _get_runner(agent_name, subtask_runners)(
            thread_id, input_text, history=history, workspace_path=workspace_path,
        ):
            _collect_event(event, collected_text, tool_traces)
    elif agent_name in get_settings().team_subagents:
        async for ev in _run_team_role_subtask(
            task, thread_id, history, permission_mode, profile_prompt, task_index,
            workspace_path, chat_model, subtask_runners, abort_event,
            collected_text, tool_traces, _done,
        ):
            yield ev
    elif agent_name.startswith("custom-"):
        key = agent_name[len("custom-"):]
        async for event in _get_runner("custom", subtask_runners)(
            key, thread_id, input_text, history=history, workspace_path=workspace_path,
        ):
            _collect_event(event, collected_text, tool_traces)
    else:
        yield _done(False, f"未知 agent: {agent_name}")
        return

    # 收尾：根据收集到的文本/痕迹产出 success/fail 哨兵
    if not collected_text and not tool_traces:
        yield _done(False, _build_summary(collected_text, tool_traces, agent_name))
        return
    yield _done(True, _build_summary(collected_text, tool_traces, agent_name))


def _collect_event(event: dict, text_parts: list[str], tool_traces: list[str]) -> None:
    etype = event.get("type", "")
    if etype == "token":
        text_parts.append(str(event.get("content", "")))
    elif etype == "tool_result":
        name = str(event.get("name", "?"))
        result = str(event.get("result", ""))[:200]
        tool_traces.append(f"{name}: {result}")
