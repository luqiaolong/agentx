"""AgentTeam v2 共享状态与数据结构（T1）。

集中定义 Team 路径 v2 的 Pydantic BaseModel schema 与 LangGraph TypedDict state。

v2 改进：
- ``with_structured_output(TeamPlan)`` 替代正则解析（D2）
- ``findings: dict[str, Finding]`` 结构化对象替代裸字符串（D4）
- ``warnings: list[str]`` channel（D14）
- ``pending_waves: list[list[TeamTask]]`` DAG 多波次（D3）
- ``completed_task_ids: list[str]`` 按 task id 累积（D4）

本模块为 v2 数据结构单一来源，被 dispatcher.py / nodes.py / graph_builder.py /
runner.py / planner.py 引用。旧 ``TeamPlanTask`` / ``TeamState`` 仍保留在
blackboard.py 供 orchestrator.py（待 T35 删除）使用，v2 模块不得引用旧类型。
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from pydantic import BaseModel, Field

from app.team.blackboard import (
    _merge_completed_task_ids,
    _merge_findings,
    _merge_pending_waves,
    _merge_team_done_emitted,
    _merge_warnings,
)

__all__ = [
    "TeamTask",
    "TeamPlan",
    "Finding",
    "ClassificationResult",
    "TeamState",
    "SubtaskState",
]


class TeamTask(BaseModel):
    """单个子任务定义（v2，替代旧 ``TeamPlanTask``）。

    ``id`` 为稳定标识，用于 ``depends_on`` 引用与 findings key 复合格式
    ``{agent}:{task_id}:{wave_index}``（D13）。旧 ``TeamPlanTask`` 无 ``id``，
    按 list index 引用；v2 改为按 id 引用，replan 时更稳健。
    """

    id: str
    agent: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str = ""
    is_dangerous_hint: bool = False


class TeamPlan(BaseModel):
    """Orchestrator LLM 的结构化输出（D2）。

    通过 ``chat_model.with_structured_output(TeamPlan)`` 获取，替代旧正则解析。
    LLM 不支持结构化输出时回退到 ``_parse_plan_from_text_fallback``。
    """

    tasks: list[TeamTask]
    summary: str = Field(default="", description="整体规划摘要")
    needs_iterative: bool = Field(
        default=False, description="是否建议迭代式拆解（触发 replan 检查）"
    )


class Finding(BaseModel):
    """单个子任务的产出结果（D4，替代旧裸字符串 findings）。

    结构化 Finding 对象携带 ``agent`` / ``task_id`` / ``wave_index`` / ``success``
    / ``error`` / ``retries``，供 aggregator 质量门判定与前端展示。
    """

    agent: str
    task_id: str
    wave_index: int
    content: str
    success: bool = True
    error: Optional[str] = None
    retries: int = 0


class ClassificationResult(BaseModel):
    """DangerousTaskClassifier 的 LLM 结构化输出（D6）。"""

    is_dangerous: bool
    reason: str = ""
    suggested_agent: str = "deep"


class TeamState(TypedDict, total=False):
    """Team 路径 v2 全局 state（LangGraph StateGraph）。

    v2 字段变更（对比旧 blackboard.TeamState）：
    - ``plan: list[TeamTask]`` 替代旧 ``plan: list``（裸 dict）
    - ``pending_waves: list[list[TeamTask]]`` 替代旧 ``pending_levels: list[list[int]]``
    - ``findings: dict[str, Finding]`` 替代旧 ``findings: dict[str, str]``
    - ``warnings: list[str]`` 新增（D14）
    - ``completed_task_ids: list[str]`` 替代旧 ``completed_tasks: list[int]``
    - ``errors: list[str]`` 替代旧 ``errors: dict[str, str]``

    Reducers（从 blackboard.py 导入）确保并行 execute 节点返回部分 dict/list
    时自动归并。
    """

    message: str
    thread_id: str
    history: list
    permission_mode: str
    scene_prompt: str | None
    profile_prompt: str
    workspace_path: str | None
    chat_model: Any
    subtask_runners: Any

    plan: list[TeamTask]
    pending_waves: Annotated[list[list[TeamTask]], _merge_pending_waves]
    findings: Annotated[dict[str, Finding], _merge_findings]
    errors: Annotated[list[str], _merge_warnings]
    warnings: Annotated[list[str], _merge_warnings]
    completed_task_ids: Annotated[list[str], _merge_completed_task_ids]
    replan_count: int
    quality_gate_passed: bool  # D16: aggregate_node 写入，_route_after_aggregate 读取
    team_done_emitted: Annotated[bool, _merge_team_done_emitted]  # BE-B: team_done 单次发射标记

    # 运行时对象（不参与 checkpoint 序列化，与旧 TeamState 同策略）
    team_semaphore: Any  # asyncio.Semaphore
    subtask_timeout: int
    abort_event: Any  # asyncio.Event


class SubtaskState(TypedDict, total=False):
    """单个子任务节点的输入 state（通过 ``langgraph.types.Send`` 携带，D4）。

    v2 新增字段：
    - ``task: TeamTask`` 替代旧 ``task: dict``
    - ``upstream_findings: dict[str, Finding]`` 依赖任务结果注入
    - ``wave_index: int`` 当前 wave 索引（findings key 复合格式用）
    - ``replan_count: int`` replan 次数透传
    """

    task: TeamTask
    upstream_findings: dict[str, Finding]
    parent_thread_id: str
    child_thread_id: str
    todos: list[dict]
    wave_index: int
    replan_count: int
    remaining_waves: list[list[TeamTask]]

    history: list
    permission_mode: str
    scene_prompt: str | None
    profile_prompt: str
    workspace_path: str | None
    chat_model: Any
    subtask_runners: Any
    team_semaphore: Any
    subtask_timeout: int
    abort_event: Any
