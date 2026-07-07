"""Supervisor 委派工具：delegate_to_expert / delegate_to_subagent。

这两个工具作为 LangGraph ToolNode 注入 Supervisor 的 ReAct 图。
当 Supervisor LLM 决定委派时，调用这些工具执行子代理/专家。

工具内部运行子代理并收集最终文本结果返回给 Supervisor LLM。
子代理的流式事件（token/tool_call/tool_result）通过回调函数
透传给外层 SSE 流，实现前端可观测性。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from langchain_core.tools import tool

from app.config import BUILTIN_EXPERT_KEYS, BUILTIN_SUBAGENT_KEYS, get_settings
from app.observability.logger import logger

__all__ = [
    "make_delegation_tools",
    "DelegationEventCallback",
]

# 委派事件回调类型：子代理事件透传给外层 SSE 流
DelegationEventCallback = Callable[[dict], Any]


def _build_expert_list_description() -> str:
    """构建可用 Expert 列表描述（用于 delegate_to_expert 工具的 docstring）。"""
    experts = get_settings().agents.experts
    lines: list[str] = []
    for name in sorted(BUILTIN_EXPERT_KEYS):
        cfg = experts.get(name)
        if cfg and cfg.enabled:
            lines.append(f"  - {name}: {cfg.scenario or name} 场景专家")
    if not lines:
        return "  (当前无可用 Expert)"
    return "\n".join(lines)


def _build_subagent_list_description() -> str:
    """构建可用子代理列表描述（用于 delegate_to_subagent 工具的 docstring）。"""
    settings = get_settings()
    lines: list[str] = []
    # 内置子代理
    for name in sorted(BUILTIN_SUBAGENT_KEYS):
        cfg = settings.subagents.get(name)
        if cfg and cfg.enabled:
            lines.append(f"  - {name}: {cfg.trigger_description or name}")
    # 自定义子代理
    for key in sorted(settings.custom_subagents.keys()):
        cfg = settings.custom_subagents[key]
        if cfg.enabled:
            lines.append(f"  - {key}: {cfg.trigger_description or key}")
    if not lines:
        return "  (当前无可用子代理)"
    return "\n".join(lines)


async def _collect_agent_result(
    agent_runner: Callable[..., AsyncIterator[dict]],
    *args: Any,
    **kwargs: Any,
) -> str:
    """运行子代理并收集最终文本结果。

    子代理的 token 事件被拼接为最终文本，tool_call/tool_result 事件被忽略
    （子代理内部工具调用不透传给 Supervisor LLM）。

    Args:
        agent_runner: 子代理运行函数（如 run_rag_agent）。
        *args, **kwargs: 传给 agent_runner 的参数。

    Returns:
        子代理最终输出的文本（所有 token 拼接）。
    """
    parts: list[str] = []
    async for event in agent_runner(*args, **kwargs):
        etype = event.get("type", "")
        if etype == "token":
            content = event.get("content", "")
            if content:
                parts.append(content)
    return "".join(parts).strip()


def make_delegation_tools(
    thread_id: str,
    workspace_path: str | None = None,
    event_callback: DelegationEventCallback | None = None,
) -> list:
    """构建 Supervisor 的委派工具列表。

    Args:
        thread_id: 会话 ID（传给子代理用于沙箱授权）。
        workspace_path: 当前工作区路径（传给子代理用于相对路径解析）。
        event_callback: 可选的回调函数，子代理事件透传给外层 SSE 流。
            若为 None，子代理事件被丢弃（仅收集最终文本）。

    Returns:
        [delegate_to_expert, delegate_to_subagent] 工具列表。
    """
    # 动态构建工具描述（含可用 Expert / 子代理列表）
    expert_desc = (
        "委派任务给领域专家（Expert）。\n\n"
        f"可用 Expert 列表：\n{_build_expert_list_description()}\n\n"
        "何时使用：\n"
        "- 用户任务涉及特定领域的专业工作（如代码重构、项目分析）\n"
        "- 任务需要专家级别的处理能力\n"
        "- 通用闲聊、简单文件操作不需要委派\n\n"
        "Args:\n"
        "    expert_name: Expert 名称（如 \"coding\"）\n"
        "    task: 要委派的任务描述\n"
        "    context: 可选的上下文信息\n\n"
        "Returns:\n"
        "    Expert 的最终输出文本。"
    )
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

    @tool(description=expert_desc)
    async def delegate_to_expert(expert_name: str, task: str, context: str = "") -> str:
        """委派任务给领域专家（Expert）。"""
        settings = get_settings()
        experts = settings.agents.experts

        if expert_name not in experts:
            return f"错误：未知的 Expert '{expert_name}'。可用: {', '.join(sorted(experts.keys()))}"

        cfg = experts[expert_name]
        if not cfg.enabled:
            return f"错误：Expert '{expert_name}' 已禁用"

        logger.info(
            "supervisor.delegate_to_expert",
            thread_id=thread_id,
            expert=expert_name,
            task_len=len(task),
        )

        # 目前仅 coding Expert 实现，未来扩展 research/trading 等
        if expert_name == "coding":
            from app.agents.expert.coding import run_coding_expert

            result_text = await _collect_agent_result(
                run_coding_expert,
                thread_id,
                task,
                history=None,
                workspace_path=workspace_path,
            )
            return result_text or f"Expert '{expert_name}' 未返回结果"

        return f"错误：Expert '{expert_name}' 尚未实现"

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
                "supervisor.delegate_to_subagent",
                thread_id=thread_id,
                agent=agent_name,
                task_len=len(task),
            )

            if agent_name == "rag":
                from app.subagents.rag_agent import run_rag_agent

                result_text = await _collect_agent_result(
                    run_rag_agent,
                    thread_id,
                    task,
                    history=None,
                )
                return result_text or f"子代理 '{agent_name}' 未返回结果"

            if agent_name == "web":
                from app.subagents.web_agent import run_web_agent

                result_text = await _collect_agent_result(
                    run_web_agent,
                    thread_id,
                    task,
                    history=None,
                )
                return result_text or f"子代理 '{agent_name}' 未返回结果"

            return f"错误：内置子代理 '{agent_name}' 尚未实现"

        # 自定义子代理
        if agent_name in custom_cfg:
            cfg = custom_cfg[agent_name]
            if not cfg.enabled:
                return f"错误：自定义子代理 '{agent_name}' 已禁用"

            logger.info(
                "supervisor.delegate_to_custom_subagent",
                thread_id=thread_id,
                agent=agent_name,
                task_len=len(task),
            )

            from app.subagents.custom_agent import run_custom_agent

            result_text = await _collect_agent_result(
                run_custom_agent,
                agent_name,
                thread_id,
                task,
                history=None,
                workspace_path=workspace_path,
            )
            return result_text or f"自定义子代理 '{agent_name}' 未返回结果"

        return f"错误：未知的子代理 '{agent_name}'。可用: rag, web, {', '.join(sorted(custom_cfg.keys()))}"

    return [delegate_to_expert, delegate_to_subagent]
