"""子代理公共基类与工具函数。

提供内置子代理（rag/web）共享的：
- ``extract_text``：流式 chunk 文本提取
- ``THINK_PROMPT_SUFFIX``：思考过程提示常量
- ``run_react_agent_stream``：标准化事件流转换
- ``build_builtin_subagent`` / ``run_builtin_subagent``：子代理构建/运行工厂

工具工厂（``make_rag_tools`` / ``make_web_tools``）已下沉到
``app.tools.subagent_tools``，供 ``app.deepagent.tool_assembly`` 与本模块
共同复用，消除 ``deepagent.tool_assembly ↔ deepagent.subagents`` 循环依赖。

内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由 ``AuthorizedLocalShellBackend``
自动注入，子代理通过 ``create_agent(excluded_tools=FORBIDDEN_SUBAGENT_TOOLS)`` 过滤写工具。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.config import get_settings
from app.llm import get_chat_model
from app.utils.text import extract_chunk_text

__all__ = [
    "extract_text",
    "THINK_PROMPT_SUFFIX",
    "run_react_agent_stream",
    "build_builtin_subagent",
    "run_builtin_subagent",
]


# 子代理思考过程提示：要求模型在思考时包裹 think 标签，供前端展示 reasoning block
# 用 chr(60)/chr(62) 拼接 <> 避免在源码字符串中直接出现标签导致渲染层歧义
_THINK_OPEN = chr(60) + "think" + chr(62)
_THINK_CLOSE = chr(60) + "/think" + chr(62)
THINK_PROMPT_SUFFIX = (
    "\n\n在调用工具前，请先用 " + _THINK_OPEN + ".." + _THINK_CLOSE + " 标签包裹你的思考过程，"
    "例如：" + _THINK_OPEN + "我需要查看相关信息来回答这个问题" + _THINK_CLOSE + "。"
    "这样用户可以看到你的推理过程。"
    "\n\n重要：思考标签外不要输出任何可见文本。所有可见内容必须在工具调用完成后，"
    "根据工具返回结果再输出。"
)


def extract_text(chunk: Any) -> str:
    """从流式 chunk 中提取纯文本内容（兼容 str / list 内容块）。

    委托给 ``app.utils.text.extract_chunk_text(strip=False)``——
    保留原始文本（不剥离 think 块），由下游 ``ThinkFilter`` 流式处理。
    保留本函数是为了向后兼容（``__all__`` 导出 + 外部可能引用）。
    """
    return extract_chunk_text(chunk, strip=False)


async def run_react_agent_stream(
    agent: Any,
    inputs: dict,
    source: str,
    config: dict | None = None,
) -> AsyncIterator[dict]:
    """运行 ReAct agent 并 yield 标准化事件流。

    事件类型:
    - ``{"type": "token", "content": str}``: 模型流式输出 token
    - ``{"type": "tool_call", "id": str, "name": str, "args": dict}``: 工具调用开始
    - ``{"type": "tool_result", "id": str, "name": str, "result": Any}``: 工具调用结束

    ``id`` 来自 astream_events v2 的 ``run_id``，同一 tool run 的 start/end 共享，
    供前端按 id 配对（chat-rendering-trace-v2 D5）。

    Args:
        agent: 已编译的 ReAct agent（CompiledStategraph）。
        inputs: agent 输入，形如 ``{"messages": [...]}``。
        source: 事件源标签（"code" / "rag" / "web" / 自定义 agent key）。
        config: 可选的 LangGraph 运行配置，含 ``configurable`` 等。
            若 agent 绑定了 checkpointer，必须提供 ``{"configurable": {"thread_id": ...}}``。
    """
    astream_kwargs: dict[str, Any] = {"version": "v2"}
    if config is not None:
        astream_kwargs["config"] = config
    async for event in agent.astream_events(inputs, **astream_kwargs):
        kind = event["event"]
        name = event.get("name", "")
        data = event.get("data", {}) or {}
        run_id = event.get("run_id", "")
        if kind == "on_chat_model_stream":
            content = extract_text(data.get("chunk"))
            if content:
                yield {"type": "token", "content": content}
        elif kind == "on_tool_start":
            yield {
                "type": "tool_call",
                "id": run_id,
                "name": name,
                "args": data.get("input"),
                "source": source,
            }
        elif kind == "on_tool_end":
            yield {
                "type": "tool_result",
                "id": run_id,
                "name": name,
                "result": data.get("output"),
                "source": source,
            }


def build_builtin_subagent(
    name: str,
    thread_id: str,
    checkpointer: Any = None,
    chat_model: Any = None,
) -> Any:
    """构建内置子代理（rag/web）deep_agent 子图，返回 CompiledStateGraph。

    Args:
        name: 子代理名称，"rag" 或 "web"。
        thread_id: 会话 ID。
        checkpointer: 可选的 LangGraph checkpointer。
        chat_model: 可选的注入 ChatModel（eval mock 模式透传）；为 None 时回退到 ``get_chat_model``。
    """
    from app.deepagent.factory import create_agent
    from app.security.dangerous_tools import FORBIDDEN_SUBAGENT_TOOLS
    from app.tools.subagent_tools import make_rag_tools, make_web_tools

    settings = get_settings()
    cfg = settings.subagents[name]
    model = chat_model if chat_model is not None else get_chat_model(temperature=cfg.temperature, streaming=True)
    if name == "rag":
        tools = make_rag_tools(thread_id)
    elif name == "web":
        tools = make_web_tools(thread_id)
    else:
        raise ValueError(f"unknown builtin subagent: {name}")
    prompt = (cfg.system_prompt or "") + THINK_PROMPT_SUFFIX
    return create_agent(
        model,
        tools,
        system_prompt=prompt,
        checkpointer=checkpointer,
        name=f"{name}_agent",
        excluded_tools=FORBIDDEN_SUBAGENT_TOOLS,
    )


async def run_builtin_subagent(
    name: str,
    thread_id: str,
    message: str,
    history: list | None = None,
    checkpointer: Any = None,
) -> AsyncIterator[dict]:
    """运行内置子代理，yield 标准化事件流。"""
    agent = build_builtin_subagent(name, thread_id, checkpointer=checkpointer)
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}
    config = {"configurable": {"thread_id": thread_id}}
    async for event in run_react_agent_stream(agent, inputs, source=name, config=config):
        yield event
