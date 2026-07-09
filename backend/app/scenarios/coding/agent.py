"""coding 场景 Expert（代码专家 agent）实现。

基于 ``app.deepagent.factory.create_agent`` / ``build_deep_agent`` 框架构建，与 DeepAgent
共享 streaming/approval 基础设施，但有以下区别：
1. 使用 coding Expert 专用 system prompt（``_DEFAULT_CODING_EXPERT_SYSTEM_PROMPT``）
2. 通过 deepagents ``SubAgentMiddleware`` 声明式注入 rag/web/custom 子代理
   （不可委派其他 Expert，也无 invoke_agent_team）
3. SSE 事件 source 标识为 ``"coding"``

流程:
1. 构建 coding Expert agent（含标准工具集 + subagents + interrupt_on 审批）
2. ``run_agent_with_approval`` 统一驱动：流式执行 → 危险工具中断 → 审批 → 恢复
3. 循环直至图完成
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from deepagents import SubAgent

from app.config import BUILTIN_SUBAGENT_KEYS, get_settings
from app.deepagent.agent import build_deep_agent
from app.deepagent.approval_runner import run_agent_with_approval
from app.deepagent.streaming import _stream_agent_events
from app.deepagent.tool_assembly import (
    DANGEROUS_TOOLS,
    _TOOL_NAME_MAP,
    _load_mcp_tools,
    _make_deep_tools,
)
from app.observability.logger import logger
from app.sandbox import get_sandbox
from app.subagents.base import THINK_PROMPT_SUFFIX, make_rag_tools, make_web_tools
from app.sse.events import make_error_event

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = [
    "build_coding_expert",
    "run_coding_expert",
]


def _build_subagents(
    thread_id: str,
    workspace_path: str | None = None,
) -> list[SubAgent]:
    """构建 coding Expert 可用的子代理声明列表。

    只包含 rag / web / 自定义子代理，不包含其他 Expert。
    工具集经过安全过滤：不包含 ``FORBIDDEN_SUBAGENT_TOOLS`` 中的写/编辑/git 工具。

    Args:
        thread_id: 会话 ID（传给子代理工具用于沙箱授权）。
        workspace_path: 当前工作区路径（用于 fs 工具相对路径解析）。

    Returns:
        ``SubAgent`` 声明列表，可直接透传给 ``create_agent(subagents=...)``。
    """
    from app.security.dangerous_tools import FORBIDDEN_SUBAGENT_TOOLS

    settings = get_settings()
    subagents: list[SubAgent] = []

    # 内置子代理：rag / web
    for name in sorted(BUILTIN_SUBAGENT_KEYS):
        cfg = settings.subagents.get(name)
        if not cfg or not cfg.enabled:
            continue

        if name == "rag":
            tools = make_rag_tools(thread_id)
        elif name == "web":
            tools = make_web_tools(thread_id)
        else:
            continue

        subagents.append(
            SubAgent(
                name=name,
                description=cfg.trigger_description or name,
                system_prompt=(cfg.system_prompt or "") + THINK_PROMPT_SUFFIX,
                tools=tools,
            )
        )

    # 自定义子代理：按配置逐个声明，工具集防御性过滤危险工具
    for key, cfg in sorted(settings.custom_subagents.items()):
        if not cfg.enabled:
            continue

        # 安全过滤：移除 FORBIDDEN_SUBAGENT_TOOLS 中的工具
        safe_tool_names = [
            t for t in cfg.tools if t not in FORBIDDEN_SUBAGENT_TOOLS
        ]

        # 延迟导入，避免与 custom_agent 构造路径产生循环引用
        from app.subagents.custom_agent import _make_custom_tools

        tools = _make_custom_tools(thread_id or "", safe_tool_names, workspace_path)
        subagents.append(
            SubAgent(
                name=key,
                description=cfg.trigger_description or cfg.name or key,
                system_prompt=(cfg.system_prompt or "") + THINK_PROMPT_SUFFIX,
                tools=tools,
            )
        )

    return subagents


async def build_coding_expert(
    thread_id: str,
    tools: list | None = None,
    profile_prompt: str = "",
    checkpointer: Any = None,
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
    rubric: str | None = None,
    grader_model: Any | None = None,
) -> Any:
    """构造 coding 场景 Expert agent。

    基于 ``build_deep_agent`` 框架，使用 coding Expert 专用 system prompt，
    并通过 ``subagents`` 参数注入 rag/web/custom 子代理。

    与 Supervisor 的区别：
    - 仅含子代理（rag/web/custom），不含其他 Expert
    - 无 ``invoke_agent_team`` 工具（不可触发 AgentTeam）
    - source 标识为 ``"coding"``

    Args:
        thread_id: 会话 ID（用于工具沙箱授权绑定）。
        tools: 可选，已构建的工具列表。若未传则内部构建（标准工具集）。
            子代理声明由本函数独立构建，不依赖该参数。
        profile_prompt: 可选，用户画像前缀，拼到 system prompt 前。
        checkpointer: 可选，共享的 LangGraph checkpointer。
        workspace_path: 可选当前工作区路径。
        chat_model: 可选注入的 ChatModel。非 None 时直接使用（评测框架注入 MockChatModel）；
            None 时调用 ``get_chat_model()`` 获取真实 LLM。

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
        tools = _make_deep_tools(thread_id, workspace_path=workspace_path)

    # scene_prompt 透传给 build_deep_agent，覆盖默认 _DEEP_SYSTEM_PROMPT
    scene_prompt = expert_cfg.system_prompt or _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT

    subagents = _build_subagents(thread_id, workspace_path)

    return await build_deep_agent(
        thread_id,
        tools=tools,
        profile_prompt=profile_prompt,
        checkpointer=checkpointer,
        scene_prompt=scene_prompt,
        workspace_path=workspace_path,
        chat_model=chat_model,
        subagents=subagents,
        rubric=expert_cfg.rubric if expert_cfg.rubric else rubric,
        grader_model=grader_model,
    )


async def run_coding_expert(
    message: str,
    thread_id: str,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
    workspace_path: str | None = None,
    parent_thread_id: str | None = None,
    chat_model: BaseChatModel | None = None,
) -> AsyncIterator[dict]:
    """运行 coding 场景 Expert，yield SSE 事件。

    流程:
    1. 构建 coding Expert agent（含标准工具集 + subagents + interrupt_on 审批）
    2. 流式执行，危险工具中断 → 审批 → 恢复，循环直至完成

    Args:
        message: 用户消息。
        thread_id: 会话 ID。
        profile_prompt: 用户画像前缀。
        history: 历史 messages 列表（已截断）。
        permission_mode: 权限模式，"standard" 或 "full_trust"。
        workspace_path: 可选当前工作区绝对路径。
        parent_thread_id: 父 thread_id（Team 模式下子任务继承父 thread 的沙箱授权）。
        chat_model: 可选注入的 ChatModel，透传到 ``build_coding_expert``。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    config: dict = {"configurable": {"thread_id": thread_id or "coding-default"}}
    sandbox = get_sandbox()
    settings = get_settings()
    is_full_trust = permission_mode == "full_trust"

    try:
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, True)
            logger.info("coding_expert full_trust mode enabled", thread_id=thread_id)

        history_msgs = list(history) if history else []
        inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}

        # 构建 agent 工具集（标准工具 + MCP）并构造 agent
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

            agent = await build_coding_expert(
                thread_id,
                tools=agent_tools,
                profile_prompt=profile_prompt,
                workspace_path=workspace_path,
                chat_model=chat_model,
            )
        except ValueError as exc:
            yield make_error_event(f"LLM 不可用: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("build_coding_expert failed", thread_id=thread_id)
            yield make_error_event(f"Coding Expert 初始化失败: {exc}")
            return

        # 运行时危险工具集合
        enabled_tool_names = {
            _TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools
        }
        runtime_dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names
        # execute 不再属于 DANGEROUS_TOOLS；其审批通过 directory_extension 机制处理
        # （workspace 之外未授权时触发审批），由 run_agent_with_approval 统一处理。

        # 统一审批执行循环（deep.execution.run_agent_with_approval）
        # stream_fn 传入模块级引用，以便测试通过
        # patch("app.scenarios.coding.agent._stream_agent_events") 替换。
        # is_interrupted_fn 使用 execution 默认值（app.deepagent.approval_runner._is_interrupted）。
        async for sse in run_agent_with_approval(
            agent,
            config,
            thread_id=thread_id,
            workspace_path=workspace_path,
            permission_mode=permission_mode,
            runtime_dangerous=runtime_dangerous,
            source="coding",
            inputs=inputs,
            sandbox=sandbox,
            parent_thread_id=parent_thread_id,
            stream_fn=_stream_agent_events,
            readonly_streak_threshold=settings.readonly_streak_threshold,
        ):
            yield sse
    finally:
        if is_full_trust:
            await sandbox.set_full_trust(thread_id, False)
