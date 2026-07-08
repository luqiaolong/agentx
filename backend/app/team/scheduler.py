"""AgentTeam Scheduler：并行调度子任务到对应专家。

包含：
- ``_SUBTASK_DONE_EVENT``：子任务完成哨兵事件类型（内部使用，绝不输出到前端）。
- ``_PASSTHROUGH_EVENTS``：deep 子任务中需要实时透传到前端的事件类型集合。
- ``_run_subtask``：执行单个子任务，流式产出透传事件 + 完成哨兵。
- ``_collect_event``：从 rag/web/custom 子代理事件流中收集 token / tool_result。

注意：
- ``_run_subtask`` 通过 ``app.team.orchestrator`` 模块属性访问 ``run_coding_expert`` /
  ``run_rag_agent`` / ``run_web_agent`` / ``run_deep_path`` / ``run_custom_agent``，
  以便测试通过 ``monkeypatch.setattr("app.team.orchestrator.run_xxx", ...)`` 替换。
- ``_run_subtask`` 内部对团队角色场景保留 ``from app.subagents.custom_agent import
  build_custom_agent`` 延迟 import（与原 orchestrator.py 行为等价）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

from app.approval import get_abort_event
from app.config import get_settings
from app.utils.sse_events import make_team_event
from app.utils.text import extract_chunk_text
from app.team.aggregator import _build_summary
from app.team.blackboard import TeamPlanTask

if TYPE_CHECKING:
    from app.router.state import RouterState

__all__ = [
    "_run_subtask",
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
) -> AsyncIterator[dict[str, str]]:
    """执行单个子任务，流式产出透传事件，最后产出 _subtask_done 哨兵。

    deep 子任务复用 DeepAgent 路径，其 approval_request / todo_update /
    delegation / tool_call 事件必须实时透传到前端——否则审批流会死锁。
    其余子代理（code/rag/web/custom）只产出文本与工具痕迹，无需透传。

    Args:
        task_index: 子任务序号，用于为 deep 子任务生成独立 thread_id，
            避免并行 deep 子任务共享 checkpoint 与审批流冲突。
        workspace_path: 当前会话绑定的 workspace 路径，透传到 fs 工具
            用于解析相对路径。
    """
    # 通过 orchestrator 模块属性访问 run_xxx 函数，
    # 以便测试通过 monkeypatch app.team.orchestrator.run_xxx 替换。
    # 延迟 import 避免与 orchestrator.py 顶部的 import 形成循环。
    from app.team import orchestrator

    agent_name = task.agent
    input_text = task.input

    collected_text: list[str] = []
    tool_traces: list[str] = []

    def _done(success: bool, payload: str) -> dict[str, str]:
        return make_team_event(
            _SUBTASK_DONE_EVENT,
            {"agent": agent_name, "success": success, "payload": payload},
        )

    abort_event = await get_abort_event(thread_id)

    def _inherit_workspace(child_thread_id: str) -> None:
        """将主 thread_id 的 workspace 授权继承到子任务 thread_id。

        子任务使用独立 thread_id（如 ``{thread_id}-team-code-{idx}``），
        若不继承授权，fs 工具的沙箱检查会失败，触发 directory_extension
        审批死锁（_handle_directory_extension 在返回前等待审批，但审批事件
        在返回后才 yield 到前端）。
        """
        if not workspace_path:
            return
        from app.utils.security import get_sandbox

        sandbox = get_sandbox()
        try:
            sandbox.authorize(child_thread_id, workspace_path, writable=True, source="team_inherit")
        except ValueError as exc:  # noqa: BLE001
            logger.warning(
                "team subtask workspace inherit failed",
                child_thread_id=child_thread_id,
                workspace=workspace_path,
                error=str(exc),
            )

    if agent_name == "deep":
        # deep 子任务使用独立 thread_id，避免并行 deep 子任务共享 checkpoint
        # 与 _pending_approvals 审批流冲突
        deep_thread_id = f"{thread_id}-team-deep-{task_index}"
        _inherit_workspace(deep_thread_id)
        deep_state: RouterState = {
            "thread_id": deep_thread_id,
            "messages": [{"role": "user", "content": input_text}],
        }
        try:
            async for event in orchestrator.run_deep_path(
                deep_state,
                input_text,
                profile_prompt=profile_prompt,
                history=history,
                permission_mode=permission_mode,
                scene_prompt=scene_prompt,
                workspace_path=workspace_path,
            ):
                if abort_event.is_set():
                    yield _done(False, "用户中止")
                    return
                etype = event.get("event", "")
                data = event.get("data", "")
                if etype == "token":
                    collected_text.append(str(data))
                elif etype == "tool_result":
                    # 收集工具痕迹用于 summary，同时透传到前端（配对 tool_call）
                    try:
                        obj = json.loads(data) if isinstance(data, str) else data
                        if isinstance(obj, dict):
                            tool_traces.append(f"{obj.get('name', '?')}: {str(obj.get('result', ''))[:200]}")
                    except Exception:  # noqa: BLE001
                        pass
                    yield event
                elif etype == "error":
                    yield _done(False, f"deep 子任务失败: {data}")
                    return
                elif etype in _PASSTHROUGH_EVENTS:
                    # 透传审批/委派/工具/推理事件到前端
                    yield event
        except Exception as exc:  # noqa: BLE001
            yield _done(False, f"deep 子任务异常: {exc}")
            return
    elif agent_name == "code":
        # code 子任务映射到 coding Expert（场景化架构）
        # coding Expert 使用独立 thread_id，避免并行子任务共享 checkpoint
        code_thread_id = f"{thread_id}-team-code-{task_index}"
        _inherit_workspace(code_thread_id)
        try:
            async for event in orchestrator.run_coding_expert(
                input_text,
                code_thread_id,
                profile_prompt=profile_prompt,
                history=history,
                permission_mode=permission_mode,
                workspace_path=workspace_path,
            ):
                if abort_event.is_set():
                    yield _done(False, "用户中止")
                    return
                etype = event.get("event", "")
                data = event.get("data", "")
                if etype == "token":
                    collected_text.append(str(data))
                elif etype == "tool_result":
                    try:
                        obj = json.loads(data) if isinstance(data, str) else data
                        if isinstance(obj, dict):
                            tool_traces.append(f"{obj.get('name', '?')}: {str(obj.get('result', ''))[:200]}")
                    except Exception:  # noqa: BLE001
                        pass
                    yield event
                elif etype == "error":
                    yield _done(False, f"coding Expert 子任务失败: {data}")
                    return
                elif etype in _PASSTHROUGH_EVENTS:
                    yield event
        except Exception as exc:  # noqa: BLE001
            yield _done(False, f"coding Expert 子任务异常: {exc}")
            return
    elif agent_name == "rag":
        async for event in orchestrator.run_rag_agent(
            thread_id, input_text, history=history, workspace_path=workspace_path
        ):
            _collect_event(event, collected_text, tool_traces)
    elif agent_name == "web":
        async for event in orchestrator.run_web_agent(
            thread_id, input_text, history=history, workspace_path=workspace_path
        ):
            _collect_event(event, collected_text, tool_traces)
    # 软件开发专家团角色：用 custom_agent 工厂构建专属 agent，复用 astream_events 事件流
    elif agent_name in ("frontend_dev", "backend_dev", "tester", "architect", "devops", "ui_designer", "product_manager"):
        cfg = get_settings().team_subagents.get(agent_name)
        if cfg and cfg.system_prompt:
            # MUST 传 thread_id：_make_custom_tools 用 thread_id 绑定沙箱授权
            from app.subagents.custom_agent import build_custom_agent
            agent = build_custom_agent(
                key=agent_name,
                thread_id=thread_id,
                system_prompt=cfg.system_prompt,
                tools=cfg.tools,
                temperature=cfg.temperature,
                workspace_path=workspace_path,
            )
            history_msgs = list(history) if history else []
            inputs = {"messages": [*history_msgs, {"role": "user", "content": input_text}]}
            config = {"configurable": {"thread_id": thread_id}}
            async for event in agent.astream_events(inputs, version="v2", config=config):
                if abort_event.is_set():
                    yield _done(False, "用户中止")
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
        else:
            # 无专属配置时降级到 coding Expert
            fallback_thread_id = f"{thread_id}-team-fallback-{task_index}"
            _inherit_workspace(fallback_thread_id)
            try:
                async for event in orchestrator.run_coding_expert(
                    input_text,
                    fallback_thread_id,
                    history=history,
                    permission_mode=permission_mode,
                    workspace_path=workspace_path,
                ):
                    if abort_event.is_set():
                        yield _done(False, "用户中止")
                        return
                    etype = event.get("event", "")
                    data = event.get("data", "")
                    if etype == "token":
                        collected_text.append(str(data))
                    elif etype == "tool_result":
                        try:
                            obj = json.loads(data) if isinstance(data, str) else data
                            if isinstance(obj, dict):
                                tool_traces.append(f"{obj.get('name', '?')}: {str(obj.get('result', ''))[:200]}")
                        except Exception:  # noqa: BLE001
                            pass
                        yield event
                    elif etype == "error":
                        yield _done(False, f"降级 coding Expert 子任务失败: {data}")
                        return
                    elif etype in _PASSTHROUGH_EVENTS:
                        yield event
            except Exception as exc:  # noqa: BLE001
                yield _done(False, f"降级 coding Expert 子任务异常: {exc}")
                return
    elif agent_name.startswith("custom-"):
        key = agent_name[len("custom-"):]
        async for event in orchestrator.run_custom_agent(
            key, thread_id, input_text, history=history, workspace_path=workspace_path
        ):
            _collect_event(event, collected_text, tool_traces)
    else:
        yield _done(False, f"未知 agent: {agent_name}")
        return

    # 直接检查输出是否为空，不依赖字符串匹配
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
