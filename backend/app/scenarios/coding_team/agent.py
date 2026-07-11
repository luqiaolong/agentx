"""coding_team 场景级 AgentTeam 实现。

本文件是场景入口薄封装，框架实现见 ``app.team.orchestrator``。

AgentTeam 是统称（类型），当前唯一实例是 coding team。
复用现有 ``app.team`` 框架（orchestrator/scheduler/planner/aggregator/blackboard），
但作为场景的子模式（``coding_team``），不是顶层模式。

与旧 ``run_team_path`` 的区别：
1. 入参签名与 ``run_work_supervisor`` / ``run_coding_expert`` 对齐（无 state 参数）
2. 降级时不回退到 CHAT 路径，改为建议用户切换到 work 模式
3. source 标识为 ``"coding_team"``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from app.deepagent.context import current_thread_id
from app.observability.logger import logger
from app.sse.events import make_error_event
from app.team.orchestrator import run_team_path

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = [
    "run_coding_team",
]


async def run_coding_team(
    message: str,
    thread_id: str,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "standard",
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
) -> AsyncIterator[dict]:
    """运行 coding_team 场景级 AgentTeam，yield SSE 事件。

    复用 ``run_team_path`` 的 Orchestrator + 并行子代理 + Blackboard + Aggregator
    流程，但适配场景化架构：
    - 构造最小 RouterState（仅含 thread_id）传给 run_team_path
    - permission_mode 对齐新架构（"standard" / "full_trust"）

    Args:
        message: 用户消息。
        thread_id: 会话 ID。
        profile_prompt: 用户画像前缀。
        history: 历史 messages 列表（已截断）。
        permission_mode: 权限模式，"standard" 或 "full_trust"。
        workspace_path: 可选当前工作区绝对路径。
        chat_model: 可选注入的 ChatModel，透传到 ``run_team_path``。None 时使用真实 LLM。

    Yields:
        SSE 事件 dict: {event: str, data: str}
    """
    # 构造最小 RouterState（run_team_path 需要 thread_id 字段）
    state: dict = {
        "thread_id": thread_id,
        "agent_mode": "coding_team",
    }
    # 设置 contextvar，供 AuthorizedLocalShellBackend 读取 thread_id 做沙箱授权
    current_thread_id.set(thread_id)

    logger.info(
        "coding_team.start",
        thread_id=thread_id,
        message_len=len(message),
    )

    # M24 修复：用 try/except 包裹 run_team_path，异常时 yield error 事件
    # 而非让异常向上传播到 Router 导致整条 SSE 流中断。
    try:
        async for sse in run_team_path(
            message,
            thread_id,
            state,
            profile_prompt=profile_prompt,
            history=history,
            permission_mode=permission_mode,
            workspace_path=workspace_path,
            chat_model=chat_model,
        ):
            yield sse
    except Exception as exc:  # noqa: BLE001 — Team 异常不应让 SSE 流中断
        logger.exception("coding_team failed", thread_id=thread_id)
        yield make_error_event(f"Team 执行失败: {exc}")
