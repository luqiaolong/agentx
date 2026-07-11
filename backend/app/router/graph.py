"""LangGraph Router 入口：``run_router`` 场景+模式直接分发。

场景化架构（Supervisor + Expert）下，Router 不再做 CHAT / SINGLE_TOOL /
DEEP_TASK / AgentTeam 四路径分类，而是根据前端传入的 ``agent_mode`` 直接
分发到对应场景的 runner：

- ``agent_mode == "work"`` → ``run_work_supervisor``（Supervisor 全能 agent）
- ``agent_mode == "coding"`` → ``run_coding_expert``（coding Expert）
- ``agent_mode == "coding_team"`` → ``run_coding_team``（coding 场景级 AgentTeam）

Router 保留的公共职责：
1. ``/skill:<name>`` 标记解析（仅 work 场景注入 system prompt）
2. workspace 授权同步
3. 用户画像加载
4. 从 checkpointer 加载历史 messages + 截断（仅用于 coding_team 子任务上下文 + 观测）
5. 统一 yield ``done`` 事件（chat.py 侧需过滤避免双重 done，见 H3）

work / coding 路径的 checkpointer 历史加载 + 新消息写回由 LangGraph astream 自动处理，
Router 不再传 history 也不手动写回（避免双重写入）。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path

from langchain_core.messages import trim_messages
from typing import TYPE_CHECKING, Any, AsyncIterator

from app.scenarios.coding import run_coding_expert
from app.scenarios.work import run_work_supervisor
from app.scenarios.coding_team import run_coding_team
from app.config import get_settings
from app.memory.profile_store import build_profile_prompt
from app.memory.skills_loader import SkillDef, _parse_frontmatter, _tools_from_meta
from app.memory.skills_store import get_skill_file
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.observability.observation import get_observation_sink
from app.observability.trace import current_trace_id
from app.workspace.config import load_project_config, merge_configs
from app.sse.events import make_sse_event

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = ["run_router", "_parse_skill_tag"]

# 合法 agent_mode 集合
_VALID_AGENT_MODES: frozenset[str] = frozenset({"work", "coding", "coding_team"})


# ============================================================
# /skill:<name> 标记解析
# ============================================================

# 单 skill content 注入上限（超出截断）
_SKILL_CONTENT_MAX = 4000

# /skill:<name> 标记正则
_SKILL_TAG_RE = re.compile(r"/skill:([^\s/]+)")


def _load_skill_def(name: str) -> SkillDef | None:
    """按名称读取 ``data/skills/<name>/SKILL.md`` 并解析为 ``SkillDef``。

    文件不存在或解析失败时返回 None。
    """
    try:
        text = get_skill_file(name)
    except FileNotFoundError:
        return None
    parsed = _parse_frontmatter(text)
    if parsed is None:
        return SkillDef(name=name, content=text)
    meta, body = parsed
    try:
        return SkillDef(
            name=str(meta.get("name", name)),
            description=str(meta.get("description", "")),
            trigger=str(meta.get("trigger", "")),
            tools=_tools_from_meta(meta.get("tools")),
            content=body,
        )
    except Exception:  # noqa: BLE001
        return None


def _parse_skill_tag(message: str) -> tuple[str, str | None]:
    """解析用户消息中的所有 ``/skill:<name>`` 标记。

    - 移除所有 /skill: 标记（无论技能是否存在），避免 LLM 看到未知标记困惑。
    - 首个存在的技能 → 注入其 content；其余标记仅移除。
    - 无标记或所有技能都不存在 → ``(cleaned_message, None)``。
    - 单 skill content 超过 ``_SKILL_CONTENT_MAX`` 字符时截断并追加标记。

    Examples:
        >>> _parse_skill_tag("/skill:coder 帮我写代码")
        ("帮我写代码", "<coder skill content>")
        >>> _parse_skill_tag("/skill:unknown 帮我")  # 技能不存在
        ("帮我", None)
        >>> _parse_skill_tag("/skill:a /skill:b 任务")  # 多标签，a 存在
        ("任务", "<a skill content>")
    """
    matches = list(_SKILL_TAG_RE.finditer(message))
    if not matches:
        return (message, None)

    skill_content: str | None = None
    for match in matches:
        skill_name = match.group(1)
        skill = _load_skill_def(skill_name)
        if skill is not None:
            content = skill.content
            if len(content) > _SKILL_CONTENT_MAX:
                content = content[:_SKILL_CONTENT_MAX] + "\n[skill content truncated]"
            skill_content = content
            break  # 仅注入首个存在的技能

    # 移除所有 /skill:<name> 标记（含不存在的），剩余文本作为用户消息
    # 注意：不合并内部空白，避免破坏代码块换行和缩进
    cleaned = _SKILL_TAG_RE.sub("", message).strip()
    return (cleaned, skill_content)


# ============================================================
# Router 主入口
# ============================================================


async def _run_router_inner(
    message: str,
    thread_id: str,
    checkpointer: Any,
    permission_mode: str,
    agent_mode: str,
    workspace_path: str | None,
    revoked_paths: list[str] | None,
    chat_model: BaseChatModel | None,
) -> AsyncIterator[dict[str, str]]:
    """run_router 实际逻辑（被外层 trace bind 包裹）。"""
    with trace_span("router.run", thread_id=thread_id, message_len=len(message), agent_mode=agent_mode):
        # ---- 1. 校验 agent_mode ----
        if agent_mode not in _VALID_AGENT_MODES:
            logger.warning(
                "router.invalid_agent_mode",
                thread_id=thread_id,
                agent_mode=agent_mode,
            )
            yield make_sse_event(
                "error",
                f"无效的 agent_mode: '{agent_mode}'，合法值为 {sorted(_VALID_AGENT_MODES)}",
            )
            yield make_sse_event("done", "{}")
            return

        # ---- 2. 解析 /skill 标记 ----
        cleaned_message, skill_content = _parse_skill_tag(message)

        # ---- 3. workspace 授权同步 ----
        effective_workspace = (workspace_path or "").strip() or None
        if not effective_workspace:
            from app.sandbox import get_sandbox as _get_sandbox_fallback
            try:
                _existing = await _get_sandbox_fallback().list_authorized(thread_id)
            except Exception:
                _existing = []
            if _existing:
                effective_workspace = str(_existing[0][0])
                logger.info(
                    "router.workspace_fallback_to_existing",
                    thread_id=thread_id,
                    workspace=effective_workspace,
                )
        if effective_workspace:
            _revoked: set[str] = set()
            for p in (revoked_paths or []):
                try:
                    _revoked.add(str(Path(p).resolve()).strip().lower())
                except Exception:  # noqa: BLE001
                    _revoked.add(str(p).strip().lower())
            try:
                ws_normalized = str(Path(effective_workspace).resolve()).strip().lower()
            except Exception:  # noqa: BLE001
                ws_normalized = effective_workspace.strip().lower()
            if ws_normalized in _revoked:
                logger.info(
                    "router.workspace_skipped_revoked",
                    thread_id=thread_id,
                    workspace=effective_workspace,
                )
            else:
                from app.sandbox import get_sandbox
                sandbox = get_sandbox()
                try:
                    await sandbox.authorize(thread_id, effective_workspace, writable=True, source="chip")
                    logger.info(
                        "router.workspace_authorized",
                        thread_id=thread_id,
                        workspace=effective_workspace,
                    )
                except ValueError as exc:
                    logger.warning(
                        "router.workspace_authorize_failed",
                        thread_id=thread_id,
                        workspace=effective_workspace,
                        error=str(exc),
                    )

        # ---- 4. 读取用户画像 + 项目级 system_prompt ----
        try:
            profile_prompt = await asyncio.to_thread(
                build_profile_prompt, workspace_path=effective_workspace
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("build_profile_prompt failed", error=str(exc))
            profile_prompt = ""

        project_system_prompt = ""
        if effective_workspace:
            try:
                project_config = await asyncio.to_thread(
                    load_project_config, Path(effective_workspace)
                )
                if project_config.exists and project_config.system_prompt:
                    merged = merge_configs(get_settings(), project_config)
                    project_system_prompt = merged.default_system_prompt
                    if project_system_prompt:
                        logger.info(
                            "router.workspace_config_loaded",
                            thread_id=thread_id,
                            workspace=effective_workspace,
                            has_system_prompt=True,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "router.workspace_config_load_failed",
                    thread_id=thread_id,
                    workspace=effective_workspace,
                    error=str(exc),
                )

        if project_system_prompt:
            profile_prompt = (project_system_prompt + "\n" + profile_prompt).strip()
        if agent_mode == "work" and skill_content:
            profile_prompt = (skill_content + "\n" + profile_prompt).strip()

        # ---- 5. 加载历史 messages ----
        history: list = []
        if checkpointer is not None:
            logger.info("router.inner.before_load_history", thread_id=thread_id)
            try:
                history = await _load_history_from_checkpointer(checkpointer, thread_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("load history failed", error=str(exc))
                history = []
            logger.info("router.inner.after_load_history", thread_id=thread_id, history_count=len(history))

        settings = get_settings()
        try:
            # 第一轮：按消息条数截断
            max_msgs = settings.context_max_messages
            if len(history) > max_msgs:
                history = trim_messages(
                    history,
                    max_tokens=max_msgs,
                    token_counter=len,
                    strategy="last",
                    include_system=True,
                )
            # 第二轮：按 token 数截断（如果 chat_model 提供了 token 计数方法）
            if chat_model and hasattr(chat_model, "get_num_tokens_from_messages"):
                max_tokens = settings.context_max_tokens
                try:
                    token_count = chat_model.get_num_tokens_from_messages(history)
                    if token_count > max_tokens:
                        history = trim_messages(
                            history,
                            max_tokens=max_tokens,
                            token_counter=chat_model.get_num_tokens_from_messages,
                            strategy="last",
                            include_system=True,
                        )
                except Exception:  # noqa: BLE001
                    pass  # token 计数失败时回退到条数截断
        except Exception:  # noqa: BLE001
            logger.exception("trim_messages failed, fallback to simple slice")
            max_msgs = settings.context_max_messages
            history = history[-max_msgs:] if len(history) > max_msgs else history

        logger.info(
            "router dispatch",
            thread_id=thread_id,
            agent_mode=agent_mode,
            message_len=len(cleaned_message),
            history_count=len(history),
        )

        # ---- 5.5 观测中心 ----
        run_id = current_trace_id() or ""
        sink = None  # 预初始化，避免 start 块异常时 end 块引用未定义变量
        if run_id:
            history_preview = "\n".join(
                f"{getattr(m, 'type', '?')}: {str(getattr(m, 'content', ''))[:200]}"
                for m in history[-4:]
            )
            try:
                sink = get_observation_sink()
                await sink.record_prompt(
                    run_id=run_id,
                    system_prompt=profile_prompt,
                    user_message=cleaned_message,
                    history_preview=history_preview,
                )
                if checkpointer is not None and hasattr(checkpointer, "aget"):
                    cp_config = {"configurable": {"thread_id": thread_id}}
                    checkpoint = await checkpointer.aget(cp_config)
                    if checkpoint and isinstance(checkpoint, dict):
                        channel_values = checkpoint.get("channel_values", {}) or {}
                        await sink.record_state_snapshot(run_id, "start", channel_values)
            except Exception as exc:  # noqa: BLE001
                logger.warning("observation record_prompt/start failed", error=str(exc))

        # ---- 6. 场景分发 ----
        try:
            if agent_mode == "work":
                async for sse in run_work_supervisor(
                    cleaned_message,
                    thread_id,
                    profile_prompt=profile_prompt,
                    permission_mode=permission_mode,
                    workspace_path=effective_workspace,
                    chat_model=chat_model,
                    checkpointer=checkpointer,
                ):
                    yield sse
            elif agent_mode == "coding":
                async for sse in run_coding_expert(
                    cleaned_message,
                    thread_id,
                    profile_prompt=profile_prompt,
                    permission_mode=permission_mode,
                    workspace_path=effective_workspace,
                    chat_model=chat_model,
                    checkpointer=checkpointer,
                ):
                    yield sse
            else:  # coding_team
                # M11: token 事件已由 scheduler._route_event_for_node 过滤，
                # 到达此处的 token 事件均来自 aggregator 的最终汇总输出，
                # 因此直接收集即可，无需按子代理分组。
                assistant_content_parts: list[str] = []
                has_error = False

                async def _collect_team_sse(
                    path_generator: AsyncIterator[dict[str, str]],
                ) -> AsyncIterator[dict[str, str]]:
                    nonlocal has_error
                    async for sse in path_generator:
                        if sse.get("event") == "token":
                            assistant_content_parts.append(str(sse.get("data", "")))
                        elif sse.get("event") == "error":
                            has_error = True
                        yield sse

                async for sse in _collect_team_sse(
                    run_coding_team(
                        cleaned_message,
                        thread_id,
                        profile_prompt=profile_prompt,
                        history=history,
                        permission_mode=permission_mode,
                        workspace_path=effective_workspace,
                        chat_model=chat_model,
                    )
                ):
                    yield sse

                # M10: 仅在无 error 事件时写回 checkpointer，避免部分内容被持久化
                assistant_content = "".join(assistant_content_parts).strip()
                if assistant_content and checkpointer is not None and not has_error:
                    from langchain_core.messages import AIMessage, HumanMessage
                    new_messages = [
                        HumanMessage(content=cleaned_message),
                        AIMessage(content=assistant_content),
                    ]
                    await _append_messages_to_checkpointer(checkpointer, thread_id, new_messages)
        finally:
            # ---- 7. 观测中心 end（best-effort，确保异常/断连时也能执行）----
            if run_id and sink is not None:
                try:
                    if checkpointer is not None and hasattr(checkpointer, "aget"):
                        cp_config = {"configurable": {"thread_id": thread_id}}
                        checkpoint = await checkpointer.aget(cp_config)
                        if checkpoint and isinstance(checkpoint, dict):
                            channel_values = checkpoint.get("channel_values", {}) or {}
                            await sink.record_state_snapshot(run_id, "end", channel_values)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("observation end snapshot failed", error=str(exc))

        # ---- 8. 统一 yield done ----
        # 注意：chat.py 在 run_router 循环退出后会再 yield 一个带 token_count 的 done 事件，
        # 这里保留 done 是为了 direct caller（如测试）的兼容性。
        # H3 双重 done 问题需在 chat.py 侧过滤或测试更新后才能移除此 yield。
        yield make_sse_event("done", "{}")


async def run_router(
    message: str,
    thread_id: str,
    checkpointer: Any = None,
    permission_mode: str = "standard",
    agent_mode: str = "work",
    workspace_path: str | None = None,
    revoked_paths: list[str] | None = None,
    chat_model: BaseChatModel | None = None,
    trace_id: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    """运行 Router，按 ``agent_mode`` 分发到对应场景 runner，yield SSE 事件。

    外层包裹 trace_id ContextVar，确保 LangGraph 内部节点也能读取到 trace_id。
    """
    from app.observability.trace import bind_trace, current_trace_id

    _trace_id = trace_id or current_trace_id() or ""
    _trace_cm = bind_trace(_trace_id) if _trace_id else contextlib.nullcontext()
    with _trace_cm:
        async for sse in _run_router_inner(
            message,
            thread_id,
            checkpointer=checkpointer,
            permission_mode=permission_mode,
            agent_mode=agent_mode,
            workspace_path=workspace_path,
            revoked_paths=revoked_paths,
            chat_model=chat_model,
        ):
            yield sse


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


# ============================================================
# checkpointer 写入锁 + 编译图缓存（M14 + Low 5）
# ============================================================

_checkpoint_locks: dict[str, asyncio.Lock] = {}
_checkpoint_locks_guard = asyncio.Lock()
# 按 checkpointer id 缓存编译后的 passthrough graph（Low 5）
_compiled_graph_cache: dict[int, Any] = {}


async def _get_checkpoint_lock(thread_id: str) -> asyncio.Lock:
    """获取 per-thread_id 的 asyncio.Lock，防止并发 read-modify-write 丢失更新。"""
    async with _checkpoint_locks_guard:
        if thread_id not in _checkpoint_locks:
            _checkpoint_locks[thread_id] = asyncio.Lock()
        return _checkpoint_locks[thread_id]


async def _passthrough_node(state: Any) -> dict:  # noqa: ARG001
    """passthrough 节点：不修改 state，仅用于触发 checkpointer 写入。"""
    return {"messages": []}


def _get_compiled_passthrough_graph(checkpointer: Any) -> Any:
    """获取编译后的 passthrough StateGraph（按 checkpointer id 缓存，Low 5）。"""
    from langgraph.graph import END, START, MessagesState, StateGraph

    cache_key = id(checkpointer)
    if cache_key not in _compiled_graph_cache:
        graph = StateGraph(MessagesState)
        graph.add_node("passthrough", _passthrough_node)
        graph.add_edge(START, "passthrough")
        graph.add_edge("passthrough", END)
        _compiled_graph_cache[cache_key] = graph.compile(checkpointer=checkpointer)
    return _compiled_graph_cache[cache_key]


async def _append_messages_to_checkpointer(
    checkpointer: Any, thread_id: str, new_messages: list
) -> None:
    """将 ``new_messages`` 追加到 ``thread_id`` 的 checkpointer messages channel。

    仅用于 coding_team 路径（team graph 无 checkpointer，需 Router 手动写回）。
    work / coding 路径由 LangGraph astream 自动写回，不调用此函数。

    通过 LangGraph 编译最小 ``StateGraph``（含 messages channel），调用
    ``graph.ainvoke`` 让 LangGraph 内部 schema 机制负责 channel_versions /
    checkpoint_id / checkpoint_ns 等字段的正确序列化。

    M14: 使用 per-thread_id asyncio.Lock 保护 read-modify-write，防止并发丢失更新。
    """
    from langchain_core.runnables import RunnableConfig

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    lock = await _get_checkpoint_lock(thread_id)
    async with lock:
        existing = await checkpointer.aget(config)

        existing_msgs: list = []
        if existing and isinstance(existing, dict):
            channel_values = existing.get("channel_values", {}) or {}
            existing_msgs = list(channel_values.get("messages", []) or [])
        combined_msgs = [*existing_msgs, *new_messages]

        compiled = _get_compiled_passthrough_graph(checkpointer)
        await compiled.ainvoke({"messages": combined_msgs}, config=config)



