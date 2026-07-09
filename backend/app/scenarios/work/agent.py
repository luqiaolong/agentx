"""work 场景 Supervisor（全能 agent）实现。

基于 ``app.deepagent.factory.create_agent`` 封装 ``deepagents.create_deep_agent`` 构建，
与 DeepAgent 共享 streaming/approval 基础设施，但有以下区别：
1. 使用 Supervisor 专用 system prompt（``_DEFAULT_SUPERVISOR_SYSTEM_PROMPT``）
2. 通过 deepagents ``SubAgentMiddleware`` 注入 ``task`` 委派工具，暴露
   rag / web / custom 子代理
3. 保留轻量级 ``delegate_to_expert`` 工具用于 coding Expert 委派
   （coding Expert 包含危险工具，其审批流仍在 Expert 内部闭环，不适合直接作为
   无中断的 compiled subagent）
4. 支持 @mention 语法强制委派
5. SSE 事件 source 标识为 ``"work"``

流程:
1. 解析 @mention：若命中 Expert 则直接运行 Expert；若命中子代理则运行后回注 Supervisor
2. 构建 Supervisor agent（含标准工具 + delegate_to_expert + task 子代理 + interrupt_on 危险工具审批）
3. ``astream_events`` 驱动图执行，流式产出 token / tool_call / tool_result 事件
4. 危险工具中断 → 公共审批执行层 ``app.deepagent.approval_runner.run_agent_with_approval`` 处理
5. 循环直至图完成
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from deepagents import CompiledSubAgent

from app.scenarios.work.mention import parse_mention
from app.config import get_settings
from app.deepagent.approval_runner import run_agent_with_approval
from app.deepagent.factory import create_agent
from app.deepagent.tool_assembly import (
    DANGEROUS_TOOLS,
    _TOOL_NAME_MAP,
    _load_mcp_tools,
    _make_deep_tools,
)
from app.llm import get_chat_model
from app.memory.checkpointer import get_async_checkpointer
from app.observability.logger import logger
from app.sandbox import get_sandbox
from app.utils.prompts import build_workspace_prompt_suffix, resolve_system_prompt
from app.sse.events import make_error_event, make_sse_event

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = [
    "build_work_supervisor",
    "run_work_supervisor",
]


def _build_subagent_runnables(
    thread_id: str,
    workspace_path: str | None = None,
) -> list[CompiledSubAgent]:
    """构建 Supervisor 的 compiled subagent 列表，透传给 ``SubAgentMiddleware``。

    包含 rag / web 内置子代理以及所有启用的自定义子代理。
    coding Expert 不放在此处：它包含危险工具且需要独立的审批流，仍通过
    ``delegate_to_expert`` 工具委派。

    Args:
        thread_id: 会话 ID（传给子代理用于沙箱授权 / 线程隔离）。
        workspace_path: 当前工作区路径（传给自定义子代理用于相对路径解析）。

    Returns:
        ``CompiledSubAgent`` 列表，可直接作为 ``create_deep_agent(subagents=...)`` 参数。
    """
    settings = get_settings()
    subagents: list[CompiledSubAgent] = []
    subagents_cfg = settings.subagents

    # rag 子代理
    rag_cfg = subagents_cfg.get("rag")
    if rag_cfg and rag_cfg.enabled:
        from app.subagents.rag_agent import build_rag_agent

        subagents.append(
            {
                "name": "rag",
                "description": rag_cfg.trigger_description or "检索内部知识库并回答",
                "runnable": build_rag_agent(thread_id),
            }
        )

    # web 子代理
    web_cfg = subagents_cfg.get("web")
    if web_cfg and web_cfg.enabled:
        from app.subagents.web_agent import build_web_agent

        subagents.append(
            {
                "name": "web",
                "description": web_cfg.trigger_description or "联网搜索最新信息",
                "runnable": build_web_agent(thread_id),
            }
        )

    # 自定义子代理
    for key in sorted(settings.custom_subagents.keys()):
        cfg = settings.custom_subagents[key]
        if not cfg.enabled:
            continue
        from app.subagents.custom_agent import build_custom_agent

        subagents.append(
            {
                "name": key,
                "description": cfg.trigger_description or key,
                "runnable": build_custom_agent(
                    key,
                    thread_id=thread_id,
                    workspace_path=workspace_path,
                ),
            }
        )

    return subagents


def make_expert_delegation_tool(
    thread_id: str,
    workspace_path: str | None = None,
) -> Any:
    """构建 coding Expert 委派工具 ``delegate_to_expert``。

    coding Expert 通过项目原有的 ``run_coding_expert`` 异步生成器运行，
    其内部仍保留自己的 interrupt/审批闭环，因此不适合直接作为 compiled subagent
    嵌入 ``SubAgentMiddleware``。这里保留一个专用工具，供 Supervisor LLM
    在需要代码专家能力时调用。
    """
    from langchain_core.tools import tool

    settings = get_settings()
    experts = settings.agents.experts

    description = (
        "委派任务给 coding Expert（代码专家）。\n\n"
        "何时使用：\n"
        "- 代码重构、项目分析、复杂编程任务\n"
        "- 需要代码级专家处理能力的任务\n"
        "- 通用闲聊、简单文件操作不需要委派\n\n"
        "Args:\n"
        "    expert_name: Expert 名称（目前仅支持 \"coding\"）\n"
        "    task: 要委派的任务描述\n"
        "    context: 可选的上下文信息\n\n"
        "Returns:\n"
        "    Expert 的最终输出文本。"
    )

    @tool(description=description)
    async def delegate_to_expert(expert_name: str, task: str, context: str = "") -> str:
        """委派任务给 coding Expert。"""
        if expert_name not in experts:
            return f"错误：未知的 Expert '{expert_name}'"

        cfg = experts[expert_name]
        if not cfg.enabled:
            return f"错误：Expert '{expert_name}' 已禁用"

        if expert_name != "coding":
            return f"错误：Expert '{expert_name}' 尚未实现"

        logger.info(
            "supervisor.delegate_to_expert",
            thread_id=thread_id,
            expert=expert_name,
            task_len=len(task),
        )

        from app.scenarios.coding.agent import run_coding_expert

        parts: list[str] = []
        async for event in run_coding_expert(
            task,
            thread_id,
            history=None,
            workspace_path=workspace_path,
        ):
            # 收集 Expert 最终输出 token；工具调用/审批请求等事件对 Supervisor 不可见
            if event.get("event") == "token":
                content = event.get("data", "")
                if content:
                    parts.append(content)

        return "".join(parts).strip() or f"Expert '{expert_name}' 未返回结果"

    return delegate_to_expert


async def build_work_supervisor(
    thread_id: str,
    tools: list | None = None,
    subagents: list | None = None,
    profile_prompt: str = "",
    checkpointer: Any = None,
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
    rubric: str | None = None,
    grader_model: Any | None = None,
) -> Any:
    """构造 work 场景 Supervisor agent。

    用 ``app.deepagent.factory.create_agent`` 封装 ``deepagents.create_deep_agent``，
    通过 ``interrupt_on`` 配置仅危险工具中断（只读工具自动放行）。

    Args:
        thread_id: 会话 ID（用于工具沙箱授权绑定）。
        tools: 可选，已构建的工具列表。若未传则内部构建标准工具集 + delegate_to_expert。
        subagents: 可选，compiled subagent 列表，透传给 ``create_deep_agent(subagents=...)``。
            非空时 ``SubAgentMiddleware`` 会自动注入 ``task`` 工具。
        profile_prompt: 可选，用户画像前缀，拼到 system prompt 前。
        checkpointer: 可选，共享的 LangGraph checkpointer。
        workspace_path: 可选当前工作区绝对路径。
        chat_model: 可选注入的 ChatModel。非 None 时直接使用（评测框架注入 MockChatModel）；
            None 时调用 ``get_chat_model()`` 获取真实 LLM。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    from app.config.prompts.agent import _DEFAULT_SUPERVISOR_SYSTEM_PROMPT

    settings = get_settings()
    supervisor_cfg = settings.agents.supervisor

    if chat_model is not None:
        model = chat_model
    else:
        model = get_chat_model(temperature=supervisor_cfg.temperature, streaming=True)

    if tools is None:
        # 标准工具集（fs + cli + git + rag + web）+ coding Expert 委派
        standard_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
        expert_tool = make_expert_delegation_tool(thread_id, workspace_path)
        tools = [*standard_tools, expert_tool]

    if checkpointer is None:
        checkpointer = await get_async_checkpointer()

    # 构造 system prompt：画像前缀 + Supervisor prompt + 工作区后缀
    base_prompt = resolve_system_prompt(
        default=supervisor_cfg.system_prompt or _DEFAULT_SUPERVISOR_SYSTEM_PROMPT,
        scene_prompt=None,
        skill_extra=profile_prompt or None,
    )
    system_prompt = base_prompt + build_workspace_prompt_suffix(workspace_path)

    return create_agent(
        model,
        tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        thread_id=thread_id,
        workspace_path=workspace_path,
        name="work_supervisor",
        subagents=subagents,
        rubric=rubric,
        grader_model=grader_model,
    )


async def run_work_supervisor(
    message: str,
    thread_id: str,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
) -> AsyncIterator[dict]:
    """运行 work 场景 Supervisor，yield SSE 事件。

    流程:
    1. 解析 @mention：若命中 Expert 直接运行 Expert；命中子代理运行后回注 Supervisor
    2. 构建 Supervisor agent（含标准工具 + delegate_to_expert + task 子代理 + interrupt_on 危险工具审批）
    3. 流式执行，危险工具中断 → 审批 → 恢复，循环直至完成

    Args:
        message: 用户消息（可能含 @mention）。
        thread_id: 会话 ID。
        profile_prompt: 用户画像前缀。
        history: 历史 messages 列表（已截断）。
        permission_mode: 权限模式，"standard" 或 "full_trust"。
        workspace_path: 可选当前工作区绝对路径。
        chat_model: 可选注入的 ChatModel，透传到 ``build_work_supervisor`` 与 @mention 强制委派时的 ``run_coding_expert``。None 时使用真实 LLM。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    config: dict = {"configurable": {"thread_id": thread_id or "work-default"}}
    sandbox = get_sandbox()
    is_full_trust = permission_mode == "full_trust"

    # ---- 1. @mention 解析 ----
    cleaned_message, mention_target = parse_mention(message)

    # @mention 命中 Expert：直接运行 Expert，bypass Supervisor
    if mention_target and mention_target[0] == "expert":
        expert_name = mention_target[1]
        logger.info(
            "supervisor.mention_force_expert",
            thread_id=thread_id,
            expert=expert_name,
        )
        yield make_sse_event("delegation", {
            "target": expert_name,
            "source": expert_name,
            "message": f"@mention 强制委派给 {expert_name} Expert",
        })
        from app.scenarios.coding.agent import run_coding_expert

        async for sse in _convert_expert_events(
            run_coding_expert(
                cleaned_message,
                thread_id,
                profile_prompt=profile_prompt,
                history=history,
                workspace_path=workspace_path,
                permission_mode=permission_mode,
                chat_model=chat_model,
            ),
            source=expert_name,
        ):
            yield sse
        return

    # @mention 命中子代理：运行子代理，结果回注 Supervisor 合成
    if mention_target and mention_target[0] == "subagent":
        agent_name = mention_target[1]
        logger.info(
            "supervisor.mention_force_subagent",
            thread_id=thread_id,
            agent=agent_name,
        )
        yield make_sse_event("delegation", {
            "target": agent_name,
            "source": agent_name,
            "message": f"@mention 强制委派给 {agent_name} 子代理",
        })

        subagent_result = await _run_subagent_for_mention(
            agent_name, cleaned_message, thread_id, workspace_path
        )

        # 将子代理结果作为上下文注入 Supervisor
        cleaned_message = (
            f"用户通过 @{agent_name} 委派了子代理，子代理返回结果如下：\n\n"
            f"{subagent_result}\n\n"
            f"请基于以上结果为用户综合回复。原始用户消息：{cleaned_message}"
        )

    # ---- 2. 构建 Supervisor agent + 运行审批执行层 ----
    try:
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, True)
            logger.info("supervisor full_trust mode enabled", thread_id=thread_id)

        # 加载 supervisor 配置（rubric / grader_model 在此读取）
        supervisor_cfg = get_settings().agents.supervisor

        history_msgs = list(history) if history else []
        inputs = {"messages": [*history_msgs, {"role": "user", "content": cleaned_message}]}

        try:
            agent_tools = _make_deep_tools(thread_id, workspace_path=workspace_path)
            mcp_tools, mcp_untrusted_names = await _load_mcp_tools()
            if mcp_tools:
                agent_tools.extend(mcp_tools)
                logger.info(
                    "MCP tools merged into Supervisor",
                    thread_id=thread_id,
                    count=len(mcp_tools),
                    untrusted=len(mcp_untrusted_names),
                )
            expert_tool = make_expert_delegation_tool(thread_id, workspace_path)
            all_tools = [*agent_tools, expert_tool]

            subagents = _build_subagent_runnables(thread_id, workspace_path)

            agent = await build_work_supervisor(
                thread_id,
                tools=all_tools,
                subagents=subagents,
                profile_prompt=profile_prompt,
                workspace_path=workspace_path,
                chat_model=chat_model,
                rubric=supervisor_cfg.rubric or None,
                grader_model=supervisor_cfg.grader_model,
            )
        except ValueError as exc:
            yield make_error_event(f"LLM 不可用: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("build_work_supervisor failed", thread_id=thread_id)
            yield make_error_event(f"Supervisor 初始化失败: {exc}")
            return

        # 运行时危险工具集合 = DANGEROUS_TOOLS 与已启用工具的交集 + MCP untrusted
        enabled_tool_names = {
            _TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools
        }
        runtime_dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names
        # execute 由 SafeLocalShellBackend 提供，不在 agent_tools 中但需审批
        if workspace_path:
            runtime_dangerous = runtime_dangerous | {"execute"}

        # ---- 3. 公共审批执行层（app.deepagent.approval_runner.run_agent_with_approval）----
        # 由统一执行层负责 _is_interrupted、中断循环、危险工具判定等逻辑，
        # work_supervisor 不再重复实现。
        async for sse in run_agent_with_approval(
            agent,
            config,
            thread_id=thread_id,
            workspace_path=workspace_path,
            permission_mode=permission_mode,
            runtime_dangerous=runtime_dangerous,
            source="work",
            inputs=inputs,
            sandbox=sandbox,
            readonly_streak_threshold=get_settings().readonly_streak_threshold,
        ):
            yield sse
    finally:
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, False)


async def _run_subagent_for_mention(
    agent_name: str,
    task: str,
    thread_id: str,
    workspace_path: str | None,
) -> str:
    """运行子代理并收集最终文本结果（用于 @mention 子代理委派）。"""
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
        # 自定义子代理
        from app.subagents.custom_agent import run_custom_agent

        async for event in run_custom_agent(
            agent_name, thread_id, task, history=None, workspace_path=workspace_path
        ):
            if event.get("type") == "token":
                parts.append(event.get("content", ""))

    return "".join(parts).strip()


async def _convert_expert_events(
    expert_stream: AsyncIterator[dict],
    source: str,
) -> AsyncIterator[dict]:
    """将 Expert 事件流转换为 SSE 事件（source 标识为 Expert）。

    Expert 内部使用与 DeepAgent 相同的事件格式（token/tool_call/tool_result/
    approval_request 等），此处透传并补充 source 字段。
    """
    async for event in expert_stream:
        # Expert 事件已经是 SSE 格式（make_sse_event），直接透传
        yield event
