"""LangGraph Router 入口：``run_router`` 场景+模式直接分发。

场景化架构（Supervisor + Expert）下，Router 不再做 CHAT / SINGLE_TOOL /
DEEP_TASK / AgentTeam 四路径分类，而是根据前端传入的 ``agent_mode`` 直接
分发到对应场景的 runner：

- ``agent_mode == "work"`` → ``run_work_supervisor``（Supervisor 全能 agent）
- ``agent_mode == "coding"`` → ``run_coding_expert``（coding Expert）
- ``agent_mode == "coding_team"`` → ``run_coding_team``（coding 场景级 AgentTeam）

Router 保留的公共职责：
1. ``@skill:<name>`` 标记解析（仅 work 场景注入 system prompt）
2. workspace 授权同步
3. 用户画像加载
4. 从 checkpointer 加载历史 messages + 截断
5. 收集 assistant 内容并写回 checkpointer
6. 统一 yield ``done`` 事件
"""

from __future__ import annotations

import asyncio
import re

from langchain_core.messages import trim_messages
from typing import TYPE_CHECKING, Any, AsyncIterator

from app.agents.expert import run_coding_expert
from app.agents.supervisor import run_work_supervisor
from app.agents.team import run_coding_team
from app.config import get_settings
from app.memory.profile_store import build_profile_prompt
from app.memory.skills_loader import SkillDef, _parse_frontmatter, _tools_from_meta
from app.memory.skills_store import get_skill_file
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.observability.observation import get_observation_sink
from app.observability.trace import current_trace_id
from app.workspace.config import load_project_config, merge_configs
from app.utils.sse_events import make_sse_event

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = ["run_router", "_parse_skill_tag"]

# 合法 agent_mode 集合
_VALID_AGENT_MODES: frozenset[str] = frozenset({"work", "coding", "coding_team"})


# ============================================================
# @skill / <workspace> 标记解析
# ============================================================

# 单 skill content 注入上限（超出截断）
_SKILL_CONTENT_MAX = 4000

# @skill:<name> 标记正则
_SKILL_TAG_RE = re.compile(r"@skill:(\S+)")


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
    agent_mode: str = "work",
    workspace_path: str | None = None,
    revoked_paths: list[str] | None = None,
    chat_model: BaseChatModel | None = None,
) -> AsyncIterator[dict[str, str]]:
    """运行 Router，按 ``agent_mode`` 分发到对应场景 runner，yield SSE 事件。

    流程:
    1. 校验 ``agent_mode``，非法值直接 yield error
    2. 解析 ``@skill:<name>`` 标记（仅 work 场景注入 system prompt）
    3. 从请求字段同步 workspace 授权（跳过 revoked_paths 中的路径）
    4. 读取用户画像
    5. 从 checkpointer 加载历史 messages（若提供）+ 截断到预算
    6. 按 ``agent_mode`` 分发：
       - ``"work"`` → ``run_work_supervisor``
       - ``"coding"`` → ``run_coding_expert``
       - ``"coding_team"`` → ``run_coding_team``
    7. 收集 assistant token 内容，写回 checkpointer
    8. 统一 yield ``done`` 事件

    Args:
        message: 用户消息。
        thread_id: 会话 ID。
        checkpointer: 可选的 LangGraph checkpointer，用于加载/写回历史 messages。
        permission_mode: 权限模式，"standard"（审批流）或 "full_trust"（会话内全量放行）。
        agent_mode: 场景+模式枚举，``"work"`` / ``"coding"`` / ``"coding_team"``。
            默认 ``"work"``（Supervisor 全能 agent）。
        workspace_path: 可选当前会话绑定的 workspace 绝对路径，非空时自动授权沙箱写入。
        revoked_paths: 可选用户手动撤销过的路径列表；若 effective_workspace 在此列表中，
            则跳过 chip 自动授权，尊重用户撤销意图。
        chat_model: 可选注入的 ChatModel（用于评测框架注入 MockChatModel）。``None`` 时下游 runner 各自调用 ``get_chat_model()``。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
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

        # ---- 2. 解析 @skill 标记 ----
        cleaned_message, skill_content = _parse_skill_tag(message)

        # ---- 3. workspace 授权同步 ----
        # 优先使用会话级 workspace_path；若为空则回退到 thread 已有授权中的第一个
        # （重新发送消息的场景：会话从持久化恢复后 path 为 None，但上次授权仍然有效）。
        # 都不存在才跳过授权，以免污染 authorized_dirs。
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
            # 若用户已显式撤销该路径，跳过 chip 自动授权，尊重撤销意图
            _revoked = {str(p).strip().lower() for p in (revoked_paths or [])}
            if effective_workspace.strip().lower() in _revoked:
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
        # build_profile_prompt 失败时返回空字符串，不影响主流程
        try:
            profile_prompt = build_profile_prompt()
        except Exception as exc:  # noqa: BLE001 — 画像读取兜底
            logger.warning("build_profile_prompt failed", error=str(exc))
            profile_prompt = ""

        # 加载项目级 system_prompt（.agentx/system_prompt.md）
        # AGENTS.md + rules 由 deepagents memory= 参数自动加载（harness.resolve_memory_paths）
        project_system_prompt = ""
        if effective_workspace:
            try:
                from pathlib import Path

                # H2: async 热路径中用 to_thread 包装同步文件 IO，避免阻塞事件循环
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
            except Exception as exc:  # noqa: BLE001 — 项目配置加载兜底
                logger.warning(
                    "router.workspace_config_load_failed",
                    thread_id=thread_id,
                    workspace=effective_workspace,
                    error=str(exc),
                )

        # 项目级 system_prompt 前置到 profile_prompt
        # AGENTS.md + rules 由 deepagents memory= 自动注入，不再手动拼接
        if project_system_prompt:
            profile_prompt = (project_system_prompt + "\n" + profile_prompt).strip()

        # work 场景：skill_content 拼到 profile_prompt 前（作为 system prompt 前缀）
        # coding / coding_team 场景：skill_content 不注入（Expert 有自己的 prompt 体系）
        if agent_mode == "work" and skill_content:
            profile_prompt = (skill_content + "\n" + profile_prompt).strip()

        # ---- 5. 加载历史 messages（从 checkpointer）----
        history: list = []
        if checkpointer is not None:
            try:
                history = await _load_history_from_checkpointer(checkpointer, thread_id)
            except Exception as exc:  # noqa: BLE001 — 历史加载兜底
                logger.warning("load history failed", error=str(exc))
                history = []

        # T-P3-1: 用 LangChain 标准 trim_messages 替代手写 history[-max_msgs:]。
        # strategy="last" 保留最近消息（行为等价），避免破坏 tool_call 配对
        # （trim_messages 自动检测 tool_call ↔ ToolMessage 完整性）。
        # token 预算由 deepagents SummarizationMiddleware 处理。
        settings = get_settings()
        max_msgs = settings.context_max_messages
        if len(history) > max_msgs:
            history = trim_messages(
                history,
                max_tokens=max_msgs,
                token_counter=len,
                strategy="last",
            )

        logger.info(
            "router dispatch",
            thread_id=thread_id,
            agent_mode=agent_mode,
            message_len=len(cleaned_message),
            history_count=len(history),
        )

        # ---- 5.5 观测中心：record_prompt + start state snapshot ----
        run_id = current_trace_id() or ""
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
                # start snapshot：从 checkpointer 读 channel_values
                if checkpointer is not None and hasattr(checkpointer, "aget"):
                    cp_config = {"configurable": {"thread_id": thread_id}}
                    checkpoint = await checkpointer.aget(cp_config)
                    if checkpoint and isinstance(checkpoint, dict):
                        channel_values = checkpoint.get("channel_values", {}) or {}
                        await sink.record_state_snapshot(run_id, "start", channel_values)
            except Exception as exc:  # noqa: BLE001 — 观测失败不阻塞 router
                logger.warning("observation record_prompt/start failed", error=str(exc))

        # ---- 6. 场景分发 ----
        # 收集本次对话的 user + assistant 消息并写回 checkpointer
        assistant_content_parts: list[str] = []

        async def _collect_path_sse(path_generator: AsyncIterator[dict[str, str]]) -> AsyncIterator[dict[str, str]]:
            async for sse in path_generator:
                if sse.get("event") == "token":
                    assistant_content_parts.append(str(sse.get("data", "")))
                yield sse

        if agent_mode == "work":
            async for sse in _collect_path_sse(
                run_work_supervisor(
                    cleaned_message,
                    thread_id,
                    profile_prompt=profile_prompt,
                    history=history,
                    permission_mode=permission_mode,
                    workspace_path=workspace_path,
                    chat_model=chat_model,
                )
            ):
                yield sse
        elif agent_mode == "coding":
            async for sse in _collect_path_sse(
                run_coding_expert(
                    cleaned_message,
                    thread_id,
                    profile_prompt=profile_prompt,
                    history=history,
                    permission_mode=permission_mode,
                    workspace_path=workspace_path,
                    chat_model=chat_model,
                )
            ):
                yield sse
        else:  # coding_team
            async for sse in _collect_path_sse(
                run_coding_team(
                    cleaned_message,
                    thread_id,
                    profile_prompt=profile_prompt,
                    history=history,
                    permission_mode=permission_mode,
                    workspace_path=workspace_path,
                    chat_model=chat_model,
                )
            ):
                yield sse

        # ---- 7. 写回 checkpointer ----
        assistant_content = "".join(assistant_content_parts).strip()
        if assistant_content and checkpointer is not None:
            from langchain_core.messages import AIMessage, HumanMessage

            new_messages = [
                HumanMessage(content=cleaned_message),
                AIMessage(content=assistant_content),
            ]
            await _append_messages_to_checkpointer(checkpointer, thread_id, new_messages)

        # ---- 7.5 观测中心：end state snapshot ----
        if run_id:
            try:
                if checkpointer is not None and hasattr(checkpointer, "aget"):
                    cp_config = {"configurable": {"thread_id": thread_id}}
                    checkpoint = await checkpointer.aget(cp_config)
                    if checkpoint and isinstance(checkpoint, dict):
                        channel_values = checkpoint.get("channel_values", {}) or {}
                        await sink.record_state_snapshot(run_id, "end", channel_values)
            except Exception as exc:  # noqa: BLE001 — 观测失败不阻塞 router
                logger.warning("observation end snapshot failed", error=str(exc))

        # ---- 8. 统一 yield done ----
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


async def _append_messages_to_checkpointer(
    checkpointer: Any, thread_id: str, new_messages: list
) -> None:
    """将 ``new_messages`` 追加到 ``thread_id`` 的 checkpointer messages channel。

    实现策略：通过 LangGraph 编译一个最小 ``StateGraph``（含 messages channel），
    调用 ``graph.ainvoke`` 让 LangGraph 内部 schema 机制负责 channel_versions /
    checkpoint_id / checkpoint_ns 等字段的正确序列化。直接手工 ``aput`` 会因为
    缺少 ``checkpoint_ns`` / ``id`` 字段触发 ``InternalError: 'checkpoint_ns'``。

    仅支持异步 checkpointer（``aget`` 接口）。生产环境使用 ``AsyncSqliteSaver``，
    测试使用 ``InMemorySaver``，两者均实现 ``aget``。同步 ``SqliteSaver`` 不支持
    异步接口，调用方应使用 ``get_async_checkpointer()`` 获取异步实例。
    """
    from langchain_core.runnables import RunnableConfig
    from langgraph.graph import END, START, MessagesState, StateGraph

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    # 读取已有 messages，与 new_messages 合并后重新调用 ainvoke
    existing = await checkpointer.aget(config)

    existing_msgs: list = []
    if existing and isinstance(existing, dict):
        channel_values = existing.get("channel_values", {}) or {}
        existing_msgs = list(channel_values.get("messages", []) or [])
    combined_msgs = [*existing_msgs, *new_messages]

    # 构建单节点最小图：passthrough 节点把 input.messages 直接 emit 到 output.messages
    async def _passthrough(state: MessagesState) -> dict:  # noqa: ARG001
        return {"messages": []}

    graph = StateGraph(MessagesState)
    graph.add_node("passthrough", _passthrough)
    graph.add_edge(START, "passthrough")
    graph.add_edge("passthrough", END)
    compiled = graph.compile(checkpointer=checkpointer)
    await compiled.ainvoke({"messages": combined_msgs}, config=config)
