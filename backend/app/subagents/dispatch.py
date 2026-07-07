"""路径 B 子代理分发：LLM/关键词路由 + 子代理事件转换。

从 ``app.router.graph`` 抽取的子代理选择与事件转换逻辑（原 ``_run_tool_path`` 等），
行为与原实现完全一致，仅替换 SSE 构造为公共模块，并改为从 ``app.chat.run`` 引入路径 A 回退。
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from uuid import uuid4

from app.config import get_settings
from app.llm import get_chat_model
from app.observability.logger import logger
from app.subagents import run_code_agent, run_custom_agent, run_rag_agent, run_web_agent
from app.utils.sse_events import make_sse_event
from app.utils.text import ThinkFilter, strip_think

__all__ = [
    "select_subagent",
    "run_tool_path",
    "convert_subagent_event",
    "llm_select_subagent",
    "keyword_select_subagent",
    "extract_keywords_from_trigger",
    "SUBAGENT_ROUTER_PROMPT",
]


# LLM 子代理路由 system prompt
SUBAGENT_ROUTER_PROMPT = (
    "你是子代理路由决策器。根据用户消息，选择最合适的子代理来处理。\n\n"
    "可用子代理及其触发场景：\n"
    "{descriptions}\n\n"
    "规则：\n"
    "1. 只输出子代理名称（如 code / rag / web），不要解释\n"
    "2. 根据每个子代理的触发场景描述，判断哪个最匹配用户意图\n"
    "3. 如果都不匹配，选 code 作为默认兜底"
)


def extract_keywords_from_trigger(trigger: str) -> list[str]:
    """从触发条件描述中提取关键词（用于降级匹配）。

    策略：按中文标点、英文逗号、顿号分词，过滤短词和常见虚词。
    """
    if not trigger:
        return []
    import re
    # 按常见分隔符拆分
    parts = re.split(r'[，,、；;。\.\s]+', trigger)
    keywords = []
    stop_words = {"用户", "问题", "涉及", "需要", "获取", "相关", "时", "触发", "的", "是", "和", "或", "等", "建议", "如下", "包括", "以及", "与", "及", "如", "例如", "比如", "比如", "例如", "比如"}
    for p in parts:
        p = p.strip()
        # 保留有意义的词（2-20 字符，非纯数字，非停用词）
        if 2 <= len(p) <= 20 and not p.isdigit() and p not in stop_words:
            keywords.append(p)
    return keywords


async def llm_select_subagent(message: str) -> str | None:
    """通过 LLM 语义分析选择最合适的子代理。

    Returns:
        子代理名称（"code" / "rag" / "web" / 自定义 key）或 None（LLM 不可用）
    """
    settings = get_settings()
    subagents = settings.subagents
    tools_enabled = settings.tools_enabled

    # 构建可用子代理触发场景描述
    available: list[str] = []
    for name in ("code", "rag", "web"):
        cfg = subagents[name]
        if not cfg.enabled:
            continue
        if not any(tools_enabled.get(t, True) for t in cfg.tools):
            continue
        trigger = cfg.trigger_description or ""
        available.append(f"- {name}: {trigger}")

    custom = settings.custom_subagents
    custom_available: list[str] = []
    for key in sorted(custom.keys()):
        cfg = custom[key]
        if not cfg.enabled:
            continue
        if not any(tools_enabled.get(t, True) for t in cfg.tools):
            continue
        trigger = cfg.trigger_description or ""
        custom_available.append(f"- {key}: {trigger}")

    all_available = available + custom_available
    if not all_available:
        return None

    # 构建 prompt
    descriptions = "\n".join(all_available)
    system_prompt = SUBAGENT_ROUTER_PROMPT.format(descriptions=descriptions)

    try:
        llm = get_chat_model(temperature=0.0, streaming=False)
    except ValueError as exc:
        logger.warning("LLM 不可用，子代理路由退回路径 A", error=str(exc))
        return None

    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=message),
        ]
        result = await llm.ainvoke(messages)
    except Exception as exc:
        logger.warning("LLM 子代理路由调用失败，退回路径 A", error=str(exc))
        return None

    content = getattr(result, "content", "") or ""
    stripped = strip_think(content) if isinstance(content, str) else ""
    if not stripped:
        logger.warning("LLM 子代理路由输出为空，退回路径 A")
        return None

    label = stripped.splitlines()[0].strip().lower()

    # 验证返回的子代理是否可用
    builtin_keys = {"code", "rag", "web"}
    if label in builtin_keys:
        cfg = subagents[label]
        if cfg.enabled and any(tools_enabled.get(t, True) for t in cfg.tools):
            return label
        logger.warning(f"LLM 路由返回的子代理 {label} 不可用，退回路径 A")
        return None

    if label in custom:
        cfg = custom[label]
        if cfg.enabled and any(tools_enabled.get(t, True) for t in cfg.tools):
            return label
        logger.warning(f"LLM 路由返回的自定义子代理 {label} 不可用，退回路径 A")
        return None

    # 未知标签，退回路径 A
    logger.warning(f"LLM 路由返回未知子代理 '{label}'，退回路径 A")
    return None


def keyword_select_subagent(message: str) -> str | None:
    """触发条件回退：LLM 不可用时，从 trigger_description 提取关键词进行匹配。

    Returns:
        "code" / "rag" / "web" / 自定义子代理 key / None
        - None 表示命中的子代理被禁用或工具全禁用，退回路径 A

    匹配优先级：
    1. 内置子代理（web → rag → code，按 trigger_description 提取的关键词命中）
    2. 自定义子代理（按 key 字典序，trigger_description 提取的关键词命中）
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
        # 从 trigger_description 提取关键词进行匹配
        keywords = extract_keywords_from_trigger(cfg.trigger_description or "")
        # keywords 为空列表（trigger_description 为空）时，不匹配任何消息（跳过）
        if not keywords:
            continue
        if not any(kw in message for kw in keywords):
            continue
        # 检查绑定的工具是否全部被禁用
        if not any(tools_enabled.get(t, True) for t in cfg.tools):
            logger.warning(f"subagent {agent_name} matched but all tools disabled, fallback to CHAT")
            return None
        return agent_name

    # 2. 自定义子代理：按 key 字典序遍历，trigger_description 提取的关键词命中即返回
    custom = settings.custom_subagents
    for key in sorted(custom.keys()):
        cfg = custom[key]
        if not cfg.enabled:
            continue
        keywords = extract_keywords_from_trigger(cfg.trigger_description or "")
        # keywords 为空列表（trigger_description 为空）时，不匹配任何消息（跳过）
        if not keywords:
            continue
        if not any(kw in message for kw in keywords):
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


def select_subagent(message: str) -> str | None:
    """根据消息内容选择路径 B 的子代理（关键词匹配回退）。

    Returns:
        "code" / "rag" / "web" / 自定义子代理 key / None
        - None 表示无可用子代理，退回路径 A
    """
    return keyword_select_subagent(message)


async def run_tool_path(
    message: str,
    thread_id: str,
    profile_prompt: str | None = None,
    history: list | None = None,
    scene_prompt: str | None = None,
    workspace_path: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    """路径 B：选择子代理并透传事件流。

    Args:
        message: 用户消息（已移除 @skill 标记）。
        thread_id: 会话 ID。
        profile_prompt: 用户画像，回退路径 A 时注入 system prompt。
        history: 历史 messages 列表（已截断），传给子代理拼到 inputs 前。
        scene_prompt: 可选场景 prompt，回退路径 A 时透传。
        workspace_path: 当前会话绑定的 workspace 绝对路径，子代理 fs 工具用其解析相对路径。

    Note:
        - 入口 yield ``delegation`` SSE 事件标识委派目标（spec D6）。
        - 子代理 token 事件经 ``ThinkFilter(retain_think=True)`` 分离：
          reasoning chunk 走 ``reasoning`` SSE 事件，visible text 走 ``token`` SSE 事件。
        - 子代理 tool_call/tool_result 标准化事件透传为同名 SSE 事件（含 source 字段），
          不再压扁为 todo_update（spec D1）。
    """
    agent_type = await llm_select_subagent(message)
    if agent_type is None:
        # 子代理禁用或工具全禁用，退回路径 A（注入画像）
        logger.info("router.tool_path fallback to CHAT", thread_id=thread_id)
        from app.chat.run import run_chat_path

        async for sse in run_chat_path(
            message,
            thread_id,
            system_prompt_extra=profile_prompt,
            history=history,
            scene_prompt=scene_prompt,
        ):
            yield sse
        return

    # 计算 source 字段：内置子代理用 agent_type，自定义子代理用 "custom-<key>"
    if agent_type in ("web", "rag", "code"):
        source = agent_type
    else:
        source = f"custom-{agent_type}"

    # 路径 B 入口：yield delegation 事件标识委派目标（spec D6）
    yield make_sse_event("delegation", {
        "target": source,
        "source": "router",
        "message": f"委派给 {source} 子代理",
    })

    logger.info("router.tool_path", agent=agent_type, source=source, thread_id=thread_id)
    if agent_type == "web":
        runner = run_web_agent
    elif agent_type == "rag":
        runner = run_rag_agent
    elif agent_type == "code":
        runner = run_code_agent
    else:
        # 自定义子代理：runner 需要 key 参数，单独处理
        think_filter = ThinkFilter(
            max_hold=get_settings().think_filter_max_hold,
            retain_think=True,
        )
        try:
            async for event in run_custom_agent(
                agent_type,
                thread_id,
                message,
                history=history,
                workspace_path=workspace_path,
            ):
                for sse in convert_subagent_event(event, think_filter, source=source):
                    yield sse
        except Exception as exc:  # noqa: BLE001 — SSE 兜底
            logger.warning("custom subagent failed", key=agent_type, error=str(exc))
            yield make_sse_event("error", f"自定义子代理执行失败: {exc}")
        finally:
            tail = think_filter.flush()
            if tail:
                yield make_sse_event("token", tail)
        return

    think_filter = ThinkFilter(
        max_hold=get_settings().think_filter_max_hold,
        retain_think=True,
    )
    try:
        async for event in runner(
            thread_id, message, history=history, workspace_path=workspace_path
        ):
            for sse in convert_subagent_event(event, think_filter, source=source):
                yield sse
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.warning("tool path subagent failed", error=str(exc))
        yield make_sse_event("error", f"子代理执行失败: {exc}")
        return
    finally:
        tail = think_filter.flush()
        if tail:
            yield make_sse_event("token", tail)


def convert_subagent_event(
    event: dict[str, Any],
    think_filter: ThinkFilter | None = None,
    source: str = "",
) -> list[dict[str, str]]:
    """将子代理标准化事件转为 SSE 事件列表（一次可能产出多个，如 reasoning + token）。

    子代理事件: {type: "token"/"tool_call"/"tool_result", ...}
    SSE 事件: {event: "token"/"reasoning"/"tool_call"/"tool_result", data: ...}

    - token → 经 ``ThinkFilter`` 分离：reasoning chunk 走 ``reasoning`` SSE，
      visible text 走 ``token`` SSE（spec D2）
    - tool_call → 透传为 ``tool_call`` SSE（含 id/name/args/source，spec D1）
    - tool_result → 透传为 ``tool_result`` SSE（含 id/name/result/source/error?，spec D1）

    Args:
        event: 子代理标准化事件 dict。
        think_filter: 可选的流式 think 过滤器。若提供且 ``retain_think=True``，
            则 token 内容经 ``feed`` 过滤后，``take_think`` 取 reasoning chunk。
            若不提供则直接透传 token（仅供单元测试 mock 使用）。
        source: 子代理类型标识（"code"/"rag"/"web"/"custom-xxx"），
            用于 reasoning/tool_call/tool_result 事件的 source 字段。
            若 event 自带 source 字段（T3 子代理已补），优先用 event 的 source。

    Returns:
        SSE 事件 dict 列表（可能为空，如空 token 被过滤后）。
    """
    out: list[dict[str, str]] = []
    etype = event.get("type", "")
    if etype == "token":
        content = event.get("content", "")
        if not content:
            return out
        if think_filter is not None:
            cleaned = think_filter.feed(content)
            # retain_think 模式：take_think 取本次 feed 累积的 reasoning chunk
            if getattr(think_filter, "_retain_think", False):
                reasoning = think_filter.take_think()
                if reasoning:
                    out.append(
                        make_sse_event("reasoning", {"content": reasoning, "source": source})
                    )
            if cleaned:
                out.append(make_sse_event("token", cleaned))
        else:
            out.append(make_sse_event("token", content))
        return out
    if etype == "tool_call":
        name = event.get("name", "")
        args = event.get("args", {})
        # 优先用 event 自带 source（T3 子代理已补），兜底用 caller 传入的 source
        ev_source = event.get("source") or source
        tc_id = event.get("id") or str(uuid4())
        out.append(
            make_sse_event(
                "tool_call",
                {
                    "id": tc_id,
                    "name": name,
                    "args": args,
                    "source": ev_source,
                },
            )
        )
        return out
    if etype == "tool_result":
        name = event.get("name", "")
        result = event.get("result", "")
        ev_source = event.get("source") or source
        tc_id = event.get("id") or str(uuid4())
        data: dict[str, Any] = {
            "id": tc_id,
            "name": name,
            "result": result,
            "source": ev_source,
        }
        if event.get("error"):
            data["error"] = event["error"]
        out.append(make_sse_event("tool_result", data))
        return out
    return out
