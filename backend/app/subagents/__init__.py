"""Sub-agents：rag / web 两个内置 ReAct 子代理 + 自定义子代理工厂。

场景化架构（Supervisor + Expert）下，code 子代理已被 coding Expert 取代，
路径 B（SINGLE_TOOL）分发逻辑已删除。本包仅保留 rag/web 子代理与自定义子代理工厂，
供 Supervisor / Expert 通过 delegate_to_subagent 工具调用。
"""

from .custom_agent import build_custom_agent, run_custom_agent
from .rag_agent import build_rag_agent, run_rag_agent
from .web_agent import build_web_agent, run_web_agent

__all__ = [
    "build_rag_agent",
    "run_rag_agent",
    "build_web_agent",
    "run_web_agent",
    "build_custom_agent",
    "run_custom_agent",
]
