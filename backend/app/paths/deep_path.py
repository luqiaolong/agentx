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

import asyncio
import json
from typing import TYPE_CHECKING, Any, AsyncIterator

from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.llm import get_chat_model
from app.observability.logger import logger
from app.subagents.code_agent import _make_fs_tools
from app.subagents.rag_agent import _make_rag_tools
from app.subagents.web_agent import _make_web_tools

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


def build_deep_agent(
    thread_id: str,
    tools: list | None = None,
    profile_prompt: str = "",
) -> Any:
    """构造真实 DeepAgent 图。

    用 ``create_react_agent`` 构建 ReAct 子图，``interrupt_before=["tools"]`` 使图在
    执行任何工具前暂停。``MemorySaver`` 作为 agent 内部 checkpointer 支持
    interrupt/resume 循环（每次 ``run_deep_path`` 调用构建新 agent + 新 saver）。

    Args:
        thread_id: 会话 ID（用于工具的沙箱授权绑定）。
        tools: 可选，已构建的工具列表。若未传则内部调用 ``_make_deep_tools(thread_id)``。
            ``run_deep_path`` 可先构建工具集，复用于 dangerous 判断。
        profile_prompt: 可选，用户画像前缀，拼到 ``_DEEP_SYSTEM_PROMPT`` 前。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    model = get_chat_model(temperature=0.3, streaming=True)
    if tools is None:
        tools = _make_deep_tools(thread_id)
    checkpointer = MemorySaver()
    # T9：画像前缀拼到默认 system prompt 前（遵循与路径 A 一致的"画像优先"约定）
    system_prompt = _DEEP_SYSTEM_PROMPT
    if profile_prompt:
        system_prompt = f"{profile_prompt}\n{system_prompt}"
    return create_react_agent(
        model,
        tools,
        name="deep_agent",
        prompt=system_prompt,
        interrupt_before=["tools"],
        checkpointer=checkpointer,
    )


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


def _make_approval_event(tool_call: dict, thread_id: str) -> dict[str, str]:
    """构造 approval_request SSE 事件。

    MUST 包含 ``thread_id``：前端 ApprovalDialog 据此调
    ``POST /api/chat/approve {thread_id, approval}``，后端 ``_pending_approvals``
    按 thread_id 索引。缺失 thread_id 会导致审批提交后无法被 DeepAgent 消费，
    危险操作链路彻底断开。
    """
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
                "thread_id": thread_id,
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
    """驱动 ``agent.astream(stream_mode="values")``，尊重 ``interrupt_before``。

    ``astream_events`` 不尊重 ``interrupt_before``（会直接执行工具），
    MUST 用 ``astream`` + ``stream_mode="values"`` 才能在 tools 节点前暂停。

    SSE 事件映射:
    - AIMessage with tool_calls → todo_update（工具调用开始，done=False）
    - AIMessage without tool_calls → token（最终回复，strip_think 后一次性 yield）
    - ToolMessage → todo_update（工具完成，done=True）

    在 ``interrupt_before=["tools"]`` 处暂停时，最后一个 state 的 messages[-1]
    是 AIMessage（含 tool_calls），此处 yield todo_update 后流结束，
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
            # 工具执行完成
            yield _make_todo_event(f"工具 {last_msg.name} 完成", done=True)

        elif isinstance(last_msg, AIMessage):
            if getattr(last_msg, "tool_calls", None):
                # AIMessage with tool_calls → 工具调用开始
                for tc in last_msg.tool_calls:
                    tc_name = tc.get("name", tc.get("tool", "unknown")) if isinstance(tc, dict) else "unknown"
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
                if text:
                    yield {"event": "token", "data": text}


async def run_deep_path(
    state: RouterState,
    message: str,
    profile_prompt: str = "",
) -> AsyncIterator[dict]:
    """DeepAgent 路径 SSE 生成器（真实实现）。

    流程:
    1. 构建 DeepAgent（含 ``interrupt_before=["tools"]``）
    2. ``astream_events`` 驱动图执行，流式产出 token / todo_update 事件
    3. 流结束后检查是否在 tools 前中断
    4. 若中断：检查待执行工具是否危险
       - 危险 → yield approval_request → ``_await_approval`` → 通过则恢复 / 拒绝则终止
       - 安全 → 自动恢复
    5. 恢复后继续流式，循环直至图完成（``state.next`` 为空）
    6. T10：若 ``profile_auto_extract`` 开启，异步调 LLM 抽取画像并写入 profile.json
       （失败仅 warning，不阻塞 ``done`` 事件）

    Args:
        state: Router 状态（含 thread_id）。
        message: 用户消息。
        profile_prompt: 用户画像前缀，拼到 DeepAgent system prompt 前。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    thread_id = state.get("thread_id", "")
    config: dict = {"configurable": {"thread_id": thread_id or "deep-default"}}
    inputs = {"messages": [{"role": "user", "content": message}]}

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
        agent = build_deep_agent(thread_id, tools=agent_tools, profile_prompt=profile_prompt)
    except ValueError as exc:
        yield {"event": "error", "data": f"LLM 不可用: {exc}"}
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_deep_agent failed", thread_id=thread_id)
        yield {"event": "error", "data": f"DeepAgent 初始化失败: {exc}"}
        return

    # 运行时危险工具集合 = DANGEROUS_TOOLS 与已启用工具的交集
    # 安全关键：若 edit_file 被禁用，此处不含 edit_file，审批流不会误触发
    # MCP 工具：来自 trusted=False server 的工具也加入危险集合，触发审批
    enabled_tool_names = {
        _TOOL_NAME_MAP.get(t.name, t.name) for t in agent_tools
    }
    runtime_dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names

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

        # 检查是否有危险工具（运行时集合 = DANGEROUS_TOOLS ∩ 已启用工具）
        dangerous_calls = [
            tc for tc in pending_calls if tc.get("name") in runtime_dangerous
        ]

        if dangerous_calls:
            # 4a. 危险工具 → yield approval_request，等待审批
            tool_call = dangerous_calls[0]
            yield _make_approval_event(tool_call, thread_id)

            approval = await _await_approval(
                thread_id,
                poll_interval=_APPROVAL_POLL_INTERVAL,
                max_wait=float("inf")
                if get_settings().approval_max_wait == 0
                else get_settings().approval_max_wait,
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

    # T10：路径 C 流式结束后，若开关开启则异步触发画像抽取（失败仅 warning，不报错）
    # 不阻塞 done 事件：fire-and-forget（spec memory-management R10）
    if get_settings().profile_auto_extract:
        async def _do_extract() -> None:
            try:
                assistant_reply = _extract_last_assistant_reply(agent, config)
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


def _extract_last_assistant_reply(agent: Any, config: dict) -> str:
    """从 agent state 读取最后一条 AIMessage 的 content。

    用于 T10 画像抽取：取最终回复作为 LLM 抽取输入。
    跳过含 tool_calls 的 AIMessage（那些是工具调用而非最终回复）。
    """
    state = agent.get_state(config)
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
