"""终端审批交互。

当 SSE 事件流中出现 ``approval_request`` 时，暂停生成并阻塞等待用户输入。
"""

from __future__ import annotations

import asyncio
import sys

__all__ = ["handle_approval"]


async def handle_approval(thread_id: str) -> None:
    """终端审批交互：阻塞等待用户输入。

    用户输入：
    - ``y`` / ``yes`` → 批准（approve）
    - ``n`` / ``no`` → 拒绝（deny）
    - ``o`` / ``once`` → 仅一次（once）
    - ``s`` / ``session`` → 会话级批准（session）
    - Ctrl+C / Ctrl+D → 拒绝
    """
    from app.security.approval import ApprovalDecision, ApprovalResult, submit_approval

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, input, "Approve? [y=批准 / n=拒绝 / o=仅一次 / s=会话级]: "
            )
        except (EOFError, KeyboardInterrupt):
            # 用户 Ctrl+C 或 Ctrl+D → 拒绝
            await submit_approval(thread_id, ApprovalResult(decision=ApprovalDecision.DENY))
            print("[已拒绝]")
            return

        choice = user_input.strip().lower()
        if choice in ("y", "yes"):
            await submit_approval(thread_id, ApprovalResult(decision=ApprovalDecision.APPROVE))
            return
        elif choice in ("n", "no"):
            await submit_approval(thread_id, ApprovalResult(decision=ApprovalDecision.DENY))
            print("[已拒绝]")
            return
        elif choice in ("o", "once"):
            await submit_approval(thread_id, ApprovalResult(decision=ApprovalDecision.ONCE))
            return
        elif choice in ("s", "session"):
            await submit_approval(thread_id, ApprovalResult(decision=ApprovalDecision.SESSION))
            return
        else:
            print("请输入 y/n/o/s")
