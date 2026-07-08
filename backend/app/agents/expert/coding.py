"""coding 场景 Expert（代码专家 agent）实现。

基于 ``build_deep_agent`` 框架构建，与 DeepAgent 共享 streaming/approval 基础设施，
但有以下区别：
1. 使用 coding Expert 专用 system prompt（``_DEFAULT_CODING_EXPERT_SYSTEM_PROMPT``）
2. 额外注入 ``delegate_to_subagent`` 委派工具（不可委派其他 Expert）
3. 不包含 ``invoke_agent_team`` 工具（Expert 不可触发 AgentTeam）
4. SSE 事件 source 标识为 ``"coding"``

流程:
1. 构建 coding Expert agent（含 delegate_to_subagent + 完整代码工具集 + interrupt_before 审批）
2. ``astream_events`` 驱动图执行，流式产出 token / tool_call / tool_result 事件
3. 危险工具中断 → yield approval_request → 等待审批 → 恢复执行
4. 循环直至图完成
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.tools import tool

from app.config import BUILTIN_SUBAGENT_KEYS, get_settings
from app.deep.agent import build_deep_agent
from app.deep.recovery import (
    _inject_tool_error_messages,
    _sanitize_message_history,
)
from app.deep.streaming import _stream_agent_events
from app.deep.tools import (
    DANGEROUS_TOOLS,
    _TOOL_NAME_MAP,
    _load_mcp_tools,
    _make_deep_tools,
)
from app.observability.logger import logger
from app.sandbox import get_sandbox
from app.security.approval_flow import run_approval_loop
from app.utils.sse_events import make_sse_event

__all__ = [
    "build_coding_expert",
    "run_coding_expert",
]


def _build_subagent_list_description() -> str:
    """构建可用子代理列表描述（用于 delegate_to_subagent 工具描述）。"""
    settings = get_settings()
    lines: list[str] = []
    for name in sorted(BUILTIN_SUBAGENT_KEYS):
        cfg = settings.subagents.get(name)
        if cfg and cfg.enabled:
            lines.append(f"  - {name}: {cfg.trigger_description or name}")
    for key in sorted(settings.custom_subagents.keys()):
        cfg = settings.custom_subagents[key]
        if cfg.enabled:
            lines.append(f"  - {key}: {cfg.trigger_description or key}")
    if not lines:
        return "  (当前无可用子代理)"
    return "\n".join(lines)


def make_expert_delegation_tools(
    thread_id: str,
    workspace_path: str | None = None,
) -> list:
    """构建 Expert 的委派工具列表（仅 delegate_to_subagent）。

    Expert 不可委派其他 Expert（无 delegate_to_expert），也不可触发 AgentTeam
    （无 invoke_agent_team）。Expert 仅可调用 rag/web 子代理辅助任务。

    Args:
        thread_id: 会话 ID（传给子代理用于沙箱授权）。
        workspace_path: 当前工作区路径（传给子代理用于相对路径解析）。

    Returns:
        [delegate_to_subagent] 工具列表（仅一个元素）。
    """
    subagent_desc = (
        "委派任务给子代理（Subagent）。\n\n"
        f"可用子代理列表：\n{_build_subagent_list_description()}\n\n"
        "何时使用：\n"
        "- 知识库检索 → rag 子代理\n"
        "- 网页搜索 → web 子代理\n"
        "- 自定义子代理按需使用\n\n"
        "Args:\n"
        "    agent_name: 子代理名称（如 \"rag\" / \"web\"）\n"
        "    task: 要委派的任务描述\n\n"
        "Returns:\n"
        "    子代理的最终输出文本。"
    )

    @tool(description=subagent_desc)
    async def delegate_to_subagent(agent_name: str, task: str) -> str:
        """委派任务给子代理（Subagent）。"""
        settings = get_settings()
        subagents_cfg = settings.subagents
        custom_cfg = settings.custom_subagents

        # 内置子代理
        if agent_name in BUILTIN_SUBAGENT_KEYS:
            cfg = subagents_cfg.get(agent_name)
            if not cfg or not cfg.enabled:
                return f"错误：子代理 '{agent_name}' 已禁用"

            logger.info(
                "coding_expert.delegate_to_subagent",
                thread_id=thread_id,
                agent=agent_name,
                task_len=len(task),
            )

            parts: list[str] = []
            if agent_name == "rag":
                from app.subagents.rag_agent import run_rag_agent

                async for event in run_rag_agent(thread_id, task, history=None):
                    if event.get("type") == "token":
                        parts.append(event.get("content", ""))
            elif agent_name == "web":
                from app.subagents.web_agent import run_web_agent

                async for event in run_web_agent(thread_id, task, history=None):
                    if event.get("type") == "token":
                        parts.append(event.get("content", ""))
            else:
                return f"错误：内置子代理 '{agent_name}' 尚未实现"

            return "".join(parts).strip() or f"子代理 '{agent_name}' 未返回结果"

        # 自定义子代理
        if agent_name in custom_cfg:
            cfg = custom_cfg[agent_name]
            if not cfg.enabled:
                return f"错误：自定义子代理 '{agent_name}' 已禁用"

            logger.info(
                "coding_expert.delegate_to_custom_subagent",
                thread_id=thread_id,
                agent=agent_name,
                task_len=len(task),
            )

            from app.subagents.custom_agent import run_custom_agent

            parts2: list[str] = []
            async for event in run_custom_agent(
                agent_name, thread_id, task, history=None, workspace_path=workspace_path
            ):
                if event.get("type") == "token":
                    parts2.append(event.get("content", ""))
            return "".join(parts2).strip() or f"自定义子代理 '{agent_name}' 未返回结果"

        return f"错误：未知的子代理 '{agent_name}'。可用: rag, web, {', '.join(sorted(custom_cfg.keys()))}"

    return [delegate_to_subagent]


async def build_coding_expert(
    thread_id: str,
    tools: list | None = None,
    profile_prompt: str = "",
    checkpointer: Any = None,
    workspace_path: str | None = None,
) -> Any:
    """构造 coding 场景 Expert agent。

    基于 ``build_deep_agent`` 框架，使用 coding Expert 专用 system prompt。
    与 Supervisor 的区别：
    - 仅含 ``delegate_to_subagent`` 委派工具（不可委派其他 Expert）
    - 无 ``invoke_agent_team`` 工具（不可触发 AgentTeam）
    - source 标识为 ``"coding"``

    Args:
        thread_id: 会话 ID（用于工具沙箱授权绑定）。
        tools: 可选，已构建的工具列表。若未传则内部构建（标准工具集 + 委派工具）。
        profile_prompt: 可选，用户画像前缀，拼到 system prompt 前。
        checkpointer: 可选，共享的 LangGraph checkpointer。
        workspace_path: 可选当前工作区绝对路径。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    from app.config.prompts.agent import _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT

    settings = get_settings()
    expert_cfg = settings.agents.experts.get("coding")
    if expert_cfg is None:
        # 配置缺失时使用默认值
        from app.config.agents import ExpertSettings

        expert_cfg = ExpertSettings(
            system_prompt=_DEFAULT_CODING_EXPERT_SYSTEM_PROMPT,
            scenario="coding",
        )

    if tools is None:
        standard_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
        delegation_tools = make_expert_delegation_tools(thread_id, workspace_path)
        tools = [*standard_tools, *delegation_tools]

    # scene_prompt 透传给 build_deep_agent，覆盖默认 _DEEP_SYSTEM_PROMPT
    scene_prompt = expert_cfg.system_prompt or _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT

    return await build_deep_agent(
        thread_id,
        tools=tools,
        profile_prompt=profile_prompt,
        checkpointer=checkpointer,
        scene_prompt=scene_prompt,
        workspace_path=workspace_path,
    )


async def _is_interrupted(agent: Any, config: dict) -> bool:
    """检查 agent 是否在 interrupt 处暂停。

    保留为模块级函数以兼容测试 patch（``patch("app.agents.expert.coding._is_interrupted")``）。
    实际审批循环逻辑由 ``app.security.approval_flow.run_approval_loop`` 提供。
    """
    state = await agent.aget_state(config)
    if not state or not state.next:
        return False
    return "tools" in state.next


async def run_coding_expert(
    message: str,
    thread_id: str,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
    workspace_path: str | None = None,
    parent_thread_id: str | None = None,
) -> AsyncIterator[dict]:
    """运行 coding 场景 Expert，yield SSE 事件。

    流程:
    1. 构建 coding Expert agent（含 delegate_to_subagent + interrupt_before 审批）
    2. 流式执行，危险工具中断 → 审批 → 恢复，循环直至完成

    Args:
        message: 用户消息。
        thread_id: 会话 ID。
        profile_prompt: 用户画像前缀。
        history: 历史 messages 列表（已截断）。
        permission_mode: 权限模式，"standard" 或 "full_trust"。
        workspace_path: 可选当前工作区绝对路径。
        parent_thread_id: 父 thread_id（Team 模式下子任务继承父 thread 的沙箱授权）。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    config: dict = {"configurable": {"thread_id": thread_id or "coding-default"}}
    sandbox = get_sandbox()
    is_full_trust = permission_mode == "full_trust"

    try:
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, True)
            logger.info("coding_expert full_trust mode enabled", thread_id=thread_id)

        history_msgs = list(history) if history else []
        inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}

        # 构建 agent
        try:
            agent_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
            mcp_tools, mcp_untrusted_names = await _load_mcp_tools()
            if mcp_tools:
                agent_tools.extend(mcp_tools)
                logger.info(
                    "MCP tools merged into coding Expert",
                    thread_id=thread_id,
                    count=len(mcp_tools),
                    untrusted=len(mcp_untrusted_names),
                )
            delegation_tools = make_expert_delegation_tools(thread_id, workspace_path)
            all_tools = [*agent_tools, *delegation_tools]

            agent = await build_coding_expert(
                thread_id,
                tools=all_tools,
                profile_prompt=profile_prompt,
                workspace_path=workspace_path,
            )
        except ValueError as exc:
            yield make_sse_event("error", f"LLM 不可用: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("build_coding_expert failed", thread_id=thread_id)
            yield make_sse_event("error", f"Coding Expert 初始化失败: {exc}")
            return

        # 运行时危险工具集合
        enabled_tool_names = {
            _TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools
        }
        runtime_dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names

        # 防御性清理
        await _inject_tool_error_messages(
            agent, config, "上次操作未正常完成，已自动清理状态"
        )
        inputs["messages"] = _sanitize_message_history(
            inputs["messages"], "上次操作未正常完成，已自动清理状态"
        )

        # ---- 公共审批循环（security.approval_flow.run_approval_loop）----
        # stream_fn / is_interrupted_fn / inject_tool_error_messages_fn 传入模块级
        # 引用，以便测试通过 patch("app.agents.expert.coding._xxx") 替换。
        # readonly_streak_threshold=10 启用循环保护（防过度探索）。
        async for sse in run_approval_loop(
            agent,
            config,
            thread_id,
            workspace_path,
            permission_mode,
            runtime_dangerous,
            agent_tools,
            yield_event=None,
            sandbox=sandbox,
            parent_thread_id=parent_thread_id,
            source="coding",
            inputs=inputs,
            stream_fn=_stream_agent_events,
            is_interrupted_fn=_is_interrupted,
            inject_tool_error_messages_fn=_inject_tool_error_messages,
            readonly_streak_threshold=10,
        ):
            yield sse
    finally:
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, False)
