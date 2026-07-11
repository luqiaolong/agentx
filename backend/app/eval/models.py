"""eval 评测框架数据模型。

定义评测用例、评测集、评测结果、判分结果等核心数据结构。
所有模型基于 pydantic v2，支持 JSON 序列化/反序列化与 YAML 加载。

模块职责单一（AGENTS.md P2）：仅定义数据结构，不含执行/评分/输出逻辑。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# agent_mode 与后端 Router 场景分发对齐（见 AGENTS.md §12）
AgentMode = Literal["work", "coding", "coding_team"]

# 评测判分层级：L1=规则断言，L2=RubricJudge（deepagents GraderResponse），L3=SelfCorrectionRunner（RubricMiddleware）
JudgeLayer = Literal["L1", "L2", "L3"]


class EventAssertion(BaseModel):
    """SSE 事件断言：校验某类事件的出现次数与 data 字段。"""

    type: str
    count_min: int = 1
    count_max: int | None = None
    data_contains: dict[str, Any] | None = None


class CaseExpect(BaseModel):
    """单个 case 的期望结果，含 4 级 L1 断言字段 + 可选 L2 rubric / L3 自纠。

    L1 断言（必跑）：
    - events: SSE 事件断言
    - tools_called: 期望被调用的工具名（子集匹配）
    - tools_not_called: 期望不被调用的工具名
    - assertions: 自定义 Python 表达式（eval）

    L2（可选）：rubric — 自由文本完成标准，由 RubricJudge 用 deepagents GraderResponse 评分
    L3（可选）：self_correct — 启用 in-loop 自纠，由 SelfCorrectionRunner 用 RubricMiddleware 驱动
    """

    events: list[EventAssertion] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    tools_not_called: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)
    rubric: str | None = None
    self_correct: bool = False
    self_correct_max_iterations: int = 3


class EvalCase(BaseModel):
    """单个评测用例。"""

    id: str
    user_message: str
    # agent_mode 用 str 而非 Literal["work", "coding", "coding_team"]，
    # 以便 eval 框架测试非法 mode 触发 run_router 的 error 路径。
    agent_mode: str
    workspace_path: str | None = None
    expect: CaseExpect = Field(default_factory=CaseExpect)
    timeout: float = 60.0
    tags: list[str] = Field(default_factory=list)
    # 已执行的 SSE 事件轨迹（从 observation DB 导出）。非空时 EvalRunner.run_trace
    # 跳过 run_router 直接交给 Judge 打分，避免重跑已发生的会话。
    trace_events: list[dict[str, Any]] | None = None


class EvalSuite(BaseModel):
    """评测集：一组相关 case 的集合。"""

    id: str
    name: str
    description: str = ""
    cases: list[EvalCase] = Field(default_factory=list)


class JudgeResult(BaseModel):
    """单次判分结果。"""

    case_id: str
    passed: bool
    score: float = 0.0  # 0-5
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    layer: JudgeLayer = "L1"


class CaseResult(BaseModel):
    """单个 case 的执行结果：收集到的事件 + 所有判分结果。"""

    case: EvalCase
    events: list[dict[str, Any]] = Field(default_factory=list)
    judge_results: list[JudgeResult] = Field(default_factory=list)
    passed: bool = False
    avg_score: float = 0.0
    duration_ms: int = 0
    error: str | None = None


class EvalResult(BaseModel):
    """整次评测运行的汇总结果。"""

    suite_id: str
    started_at: datetime
    duration_ms: int = 0
    case_results: list[CaseResult] = Field(default_factory=list)
