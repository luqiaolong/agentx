"""DeepAgent 路径（路径 C）：deepagents + LangGraph，带危险工具中断。

- 用 ``langgraph.prebuilt.create_react_agent`` 构建 ReAct DeepAgent（与 subagents 一致）
- 工具集: filesystem 全部 + rag_retrieve + web_search
- ``interrupt_before=["tools"]``：调用任何工具前 LangGraph 暂停，SSE handler 检查待执行
  工具是否属于 ``DANGEROUS_TOOLS``，是则 yield approval_request 等用户审批，否则自动放行
- 审批恢复: ``_await_approval`` 轮询 ``_pending_approvals``，通过后以
  ``agent.astream_events(None, config)`` 续跑（LangGraph ``interrupt_before`` 的标准恢复方式）
- 使用共享的 ``AsyncSqliteSaver`` 作为 agent checkpointer，支持跨轮次历史 + interrupt/resume
- 权限模式：
  - ``standard``（默认）：危险工具走审批；只读 fs 工具访问未授权目录时弹扩展授权弹窗
  - ``full_trust``：会话内全量放行，不弹任何审批弹窗（系统关键目录仍拒绝）
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.llm import get_chat_model
from app.memory.checkpointer import get_async_checkpointer
from app.observability.logger import logger
from app.subagents.code_agent import _make_fs_tools
from app.subagents.rag_agent import _make_rag_tools
from app.subagents.web_agent import _make_web_tools
from app.utils.security import ApprovalDecision, get_sandbox

if TYPE_CHECKING:
    # RouterState 仅用于类型注解（``from __future__ import annotations`` 使注解
    # 在运行时为字符串），延迟到 TYPE_CHECKING 避免与 ``app.router.graph`` 形成循环导入：
    # graph.py 顶部 ``from app.paths.deep_path import run_deep_path``，
    # 而 deep_path.py 原本 ``from app.router.state import RouterState`` 触发
    # ``app.router.__init__`` 加载 graph.py，此时 deep_path.py 仅部分初始化 → ImportError。
    from app.router.state import RouterState

# 触发人工审批中断的工具集合：写操作与 shell 执行
# 模块级常量保持不变；运行时危险集合 = DANGEROUS_TOOLS ∩ 已启用工具名
DANGEROUS_TOOLS: set[str] = {"edit_file", "write_file", "shell_exec"}

# 工具名映射：将内部 tool 函数名映射到 settings.tools_enabled 的 key
# （_make_fs_tools 中 glob_files/grep_files 与 config key glob/grep 不一致）
_TOOL_NAME_MAP = {"glob_files": "glob", "grep_files": "grep"}

# DeepAgent 系统提示
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
)

# 审批轮询参数
_APPROVAL_POLL_INTERVAL = 0.3

# T10：异步画像抽取任务引用集合，防止被 GC 回收（asyncio 已知坑）
_extract_tasks: set[asyncio.Task] = set()

def _to_serializable(value: Any) -> Any:
    """将可能不可 JSON 序列化的值（如 LangChain Message 对象）转为可序列化类型。"""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_serializable(item) for item in value]
    if isinstance(value, dict):
        return {k: _to_serializable(v) for k, v in value.items()}
    # 对 LangChain BaseMessage 对象提取 content（常见不可序列化场景）
    if hasattr(value, "content"):
        return _to_serializable(value.content)
    # 兜底：转字符串
    return str(value)


__all__ = [
    "DANGEROUS_TOOLS",
    "build_deep_agent",
    "run_deep_path",
    "wait_for_approval",
]


def _make_deep_tools(thread_id: str) -> list:
    """构建 DeepAgent 内置工具集：只读 fs + 危险 fs + rag + web（同步部分）。

    安全设计：
    - 只读工具（read_file/list_dir/glob/grep）复用 ``_make_fs_tools``，与 subagent 一致。
    - 危险工具（write_file/edit_file）**仅** 在 DeepAgent 中暴露，由
      ``interrupt_before=["tools"]`` 触发审批，避免被 subagent 路径绕过。

    T4: 根据 ``get_settings().tools_enabled`` 过滤工具集。若工具被禁用，
    则不暴露给 LLM，且运行时 dangerous 集合也不含该工具（见 ``run_deep_path``）。
    工具名映射：``glob_files``→``glob``、``grep_files``→``grep``（与 subagents 一致）。

    MCP 工具由 ``_load_mcp_tools`` 异步加载并合并（见 ``run_deep_path``）。
    """
    from langchain_core.tools import tool

    from app.tools import filesystem as fs

    fs_tools = _make_fs_tools(thread_id)  # 只读工具集
    rag_tools = _make_rag_tools(thread_id)
    web_tools = _make_web_tools(thread_id)

    # 危险工具：仅在 DeepAgent 暴露，配合 interrupt_before 审批
    @tool
    async def write_file(path: str, content: str) -> str:
        """写入文本文件（覆盖）。"""
        return await fs.write_file(thread_id, path, content)

    @tool
    async def edit_file(path: str, old_text: str, new_text: str) -> str:
        """编辑文件：将 old_text 替换为 new_text（仅首次匹配）。"""
        return await fs.edit_file(thread_id, path, old_text, new_text)

    all_tools = [*fs_tools, write_file, edit_file, *rag_tools, *web_tools]

    # 根据 settings.tools_enabled 过滤；未配置的工具默认启用
    enabled = get_settings().tools_enabled
    return [
        t for t in all_tools
        if enabled.get(_TOOL_NAME_MAP.get(t.name, t.name), True)
    ]


async def _load_mcp_tools() -> tuple[list, set[str]]:
    """加载 MCP 工具，返回 (tools, untrusted_tool_names)。

    - ``tools``: MCP 工具列表（LangChain BaseTool），失败时为空列表。
    - ``untrusted_tool_names``: 来自 ``trusted=False`` server 的工具名集合，
      调用方应将其加入 ``runtime_dangerous``，触发 ``interrupt_before`` 审批流。

    失败降级：MCP 客户端未安装或连接失败时返回空列表，不影响 DeepAgent 主流程。
    """
    try:
        from app.mcp import get_mcp_manager

        manager = get_mcp_manager()
        return await manager.get_tools_with_trust()
    except Exception as exc:  # noqa: BLE001 — MCP 失败不阻塞主流程
        logger.warning("MCP tools load failed, skipping: {}", exc)
        return [], set()


async def build_deep_agent(
    thread_id: str,
    tools: list | None = None,
    profile_prompt: str = "",
    checkpointer: Any = None,
    scene_prompt: str | None = None,
) -> Any:
    """构造真实 DeepAgent 图。

    用 ``create_react_agent`` 构建 ReAct 子图，``interrupt_before=["tools"]`` 使图在
    执行任何工具前暂停。使用共享的 ``AsyncSqliteSaver`` 作为 checkpointer，支持
    跨轮次历史恢复与 interrupt/resume 循环。

    Args:
        thread_id: 会话 ID（用于工具的沙箱授权绑定）。
        tools: 可选，已构建的工具列表。若未传则内部调用 ``_make_deep_tools(thread_id)``。
            ``run_deep_path`` 可先构建工具集，复用于 dangerous 判断。
        profile_prompt: 可选，用户画像前缀，拼到 ``_DEEP_SYSTEM_PROMPT`` 前。
        checkpointer: 可选，共享的 LangGraph checkpointer。若未传则用
            ``await get_async_checkpointer()`` 获取全局 ``AsyncSqliteSaver`` 单例。
        scene_prompt: 可选场景 prompt，非空时覆盖 ``_DEEP_SYSTEM_PROMPT``。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    # 延迟 import 避免与 app.router.graph 形成循环导入
    # （graph.py 顶部 from app.paths.deep_path import run_deep_path）
    from app.router.graph import resolve_system_prompt

    model = get_chat_model(temperature=0.3, streaming=True)
    if tools is None:
        tools = _make_deep_tools(thread_id)
    if checkpointer is None:
        # MUST await：get_async_checkpointer 是 async def，不 await 会传入 coroutine
        # 导致 create_react_agent 报 "Invalid checkpointer ... Received coroutine"
        checkpointer = await get_async_checkpointer()
    # T9：画像前缀拼到最前；scene_prompt 覆盖 _DEEP_SYSTEM_PROMPT（场景切换器注入）
    system_prompt = resolve_system_prompt(
        default=_DEEP_SYSTEM_PROMPT,
        scene_prompt=scene_prompt,
        skill_extra=profile_prompt or None,
    )
    return create_react_agent(
        model,
        tools,
        name="deep_agent",
        prompt=system_prompt,
        interrupt_before=["tools"],
        checkpointer=checkpointer,
    )


async def _get_pending_tool_calls(agent: Any, config: dict) -> list[dict]:
    """从 agent 状态中提取待执行的工具调用列表。

    当图在 ``interrupt_before=["tools"]`` 处暂停时，最后一条消息是 AIMessage，
    其 ``tool_calls`` 属性包含待执行的工具调用。

    MUST 使用 ``aget_state``（异步接口）：agent 的 checkpointer 是
    ``AsyncSqliteSaver``，在主线程同步调用 ``get_state`` 会抛
    "Synchronous calls to AsyncSqliteSaver are only allowed from a different thread"
    （截图 bug 根因）。同步 ``get_state`` 在主线程会阻塞事件循环；切换到
    ``aget_state`` 走 aiosqlite 异步通道，避免该异常并与其他 SSE 异步逻辑一致。
    """
    state = await agent.aget_state(config)
    if not state or not state.values:
        return []
    messages = state.values.get("messages", [])
    if not messages:
        return []
    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []
    return list(tool_calls)


async def _is_interrupted(agent: Any, config: dict) -> bool:
    """检查 agent 是否在 interrupt 处暂停（next 含 "tools"）。

    MUST 使用 ``aget_state``（异步接口）——见 ``_get_pending_tool_calls`` 注释。
    """
    state = await agent.aget_state(config)
    if not state or not state.next:
        return False
    return "tools" in state.next


def _collect_unpaired_tool_call_ids(messages: list) -> list[str]:
    """扫描消息列表，返回所有未配对 tool_call 的 id。

    遍历全部 AIMessage 的 ``tool_calls``，与已有 ``ToolMessage.tool_call_id``
    比对，返回缺失配对的 id 列表。用于在 checkpoint 异常残留或 inputs
    含历史未配对 tool_calls 时，补齐 ToolMessage 以通过 LangGraph 的
    ``_validate_chat_history`` 校验。

    MUST 扫描全部消息——只看最后一条会漏掉历史中更早的未配对 AIMessage
    （例如中断后 checkpoint 已写入新 HumanMessage，但前面的 AIMessage
    仍未配对）。
    """
    from langchain_core.messages import AIMessage, ToolMessage

    existing_ids = {
        getattr(m, "tool_call_id", None)
        for m in messages
        if isinstance(m, ToolMessage)
    }
    missing: list[str] = []
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            if tc_id and tc_id not in existing_ids:
                missing.append(tc_id)
    return missing


def _sanitize_message_history(messages: list, error_text: str) -> list:
    """为消息列表中所有未配对的 tool_calls 补齐 ToolMessage。

    用于在 ``agent.astream(inputs, ...)`` 前净化 inputs：当 checkpoint
    被 DELETE 清空、或历史含中断残留的未配对 AIMessage 时，
    ``_inject_tool_error_messages``（作用于 checkpoint state）无法覆盖，
    需要在此直接对 inputs 做修复，确保 ``_validate_chat_history`` 通过。
    """
    from langchain_core.messages import ToolMessage

    missing_ids = _collect_unpaired_tool_call_ids(messages)
    if not missing_ids:
        return messages
    return [
        *messages,
        *(
            ToolMessage(content=error_text, tool_call_id=tc_id)
            for tc_id in missing_ids
        ),
    ]


async def _inject_tool_error_messages(agent: Any, config: dict, error_text: str) -> None:
    """为 checkpoint 中未配对的 tool_calls 注入 ToolMessage，防止 INVALID_CHAT_HISTORY。

    当 ``interrupt_before=["tools"]`` 中断后，如果恢复执行时抛异常（如 tool 失败、
    进程崩溃），checkpoint 中 AIMessage 有 ``tool_calls`` 但没有对应的 ``ToolMessage``。
    用户再次发消息时，LangGraph 校验历史消息会抛出 ``INVALID_CHAT_HISTORY``。

    本函数在异常退出前调用：扫描 state 中**全部** AIMessage 的 ``tool_calls``
    （不只为最后一条，因为中断后可能已有新 HumanMessage 追加到末尾），
    为每个缺失的 tool_call 构造 ``ToolMessage(content=error_text, tool_call_id=...)``，
    通过 ``aupdate_state`` 追加到 messages 列表，保持配对关系。

    Args:
        agent: 编译后的 CompiledStateGraph。
        config: 含 ``configurable.thread_id`` 的字典。
        error_text: ToolMessage 的 content，描述失败原因。
    """
    from langchain_core.messages import ToolMessage

    try:
        state = await agent.aget_state(config)
        if not state or not state.values:
            return
        messages = list(state.values.get("messages", []))
        if not messages:
            return

        missing_ids = _collect_unpaired_tool_call_ids(messages)
        if not missing_ids:
            return

        new_messages = [
            ToolMessage(content=error_text, tool_call_id=tc_id)
            for tc_id in missing_ids
        ]
        await agent.aupdate_state(config, {"messages": new_messages})
        logger.info(
            "injected_tool_error_messages",
            thread_id=config.get("configurable", {}).get("thread_id", ""),
            count=len(new_messages),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("inject_tool_error_messages failed", error=str(exc))


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


def _make_approval_event(
    tool_call: dict,
    thread_id: str,
    kind: str = "dangerous_tool",
    requested_path: str | None = None,
    writable: bool = False,
) -> dict[str, str]:
    """构造 approval_request SSE 事件。

    MUST 包含 ``thread_id``：前端 ApprovalDialog 据此调
    ``POST /api/chat/approve {thread_id, approval}``，后端 ``_pending_approvals``
    按 thread_id 索引。缺失 thread_id 会导致审批提交后无法被 DeepAgent 消费，
    危险操作链路彻底断开。

    Args:
        tool_call: 工具调用 dict（含 name/args）。
        thread_id: 会话 ID。
        kind: 审批类型，"dangerous_tool"（默认）或 "directory_extension"。
        requested_path: directory_extension 时填，目标路径。
        writable: directory_extension 时填，是否需要写入。
    """
    name = tool_call.get("name", "unknown")
    args = tool_call.get("args", {})
    redacted_args = _redact_args(name, args if isinstance(args, dict) else {})

    # 生成预览描述
    if kind == "directory_extension":
        preview = f"AI 想访问目录: {requested_path}"
    elif name == "write_file":
        path = args.get("path", "?") if isinstance(args, dict) else "?"
        preview = f"将写入文件: {path}"
    elif name == "edit_file":
        path = args.get("path", "?") if isinstance(args, dict) else "?"
        preview = f"将编辑文件: {path}"
    elif name == "shell_exec":
        preview = "将执行系统命令"
    else:
        preview = f"将执行工具: {name}"

    data: dict[str, Any] = {
        "thread_id": thread_id,
        "tool_name": name,
        "args": redacted_args,
        "preview": preview,
        "kind": kind,
    }
    if kind == "directory_extension":
        data["requestedPath"] = requested_path or ""
        data["writable"] = writable

    return {
        "event": "approval_request",
        "data": json.dumps(data, ensure_ascii=False),
    }


# 只读 fs 工具名集合（用于 directory_extension 预检查）
_READ_ONLY_FS_TOOLS: frozenset[str] = frozenset(
    {"read_file", "list_dir", "glob_files", "grep_files", "glob", "grep"}
)


def _extract_paths_from_tool_call(tool_call: dict) -> list[str]:
    """从工具调用参数中提取路径字符串（用于 directory_extension 预检查）。

    支持的工具：
    - read_file / write_file / edit_file / list_dir / grep: args["path"]
    - glob / glob_files: args["pattern"] → 取 _glob_base
    """
    from app.tools.filesystem import _glob_base

    name = tool_call.get("name", "")
    args = tool_call.get("args", {})
    if not isinstance(args, dict):
        return []
    if name in ("read_file", "write_file", "edit_file", "list_dir", "grep", "grep_files"):
        p = args.get("path")
        return [str(p)] if p else []
    if name in ("glob", "glob_files"):
        pattern = args.get("pattern", "")
        if not pattern:
            return []
        base = _glob_base(str(pattern))
        return [base] if base else []
    return []


def _is_read_only_fs_tool(name: str) -> bool:
    """是否为只读 fs 工具（用于 directory_extension 预检查）。"""
    return name in _READ_ONLY_FS_TOOLS


def _make_todo_event(text: str, done: bool = False) -> dict[str, str]:
    """构造 todo_update SSE 事件。"""
    return {
        "event": "todo_update",
        "data": json.dumps(
            {"todos": [{"text": text, "done": done}]},
            ensure_ascii=False,
        ),
    }


def _make_tool_call_event(tc_id: str, name: str, args: Any) -> dict[str, str]:
    """构造 tool_call SSE 事件（source 固定为 "deep"）。"""
    return {
        "event": "tool_call",
        "data": json.dumps(
            {
                "id": tc_id,
                "name": name,
                "args": args if args is not None else {},
                "source": "deep",
            },
            ensure_ascii=False,
        ),
    }


def _make_tool_result_event(tc_id: str, name: str, result: Any) -> dict[str, str]:
    """构造 tool_result SSE 事件（source 固定为 "deep"）。"""
    # result 可能是 LangChain ToolMessage / BaseMessage 对象，先转为可序列化类型
    serializable_result = _to_serializable(result)
    return {
        "event": "tool_result",
        "data": json.dumps(
            {
                "id": tc_id,
                "name": name,
                "result": serializable_result,
                "source": "deep",
            },
            ensure_ascii=False,
            default=str,
        ),
    }


async def _stream_agent_events(
    agent: Any, inputs: Any, config: dict
) -> AsyncIterator[dict[str, str]]:
    """驱动 ``agent.astream(stream_mode="values")``，尊重 ``interrupt_before``。

    ``astream_events`` 不尊重 ``interrupt_before``（会直接执行工具），
    MUST 用 ``astream`` + ``stream_mode="values"`` 才能在 tools 节点前暂停。

    SSE 事件映射（spec D1 + T5 扩展）:
    - AIMessage with tool_calls → ``tool_call`` SSE（含 id/name/args/source="deep"）
      + ``todo_update``（任务级进度，与 tool_call 事件并存，语义不同）
    - AIMessage without tool_calls → ``token``（最终回复，strip_think 后一次性 yield）
    - ToolMessage → ``tool_result`` SSE（含 id/name/result/source="deep"）
      + ``todo_update``（标记完成）

    在 ``interrupt_before=["tools"]`` 处暂停时，最后一个 state 的 messages[-1]
    是 AIMessage（含 tool_calls），此处 yield tool_call + todo_update 后流结束，
    调用方 ``_is_interrupted`` 返回 True 进入审批流程。
    """
    from langchain_core.messages import AIMessage, ToolMessage
    from app.utils.text import strip_think

    async for state in agent.astream(inputs, config=config, stream_mode="values"):
        messages = state.get("messages", []) if hasattr(state, "get") else []
        if not messages:
            continue
        last_msg = messages[-1]

        if isinstance(last_msg, ToolMessage):
            # 工具执行完成 → tool_result SSE + todo_update（任务级进度）
            tool_name = getattr(last_msg, "name", "") or ""
            tool_call_id = getattr(last_msg, "tool_call_id", "") or str(uuid4())
            content = getattr(last_msg, "content", "")
            if isinstance(content, list):
                content = "".join(
                    block if isinstance(block, str)
                    else block.get("text", "") if isinstance(block, dict)
                    else ""
                    for block in content
                )
            yield _make_tool_result_event(tool_call_id, tool_name, content)
            yield _make_todo_event(f"工具 {tool_name} 完成", done=True)

        elif isinstance(last_msg, AIMessage):
            if getattr(last_msg, "tool_calls", None):
                # AIMessage with tool_calls → 先展示思考计划，再 yield tool_call
                # LLM 的 content 通常包含 <think>... 计划 ...</think> 或纯文本计划
                content = last_msg.content
                if isinstance(content, list):
                    content = "".join(
                        block if isinstance(block, str)
                        else block.get("text", "") if isinstance(block, dict)
                        else ""
                        for block in content
                    )
                plan_text = str(content) if content else ""
                # 防御性剥离：部分 OpenAI 兼容推理模型（典型如 MiniMax-M3）在
                # tool_calls 字段已正确填充时，仍会在 content 中重复输出 XML 格式
                # 工具调用文本。剥离后再 split_think，避免 XML 块泄露到 reasoning 事件。
                from app.utils.text import split_think, strip_tool_call_xml
                plan_text = strip_tool_call_xml(plan_text)
                reasoning, visible = split_think(plan_text)
                # 优先展示 reasoning（think 块内），其次展示 visible（非 think 内容）
                display_plan = reasoning.strip() if reasoning.strip() else visible.strip()
                if display_plan:
                    # yield reasoning 事件供前端展示思考过程
                    yield {
                        "event": "reasoning",
                        "data": json.dumps(
                            {"content": display_plan, "source": "deep"},
                            ensure_ascii=False,
                        ),
                    }
                # 再 yield 每个 tool_call
                for tc in last_msg.tool_calls:
                    if isinstance(tc, dict):
                        tc_name = tc.get("name", tc.get("tool", "unknown"))
                        tc_args = tc.get("args", {}) or {}
                        tc_id = tc.get("id") or str(uuid4())
                    else:
                        tc_name = getattr(tc, "name", "unknown")
                        tc_args = getattr(tc, "args", {}) or {}
                        tc_id = getattr(tc, "id", None) or str(uuid4())
                    yield _make_tool_call_event(tc_id, tc_name, tc_args)
                    yield _make_todo_event(f"调用工具: {tc_name}", done=False)
            elif getattr(last_msg, "content", ""):
                # AIMessage without tool_calls → 最终回复
                content = last_msg.content
                if isinstance(content, list):
                    # 兼容 list 内容块
                    content = "".join(
                        block if isinstance(block, str)
                        else block.get("text", "") if isinstance(block, dict)
                        else ""
                        for block in content
                    )
                text = strip_think(content if isinstance(content, str) else str(content))
                # 防御性剥离：避免 XML 格式工具调用文本泄露到最终回复 token 流。
                from app.utils.text import strip_tool_call_xml
                text = strip_tool_call_xml(text)
                if text:
                    yield {"event": "token", "data": text}


async def run_deep_path(
    state: RouterState,
    message: str,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
    scene_prompt: str | None = None,
) -> AsyncIterator[dict]:
    """DeepAgent 路径 SSE 生成器（真实实现）。

    流程:
    1. 构建 DeepAgent（含 ``interrupt_before=["tools"]``）
    2. 若 ``permission_mode == "full_trust"``：``sandbox.set_full_trust(thread_id, True)``
    3. ``astream_events`` 驱动图执行，流式产出 token / todo_update 事件
    4. 流结束后检查是否在 tools 前中断
    5. 若中断：按权限模式处理待执行工具
       - full_trust：直接放行所有工具（包括危险工具）
       - standard：
         - 危险工具 → yield approval_request(kind=dangerous_tool) → 等待审批
         - 只读 fs 工具访问未授权目录 → yield approval_request(kind=directory_extension)
           → 等待决策（once/session/deny）
         - 其他 → 自动放行
    6. 恢复后继续流式，循环直至图完成（``state.next`` 为空）
    7. 清理：``sandbox.set_full_trust(thread_id, False)`` + ``sandbox.clear_temp(thread_id)``
    8. T10：若 ``profile_auto_extract`` 开启，异步调 LLM 抽取画像并写入 profile.json

    Args:
        state: Router 状态（含 thread_id）。
        message: 用户消息。
        profile_prompt: 用户画像前缀，拼到 DeepAgent system prompt 前。
        history: 历史 messages 列表（已截断），拼到 inputs 前。
        permission_mode: 权限模式，"workspace"（默认，仅当前工作区）或 "full_trust"。
        scene_prompt: 可选场景 prompt，透传给 build_deep_agent。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    thread_id = state.get("thread_id", "")
    config: dict = {"configurable": {"thread_id": thread_id or "deep-default"}}
    sandbox = get_sandbox()
    is_full_trust = permission_mode == "full_trust"

    # full_trust 模式：设置 sandbox 标志，fs 工具自动放行
    if is_full_trust:
        sandbox.set_full_trust(thread_id, True)
        logger.info("deep agent full_trust mode enabled", thread_id=thread_id)

    # 构建完整 inputs：history + 当前消息
    # history 已是 BaseMessage 列表，create_react_agent 的 messages channel 接受 BaseMessage
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}

    # 1. 构建 agent（先构建工具集，便于计算运行时 dangerous 集合）
    try:
        agent_tools = _make_deep_tools(thread_id)
        # 异步加载 MCP 工具并合并到 DeepAgent 工具集
        # MCP 工具仅暴露给 DeepAgent（路径 C），subagent 不暴露（安全红线）
        mcp_tools, mcp_untrusted_names = await _load_mcp_tools()
        if mcp_tools:
            agent_tools.extend(mcp_tools)
            logger.info(
                "MCP tools merged into DeepAgent",
                thread_id=thread_id,
                count=len(mcp_tools),
                untrusted=len(mcp_untrusted_names),
            )
        agent = await build_deep_agent(
            thread_id,
            tools=agent_tools,
            profile_prompt=profile_prompt,
            scene_prompt=scene_prompt,
        )
    except ValueError as exc:
        yield {"event": "error", "data": f"LLM 不可用: {exc}"}
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_deep_agent failed", thread_id=thread_id)
        yield {"event": "error", "data": f"DeepAgent 初始化失败: {exc}"}
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return

    # 运行时危险工具集合 = DANGEROUS_TOOLS 与已启用工具的交集
    # 安全关键：若 edit_file 被禁用，此处不含 edit_file，审批流不会误触发
    # MCP 工具：来自 trusted=False server 的工具也加入危险集合，触发审批
    enabled_tool_names = {
        _TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools
    }
    runtime_dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names

    # 1b. 防御性清理：若之前异常退出导致 checkpoint 中残留未配对的
    # tool_calls，首次 astream 会因 _validate_chat_history 抛 INVALID_CHAT_HISTORY。
    # 此处提前注入 ToolMessage 修复，确保历史消息一致性。
    await _inject_tool_error_messages(
        agent, config, "上次操作未正常完成，已自动清理状态"
    )

    # 1c. 净化 inputs：当 checkpoint 被 DELETE 清空（用户清空历史）或
    # history 含中断残留的未配对 AIMessage 时，_inject_tool_error_messages
    # 因 state 为空直接返回，但 inputs["messages"] 仍含未配对 tool_calls，
    # _validate_chat_history 仍会抛 INVALID_CHAT_HISTORY。此处直接对 inputs
    # 补齐 ToolMessage，确保 astream 不会因校验失败而中断。
    inputs["messages"] = _sanitize_message_history(
        inputs["messages"], "上次操作未正常完成，已自动清理状态"
    )

    # 2. 初始流式运行（可能中断在 tools 前）
    try:
        async for sse in _stream_agent_events(agent, inputs, config):
            yield sse
    except Exception as exc:  # noqa: BLE001 — SSE 兜底
        logger.exception("deep agent stream failed", thread_id=thread_id)
        await _inject_tool_error_messages(agent, config, f"DeepAgent 执行失败: {exc}")
        yield {"event": "error", "data": f"DeepAgent 执行失败: {exc}"}
        if is_full_trust:
            sandbox.set_full_trust(thread_id, False)
        return

    # 3. 中断/恢复循环
    max_iterations = 50  # 安全上限，防止无限循环
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        if not await _is_interrupted(agent, config):
            # 图已完成，退出循环
            break

        # 获取待执行的工具调用
        pending_calls = await _get_pending_tool_calls(agent, config)
        if not pending_calls:
            # 无待执行工具调用，不应发生但安全退出
            logger.warning("interrupted but no pending tool calls", thread_id=thread_id)
            break

        # full_trust 模式：所有工具直接放行，不弹审批
        if is_full_trust:
            # 清理 once 临时授权（防御性，full_trust 模式理论上不用 temp）
            sandbox.clear_temp(thread_id)
            try:
                async for sse in _stream_agent_events(agent, None, config):
                    yield sse
            except Exception as exc:  # noqa: BLE001
                logger.exception("deep agent resume failed", thread_id=thread_id)
                await _inject_tool_error_messages(agent, config, f"DeepAgent 恢复失败: {exc}")
                yield {"event": "error", "data": f"DeepAgent 恢复失败: {exc}"}
                sandbox.set_full_trust(thread_id, False)
                return
            continue

        # standard 模式：按工具类型处理
        # 检查是否有危险工具（运行时集合 = DANGEROUS_TOOLS ∩ 已启用工具）
        # 优化：若危险工具的目标路径已授权写入，则跳过审批（工作区内免审批）
        dangerous_calls = []
        for tc in pending_calls:
            name = tc.get("name", "")
            if name not in runtime_dangerous:
                continue
            # 提取路径并检查是否已授权写入
            paths = _extract_paths_from_tool_call(tc)
            # 无路径参数的工具（如 shell_exec）或路径未授权 → 需审批
            if not paths:
                dangerous_calls.append(tc)
                continue
            # 所有路径均已授权写入 → 跳过审批
            all_authorized = all(
                sandbox.is_path_authorized(thread_id, p, writable=True)
                for p in paths
            )
            if not all_authorized:
                dangerous_calls.append(tc)

        if dangerous_calls:
            # 4a. 危险工具 → yield approval_request(dangerous_tool)，等待审批
            tool_call = dangerous_calls[0]
            yield _make_approval_event(tool_call, thread_id, kind="dangerous_tool")

            decision = await _await_approval(
                thread_id,
                poll_interval=_APPROVAL_POLL_INTERVAL,
                max_wait=float("inf")
                if get_settings().approval_max_wait == 0
                else get_settings().approval_max_wait,
            )

            if decision is None or not decision.approved:
                yield {"event": "error", "data": "用户拒绝执行危险操作"}
                await _inject_tool_error_messages(
                    agent, config, "用户拒绝执行危险操作"
                )
                sandbox.set_full_trust(thread_id, False)
                return

            # 审批通过，继续恢复执行
            logger.info(
                "deep agent approval granted",
                thread_id=thread_id,
                tool=tool_call.get("name"),
            )
        else:
            # 4b. 非危险工具：检查只读 fs 工具是否越界（directory_extension）
            extension_handled = await _handle_directory_extension(
                pending_calls, thread_id, sandbox
            )
            for evt in extension_handled.events:
                yield evt
            if extension_handled.denied:
                yield {"event": "error", "data": "用户拒绝访问该目录"}
                await _inject_tool_error_messages(
                    agent, config, "用户拒绝访问该目录"
                )
                sandbox.set_full_trust(thread_id, False)
                return
            if extension_handled.timed_out:
                yield {"event": "error", "data": "目录授权等待被中断，操作未执行"}
                await _inject_tool_error_messages(
                    agent, config, "目录授权等待被中断，操作未执行"
                )
                sandbox.set_full_trust(thread_id, False)
                return

        # 5. 恢复执行：用 None 输入续跑（LangGraph interrupt_before 标准恢复方式）
        try:
            async for sse in _stream_agent_events(agent, None, config):
                yield sse
        except Exception as exc:  # noqa: BLE001 — SSE 兜底
            logger.exception("deep agent resume failed", thread_id=thread_id)
            await _inject_tool_error_messages(agent, config, f"DeepAgent 恢复失败: {exc}")
            yield {"event": "error", "data": f"DeepAgent 恢复失败: {exc}"}
            sandbox.set_full_trust(thread_id, False)
            return

        # 6. 清理 once 临时授权（每次工具调用恢复后清理）
        sandbox.clear_temp(thread_id)

    if iteration >= max_iterations:
        logger.warning("deep agent hit max iterations", thread_id=thread_id)
        yield {"event": "error", "data": "DeepAgent 达到最大迭代上限"}
        await _inject_tool_error_messages(
            agent, config, "DeepAgent 达到最大迭代上限"
        )
        sandbox.set_full_trust(thread_id, False)
        return

    # 7. 清理 full_trust 标志（防御性）
    if is_full_trust:
        sandbox.set_full_trust(thread_id, False)

    # T10：路径 C 流式结束后，若开关开启则异步触发画像抽取（失败仅 warning，不报错）
    # 不阻塞 done 事件：fire-and-forget（spec memory-management R10）
    if get_settings().profile_auto_extract:
        async def _do_extract() -> None:
            try:
                assistant_reply = await _extract_last_assistant_reply(agent, config)
                if assistant_reply:
                    from app.memory.profile_store import upsert_from_llm

                    entries = await _extract_profile_via_llm(message, assistant_reply)
                    upsert_from_llm(entries)
                    logger.info("profile auto extracted", count=len(entries))
            except Exception as exc:  # noqa: BLE001 — 抽取失败不报错
                logger.warning("profile auto extract failed", error=str(exc))

        task = asyncio.create_task(_do_extract())
        _extract_tasks.add(task)
        task.add_done_callback(_extract_tasks.discard)

    # done 事件由 run_router 统一 yield，此处不再重复


async def _extract_last_assistant_reply(agent: Any, config: dict) -> str:
    """从 agent state 读取最后一条 AIMessage 的 content。

    用于 T10 画像抽取：取最终回复作为 LLM 抽取输入。
    跳过含 tool_calls 的 AIMessage（那些是工具调用而非最终回复）。

    MUST 使用 ``aget_state``（异步接口）——见 ``_get_pending_tool_calls`` 注释。
    """
    state = await agent.aget_state(config)
    if not state or not state.values:
        return ""
    messages = state.values.get("messages", [])
    if not messages:
        return ""
    from langchain_core.messages import AIMessage

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = msg.content
            if isinstance(content, list):
                # 兼容 list 内容块（OpenAI vision 等多模态返回）
                return "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            return str(content)
    return ""


async def _extract_profile_via_llm(message: str, assistant_reply: str) -> list[dict]:
    """调 LLM 抽取画像条目。

    Prompt 引导 LLM 抽取「值得跨会话记住的事实」：用户偏好、项目约定、重要事实。
    输出 JSON ``{"entries": [{"key", "category", "content"}]}``，无内容返回空列表。

    失败时返回空列表（调用方按"无可抽取"处理，不报错）。
    """
    prompt = (
        "你是一个用户画像抽取器。分析以下对话，抽取\"值得跨会话记住的事实\"：\n"
        "- 用户偏好（如\"喜欢简洁回复\"、\"用 TypeScript\"）\n"
        "- 项目约定（如\"项目用 FastAPI\"、\"测试用 pytest\"）\n"
        "- 重要事实（如\"用户是前端工程师\"、\"工作日 9-18 点在线\"）\n\n"
        f"对话：\n用户: {message}\n助手: {assistant_reply}\n\n"
        '输出 JSON: {{"entries": [{{"key": "...", "category": "...", "content": "..."}}]}}\n'
        '若无可抽取内容，返回 {{"entries": []}}。不要编造，只抽取明确的事实。'
    )
    llm = get_chat_model(temperature=0.0)
    response = await llm.ainvoke(prompt)
    text = response.content if hasattr(response, "content") else str(response)
    import re

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group())
        return data.get("entries", []) if isinstance(data, dict) else []
    except json.JSONDecodeError:
        return []


async def wait_for_approval(thread_id: str, timeout: float = 0.5) -> ApprovalDecision | None:
    """轮询 ``app.main._pending_approvals[thread_id]``，返回审批决策（单次非阻塞查询）。

    Args:
        thread_id: 会话 ID。
        timeout: 保留参数（单次查询不阻塞）。

    Returns:
        - ``ApprovalDecision``：用户已决策。
        - ``None``：尚未决定。

    Note:
        延迟 import ``app.main`` 避免循环依赖（main.py 间接 import 本模块）。
    """
    from app import main  # noqa: WPS433 — 延迟 import 破环

    pending: dict[str, ApprovalDecision] = getattr(main, "_pending_approvals", {})
    if thread_id in pending:
        return pending.pop(thread_id)
    return None


async def _await_approval(
    thread_id: str,
    poll_interval: float = 0.3,
    max_wait: float = 300.0,
) -> ApprovalDecision | None:
    """阻塞轮询直至收到审批决策或达到 max_wait。

    - 收到决策 → 返回 ``ApprovalDecision``。
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
        pending: dict[str, ApprovalDecision] = getattr(main, "_pending_approvals", {})
        if thread_id in pending:
            return pending.pop(thread_id)
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return None


@dataclass
class _ExtensionResult:
    """``_handle_directory_extension`` 的返回值。"""

    events: list[dict[str, str]]
    denied: bool = False
    timed_out: bool = False


async def _handle_directory_extension(
    pending_calls: list[dict],
    thread_id: str,
    sandbox: Any,
) -> _ExtensionResult:
    """处理只读 fs 工具的目录越界扩展授权。

    遍历 pending_calls，对每个只读 fs 工具提取路径，检查是否已授权。
    未授权的工具调用 yield approval_request(kind=directory_extension)，等待用户决策：
    - once：``sandbox.authorize_temp`` 临时授权
    - session：``sandbox.authorize`` 持久授权
    - deny：返回 denied=True

    Args:
        pending_calls: 待执行的工具调用列表。
        thread_id: 会话 ID。
        sandbox: SessionSandbox 实例。

    Returns:
        _ExtensionResult：含 events（需 yield 的 SSE 事件）+ denied/timed_out 标志。
    """
    events: list[dict[str, str]] = []
    for tc in pending_calls:
        name = tc.get("name", "")
        if not _is_read_only_fs_tool(name):
            continue
        paths = _extract_paths_from_tool_call(tc)
        if not paths:
            continue
        for path in paths:
            if sandbox.is_path_authorized(thread_id, path, writable=False):
                continue
            # 越界 → 弹扩展授权
            events.append(
                _make_approval_event(
                    tc, thread_id, kind="directory_extension",
                    requested_path=path, writable=False,
                )
            )
            decision = await _await_approval(
                thread_id,
                poll_interval=_APPROVAL_POLL_INTERVAL,
                max_wait=float("inf")
                if get_settings().approval_max_wait == 0
                else get_settings().approval_max_wait,
            )
            if decision is None:
                return _ExtensionResult(events=events, timed_out=True)
            if decision.decision == "deny" or not decision.approved:
                return _ExtensionResult(events=events, denied=True)
            if decision.decision == "once":
                try:
                    sandbox.authorize_temp(thread_id, path, writable=False)
                except ValueError as exc:
                    logger.warning("authorize_temp failed", path=path, error=str(exc))
                    return _ExtensionResult(events=events, denied=True)
            elif decision.decision == "session":
                try:
                    sandbox.authorize(thread_id, path, writable=False)
                except ValueError as exc:
                    logger.warning("authorize session failed", path=path, error=str(exc))
                    return _ExtensionResult(events=events, denied=True)
            # approve（旧 dangerous_tool 决策类型）不应当出现在 directory_extension，
            # 防御性按 once 处理
            elif decision.decision == "approve":
                try:
                    sandbox.authorize_temp(thread_id, path, writable=False)
                except ValueError:
                    pass
    return _ExtensionResult(events=events)
