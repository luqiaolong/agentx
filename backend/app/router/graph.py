"""LangGraph Router 主图：分类 → 三路径分发。

节点:
- classify_node: 调 classify_message，写入 state["classification"]
- route_conditional: 根据 classification 路由到 chat_node / tool_node / deep_node
- chat_node: 路径 A，LLM 直答 + 流式 token
- tool_node: 路径 B，根据消息内容选择 subagent（code/rag/web）
- deep_node: 路径 C，调 DeepAgent

图结构: classify_node → [conditional] → {CHAT: chat_node, SINGLE_TOOL: tool_node, DEEP_TASK: deep_node} → END

SSE 事件由 ``run_router`` 驱动：先分类，再按分类调用对应路径的流式生成器，
将路径事件转为前端契约格式（token / todo_update / approval_request / done / error）。
图本身用于结构化编排与状态持久化（checkpointer），token 级流式由路径生成器直接产出。

注：路径实现已迁移到按能力域模块化的独立包：
- 路径 A → ``app.chat.run``
- 路径 B → ``app.subagents.dispatch``
- 路径 C → ``app.deep.agent``
- 路径 D → ``app.team.orchestrator``
本模块仅保留图结构、``run_router`` 编排、``@skill`` 标记解析与 checkpointer 历史加载。
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterator

from langgraph.graph import END, StateGraph

from app.chat.run import run_chat_path
from app.config import get_settings
from app.deep.agent import run_deep_path
from app.memory.context import trim_messages_with_budget
from app.memory.profile_store import build_profile_prompt
from app.memory.skills_loader import get_skills
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.router.classifier import classify_message
from app.router.state import RouterState
from app.subagents.dispatch import run_tool_path
from app.team.orchestrator import run_team_path
from app.utils.sse_events import make_sse_event

__all__ = ["build_router_graph", "run_router", "_parse_skill_tag"]


# ============================================================
# 图节点函数
# ============================================================


async def classify_node(state: RouterState) -> RouterState:
    """分类节点：调 classify_message，将标签写入 state。

    规则前置过滤 + LLM 分类均在 classify_message 内完成。
    """
    messages = state.get("messages", [])
    message_text = ""
    if messages:
        last = messages[-1]
        if isinstance(last, dict):
            message_text = str(last.get("content", ""))
        else:
            message_text = str(getattr(last, "content", ""))

    label = await classify_message(message_text)
    logger.info("router.classify_node", label=label, message_len=len(message_text))
    return {"classification": label}


async def chat_node(state: RouterState) -> RouterState:
    """路径 A 标记节点：标记进入闲聊路径，实际流式由 run_router 驱动。"""
    return {"classification": state.get("classification", "CHAT")}


async def tool_node(state: RouterState) -> RouterState:
    """路径 B 标记节点：标记进入单工具路径，实际流式由 run_router 驱动。"""
    return {"classification": state.get("classification", "SINGLE_TOOL")}


async def deep_node(state: RouterState) -> RouterState:
    """路径 C 标记节点：标记进入 DeepAgent 路径，实际流式由 run_router 驱动。"""
    return {"classification": state.get("classification", "DEEP_TASK")}


def route_conditional(state: RouterState) -> str:
    """条件路由：根据 classification 返回目标节点名。"""
    classification = state.get("classification", "CHAT")
    return {
        "CHAT": "chat",
        "SINGLE_TOOL": "tool",
        "DEEP_TASK": "deep",
    }.get(classification, "chat")


def build_router_graph(checkpointer: Any = None) -> Any:
    """构建 Router 主图，返回 CompiledStateGraph。

    Args:
        checkpointer: 可选的 LangGraph checkpointer（用于状态持久化）。

    Returns:
        编译后的 StateGraph 实例。
    """
    graph = StateGraph(RouterState)
    graph.add_node("classify", classify_node)
    graph.add_node("chat", chat_node)
    graph.add_node("tool", tool_node)
    graph.add_node("deep", deep_node)

    graph.set_entry_point("classify")
    graph.add_conditional_edges(
        "classify",
        route_conditional,
        {"chat": "chat", "tool": "tool", "deep": "deep"},
    )
    graph.add_edge("chat", END)
    graph.add_edge("tool", END)
    graph.add_edge("deep", END)

    compile_kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    return graph.compile(**compile_kwargs)


# ============================================================
# @skill / <workspace> 标记解析
# ============================================================

# 单 skill content 注入上限（超出截断）
_SKILL_CONTENT_MAX = 4000

# @skill:<name> 标记正则
_SKILL_TAG_RE = re.compile(r"@skill:(\S+)")

# <workspace>path</workspace> 标记正则（前端 ChatComposer 附加当前工作区）
_WORKSPACE_TAG_RE = re.compile(r"<workspace>(.*?)</workspace>\s?", re.S)


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


def _parse_workspace_tag(message: str) -> tuple[str, str | None]:
    """解析前端附加的 ``<workspace>path</workspace>`` 工作区标记。

    - 提取首个 ``<workspace>`` 标签内的绝对路径。
    - 从用户消息中移除该标签，避免污染 LLM 看到的实际内容。
    - 无标签或标签为空时返回 ``(message, None)``。

    Examples:
        >>> _parse_workspace_tag("<workspace>/tmp/foo</workspace> 帮我看看")
        ("帮我看看", "/tmp/foo")
        >>> _parse_workspace_tag("hello")
        ("hello", None)
    """
    match = _WORKSPACE_TAG_RE.search(message)
    if not match:
        return (message, None)
    workspace_path = match.group(1).strip()
    cleaned = _WORKSPACE_TAG_RE.sub("", message, count=1).strip()
    cleaned = " ".join(cleaned.split())
    if not workspace_path:
        return (cleaned, None)
    return (cleaned, workspace_path)


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

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    with trace_span("router.run", thread_id=thread_id, message_len=len(message)):
        # 解析 @skill 标记（在 classify 之前）
        cleaned_message, skill_content = _parse_skill_tag(message)

        # 解析前端附加的 <workspace> 工作区标记：提取路径、移除标签、同步后端授权
        cleaned_message, workspace_path = _parse_workspace_tag(cleaned_message)
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

        try:
            classification = await classify_message(cleaned_message)
        except Exception as exc:  # noqa: BLE001 — 分类器兜底
            logger.warning("classify_message failed, fallback to CHAT", error=str(exc))
            classification = "CHAT"

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

        if agent_mode == "agent_team":
            # 路径 D：多代理协作（Orchestrator + 并行子代理 + Blackboard + Aggregator）
            async for sse in run_team_path(
                cleaned_message,
                thread_id,
                state,
                profile_prompt=profile_prompt,
                history=history,
                permission_mode=permission_mode,
                scene_prompt=scene_prompt,
            ):
                yield sse
        elif classification == "CHAT":
            # 路径 A：画像 + skill content 拼到 system prompt
            system_prompt_extra = ""
            if profile_prompt:
                system_prompt_extra += profile_prompt
            if skill_content:
                system_prompt_extra += skill_content
            async for sse in run_chat_path(
                cleaned_message,
                thread_id,
                system_prompt_extra=system_prompt_extra,
                history=history,
                scene_prompt=scene_prompt,
            ):
                yield sse
        elif classification == "SINGLE_TOOL":
            async for sse in run_tool_path(
                cleaned_message,
                thread_id,
                profile_prompt=profile_prompt,
                history=history,
                scene_prompt=scene_prompt,
            ):
                yield sse
        else:  # DEEP_TASK
            # 路径 C：画像传给 run_deep_path，由 deep_path 注入到 agent system prompt
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

        yield make_sse_event("done", "{}")


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
