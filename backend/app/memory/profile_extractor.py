"""画像抽取工具：从对话中抽取用户画像条目并写入 profile.json。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.llm import get_chat_model

# T10：异步画像抽取任务引用集合，防止被 GC 回收（asyncio 已知坑）
_extract_tasks: set[asyncio.Task] = set()


async def extract_last_assistant_reply(agent: Any, config: dict) -> str:
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


async def extract_profile_via_llm(message: str, assistant_reply: str) -> list[dict]:
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

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group())
        return data.get("entries", []) if isinstance(data, dict) else []
    except json.JSONDecodeError:
        return []


__all__ = ["extract_profile_via_llm", "extract_last_assistant_reply"]
