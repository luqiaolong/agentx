"""DeepAgent 包：deepagents 0.6+ harness + 危险工具中断审批 + SSE 流式。

公共 API 边界（``__all__``）覆盖所有被上层 ``scenarios`` / ``team`` / ``subagents``
稳定复用的符号；实现细节（``_DEEP_SYSTEM_PROMPT`` 常量、``_is_interrupted`` 检测、
backend 类、middleware 类）保留在各自子模块，外部按需从子模块 import。

公共符号分组:
- 入口: ``build_deep_agent`` / ``run_deep_path`` / ``trigger_profile_auto_extract``
- 工厂: ``create_agent``
- 审批循环: ``run_agent_with_approval``
- 流式: ``stream_agent_events``
- 工具: ``DANGEROUS_TOOLS`` / ``compute_runtime_dangerous`` / ``make_deep_tools`` / ``load_mcp_tools``
- 上下文: ``current_thread_id`` / ``current_parent_thread_id``
"""

from app.deepagent.agent import (
    DANGEROUS_TOOLS,
    build_deep_agent,
    run_deep_path,
    trigger_profile_auto_extract,
)
from app.deepagent.approval_runner import run_agent_with_approval
from app.deepagent.context import (
    current_parent_thread_id,
    current_thread_id,
)
from app.deepagent.factory import create_agent
from app.deepagent.streaming import stream_agent_events
from app.deepagent.tool_assembly import (
    compute_runtime_dangerous,
    load_mcp_tools,
    make_deep_tools,
)

__all__ = [
    # 入口
    "build_deep_agent",
    "run_deep_path",
    "trigger_profile_auto_extract",
    # 工厂
    "create_agent",
    # 审批循环
    "run_agent_with_approval",
    # 流式
    "stream_agent_events",
    # 工具
    "DANGEROUS_TOOLS",
    "compute_runtime_dangerous",
    "make_deep_tools",
    "load_mcp_tools",
    # 上下文
    "current_thread_id",
    "current_parent_thread_id",
]
