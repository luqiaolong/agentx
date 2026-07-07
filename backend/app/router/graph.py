"""LangGraph Router 入口：``run_router`` 编排分类 + 三路径分发。

历史背景：本模块早期曾定义一个 LangGraph StateGraph（``classify_node`` /
``chat_node`` / ``tool_node`` / ``deep_node`` / ``build_router_graph``），但路由流
实际由 ``run_router`` SSE 生成器驱动（先调 ``classify_message``，再 dispatch 到
``run_chat_path`` / ``run_tool_path`` / ``run_deep_path`` / ``run_team_path``），
从未被生产代码使用。原图节点 + ``build_router_graph`` 已删除，保留本模块的
SSE 编排、``@skill`` 标记解析与 checkpointer 历史加载。

路径实现已迁移到按能力域模块化的独立包：
- 路径 A → ``app.chat.run``
- 路径 B → ``app.subagents.dispatch``
- 路径 C → ``app.deep.agent``
- 路径 D → ``app.team.orchestrator``
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterator

from app.chat.run import run_chat_path
from app.config import get_settings
from app.deep.agent import run_deep_path
from app.memory.context import trim_messages_with_budget
from app.memory.profile_store import build_profile_prompt
from app.memory.skills_loader import get_skills
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.router.classifier import classify_message
from app.subagents.dispatch import run_tool_path
from app.team.orchestrator import run_team_path
from app.utils.sse_events import make_sse_event

__all__ = ["run_router", "_parse_skill_tag"]


# ============================================================
# @skill / <workspace> 标记解析
# ============================================================

# 单 skill content 注入上限（超出截断）
_SKILL_CONTENT_MAX = 4000

# @skill:<name> 标记正则
_SKILL_TAG_RE = re.compile(r"@skill:(\S+)")


def _parse_skill_tag(message: str) -> tuple[str, str | None]:
    """解析用户消息中的所有 ``@skill:<name>`` 标记。

    - 移除所有 @skill: 标记（无论技能是否存在），避免 LLM 看到未知标记困惑。
    - 首个存在的技能 → 注入其 content；其余标记仅移除。
    - 无标记或所有技能都不存在 → ``(cleaned_message, None)``。
    - 单 skill content 超过 ``_SKILL_CONTENT_MAX`` 字符时截断并追加标记。

    Examples:
        >>> _parse_skill_tag("@skill:coder 帮我写代码")
        ("帮我写代码", "<coder skill content>")
        >>> _parse_skill_tag("@skill:unknown 帮我")  # 技能不存在
        ("帮我", None)
        >>> _parse_skill_tag("@skill:a @skill:b 任务")  # 多标签，a 存在
        ("任务", "<a skill content>")
    """
    matches = list(_SKILL_TAG_RE.finditer(message))
    if not matches:
        return (message, None)

    skills = get_skills()
    skill_content: str | None = None
    for match in matches:
        skill_name = match.group(1)
        skill = next((s for s in skills if s.name == skill_name), None)
        if skill is not None:
            content = skill.content
            if len(content) > _SKILL_CONTENT_MAX:
                content = content[:_SKILL_CONTENT_MAX] + "\n[skill content truncated]"
            skill_content = content
            break  # 仅注入首个存在的技能

    # 移除所有 @skill:<name> 标记（含不存在的），剩余文本作为用户消息
    cleaned = _SKILL_TAG_RE.sub("", message).strip()
    # 合并多余空白（移除标记后可能留下连续空格）
    cleaned = " ".join(cleaned.split())
    return (cleaned, skill_content)


# ============================================================
# Router 主入口
# ============================================================


async def run_router(
    message: str,
    thread_id: str,
    checkpointer: Any = None,
    permission_mode: str = "standard",
    scene_prompt: str | None = None,
    agent_mode: str = "agent",
    workspace_path: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    """运行 Router，yield SSE 事件。

    流程:
    1. 解析 ``@skill:<name>`` 标记，提取 skill content（注入路径 A system prompt）
    2. 从 checkpointer 加载 ``thread_id`` 的历史 ``messages``（若提供 checkpointer）
    3. 若 ``agent_mode == "agent_team"``，直接进入 AgentTeam 路径（路径 D）
    4. 否则调 classify_message 获取分类，按分类驱动对应路径的流式生成器
    5. 将路径事件转为 SSE 格式（token / todo_update / approval_request / done / error）

    Args:
        message: 用户消息。
        thread_id: 会话 ID。
        checkpointer: 可选的 LangGraph checkpointer，用于加载历史 messages。
        permission_mode: 权限模式，"standard"（审批流）或 "full_trust"（会话内全量放行）。
            仅影响路径 C（DeepAgent）的危险工具审批与目录越界扩展授权。
        scene_prompt: 可选场景 prompt（前端场景切换器注入），非空时覆盖
            ``default_system_prompt``（路径 A/B）或 ``_DEEP_SYSTEM_PROMPT``（路径 C）。
        agent_mode: 代理模式，"agent"（单代理，默认）或 "agent_team"（多代理协作）。
        workspace_path: 可选当前会话绑定的 workspace 绝对路径，非空时自动授权沙箱写入。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    with trace_span("router.run", thread_id=thread_id, message_len=len(message)):
        # 解析 @skill 标记（在 classify 之前）
        cleaned_message, skill_content = _parse_skill_tag(message)

        # 从请求字段读取 workspace_path 并同步后端授权（不再解析消息正文 <workspace> 标签）
        if workspace_path:
            from app.utils.security import get_sandbox

            sandbox = get_sandbox()
            try:
                sandbox.authorize(thread_id, workspace_path, writable=True, source="chip")
                logger.info(
                    "router.workspace_authorized",
                    thread_id=thread_id,
                    workspace=workspace_path,
                )
            except ValueError as exc:
                logger.warning(
                    "router.workspace_authorize_failed",
                    thread_id=thread_id,
                    workspace=workspace_path,
                    error=str(exc),
                )

        # T9：读取用户画像，拼到 system prompt 前（路径 A 与路径 C 都注入）
        # build_profile_prompt 失败时返回空字符串，不影响主流程
        try:
            profile_prompt = build_profile_prompt()
        except Exception as exc:  # noqa: BLE001 — 画像读取兜底
            logger.warning("build_profile_prompt failed", error=str(exc))
            profile_prompt = ""

        # 加载历史 messages（从 checkpointer）
        history: list = []
        if checkpointer is not None:
            try:
                history = await _load_history_from_checkpointer(checkpointer, thread_id)
            except Exception as exc:  # noqa: BLE001 — 历史加载兜底
                logger.warning("load history failed", error=str(exc))
                history = []

        # 截断历史到预算内（不含当前消息，当前消息在路径内部 append）
        settings = get_settings()
        history = trim_messages_with_budget(
            history,
            max_messages=settings.context_max_messages,
            max_tokens=settings.context_max_tokens,
        )

        # C3：重试路径一致性——若消息为"重试"且历史最后一条是路径 C/D，保持相同路径
        # 避免用户点击"重试"时从 DeepAgent 路径切换到子代理路径导致上下文断裂
        classification = await _classify_with_retry_consistency(
            cleaned_message, thread_id, history, checkpointer
        )

        logger.info(
            "router dispatch",
            thread_id=thread_id,
            classification=classification,
            message_len=len(cleaned_message),
            history_count=len(history),
        )

        state: RouterState = {
            "thread_id": thread_id,
            "messages": [{"role": "user", "content": cleaned_message}],
            "classification": classification,
        }

        # 收集本次对话的 user + assistant 消息并写回 checkpointer
        assistant_content_parts: list[str] = []

        async def _collect_path_sse(path_generator: AsyncIterator[dict[str, str]]) -> AsyncIterator[dict[str, str]]:
            async for sse in path_generator:
                if sse.get("event") == "token":
                    assistant_content_parts.append(str(sse.get("data", "")))
                yield sse

        if agent_mode == "agent_team":
            # 路径 D：多代理协作（Orchestrator + 并行子代理 + Blackboard + Aggregator）
            async for sse in _collect_path_sse(
                run_team_path(
                    cleaned_message,
                    thread_id,
                    state,
                    profile_prompt=profile_prompt,
                    history=history,
                    permission_mode=permission_mode,
                    scene_prompt=scene_prompt,
                    workspace_path=workspace_path,
                )
            ):
                yield sse
        elif classification == "CHAT":
            # 路径 A：画像 + skill content 拼到 system prompt
            system_prompt_extra = ""
            if profile_prompt:
                system_prompt_extra += profile_prompt
            if skill_content:
                system_prompt_extra += skill_content
            async for sse in _collect_path_sse(
                run_chat_path(
                    cleaned_message,
                    thread_id,
                    system_prompt_extra=system_prompt_extra,
                    history=history,
                    scene_prompt=scene_prompt,
                )
            ):
                yield sse
        elif classification == "SINGLE_TOOL":
            async for sse in _collect_path_sse(
                run_tool_path(
                    cleaned_message,
                    thread_id,
                    profile_prompt=profile_prompt,
                    history=history,
                    scene_prompt=scene_prompt,
                    workspace_path=workspace_path,
                    checkpointer=checkpointer,
                )
            ):
                yield sse
        else:  # DEEP_TASK
            # 路径 C：画像传给 run_deep_path，由 deep_path 注入到 agent system prompt
            # DeepAgent 自己通过 checkpointer 管理历史，run_router 不重复写入
            async for sse in run_deep_path(
                state,
                cleaned_message,
                profile_prompt=profile_prompt,
                history=history,
                permission_mode=permission_mode,
                scene_prompt=scene_prompt,
                workspace_path=workspace_path,
            ):
                yield sse

        assistant_content = "".join(assistant_content_parts).strip()
        if assistant_content and checkpointer is not None:
            from langchain_core.messages import AIMessage, HumanMessage

            new_messages = [
                HumanMessage(content=cleaned_message),
                AIMessage(content=assistant_content),
            ]
            await _append_messages_to_checkpointer(checkpointer, thread_id, new_messages)

        yield make_sse_event("done", "{}")


async def _classify_with_retry_consistency(
    message: str,
    thread_id: str,
    history: list,
    checkpointer: Any,
) -> str:
    """分类消息，重试时保持与上次相同路径（方案 C）。

    若消息为"重试"类意图（如"重试""再试一次""重新执行"），且历史最后一条消息
    来自路径 C（DeepAgent）或路径 D（AgentTeam），则强制使用相同路径分类，
    避免用户点击"重试"时从 DeepAgent 路径切换到子代理路径导致上下文断裂。

    Args:
        message: 清理后的用户消息。
        thread_id: 会话 ID。
        history: 历史 messages 列表。
        checkpointer: LangGraph checkpointer。

    Returns:
        分类标签："CHAT" / "SINGLE_TOOL" / "DEEP_TASK"。
    """
    # 1. 先正常分类
    try:
        classification = await classify_message(message)
    except Exception as exc:  # noqa: BLE001 — 分类器兜底
        logger.warning("classify_message failed, fallback to CHAT", error=str(exc))
        classification = "CHAT"

    # 2. 重试一致性检测：消息是否为重试意图
    retry_keywords = {"重试", "再试", "重新执行", "retry", "again", "重新来"}
    is_retry = any(kw in message for kw in retry_keywords)
    if not is_retry:
        return classification

    # 3. 从历史推断上次路径：检查 checkpoint 中的消息来源
    # 若历史最后一条 assistant 消息来自 deep_agent / team，则上次是路径 C/D
    last_path = _infer_last_path_from_history(history)
    if last_path == "DEEP_TASK" and classification != "DEEP_TASK":
        logger.info(
            "retry consistency: force DEEP_TASK",
            thread_id=thread_id,
            original_classification=classification,
        )
        return "DEEP_TASK"
    if last_path == "AGENT_TEAM" and classification != "DEEP_TASK":
        # AgentTeam 模式由 agent_mode 控制，此处仅标记
        logger.info(
            "retry consistency: keep AGENT_TEAM",
            thread_id=thread_id,
            original_classification=classification,
        )
        return classification  # agent_mode 在 run_router 外层已处理

    return classification


def _infer_last_path_from_history(history: list) -> str | None:
    """从历史消息推断上次使用的路径。

    策略：检查历史最后几条 assistant 消息的 name 字段：
    - "deep_agent" → 路径 C (DEEP_TASK)
    - "team_" 前缀 / "orchestrator" → 路径 D (AGENT_TEAM)
    - 无 name 或普通 assistant → 路径 A (CHAT)
    - 子代理无 name 标记，依赖 classify_message 正常判断

    Args:
        history: 历史 messages 列表。

    Returns:
        "CHAT" / "SINGLE_TOOL" / "DEEP_TASK" / "AGENT_TEAM" / None。
    """
    if not history:
        return None

    # 从后向前找 assistant 消息
    for msg in reversed(history):
        msg_type = ""
        if isinstance(msg, dict):
            msg_type = msg.get("type", "")
            name = msg.get("name", "")
        else:
            msg_type = getattr(msg, "type", "")
            name = getattr(msg, "name", "") or ""

        if msg_type != "ai":
            continue

        if name == "deep_agent":
            return "DEEP_TASK"
        if name.startswith("team_") or name == "orchestrator":
            return "AGENT_TEAM"
        # 普通 assistant 消息（路径 A 或路径 B）
        if name in ("", "code_agent", "rag_agent", "web_agent"):
            # 子代理没有 name 标记，返回 None 让正常分类生效
            return None
        return None

    return None


async def _load_history_from_checkpointer(
    checkpointer: Any, thread_id: str
) -> list:
    """从 checkpointer 加载 ``thread_id`` 的历史 messages。

    Args:
        checkpointer: LangGraph checkpointer（同步或异步）。
        thread_id: 会话 ID。

    Returns:
        历史 ``BaseMessage`` 列表（不含当前消息）。若 checkpoint 不存在或无 messages，
        返回空列表。
    """
    config = {"configurable": {"thread_id": thread_id}}
    # 优先用 aget（异步 checkpointer）
    if hasattr(checkpointer, "aget"):
        checkpoint = await checkpointer.aget(config)
    elif hasattr(checkpointer, "get"):
        checkpoint = checkpointer.get(config)
    else:
        return []

    if not checkpoint:
        return []

    # LangGraph checkpoint 结构：{"channel_values": {"messages": [...]}, ...}
    # 不同版本字段名可能不同，防御性读取
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    messages = channel_values.get("messages", [])
    return list(messages) if messages else []


async def _append_messages_to_checkpointer(
    checkpointer: Any, thread_id: str, new_messages: list
) -> None:
    """将 ``new_messages`` 追加到 ``thread_id`` 的 checkpointer messages channel。

    实现策略：通过 LangGraph 编译一个最小 ``StateGraph``（含 messages channel），
    调用 ``graph.ainvoke`` 让 LangGraph 内部 schema 机制负责 channel_versions /
    checkpoint_id / checkpoint_ns 等字段的正确序列化。直接手工 ``aput`` 会因为
    缺少 ``checkpoint_ns`` / ``id`` 字段触发 ``InternalError: 'checkpoint_ns'``。

    兼容同步与异步 checkpointer（优先异步接口）。若 thread_id 尚无 checkpoint，
    图会自动初始化首个 checkpoint。
    """
    # 兼容同步 checkpointer（如 SqliteSaver）—— 包一层 async 适配
    async def _append_async() -> None:
        from langchain_core.runnables import RunnableConfig
        from langgraph.graph import END, START, MessagesState, StateGraph

        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        # 读取已有 messages，与 new_messages 合并后重新调用 ainvoke
        if hasattr(checkpointer, "aget"):
            existing = await checkpointer.aget(config)
        else:
            existing = checkpointer.get(config)

        existing_msgs: list = []
        if existing and isinstance(existing, dict):
            channel_values = existing.get("channel_values", {}) or {}
            existing_msgs = list(channel_values.get("messages", []) or [])
        combined_msgs = [*existing_msgs, *new_messages]

        # 构建单节点最小图：passthrough 节点把 input.messages 直接 emit 到 output.messages
        async def _passthrough(state: MessagesState) -> dict:
            return {"messages": []}

        graph = StateGraph(MessagesState)
        graph.add_node("passthrough", _passthrough)
        graph.add_edge(START, "passthrough")
        graph.add_edge("passthrough", END)
        compiled = graph.compile(checkpointer=checkpointer)
        await compiled.ainvoke({"messages": combined_msgs}, config=config)

    async def _append_sync() -> None:
        # 同步 checkpointer 走线程池，避免阻塞事件循环
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        def _do() -> None:
            from langchain_core.runnables import RunnableConfig
            from langgraph.graph import END, START, MessagesState, StateGraph

            config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
            existing = checkpointer.get(config)
            existing_msgs: list = []
            if existing and isinstance(existing, dict):
                channel_values = existing.get("channel_values", {}) or {}
                existing_msgs = list(channel_values.get("messages", []) or [])
            combined_msgs = [*existing_msgs, *new_messages]

            async def _passthrough(state: MessagesState) -> dict:
                return {"messages": []}

            graph = StateGraph(MessagesState)
            graph.add_node("passthrough", _passthrough)
            graph.add_edge(START, "passthrough")
            graph.add_edge("passthrough", END)
            compiled = graph.compile(checkpointer=checkpointer)
            import asyncio as _aio
            try:
                _loop = _aio.get_event_loop()
            except RuntimeError:
                _loop = None
            if _loop and _loop.is_running():
                # 不会走到这里（同步路径），但保留防御
                raise RuntimeError("cannot run async checkpointer from sync context with active loop")
            _aio.run(compiled.ainvoke({"messages": combined_msgs}, config=config))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(ThreadPoolExecutor(max_workers=1), _do)

    if hasattr(checkpointer, "aget"):
        await _append_async()
    elif hasattr(checkpointer, "get"):
        await _append_sync()
    else:
        raise TypeError(f"unsupported checkpointer: {type(checkpointer).__name__}")
