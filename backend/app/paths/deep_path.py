"""DeepAgent 路径（路径 C）：deepagents + LangGraph，带危险工具中断。

- 用 ``langgraph.prebuilt.create_react_agent`` 构建 ReAct DeepAgent（与 subagents 一致）
- 工具集: filesystem 全部 + rag_retrieve + web_search
- ``interrupt_before=["tools"]``：调用任何工具前 LangGraph 暂停，SSE handler 检查待执行
  工具是否属于 ``DANGEROUS_TOOLS``，是则 yield approval_request 等用户审批，否则自动放行
- 审批恢复: ``_await_approval`` 轮询 ``_pending_approvals``，通过后以
  ``agent.astream_events(None, config)`` 续跑（LangGraph ``interrupt_before`` 的标准恢复方式）
- 使用 ``MemorySaver`` 作为 agent 内部 checkpointer，支持同一会话内 interrupt/resume 循环
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.llm import get_chat_model
from app.observability.logger import logger
from app.router.state import RouterState
from app.subagents.code_agent import _make_fs_tools
from app.subagents.rag_agent import _make_rag_tools
from app.subagents.web_agent import _make_web_tools

# 触发人工审批中断的工具集合：写操作与 shell 执行
DANGEROUS_TOOLS: set[str] = {"edit_file", "write_file", "shell_exec"}

# DeepAgent 系统提示
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
)

# 审批轮询参数
_APPROVAL_POLL_INTERVAL = 0.3
_APPROVAL_MAX_WAIT = 300.0  # 5 分钟上限，生产可配置更长

__all__ = [
    "DANGEROUS_TOOLS",
    "build_deep_agent",
    "run_deep_path",
    "wait_for_approval",
]


def _make_deep_tools(thread_id: str) -> list:
    """构建 DeepAgent 工具集：fs + rag + web。

    复用 subagents 的工具构建函数，确保 thread_id 绑定与沙箱校验一致。
    """
    fs_tools = _make_fs_tools(thread_id)
    rag_tools = _make_rag_tools(thread_id)
    web_tools = _make_web_tools(thread_id)
    return [*fs_tools, *rag_tools, *web_tools]


def build_deep_agent(thread_id: str) -> Any:
    """构造真实 DeepAgent 图。

    用 ``create_react_agent`` 构建 ReAct 子图，``interrupt_before=["tools"]`` 使图在
    执行任何工具前暂停。``MemorySaver`` 作为 agent 内部 checkpointer 支持
    interrupt/resume 循环（每次 ``run_deep_path`` 调用构建新 agent + 新 saver）。

    Args:
        thread_id: 会话 ID（用于工具的沙箱授权绑定）。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    model = get_chat_model(temperature=0.3, streaming=True)
    tools = _make_deep_tools(thread_id)
    checkpointer = MemorySaver()
    return create_react_agent(
        model,
        tools,
        name="deep_agent",
        prompt=_DEEP_SYSTEM_PROMPT,
        interrupt_before=["tools"],
        checkpointer=checkpointer,
    )


def _extract_text(chunk: Any) -> str:
    """从流式 chunk 中提取纯文本内容（兼容 str / list 内容块）。"""
    if chunk is None:
        return ""
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _get_pending_tool_calls(agent: Any, config: dict) -> list[dict]:
    """从 agent 状态中提取待执行的工具调用列表。

    当图在 ``interrupt_before=["tools"]`` 处暂停时，最后一条消息是 AIMessage，
    其 ``tool_calls`` 属性包含待执行的工具调用。
    """
    state = agent.get_state(config)
    if not state or not state.values:
        return []
    messages = state.values.get("messages", [])
    if not messages:
        return []
    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []
    return list(tool_calls)


def _is_interrupted(agent: Any, config: dict) -> bool:
    """检查 agent 是否在 interrupt 处暂停（next 含 "tools"）。"""
    state = agent.get_state(config)
    if not state or not state.next:
        return False
    return "tools" in state.next


def _redact_args(tool_name: str, args: dict) -> dict:
    """对危险工具的参数做 redaction（隐藏文件内容等敏感字段）。"""
    if not isinstance(args, dict):
        return {}
    redacted = dict(args)
    # 写文件 / 编辑文件：隐藏 content / new_text
    if tool_name in ("write_file", "edit_file"):
        if "content" in redacted:
            redacted["content"] = "<redacted>"
        if "new_text" in redacted:
            redacted["new_text"] = "<redacted>"
        if "old_text" in redacted:
            redacted["old_text"] = "<redacted>"
    return redacted


def _make_approval_event(tool_call: dict) -> dict[str, str]:
    """构造 approval_request SSE 事件。"""
    name = tool_call.get("name", "unknown")
    args = tool_call.get("args", {})
    redacted_args = _redact_args(name, args if isinstance(args, dict) else {})

    # 生成预览描述
    if name == "write_file":
        path = args.get("path", "?") if isinstance(args, dict) else "?"
        preview = f"将写入文件: {path}"
    elif name == "edit_file":
        path = args.get("path", "?") if isinstance(args, dict) else "?"
        preview = f"将编辑文件: {path}"
    elif name == "shell_exec":
        preview = "将执行系统命令"
    else:
        preview = f"将执行工具: {name}"

    return {
        "event": "approval_request",
        "data": json.dumps(
            {
                "tool_name": name,
                "args": redacted_args,
                "preview": preview,
            },
            ensure_ascii=False,
        ),
    }


def _make_todo_event(text: str, done: bool = False) -> dict[str, str]:
    """构造 todo_update SSE 事件。"""
    return {
        "event": "todo_update",
        "data": json.dumps(
            {"todos": [{"text": text, "done": done}]},
            ensure_ascii=False,
        ),
    }


async def _stream_agent_events(
    agent: Any, inputs: Any, config: dict
) -> AsyncIterator[dict[str, str]]:
    """驱动 agent.astream_events，将事件转为 SSE 格式。

    - ``on_chat_model_stream`` → token 事件
    - ``on_tool_start`` → todo_update 事件（工具调用开始）
    - ``on_tool_end`` → todo_update 事件（工具调用完成）
    """
    async for event in agent.astream_events(inputs, version="v2", config=config):
        kind = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {}) or {}

        if kind == "on_chat_model_stream":
            content = _extract_text(data.get("chunk"))
            if content:
                yield {"event": "token", "data": content}

        elif kind == "on_tool_start":
            yield _make_todo_event(f"调用工具: {name}", done=False)

        elif kind == "on_tool_end":
            yield _make_todo_event(f"工具 {name} 完成", done=True)


async def run_deep_path(state: RouterState, message: str) -> AsyncIterator[dict]:
    """DeepAgent 路径 SSE 生成器（真实实现）。

    流程:
    1. 构建 DeepAgent（含 ``interrupt_before=["tools"]``）
    2. ``astream_events`` 驱动图执行，流式产出 token / todo_update 事件
    3. 流结束后检查是否在 tools 前中断
    4. 若中断：检查待执行工具是否危险
       - 危险 → yield approval_request → ``_await_approval`` → 通过则恢复 / 拒绝则终止
       - 安全 → 自动恢复
    5. 恢复后继续流式，循环直至图完成（``state.next`` 为空）

    Args:
        state: Router 状态（含 thread_id）。
        message: 用户消息。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    thread_id = state.get("thread_id", "")
    config: dict = {"configurable": {"thread_id": thread_id or "deep-default"}}
    inputs = {"messages": [{"role": "user", "content": message}]}

    # 1. 构建 agent
    try:
        agent = build_deep_agent(thread_id)
    except ValueError as exc:
        yield {"event": "error", "data": f"LLM 不可用: {exc}"}
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_deep_agent failed", thread_id=thread_id)
        yield {"event": "error", "data": f"DeepAgent 初始化失败: {exc}"}
        return

    # 2. 初始流式运行（可能中断在 tools 前）
    try:
        async for sse in _stream_agent_events(agent, inputs, config):
            yield sse
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.exception("deep agent stream failed", thread_id=thread_id)
        yield {"event": "error", "data": f"DeepAgent 执行失败: {exc}"}
        return

    # 3. 中断/恢复循环
    max_iterations = 50  # 安全上限，防止无限循环
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        if not _is_interrupted(agent, config):
            # 图已完成，退出循环
            break

        # 获取待执行的工具调用
        pending_calls = _get_pending_tool_calls(agent, config)
        if not pending_calls:
            # 无待执行工具调用，不应发生但安全退出
            logger.warning("interrupted but no pending tool calls", thread_id=thread_id)
            break

        # 检查是否有危险工具
        dangerous_calls = [
            tc for tc in pending_calls if tc.get("name") in DANGEROUS_TOOLS
        ]

        if dangerous_calls:
            # 4a. 危险工具 → yield approval_request，等待审批
            tool_call = dangerous_calls[0]
            yield _make_approval_event(tool_call)

            approval = await _await_approval(
                thread_id,
                poll_interval=_APPROVAL_POLL_INTERVAL,
                max_wait=_APPROVAL_MAX_WAIT,
            )

            if approval is False:
                yield {"event": "error", "data": "用户拒绝执行危险操作"}
                return
            if approval is None:
                yield {"event": "error", "data": "审批等待被中断，操作未执行"}
                return

            # 审批通过，继续恢复执行
            logger.info(
                "deep agent approval granted",
                thread_id=thread_id,
                tool=tool_call.get("name"),
            )
        # 4b. 安全工具 → 自动放行，无需审批

        # 5. 恢复执行：用 None 输入续跑（LangGraph interrupt_before 标准恢复方式）
        try:
            async for sse in _stream_agent_events(agent, None, config):
                yield sse
        except Exception as exc:  # noqa: BLE001 — SSE 兜底
            logger.exception("deep agent resume failed", thread_id=thread_id)
            yield {"event": "error", "data": f"DeepAgent 恢复失败: {exc}"}
            return

    if iteration >= max_iterations:
        logger.warning("deep agent hit max iterations", thread_id=thread_id)
        yield {"event": "error", "data": "DeepAgent 达到最大迭代上限"}
        return

    # done 事件由 run_router 统一 yield，此处不再重复


async def wait_for_approval(thread_id: str, timeout: float = 0.5) -> bool | None:
    """轮询 ``app.main._pending_approvals[thread_id]``，返回审批决定（单次非阻塞查询）。

    Args:
        thread_id: 会话 ID。
        timeout: 保留参数（单次查询不阻塞）。

    Returns:
        - ``True``：用户批准。
        - ``False``：用户拒绝。
        - ``None``：尚未决定。

    Note:
        延迟 import ``app.main`` 避免循环依赖（main.py 间接 import 本模块）。
    """
    from app import main  # noqa: WPS433 — 延迟 import 破环

    pending: dict[str, bool] = getattr(main, "_pending_approvals", {})
    if thread_id in pending:
        return pending.pop(thread_id)
    return None


async def _await_approval(
    thread_id: str,
    poll_interval: float = 0.3,
    max_wait: float = 300.0,
) -> bool | None:
    """阻塞轮询直至收到审批决定或达到 max_wait。

    - 收到 True/False → 返回该值。
    - 达到 max_wait 仍未决定 → 返回 None（调用方按"中断"处理，安全失败不放行）。
    - 检测到 abort 标志（``app.main._abort_flags``）→ 返回 None。
    """
    import asyncio

    from app import main  # noqa: WPS433

    elapsed = 0.0
    while elapsed < max_wait:
        # abort 检查
        abort_flags: dict[str, bool] = getattr(main, "_abort_flags", {})
        if abort_flags.get(thread_id):
            return None
        pending: dict[str, bool] = getattr(main, "_pending_approvals", {})
        if thread_id in pending:
            return pending.pop(thread_id)
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return None
