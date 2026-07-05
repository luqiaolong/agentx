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
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.memory.context import trim_messages_with_budget
from app.memory.profile_store import build_profile_prompt
from app.memory.skills_loader import get_skills
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.paths.deep_path import run_deep_path
from app.router.classifier import classify_message
from app.router.state import RouterState
from app.subagents import run_code_agent, run_custom_agent, run_rag_agent, run_web_agent
from app.utils.text import ThinkFilter, extract_chunk_text as _extract_chunk_text

__all__ = ["build_router_graph", "resolve_system_prompt", "run_router", "_parse_skill_tag"]


def resolve_system_prompt(
    default: str,
    scene_prompt: str | None,
    skill_extra: str | None,
) -> str:
    """合并三层 system prompt，优先级：skill_extra > scene_prompt > default。

    - ``scene_prompt`` 非空时覆盖 ``default``（场景切换器注入）。
    - ``skill_extra`` 非空时拼在最前（画像 / @skill content，遵循 spec R9 画像优先约定）。
    - ``scene_prompt`` 为空字符串视为未设置（防御 pydantic 边界）。

    Examples:
        >>> resolve_system_prompt("d", None, None)
        'd'
        >>> resolve_system_prompt("d", "scene", None)
        'scene'
        >>> resolve_system_prompt("d", "scene", "skill")
        'skill\\nscene'
    """
    base = scene_prompt if scene_prompt else default
    return f"{skill_extra}\n{base}" if skill_extra else base


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
# SSE 事件生成器
# ============================================================


def _select_subagent(message: str) -> str | None:
    """根据消息内容选择路径 B 的子代理。

    Returns:
        "code" / "rag" / "web" / 自定义子代理 key / None
        - None 表示命中的子代理被禁用或工具全禁用，退回路径 A

    匹配优先级：
    1. 内置子代理（web → rag → code，按关键词命中）
    2. 自定义子代理（按 key 字典序，关键词命中）
    3. 默认 code（若可用）
    """
    settings = get_settings()
    subagents = settings.subagents
    tools_enabled = settings.tools_enabled

    # 1. 内置子代理：按优先级 web → rag → code
    for agent_name in ("web", "rag", "code"):
        cfg = subagents[agent_name]
        if not cfg.enabled:
            continue
        # 检查关键词命中（空 keywords 不匹配任何子代理，spec R6）
        if not any(kw in message for kw in cfg.keywords):
            continue
        # 检查绑定的工具是否全部被禁用
        if not any(tools_enabled.get(t, True) for t in cfg.tools):
            logger.warning(f"subagent {agent_name} matched but all tools disabled, fallback to CHAT")
            return None
        return agent_name

    # 2. 自定义子代理：按 key 字典序遍历，关键词命中即返回
    custom = settings.custom_subagents
    for key in sorted(custom.keys()):
        cfg = custom[key]
        if not cfg.enabled:
            continue
        if not any(kw in message for kw in cfg.keywords):
            continue
        if not any(tools_enabled.get(t, True) for t in cfg.tools):
            logger.warning(
                f"custom subagent {key} matched but all tools disabled, fallback"
            )
            return None
        return key

    # 3. 无命中，默认 code（若 code 可用）
    code_cfg = subagents["code"]
    if code_cfg.enabled and any(tools_enabled.get(t, True) for t in code_cfg.tools):
        return "code"
    return None  # code 也禁用，退回路径 A


def _sse(event: str, data: Any) -> dict[str, str]:
    """构造标准 SSE 事件 dict。

    - token: data 为纯字符串
    - todo_update / approval_request: data 为 JSON 字符串
    - done: data 为 "{}"
    - error: data 为错误消息字符串
    """
    if event in ("todo_update", "approval_request"):
        if isinstance(data, str):
            return {"event": event, "data": data}
        return {"event": event, "data": json.dumps(data, ensure_ascii=False)}
    if event == "done":
        return {"event": "done", "data": "{}"}
    return {"event": event, "data": str(data)}


async def _run_chat_path(
    message: str,
    thread_id: str,
    system_prompt_extra: str | None = None,
    history: list | None = None,
) -> AsyncIterator[dict[str, str]]:
    """路径 A：LLM 直答 + 流式 token。

    Args:
        message: 用户消息（已移除 @skill 标记）。
        thread_id: 会话 ID。
        system_prompt_extra: 可选的 skill content，拼到默认 system prompt 前。
        history: 历史 messages 列表（含 SystemMessage / HumanMessage / AIMessage），
            已截断到 ``context_max_messages`` / ``context_max_tokens`` 内。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.llm import get_chat_model

    system_prompt = get_settings().default_system_prompt
    if system_prompt_extra:
        # 画像/skill 在前，default 在后（spec R9：画像优先于默认 prompt，与路径 C 一致）
        system_prompt = f"{system_prompt_extra}\n{system_prompt}"
    try:
        llm = get_chat_model(temperature=0.7, streaming=True)
    except ValueError as exc:
        yield _sse("error", f"LLM 不可用: {exc}")
        return

    # 构建完整 messages：system + history + current
    # history 已是 BaseMessage 列表（含早期 SystemMessage 会被 trim 移除，
    # 但此处保险起见再过滤一次，避免多个 system prompt）
    history_msgs = [m for m in (history or []) if not isinstance(m, SystemMessage)]
    messages = [
        SystemMessage(content=system_prompt),
        *history_msgs,
        HumanMessage(content=message),
    ]
    think_filter = ThinkFilter(max_hold=get_settings().think_filter_max_hold)
    try:
        async for chunk in llm.astream(messages):
            raw = _extract_chunk_text(chunk)
            cleaned = think_filter.feed(raw)
            if cleaned:
                yield _sse("token", cleaned)
        tail = think_filter.flush()
        if tail:
            yield _sse("token", tail)
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.warning("chat path LLM stream failed", error=str(exc))
        yield _sse("error", f"LLM 流式失败: {exc}")
        return


async def _run_tool_path(
    message: str,
    thread_id: str,
    profile_prompt: str | None = None,
    history: list | None = None,
) -> AsyncIterator[dict[str, str]]:
    """路径 B：选择子代理并透传事件流。

    Args:
        message: 用户消息（已移除 @skill 标记）。
        thread_id: 会话 ID。
        profile_prompt: 用户画像，回退路径 A 时注入 system prompt。
        history: 历史 messages 列表（已截断），传给子代理拼到 inputs 前。

    Note:
        子代理 token 事件经 ``ThinkFilter`` 过滤 ``<think>...</think>`` 块后再 yield，
        与路径 A 行为对齐（claude.md §5 SSE 契约：token 必须已剥离 think 块）。
    """
    agent_type = _select_subagent(message)
    if agent_type is None:
        # 子代理禁用或工具全禁用，退回路径 A（注入画像）
        logger.info("router.tool_path fallback to CHAT", thread_id=thread_id)
        async for sse in _run_chat_path(
            message, thread_id, system_prompt_extra=profile_prompt, history=history
        ):
            yield sse
        return

    logger.info("router.tool_path", agent=agent_type, thread_id=thread_id)
    if agent_type == "web":
        runner = run_web_agent
    elif agent_type == "rag":
        runner = run_rag_agent
    elif agent_type == "code":
        runner = run_code_agent
    else:
        # 自定义子代理：runner 需要 key 参数，单独处理
        think_filter = ThinkFilter(max_hold=get_settings().think_filter_max_hold)
        try:
            async for event in run_custom_agent(agent_type, thread_id, message, history=history):
                sse = _convert_subagent_event(event, think_filter)
                if sse:
                    yield sse
        except Exception as exc:  # noqa: BLE001 — SSE 兜底
            logger.warning("custom subagent failed", key=agent_type, error=str(exc))
            yield _sse("error", f"自定义子代理执行失败: {exc}")
        finally:
            tail = think_filter.flush()
            if tail:
                yield _sse("token", tail)
        return

    think_filter = ThinkFilter(max_hold=get_settings().think_filter_max_hold)
    try:
        async for event in runner(thread_id, message, history=history):
            sse = _convert_subagent_event(event, think_filter)
            if sse:
                yield sse
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.warning("tool path subagent failed", error=str(exc))
        yield _sse("error", f"子代理执行失败: {exc}")
        return
    finally:
        tail = think_filter.flush()
        if tail:
            yield _sse("token", tail)


async def _run_deep_path(
    message: str,
    thread_id: str,
    state: RouterState,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
) -> AsyncIterator[dict[str, str]]:
    """路径 C：DeepAgent + 危险工具中断审批。

    Args:
        message: 用户消息（已移除 @skill 标记）。
        thread_id: 会话 ID。
        state: Router 状态。
        profile_prompt: 用户画像前缀，由 ``run_router`` 注入到 DeepAgent system prompt。
        history: 历史 messages 列表（已截断），传给 DeepAgent 拼到 inputs 前。
        permission_mode: 权限模式，"standard" 或 "full_trust"。
    """
    try:
        async for event in run_deep_path(
            state, message, profile_prompt=profile_prompt, history=history,
            permission_mode=permission_mode,
        ):
            yield event
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.warning("deep path failed", error=str(exc))
        yield _sse("error", f"DeepAgent 执行失败: {exc}")
        return


def _convert_subagent_event(
    event: dict[str, Any], think_filter: ThinkFilter | None = None
) -> dict[str, str] | None:
    """将子代理标准化事件转为 SSE 格式。

    子代理事件: {type: "token"/"tool_call"/"tool_result", ...}
    SSE 事件: {event: "token"/"todo_update", data: ...}

    - token → token（经 ThinkFilter 过滤 ``<think>`` 块后再输出，对齐 claude.md §5 契约）
    - tool_call → todo_update（工具调用进度）
    - tool_result → todo_update（工具调用完成）

    Args:
        event: 子代理标准化事件 dict。
        think_filter: 可选的流式 think 过滤器。若提供则 token 内容经 ``feed`` 过滤；
            若不提供（向后兼容）则直接透传（仅供单元测试 mock 使用）。
    """
    etype = event.get("type", "")
    if etype == "token":
        content = event.get("content", "")
        if not content:
            return None
        if think_filter is not None:
            cleaned = think_filter.feed(content)
            if not cleaned:
                return None
            return _sse("token", cleaned)
        return _sse("token", content)
    if etype == "tool_call":
        name = event.get("name", "")
        args = event.get("args", {})
        return _sse(
            "todo_update",
            {
                "todos": [
                    {"text": f"调用工具: {name}", "done": False, "args": args},
                ]
            },
        )
    if etype == "tool_result":
        name = event.get("name", "")
        return _sse(
            "todo_update",
            {
                "todos": [
                    {"text": f"工具 {name} 完成", "done": True},
                ]
            },
        )
    return None


# ============================================================
# @skill 标记解析
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


async def run_router(
    message: str,
    thread_id: str,
    checkpointer: Any = None,
    permission_mode: str = "standard",
) -> AsyncIterator[dict[str, str]]:
    """运行 Router，yield SSE 事件。

    流程:
    1. 解析 ``@skill:<name>`` 标记，提取 skill content（注入路径 A system prompt）
    2. 从 checkpointer 加载 ``thread_id`` 的历史 ``messages``（若提供 checkpointer）
    3. 调 classify_message 获取分类
    4. 按分类驱动对应路径的流式生成器，传入截断后的历史
    5. 将路径事件转为 SSE 格式（token / todo_update / approval_request / done / error）

    Args:
        message: 用户消息。
        thread_id: 会话 ID。
        checkpointer: 可选的 LangGraph checkpointer，用于加载历史 messages。
        permission_mode: 权限模式，"standard"（审批流）或 "full_trust"（会话内全量放行）。
            仅影响路径 C（DeepAgent）的危险工具审批与目录越界扩展授权。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    with trace_span("router.run", thread_id=thread_id, message_len=len(message)):
        # 解析 @skill 标记（在 classify 之前）
        cleaned_message, skill_content = _parse_skill_tag(message)

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

        if classification == "CHAT":
            # 路径 A：画像 + skill content 拼到 system prompt
            system_prompt_extra = ""
            if profile_prompt:
                system_prompt_extra += profile_prompt
            if skill_content:
                system_prompt_extra += skill_content
            async for sse in _run_chat_path(
                cleaned_message,
                thread_id,
                system_prompt_extra=system_prompt_extra,
                history=history,
            ):
                yield sse
        elif classification == "SINGLE_TOOL":
            async for sse in _run_tool_path(
                cleaned_message,
                thread_id,
                profile_prompt=profile_prompt,
                history=history,
            ):
                yield sse
        else:  # DEEP_TASK
            # 路径 C：画像传给 _run_deep_path，由 deep_path 注入到 agent system prompt
            async for sse in _run_deep_path(
                cleaned_message,
                thread_id,
                state,
                profile_prompt=profile_prompt,
                history=history,
                permission_mode=permission_mode,
            ):
                yield sse

        yield _sse("done", "{}")


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
