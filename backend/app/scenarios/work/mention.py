"""@mention 语法解析：仅 work 场景生效。

支持 ``@coding`` / ``@rag`` / ``@web`` / ``@<custom_subagent_key>`` 强制委派。
``@team`` / ``@coding_team`` 不支持（team 仅通过模式切换访问）。

解析结果：
- ``(cleaned_message, target)``：target 为 None 表示无 @mention 或未知
- target 为 ``("expert", "coding")`` / ``("subagent", "rag")`` / ``("subagent", "web")`` / ``("subagent", "<custom_key>")``
"""

from __future__ import annotations

import re

from app.config import BUILTIN_EXPERT_KEYS, BUILTIN_SUBAGENT_KEYS, get_settings

__all__ = [
    "parse_mention",
    "strip_mention",
    "MENTION_PATTERN",
]

# @mention 正则：匹配 @ 后跟字母/数字/下划线/连字符
MENTION_PATTERN = re.compile(r"@([a-zA-Z0-9_-]+)")


def parse_mention(message: str) -> tuple[str, tuple[str, str] | None]:
    """解析消息中的 @mention，返回 (清理后消息, 目标 agent)。

    Args:
        message: 用户原始消息。

    Returns:
        - ``(cleaned_message, None)``：无 @mention 或 @mention 未知（已 strip）
        - ``(cleaned_message, ("expert", "coding"))``：@coding 强制委派 coding Expert
        - ``(cleaned_message, ("subagent", "rag"))``：@rag 强制委派 rag 子代理
        - ``(cleaned_message, ("subagent", "web"))``：@web 强制委派 web 子代理
        - ``(cleaned_message, ("subagent", "<custom_key>"))``：@<custom_key> 强制委派自定义子代理

    未知 @mention（如 ``@team`` / ``@unknown``）会被 strip 但返回 None。
    """
    if not message:
        return (message, None)

    match = MENTION_PATTERN.search(message)
    if not match:
        return (message, None)

    agent_name = match.group(1).lower()

    # 检查是否为 Expert
    if agent_name in BUILTIN_EXPERT_KEYS:
        cleaned = MENTION_PATTERN.sub("", message, count=1).strip()
        cleaned = " ".join(cleaned.split())  # 合并多余空白
        return (cleaned, ("expert", agent_name))

    # 检查是否为内置子代理
    if agent_name in BUILTIN_SUBAGENT_KEYS:
        cleaned = MENTION_PATTERN.sub("", message, count=1).strip()
        cleaned = " ".join(cleaned.split())
        return (cleaned, ("subagent", agent_name))

    # 检查是否为自定义子代理
    custom_subagents = get_settings().custom_subagents
    if agent_name in custom_subagents:
        cleaned = MENTION_PATTERN.sub("", message, count=1).strip()
        cleaned = " ".join(cleaned.split())
        return (cleaned, ("subagent", agent_name))

    # 未知 @mention（含 @team / @coding_team），strip 后正常处理
    from app.observability.logger import logger

    logger.info("mention.unknown_stripped", agent_name=agent_name)
    cleaned = MENTION_PATTERN.sub("", message, count=1).strip()
    cleaned = " ".join(cleaned.split())
    return (cleaned, None)


def strip_mention(message: str) -> str:
    """剥离所有 @mention 标记（用于 coding/coding_team 模式）。

    在非 work 模式下，@mention 不生效，直接 strip 后正常处理。
    """
    if not message:
        return message
    cleaned = MENTION_PATTERN.sub("", message).strip()
    return " ".join(cleaned.split())
