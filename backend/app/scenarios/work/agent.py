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
from app.deepagent.agent import trigger_profile_auto_extract
from app.deepagent.context import bind_agent_context
from app.deepagent.factory import create_agent
from app.deepagent.tool_assembly import (
    assemble_agent_toolset,
    load_mcp_tools,
    make_deep_tools,
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
    chat_model: Any = None,
) -> list[CompiledSubAgent]:
    """构建 Supervisor 的 compiled subagent 列表，透传给 ``SubAgentMiddleware``。

    包含 rag / web 内置子代理以及所有启用的自定义子代理。
    coding Expert 不放在此处：它包含危险工具且需要独立的审批流，仍通过
    ``delegate_to_expert`` 工具委派。

    Args:
        thread_id: 会话 ID（传给子代理用于沙箱授权 / 线程隔离）。
        workspace_path: 当前工作区路径（传给自定义子代理用于相对路径解析）。
        chat_model: 可选的注入 ChatModel（eval mock 模式透传）；为 None 时子代理各自调 ``get_chat_model``。

    Returns:
        ``CompiledSubAgent`` 列表，可直接作为 ``create_deep_agent(subagents=...)`` 参数。
    """
    settings = get_settings()
    subagents: list[CompiledSubAgent] = []
    subagents_cfg = settings.subagents

    # rag 子代理
    rag_cfg = subagents_cfg.get("rag")
    if rag_cfg and rag_cfg.enabled:
        from app.deepagent.subagents.rag_agent import build_rag_agent

        subagents.append(
            {
                "name": "rag",
                "description": rag_cfg.trigger_description or "检索内部知识库并回答",
                "runnable": build_rag_agent(thread_id, chat_model=chat_model),
            }
        )

    # web 子代理
    web_cfg = subagents_cfg.get("web")
    if web_cfg and web_cfg.enabled:
        from app.deepagent.subagents.web_agent import build_web_agent

        subagents.append(
            {
                "name": "web",
                "description": web_cfg.trigger_description or "联网搜索最新信息",
                "runnable": build_web_agent(thread_id, chat_model=chat_model),
            }
        )

    # 自定义子代理
    for key in sorted(settings.custom_subagents.keys()):
        cfg = settings.custom_subagents[key]
        if not cfg.enabled:
            continue
        from app.deepagent.subagents.custom_agent import build_custom_agent

        subagents.append(
            {
                "name": key,
                "description": cfg.trigger_description or key,
                "runnable": build_custom_agent(
                    key,
                    thread_id=thread_id,
                    workspace_path=workspace_path,
                    chat_model=chat_model,
                ),
            }
        )

    return subagents


def make_expert_delegation_tool(
    thread_id: str,
    workspace_path: str | None = None,
    profile_prompt: str = "",
    permission_mode: str = "standard",
    chat_model: BaseChatModel | None = None,
) -> Any:
    """构建 coding Expert 委派工具 ``delegate_to_expert``。

    coding Expert 通过 ``run_coding_expert`` 异步生成器运行，其内部仍保留自己的
    interrupt/审批闭环，因此不适合直接作为 compiled subagent 嵌入
    ``SubAgentMiddleware``。

    修复要点（Bug 1/2/3）：
    - **checkpointer 隔离**：传入 ``InMemorySaver`` 作为 Expert 的 checkpointer，
      避免 Expert 的 tool_call / tool_result 写入 Supervisor 的 checkpoint 污染上下文。
    - **参数透传**：完整传递 ``profile_prompt`` / ``permission_mode`` / ``chat_model``，
      确保 Expert 与 Supervisor 的权限模式一致（full_trust 不丢失）。
    - **事件透传**：非 token 事件（``approval_request`` / ``error`` / ``tool_call`` /
      ``tool_result`` 等）通过 ``get_stream_writer()`` 写入 Supervisor 图的 custom stream，
      由 ``stream_agent_events`` 消费后 yield 给前端，避免审批流挂死。
    - **full_trust 恢复**：Expert 的 ``finally`` 会清除 ``full_trust``，工具返回前恢复。
    """
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver

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

        # 获取 Supervisor 图的 stream writer（在 ToolNode 上下文中可用）。
        # Expert 的非 token 事件通过此 writer 写入 Supervisor 图的 custom stream，
        # 由 stream_agent_events(stream_mode=["custom","values"]) 消费后 yield 给前端。
        supervisor_writer = None
        try:
            from langgraph.config import get_stream_writer
            supervisor_writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — 非图上下文（如直接调用）降级
            logger.debug("delegate_to_expert: get_stream_writer unavailable")

        parts: list[str] = []

        async def _expert_yield_event(event: dict) -> None:
            """Expert 非 token 事件透传到 Supervisor SSE 流。

            token 事件从 generator yield 直接收集（见下方循环），
            此回调只负责把 approval_request / error / tool_call / tool_result
            等事件写入 Supervisor 图的 custom stream。
            """
            if event.get("event") != "token" and supervisor_writer is not None:
                supervisor_writer(event)

        from app.scenarios.coding.agent import run_coding_expert

        # Bug 3 修复：InMemorySaver 隔离 Expert 的 checkpoint，避免污染 Supervisor 上下文。
        # thread_id 保持原始值（沙箱授权 / approval / pause / abort 都基于原始 thread_id）。
        expert_checkpointer = InMemorySaver()

        # M21 修复：full_trust 恢复移到 try/finally，确保 run_coding_expert 抛异常时也能恢复。
        # C2 修复：移除 history=None（run_coding_expert 签名已无 history 参数）。
        try:
            async for event in run_coding_expert(
                task,
                thread_id,
                profile_prompt=profile_prompt,
                permission_mode=permission_mode,
                workspace_path=workspace_path,
                chat_model=chat_model,
                checkpointer=expert_checkpointer,
                yield_event=_expert_yield_event,
            ):
                # token 从 generator yield 直接收集；
                # 非 token 事件已通过 _expert_yield_event 回调透传到 Supervisor custom stream。
                if event.get("event") == "token":
                    content = event.get("data", "")
                    if content:
                        parts.append(content)
        finally:
            # Bug 2 修复：Expert 的 finally 可能清除了 full_trust，恢复 Supervisor 的状态。
            if permission_mode == "full_trust":
                sandbox = get_sandbox()
                await sandbox.set_full_trust(thread_id, True)

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
    permission_mode: str = "standard",
    excluded_tools: frozenset[str] | None = None,
    interrupt_on: dict[str, bool] | None = None,
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
        permission_mode: 权限模式，"standard" 或 "full_trust"。tools is None 时透传给
            ``make_expert_delegation_tool``，确保内部 delegate_to_expert 与 Supervisor 权限一致。
        excluded_tools: 可选，调用方排除的内置工具名集合。透传给 ``create_agent``。
        interrupt_on: 可选，``AgentToolset.interrupt_on`` 派生的中断配置。透传给 ``create_agent``。

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
        # M25 修复：透传 profile_prompt / permission_mode / chat_model，确保
        # tools is None 分支与 run_work_supervisor 显式构造 expert_tool 的行为一致。
        standard_tools = make_deep_tools(thread_id, workspace_path=workspace_path)
        expert_tool = make_expert_delegation_tool(
            thread_id=thread_id,
            workspace_path=workspace_path,
            profile_prompt=profile_prompt,
            permission_mode=permission_mode,
            chat_model=chat_model,
        )
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
        excluded_tools=excluded_tools,
        interrupt_on=interrupt_on,
    )


async def run_work_supervisor(
    message: str,
    thread_id: str,
    profile_prompt: str = "",
    permission_mode: str = "standard",
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
    checkpointer: Any = None,
) -> AsyncIterator[dict]:
    """运行 work 场景 Supervisor，yield SSE 事件。

    流程:
    1. 解析 @mention：若命中 Expert 直接运行 Expert；命中子代理运行后回注 Supervisor
    2. 构建 Supervisor agent（含标准工具 + delegate_to_expert + task 子代理 + interrupt_on 危险工具审批）
    3. 流式执行，危险工具中断 → 审批 → 恢复，循环直至完成

    历史 messages 由 LangGraph astream 从 checkpointer 自动加载（thread_id 匹配），
    不再显式传入 history 参数。

    Args:
        message: 用户消息（可能含 @mention）。
        thread_id: 会话 ID。
        profile_prompt: 用户画像前缀。
        permission_mode: 权限模式，"standard" 或 "full_trust"。
        workspace_path: 可选当前工作区绝对路径。
        chat_model: 可选注入的 ChatModel，透传到 ``build_work_supervisor`` 与 @mention 强制委派时的 ``run_coding_expert``。None 时使用真实 LLM。
        checkpointer: 可选的 LangGraph checkpointer。Router 传共享 checkpointer
            让 LangGraph 自动加载/写回历史；None 时 ``build_work_supervisor`` 内部获取全局 checkpointer。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    config: dict = {"configurable": {"thread_id": thread_id or "work-default"}}
    with bind_agent_context(thread_id, None):
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

            async for sse in run_coding_expert(
                cleaned_message,
                thread_id,
                profile_prompt=profile_prompt,
                workspace_path=workspace_path,
                permission_mode=permission_mode,
                chat_model=chat_model,
                checkpointer=checkpointer,
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
                agent_name, cleaned_message, thread_id, workspace_path, chat_model=chat_model
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

            # inputs 只含当前 user message；历史 messages 由 LangGraph astream 从
            # checkpointer 自动加载（thread_id 匹配时），避免双重写入。
            inputs = {"messages": [{"role": "user", "content": cleaned_message}]}

            try:
                agent_tools = make_deep_tools(thread_id, workspace_path=workspace_path)
                mcp_tools, mcp_untrusted_names = await load_mcp_tools()
                if mcp_tools:
                    agent_tools.extend(mcp_tools)
                    logger.info(
                        "MCP tools merged into Supervisor",
                        thread_id=thread_id,
                        count=len(mcp_tools),
                        untrusted=len(mcp_untrusted_names),
                    )
                expert_tool = make_expert_delegation_tool(
                    thread_id,
                    workspace_path,
                    profile_prompt=profile_prompt,
                    permission_mode=permission_mode,
                    chat_model=chat_model,
                )
                all_tools = [*agent_tools, expert_tool]

                # 单次装配 AgentToolset：统一 tool surface / excluded builtins / interrupt_on
                toolset = assemble_agent_toolset(
                    project_tools=all_tools,
                    mcp_untrusted_names=mcp_untrusted_names,
                    workspace_path=workspace_path,
                )

                subagents = _build_subagent_runnables(thread_id, workspace_path, chat_model=chat_model)

                agent = await build_work_supervisor(
                    thread_id,
                    tools=list(toolset.tools),
                    subagents=subagents,
                    profile_prompt=profile_prompt,
                    workspace_path=workspace_path,
                    chat_model=chat_model,
                    checkpointer=checkpointer,
                    rubric=supervisor_cfg.rubric or None,
                    grader_model=supervisor_cfg.grader_model,
                    excluded_tools=toolset.excluded_builtin_tools,
                    interrupt_on=toolset.interrupt_on,
                )
            except ValueError as exc:
                yield make_error_event(f"LLM 不可用: {exc}")
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("build_work_supervisor failed", thread_id=thread_id)
                yield make_error_event(f"Supervisor 初始化失败: {exc}")
                return

            # 审批运行时使用同一份 approval_required_tools（OpenSpec Decision 1）
            runtime_dangerous = set(toolset.approval_required_tools)
            # execute 不再属于 DANGEROUS_TOOLS；其审批通过 directory_extension 机制处理
            # （workspace 之外未授权时触发审批），由 run_agent_with_approval 统一处理。

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
            ):
                yield sse
        finally:
            if is_full_trust:
                await sandbox.set_full_trust(thread_id, False)

        # 异步触发画像提取（与 deep/coding 路径一致）
        await trigger_profile_auto_extract(agent, config, cleaned_message, workspace_path=workspace_path)


async def _run_subagent_for_mention(
    agent_name: str,
    task: str,
    thread_id: str,
    workspace_path: str | None,
    chat_model: Any = None,
) -> str:
    """运行子代理并收集最终文本结果（用于 @mention 子代理委派）。

    Bug 修复：
    - H9：用 try/except 包裹整个子代理调用，异常时返回错误信息字符串而非崩溃 Supervisor；
      ``error`` 类型事件不静默丢弃，记录 warning 并把错误信息 append 到结果。
    - M22：接受 ``chat_model`` 参数，透传给子代理 runner。
      由于 ``run_rag_agent`` / ``run_web_agent`` / ``run_custom_agent`` 当前签名不接受
      ``chat_model``，用 ``inspect.signature`` 检测：仅当 runner 显式声明 ``chat_model``
      形参或接受 ``**kwargs`` 时才透传，避免破坏现有签名。

    Args:
        agent_name: 子代理名（``rag`` / ``web`` / ``<custom_key>``）。
        task: 要委派给子代理的任务文本。
        thread_id: 会话 ID。
        workspace_path: 当前工作区路径（自定义子代理用）。
        chat_model: 可选注入的 ChatModel（eval/mock 模式）；为 None 时不透传。

    Returns:
        子代理 token 事件拼接后的文本；异常或 error 事件时返回带 ``[子代理...]`` 前缀的提示串。
    """
    import inspect
    import json

    parts: list[str] = []

    def _runner_accepts_chat_model(fn: Any) -> bool:
        """检查 runner 是否接受 ``chat_model`` 参数（显式形参或 **kwargs）。"""
        try:
            sig = inspect.signature(fn)
        except (ValueError, TypeError):
            return False
        return any(
            p.name == "chat_model" or p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )

    try:
        if agent_name == "rag":
            from app.deepagent.subagents.rag_agent import run_rag_agent

            kwargs: dict[str, Any] = {"history": None}
            if chat_model is not None and _runner_accepts_chat_model(run_rag_agent):
                kwargs["chat_model"] = chat_model
            runner = run_rag_agent(thread_id, task, **kwargs)
        elif agent_name == "web":
            from app.deepagent.subagents.web_agent import run_web_agent

            kwargs = {"history": None}
            if chat_model is not None and _runner_accepts_chat_model(run_web_agent):
                kwargs["chat_model"] = chat_model
            runner = run_web_agent(thread_id, task, **kwargs)
        else:
            # 自定义子代理
            from app.deepagent.subagents.custom_agent import run_custom_agent

            kwargs = {"history": None, "workspace_path": workspace_path}
            if chat_model is not None and _runner_accepts_chat_model(run_custom_agent):
                kwargs["chat_model"] = chat_model
            runner = run_custom_agent(agent_name, thread_id, task, **kwargs)

        async for event in runner:
            # 兼容两种事件契约：
            # - 内置/自定义子代理：{"type": "token", "content": str}
            # - Supervisor/Expert：{"event": "token", "data": str}
            ev_type = event.get("event") or event.get("type") or ""
            if ev_type == "error":
                # H9 修复：error 事件不静默丢弃，记录并 append 错误信息。
                ev_data = event.get("data")
                if ev_data is None:
                    ev_data = event.get("content", "")
                error_msg = (
                    ev_data
                    if isinstance(ev_data, str)
                    else json.dumps(ev_data, ensure_ascii=False)
                )
                logger.warning(
                    "subagent error in mention", error_msg=error_msg, agent_name=agent_name
                )
                parts.append(f"[子代理错误: {error_msg}]")
                continue
            if ev_type == "token":
                data = event.get("content")
                if data is None:
                    data = event.get("data", "")
                # data 可能是纯文本，也可能是 JSON 串（部分 runner 会包一层）
                if isinstance(data, str):
                    try:
                        parsed = json.loads(data)
                        if isinstance(parsed, dict):
                            data = parsed.get("content", data)
                    except json.JSONDecodeError:
                        pass
                parts.append(data)
        return "".join(parts).strip()
    except Exception as exc:  # noqa: BLE001 — 子代理异常不应崩溃 Supervisor
        logger.exception("subagent runner failed in mention", agent_name=agent_name)
        return f"[子代理执行失败: {exc}]"
