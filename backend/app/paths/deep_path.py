"""DeepAgent 路径（路径 C）：复杂多步任务，带危险工具中断 + 人工审批。

设计要点（M1 骨架）：
- ``DANGEROUS_TOOLS``：触发 ``interrupt_on`` 的工具集合，DeepAgent 调用这些工具时
  LangGraph **无限期暂停**（``auto_approve_after_seconds=0`` 时不设超时）。
- 审批通过 ``/api/chat/approve`` 写入 ``app.main._pending_approvals[thread_id]``，
  SSE handler 轮询 ``wait_for_approval`` 后以 ``Command(resume=approval)`` 恢复图执行。
- ``build_deep_agent`` 仅为骨架：声明 ``interrupt_on`` 契约 + 文档，未完整接入 LLM。
- ``run_deep_path`` 为骨架 SSE 生成器：yield 占位 todo_update / approval_request，
  真实 DeepAgent 在 M2 接入 LangGraph 后替换。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from app.observability.logger import logger
from app.router.state import RouterState

# 触发人工审批中断的工具集合：写操作与 shell 执行
DANGEROUS_TOOLS: set[str] = {"edit_file", "write_file", "shell_exec"}


def build_deep_agent() -> Any:
    """构造 DeepAgent 图（M1 骨架，仅声明 interrupt_on 契约）。

    M2 完整实现要点：
    - 使用 ``deepagents`` + ``langgraph`` 构建 ReAct 图，工具集 = filesystem + git +
      web_search + rag_retrieve。
    - ``interrupt_on=DANGEROUS_TOOLS``：调用危险工具时 LangGraph 暂停节点，
      不设 ``interrupt_timeout``（``auto_approve_after_seconds=0`` → 无限期等待）。
    - 审批恢复：SSE handler 读取 ``_pending_approvals[thread_id]`` 后调用
      ``graph.invoke(None, config, command=Command(resume=approval))`` 续跑。
    - 自动批准：若 ``settings.auto_approve_after_seconds > 0``，SSE handler 在
      倒计时归零时写入 ``_pending_approvals[thread_id]=True`` 触发自动放行。

    Returns:
        构造的 DeepAgent 实例（M1 返回 None，由调用方判断是否进入骨架流程）。
    """
    logger.info("build_deep_agent skeleton: interrupt_on=DANGEROUS_TOOLS declared")
    # M1 骨架：不构建真实图，返回 None 占位。SSE handler 调用 run_deep_path 走骨架。
    return None


async def run_deep_path(state: RouterState, message: str) -> AsyncIterator[dict]:
    """DeepAgent 路径 SSE 生成器（M1 骨架）。

    真实实现接入 LangGraph 后，此处将：
    1. ``graph.astream(...)`` 驱动图执行。
    2. 节点事件转 SSE：``token`` / ``todo_update`` / ``approval_request``。
    3. 遇 ``interrupt`` 时 yield ``approval_request``，并 ``await wait_for_approval``。
    4. 审批通过 → ``Command(resume=True)`` 续跑；拒绝 → yield error 并终止。

    M1 骨架：yield 一个占位 todo_update，模拟一次危险工具审批，最后 yield done。
    """
    thread_id = state.get("thread_id", "")

    # 1. 占位 todo_update：声明任务计划
    yield {
        "event": "todo_update",
        "data": {
            "todos": [
                {"text": "分析任务需求", "done": True},
                {"text": "执行危险操作（需审批）", "done": False},
            ]
        },
    }

    # 2. 模拟危险工具调用前的审批请求（M1 骨架，真实流程由 LangGraph interrupt 触发）
    # SSE 契约：自定义载荷嵌套在 data 内（sse-starlette 仅接受 event/data/id/retry/comment）
    yield {
        "event": "approval_request",
        "data": json.dumps(
            {
                "tool_name": "write_file",
                "args": {"path": "data/workspace/output.txt", "content": "<redacted>"},
                "preview": "将写入 data/workspace/output.txt",
            },
            ensure_ascii=False,
        ),
    }

    # 3. 等待用户审批（轮询 _pending_approvals 直至收到真实决定）。
    # 安全 spec：auto_approve_after_seconds=0（默认）时 MUST 无限期等待，不自动放行。
    # 自动批准由 SSE handler 在倒计时归零时写入 _pending_approvals[thread_id]=True 实现，
    # 此处只负责等待真实决定，不自行默认 True。
    approval = await _await_approval(thread_id)
    if approval is False:
        yield {"event": "error", "data": "用户拒绝执行危险操作"}
        return
    # approval is None：会话被中止或超时，安全失败而非放行
    if approval is None:
        yield {"event": "error", "data": "审批等待被中断，操作未执行"}
        return

    # 4. 审批通过 → 占位 token 流
    yield {"event": "token", "data": "[DeepAgent 骨架] "}
    yield {"event": "token", "data": "任务执行完成（M1 占位响应）"}

    # 5. 完成
    yield {"event": "done", "data": {}}


async def wait_for_approval(thread_id: str, timeout: float = 0.5) -> bool | None:
    """轮询 ``app.main._pending_approvals[thread_id]``，返回审批决定（单次非阻塞查询）。

    Args:
        thread_id: 会话 ID。
        timeout: 保留参数（M1 骨架不阻塞，仅做一次查询）。

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
    max_wait: float = 30.0,
) -> bool | None:
    """阻塞轮询直至收到审批决定或达到 max_wait（防止单元测试/骨架永久挂起）。

    生产 SSE handler 应使用更长的 max_wait 或无限等待；M1 骨架用 30s 上限：
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


__all__ = [
    "DANGEROUS_TOOLS",
    "build_deep_agent",
    "run_deep_path",
    "wait_for_approval",
]
