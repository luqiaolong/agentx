"""画像抽取工具：从对话中抽取用户画像条目并写入 profile.json。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm import get_chat_model, make_structured_llm
from app.observability.logger import logger

# T10：异步画像抽取任务引用集合，防止被 GC 回收（asyncio 已知坑）
_extract_tasks: set[asyncio.Task] = set()


class ExtractStatus(str, Enum):
    """画像抽取结果状态。

    - SUCCESS_EMPTY: LLM 调用成功但无可抽取记忆
    - SUCCESS_WRITTEN: LLM 调用成功且抽取到条目
    - FAILED: LLM 调用失败（超时/异常/解析错误）
    """

    SUCCESS_EMPTY = "success_empty"
    SUCCESS_WRITTEN = "success_written"
    FAILED = "failed"


@dataclass
class ExtractResult:
    """画像抽取结果，区分失败与空成功。

    Attributes:
        status: 抽取状态
        entries: 抽取到的条目（FAILED 或 SUCCESS_EMPTY 时为空列表）
        error: 失败时的错误信息（成功时为 None）
    """

    status: ExtractStatus
    entries: list[dict] = field(default_factory=list)
    error: str | None = None


class ProfileEntry(BaseModel):
    """单条用户画像条目。"""

    key: str = Field(description="条目唯一键，如 uses_ts / project_framework")
    category: str = Field(description="分类：preference / project / fact / custom")
    content: str = Field(description="条目内容描述")
    title: str | None = Field(default=None, description="可读标题,不超过20字")
    keywords: list[str] = Field(default_factory=list, description="3-5个关键词标签")
    scenarios: list[str] = Field(default_factory=list, description="1-3个应用场景")
    scope: str = Field(
        default="global",
        description="存储层级：global（跨工作区生效）或 workspace（仅当前工作区）",
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="抽取置信度 0.0-1.0，低于 0.6 不会被自动写入",
    )
    sensitivity: str = Field(
        default="public",
        description="敏感等级：public（可注入 prompt）或 private（不注入 prompt，仅存储）",
    )


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
    "- scenarios：1-3个应用场景，描述该记忆在何种情境下应被引用\n"
    "- scope：存储层级，取值 \"global\" 或 \"workspace\"：\n"
    "  · preference（用户长期偏好，如\"喜欢中文回复\"）→ scope=\"global\"\n"
    "  · fact（用户个人事实，如\"是前端工程师\"）→ scope=\"global\"\n"
    "  · project（当前工作区的技术栈/约定/任务，如\"项目用 FastAPI\"）→ scope=\"workspace\"\n"
    "  · custom → scope 视内容而定：与用户个人相关 → \"global\"；与项目相关 → \"workspace\"\n"
    "  注意：用户偏好（如\"我喜欢中文回复\"）即使在某工作区内说出，也属于 global，\n"
    "  因为它是跨工作区的个人偏好，不是项目特定约定。\n"
    "- confidence：抽取置信度 0.0-1.0，基于用户表达的明确程度：\n"
    "  · 0.9-1.0：用户明确陈述（如\"我用 TypeScript\"）\n"
    "  · 0.7-0.8：从对话可合理推断（如多次提及 TypeScript）\n"
    "  · 0.5-0.6：模糊或间接暗示\n"
    "  · 低于 0.6 的条目不会被自动写入\n"
    "- sensitivity：敏感等级，取值 \"public\" 或 \"private\"：\n"
    "  · private：个人敏感信息（如真实姓名、手机号、密码、密钥、薪资等）\n"
    "  · public：一般技术偏好/项目约定等非敏感信息\n"
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


async def extract_profile_via_llm(message: str, assistant_reply: str) -> ExtractResult:
    """调 LLM 抽取画像条目（结构化输出）。

    使用 ``llm.with_structured_output(ProfileResult)`` 让模型直接返回结构化对象，
    避免手写 JSON 解析与 markdown 剥离。返回 ``ExtractResult`` 以区分：

    - ``SUCCESS_EMPTY``: LLM 调用成功但无可抽取记忆（entries 为空）
    - ``SUCCESS_WRITTEN``: LLM 调用成功且抽取到条目
    - ``FAILED``: LLM 调用失败（超时/异常/解析错误）

    调用方（队列 worker / API 端点）必须根据 ``status`` 决定后续行为，
    不得将 ``FAILED`` 当作"无可抽取"处理。
    """
    llm = get_chat_model(temperature=get_settings().llm_temperature_extraction)
    structured_llm = make_structured_llm(llm, ProfileResult)
    prompt = _PROFILE_PROMPT.invoke({"message": message, "assistant_reply": assistant_reply})
    try:
        result: ProfileResult = await structured_llm.ainvoke(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"profile extract via llm failed: {type(exc).__name__}: {exc}"
        )
        return ExtractResult(status=ExtractStatus.FAILED, entries=[], error=str(exc))

    entries = [entry.model_dump() for entry in result.entries]
    if not entries:
        return ExtractResult(status=ExtractStatus.SUCCESS_EMPTY, entries=[])
    return ExtractResult(status=ExtractStatus.SUCCESS_WRITTEN, entries=entries)


__all__ = [
    "ExtractResult",
    "ExtractStatus",
    "extract_last_assistant_reply",
    "extract_profile_via_llm",
]
