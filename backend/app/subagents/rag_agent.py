"""RAG 子代理：检索知识库并回答（薄 re-export，实现见 base.build_builtin_subagent）。

工具集: rag_retrieve（向量检索，来自 ``app.tools.subagent_tools.make_rag_tools``）

实际逻辑已收敛到 ``app.subagents.base.build_builtin_subagent`` /
``run_builtin_subagent`` 工厂函数；本文件仅保留薄 re-export 以维持向后兼容
（``from app.subagents.rag_agent import build_rag_agent`` 等历史 import 路径
仍可用）。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.subagents.base import (
    build_builtin_subagent,
    run_builtin_subagent,
)


def build_rag_agent(
    thread_id: str,
    checkpointer: Any = None,
    chat_model: Any = None,
) -> Any:
    """构建 RAG 子代理 deep_agent 子图，返回 CompiledStateGraph。

    薄 re-export：委托给 ``build_builtin_subagent("rag", ...)``。
    ``chat_model`` 透传用于 eval mock 模式注入。
    """
    return build_builtin_subagent("rag", thread_id, checkpointer=checkpointer, chat_model=chat_model)


async def run_rag_agent(
    thread_id: str,
    message: str,
    history: list | None = None,
    checkpointer: Any = None,
) -> AsyncIterator[dict]:
    """运行 RAG 子代理，yield 标准化事件流。

    薄 re-export：委托给 ``run_builtin_subagent("rag", ...)``。
    """
    async for event in run_builtin_subagent(
        "rag", thread_id, message, history, checkpointer=checkpointer
    ):
        yield event


__all__ = ["build_rag_agent", "run_rag_agent"]
