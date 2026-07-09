"""Coding 场景包：场景绑定专家 agent。

Expert 是统称（类型），当前唯一实例是 coding Expert。
Expert 基于 ``build_deep_agent`` 构建，可调用 rag/web 子代理。
Expert 不可委派其他 Expert，也不可触发 AgentTeam。
"""

from __future__ import annotations

from app.scenarios.coding.agent import build_coding_expert, run_coding_expert

__all__ = [
    "build_coding_expert",
    "run_coding_expert",
]
