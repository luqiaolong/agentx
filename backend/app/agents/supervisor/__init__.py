"""Supervisor 包：work 场景全能 agent。

基于 ``create_react_agent`` 构建，自带完整工具集 +
``delegate_to_expert`` / ``delegate_to_subagent`` 委派工具。
写操作走 ``interrupt_before`` 审批流。
"""

from __future__ import annotations

from app.agents.supervisor.mention import parse_mention, strip_mention
from app.agents.supervisor.work_supervisor import build_work_supervisor, run_work_supervisor

__all__ = [
    "build_work_supervisor",
    "run_work_supervisor",
    "parse_mention",
    "strip_mention",
]
