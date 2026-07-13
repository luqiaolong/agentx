"""画像抽取工具：从对话中抽取用户画像条目并写入 profile.json。"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm import get_chat_model
from app.observability.logger import logger

# T10：异步画像抽取任务引用集合，防止被 GC 回收（asyncio 已知坑）
_extract_tasks: set[asyncio.Task] = set()


class ProfileEntry(BaseModel):
    """单条用户画像条目。"""

    key: str = Field(description="条目唯一键，如 uses_ts / project_framework")
    category: str = Field(description="分类：preference / project / fact")
    content: str = Field(description="条目内容描述")
    title: str | None = Field(default=None, description="可读标题,不超过20字")
    keywords: list[str] = Field(default_factory=list, description="3-5个关键词标签")
    scenarios: list[str] = Field(default_factory=list, description="1-3个应用场景")


class ProfileResult(BaseModel):
    """画像抽取结构化输出。"""

    entries: list[ProfileEntry] = Field(default_factory=list, description="抽取到的画像条目")


_PROFILE_SYSTEM = (
    "你是一个用户画像抽取器。分析对话，抽取「值得跨会话记住的事实」：\n"
    "- 用户偏好（如\"喜欢简洁回复\"、\"用 TypeScript\"）\n"
    "- 项目约定（如\"项目用 FastAPI\"、\"测试用 pytest\"）\n"
    "- 重要事实（如\"用户是前端工程师\"、\"工作日 9-18 点在线\"）\n"
    "若无可抽取内容，返回空 entries。不要编造，只抽取明确的事实。\n"
    "对每条记忆，必须生成以下结构化字段：\n"
    "- title：可读标题，不超过20字，概括该条记忆的核心要点\n"
    "- keywords：3-5个关键词标签，用于检索与分类\n"
    "- scenarios：1-3个应用场景，描述该记忆在何种情境下应被引用"
)

_PROFILE_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _PROFILE_SYSTEM), ("human", "对话：\n用户: {message}\n助手: {assistant_reply}")]
)


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
    """调 LLM 抽取画像条目（结构化输出）。

    使用 ``llm.with_structured_output(ProfileResult)`` 让模型直接返回结构化对象，
    避免手写 JSON 解析与 markdown 剥离。返回 ``list[dict]`` 以兼容
    ``upsert_from_llm(entries: list[dict])`` 契约。

    失败时返回空列表（调用方按"无可抽取"处理，不报错）。
    """
    llm = get_chat_model(temperature=get_settings().llm_temperature_extraction)
    structured_llm = llm.with_structured_output(ProfileResult)
    prompt = _PROFILE_PROMPT.invoke({"message": message, "assistant_reply": assistant_reply})
    try:
        result: ProfileResult = await structured_llm.ainvoke(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"profile extract via llm failed: {type(exc).__name__}: {exc}"
        )
        return []
    return [entry.model_dump() for entry in result.entries]


__all__ = ["extract_profile_via_llm", "extract_last_assistant_reply"]
