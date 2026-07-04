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
from app.memory.skills_loader import get_skills
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.paths.deep_path import run_deep_path
from app.router.classifier import classify_message
from app.router.state import RouterState
from app.subagents import run_code_agent, run_rag_agent, run_web_agent
from app.utils.text import ThinkFilter, extract_chunk_text as _extract_chunk_text

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
# SSE 事件生成器
# ============================================================


# 路径 B 子代理选择关键词
_WEB_KEYWORDS: tuple[str, ...] = (
    "搜索", "网页", "联网", "查一下", "search", "web", "google", "百度",
)
_RAG_KEYWORDS: tuple[str, ...] = (
    "知识库", "文档库", "检索", "向量", "rag", "知识", "文档",
)


def _select_subagent(message: str) -> str:
    """根据消息内容选择路径 B 的子代理。

    Returns:
        "code" / "rag" / "web"
    """
    for kw in _WEB_KEYWORDS:
        if kw in message:
            return "web"
    for kw in _RAG_KEYWORDS:
        if kw in message:
            return "rag"
    return "code"


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
    message: str, thread_id: str, system_prompt_extra: str | None = None
) -> AsyncIterator[dict[str, str]]:
    """路径 A：LLM 直答 + 流式 token。

    Args:
        message: 用户消息（已移除 @skill 标记）。
        thread_id: 会话 ID。
        system_prompt_extra: 可选的 skill content，拼到默认 system prompt 前。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.llm import get_chat_model

    system_prompt = get_settings().default_system_prompt
    if system_prompt_extra:
        # default 在前，skill 定制在后作为覆盖（LLM 更遵从靠后指令）
        system_prompt = f"{system_prompt}\n{system_prompt_extra}"
    try:
        llm = get_chat_model(temperature=0.7, streaming=True)
    except ValueError as exc:
        yield _sse("error", f"LLM 不可用: {exc}")
        return

    messages = [
        SystemMessage(content=system_prompt),
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
    message: str, thread_id: str
) -> AsyncIterator[dict[str, str]]:
    """路径 B：选择子代理并透传事件流。"""
    agent_type = _select_subagent(message)
    logger.info("router.tool_path", agent=agent_type, thread_id=thread_id)

    if agent_type == "web":
        runner = run_web_agent
    elif agent_type == "rag":
        runner = run_rag_agent
    else:
        runner = run_code_agent

    try:
        async for event in runner(thread_id, message):
            sse = _convert_subagent_event(event)
            if sse:
                yield sse
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.warning("tool path subagent failed", error=str(exc))
        yield _sse("error", f"子代理执行失败: {exc}")
        return


async def _run_deep_path(
    message: str, thread_id: str, state: RouterState
) -> AsyncIterator[dict[str, str]]:
    """路径 C：DeepAgent + 危险工具中断审批。"""
    try:
        async for event in run_deep_path(state, message):
            yield event
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.warning("deep path failed", error=str(exc))
        yield _sse("error", f"DeepAgent 执行失败: {exc}")
        return


def _convert_subagent_event(event: dict[str, Any]) -> dict[str, str] | None:
    """将子代理标准化事件转为 SSE 格式。

    子代理事件: {type: "token"/"tool_call"/"tool_result", ...}
    SSE 事件: {event: "token"/"todo_update", data: ...}

    - token → token（透传 content）
    - tool_call → todo_update（工具调用进度）
    - tool_result → todo_update（工具调用完成）
    """
    etype = event.get("type", "")
    if etype == "token":
        content = event.get("content", "")
        if content:
            return _sse("token", content)
        return None
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
    """解析用户消息中的 ``@skill:<name>`` 标记。

    - 命中且技能存在：从 message 中移除标记，返回 ``(cleaned_message, skill_content)``。
    - 未命中或技能不存在：返回 ``(原消息, None)``，保持原样。
    - 单 skill content 超过 ``_SKILL_CONTENT_MAX`` 字符时截断并追加标记。
    """
    match = _SKILL_TAG_RE.search(message)
    if not match:
        return (message, None)

    skill_name = match.group(1)
    skills = get_skills()
    skill = next((s for s in skills if s.name == skill_name), None)
    if skill is None:
        return (message, None)

    # 移除首个 @skill:<name> 标记，剩余文本作为用户消息
    cleaned = message.replace(match.group(0), "", 1).strip()

    content = skill.content
    if len(content) > _SKILL_CONTENT_MAX:
        content = content[:_SKILL_CONTENT_MAX] + "\n[skill content truncated]"

    return (cleaned, content)


async def run_router(
    message: str, thread_id: str
) -> AsyncIterator[dict[str, str]]:
    """运行 Router，yield SSE 事件。

    流程:
    1. 解析 ``@skill:<name>`` 标记，提取 skill content（注入路径 A system prompt）
    2. 调 classify_message 获取分类
    3. 按分类驱动对应路径的流式生成器
    4. 将路径事件转为 SSE 格式（token / todo_update / approval_request / done / error）

    Args:
        message: 用户消息。
        thread_id: 会话 ID。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    with trace_span("router.run", thread_id=thread_id, message_len=len(message)):
        # 解析 @skill 标记（在 classify 之前）
        cleaned_message, skill_content = _parse_skill_tag(message)

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
        )

        state: RouterState = {
            "thread_id": thread_id,
            "messages": [{"role": "user", "content": cleaned_message}],
            "classification": classification,
        }

        if classification == "CHAT":
            async for sse in _run_chat_path(
                cleaned_message, thread_id, system_prompt_extra=skill_content
            ):
                yield sse
        elif classification == "SINGLE_TOOL":
            async for sse in _run_tool_path(cleaned_message, thread_id):
                yield sse
        else:  # DEEP_TASK
            async for sse in _run_deep_path(cleaned_message, thread_id, state):
                yield sse

        yield _sse("done", "{}")
