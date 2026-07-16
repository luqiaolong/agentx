"""只读工具循环保护中间件。

在 LangGraph/deepagents ReAct 循环中，只读工具（ls/read_file/glob/grep）
不触发 ``interrupt_on`` 中断，全部在图内部自动执行。当 LLM 陷入只读工具
探测死循环时，会消耗完 ``recursion_limit`` 个 superstep 后抛出
``GraphRecursionError``，导致用户看到 "Recursion limit reached" 错误。

本中间件通过 ``awrap_model_call`` 在每次模型调用前检查消息历史，
若连续只读工具调用次数超过阈值，则设置 ``tool_choice="none"`` 强制
模型生成文本回复（不再调用工具），并在 system prompt 中注入提示。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NotRequired

from loguru import logger
from deepagents.middleware.memory import MemoryMiddleware, MemoryState, MemoryStateUpdate
from langchain.agents.middleware.types import AgentMiddleware, PrivateStateAttr
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage

from app.security.approval.flow import _READONLY_TOOLS
from app.deepagent.hitl import _tool_call_name

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.runnables import RunnableConfig
    from langgraph.runtime import Runtime

__all__ = ["ReadonlyLoopGuardMiddleware", "WorkspaceMemoryMiddleware"]


class ReadonlyLoopGuardMiddleware(AgentMiddleware[Any, Any, Any]):
    """检测只读工具连续调用循环，超过阈值时强制模型停止调用工具。

    工作原理：
    1. 在 ``awrap_model_call`` 中，检查 ``request.messages`` 末尾连续的
       只读 ToolMessage 数量（streak）
    2. 若 streak >= threshold，设置 ``tool_choice="none"`` 并在 system prompt
       末尾追加提示，强制模型基于已有信息直接回答
    3. 否则正常调用 handler 执行模型推理

    阈值为 0 时禁用保护（不拦截）。
    """

    def __init__(self, threshold: int = 10) -> None:
        self.threshold = threshold
        self._readonly_tools: frozenset[str] = _READONLY_TOOLS

    def _count_readonly_streak(self, messages: list[AnyMessage]) -> int:
        """从消息列表末尾统计连续只读 ReAct 轮次。

        一个完整的 ReAct 轮次 = ``AIMessage(tool_calls=[...])`` 后跟
        对应的 ``ToolMessage(s)``。从末尾向前遍历已完成的只读轮次：

        - 只读 ToolMessage → streak++
        - 发起只读轮次的 ``AIMessage(tool_calls)`` → 跳过（不中断 streak）
        - 非只读 ToolMessage / 含非只读工具的 AIMessage / 无 tool_calls 的
          AIMessage / 其他消息类型 → 停止

        末尾消息不是 ``ToolMessage`` 时返回 0（无已完成的轮次）。
        """
        if not messages or not isinstance(messages[-1], ToolMessage):
            return 0
        streak = 0
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage):
                name = getattr(msg, "name", "") or ""
                if name in self._readonly_tools:
                    streak += 1
                else:
                    break
            elif isinstance(msg, AIMessage):
                tool_calls = getattr(msg, "tool_calls", None) or []
                if not tool_calls:
                    break
                if all(_tool_call_name(tc) in self._readonly_tools for tc in tool_calls):
                    continue
                break
            else:
                break
        return streak

    async def awrap_model_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        """在模型调用前检查只读工具循环。"""
        if self.threshold <= 0:
            return await handler(request)

        messages = request.messages or []
        streak = self._count_readonly_streak(messages)

        if streak < self.threshold:
            return await handler(request)

        logger.warning(
            "readonly loop guard: forcing model to stop calling tools",
            streak=streak,
            threshold=self.threshold,
            readonly_tools=sorted(self._readonly_tools),
        )

        # 在 system prompt 末尾追加提示，引导模型基于已有信息回答
        original_prompt = request.system_prompt or ""
        hint = (
            "\n\n[系统提示] 你已连续调用了多次只读工具（ls/read_file/glob/grep）。"
            "请基于已获取的信息直接回答用户的问题，不要再调用工具。"
        )
        modified = request.override(
            system_message=SystemMessage(content=original_prompt + hint),
            tool_choice="none",
        )
        return await handler(modified)


class WorkspaceMemoryState(MemoryState):
    """State schema with mtime signature for workspace memory cache invalidation."""

    memory_sources_signature: NotRequired[Annotated[str, PrivateStateAttr]]


class WorkspaceMemoryMiddleware(MemoryMiddleware):
    """MemoryMiddleware with mtime-aware workspace memory reload (T4.5).

    DeepAgents ``MemoryMiddleware`` caches ``memory_contents`` in state and skips
    reload if already present. This subclass computes an mtime signature from all
    source files (``.agentx/memory/*.md``, ``.agentx/AGENTS.md``, ``.agentx/rules/*.md``)
    and invalidates the cache when files change on disk, ensuring subsequent turns
    in the same thread see updated memory content.

    Spec: memory-safety-contract "DeepAgents memory 缓存必须感知 workspace 变更".
    """

    state_schema = WorkspaceMemoryState

    def _compute_signature(self) -> str:
        """Compute mtime signature from all source files.

        Returns a string that changes when any source file is modified, created,
        or deleted. Uses ``st_mtime_ns`` for nanosecond precision.
        """
        parts: list[str] = []
        for path in self.sources:
            p = Path(path)
            try:
                if p.exists():
                    parts.append(f"{path}:{p.stat().st_mtime_ns}")
                else:
                    parts.append(f"{path}:missing")
            except OSError:
                parts.append(f"{path}:error")
        return "|".join(parts)

    def _is_cache_fresh(self, state: Mapping) -> bool:
        """Check if cached memory_contents is still fresh.

        Cache is fresh when:
        - ``memory_contents`` is present in state, AND
        - stored ``memory_sources_signature`` matches current file mtimes
        """
        if "memory_contents" not in state:
            return False
        current_sig = self._compute_signature()
        stored_sig = state.get("memory_sources_signature", "")
        return current_sig == stored_sig

    async def abefore_agent(
        self,
        state: WorkspaceMemoryState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> MemoryStateUpdate | None:
        """Load memory content with mtime-aware cache invalidation (async)."""
        if self._is_cache_fresh(state):
            return None
        # Cache stale or missing → invalidate so parent reloads from disk
        state_copy = {k: v for k, v in state.items() if k != "memory_contents"}
        update = await super().abefore_agent(state_copy, runtime, config)
        if update is None:
            return None
        current_sig = self._compute_signature()
        return {**update, "memory_sources_signature": current_sig}

    def before_agent(
        self,
        state: WorkspaceMemoryState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> MemoryStateUpdate | None:
        """Load memory content with mtime-aware cache invalidation (sync)."""
        if self._is_cache_fresh(state):
            return None
        state_copy = {k: v for k, v in state.items() if k != "memory_contents"}
        update = super().before_agent(state_copy, runtime, config)
        if update is None:
            return None
        current_sig = self._compute_signature()
        return {**update, "memory_sources_signature": current_sig}
