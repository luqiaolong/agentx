"""Work 场景包：全能 Supervisor agent。

基于 ``deepagents.create_deep_agent``（经 ``app.deepagent.factory.create_agent`` 封装）构建，
自带完整工具集 + ``delegate_to_expert`` / ``delegate_to_subagent`` 委派工具。
写操作走 ``interrupt_on`` 审批流（仅危险工具中断）。
"""

from __future__ import annotations

from app.scenarios.work.mention import parse_mention, strip_mention
from app.scenarios.work.agent import build_work_supervisor, run_work_supervisor

__all__ = [
    "build_work_supervisor",
    "run_work_supervisor",
    "parse_mention",
    "strip_mention",
]
