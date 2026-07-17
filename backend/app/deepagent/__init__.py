"""DeepAgent 包：deepagents 0.6+ harness + 危险工具中断审批 + SSE 流式。

公共 API 边界（``__all__``）覆盖所有被上层 ``scenarios`` / ``team`` 及内部子包
``subagents`` 稳定复用的符号；实现细节（``_DEEP_SYSTEM_PROMPT`` 常量、``_is_interrupted`` 检测、
backend 类、middleware 类）保留在各自子模块，外部按需从子模块 import。

子包归属：``app.deepagent.subagents``（rag / web / 自定义子代理工厂）已从原
``app.subagents`` 迁入本包，作为 ``deepagent`` 的内部子包存在。

内部模块归属（maintainability-refactor 后）:
- ``agent.py``: 路径 C 入口（``build_deep_agent`` / ``run_deep_path``）+ profile 抽取
- ``factory.py``: ``create_agent`` 封装 ``deepagents.create_deep_agent``，注册 HarnessProfile
- ``tool_assembly.py``: ``AgentToolset`` 不可变装配 + 工具集构建（``make_deep_tools`` /
  ``load_mcp_tools`` / ``compute_runtime_dangerous`` 兼容委托，canonical 在 ``app.security``）
- ``context.py``: thread_id / parent_thread_id contextvar + ``bind_agent_context``
- ``streaming.py``: 公共 SSE 驱动 ``stream_agent_events``（薄壳，持有 astream 循环）
- ``stream_events.py``: ``StreamRunState`` + ``StreamEventMapper`` + dedup helpers
- ``hitl.py``: LangGraph HITL interrupt/resume 纯函数（``is_interrupted`` 等）
- ``approval_session.py``: ``ApprovalSession`` 状态机 + ``LoopExitReason`` / ``ExitState``
- ``approval_runner.py``: 公共 facade ``run_agent_with_approval``（依赖归一化 + 委托循环）
- ``middleware.py``: ``ReadonlyLoopGuardMiddleware`` + ``WorkspaceMemoryMiddleware``
- ``safe_shell_backend.py``: ``SafeLocalShellBackend``（RiskClassifier 集成）
- ``authorized_backend.py``: ``AuthorizedLocalShellBackend``（SessionSandbox 动态授权）

导入方向（无循环）：``agent.py`` → ``approval_runner`` / ``factory`` / ``tool_assembly`` /
``context``；``approval_runner`` → ``hitl`` / ``approval_session`` / ``streaming``；
``streaming`` → ``stream_events``。

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
