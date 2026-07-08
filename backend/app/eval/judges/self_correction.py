"""L3 SelfCorrectionRunner：用 ``RubricMiddleware`` 驱动 agent 自我迭代。

deepagents 0.6.12 的 ``RubricMiddleware`` 在 agent 执行后用 grader 子代理评审
transcript 是否满足 rubric，``needs_revision`` 则把反馈回传 agent 重新执行，
直到 ``satisfied`` 或 ``max_iterations`` 耗尽。

核心流程：
1. 降级检查（无 chat_model + 无 API key / 无 grader_model + 无 API key → skipped）
2. ``create_deep_agent(model, middleware=[RubricMiddleware(model=grader, max_iterations=N)], checkpointer=...)``
3. ``agent.astream({"messages": [HumanMessage], "rubric": rubric}, config)`` 收集事件
4. ``agent.aget_state(config).values`` 读 ``_rubric_status`` + ``_rubric_evaluations``
5. 映射 ``RubricResult`` → ``CaseResult``（含 L3 ``JudgeResult``）

降级策略（任一触发即 skipped，passed=True, score=5.0，不惩罚用例）：
- 无 ``chat_model`` 且 ``get_chat_model()`` 抛 ValueError
- 无 ``grader_model`` 且 ``get_chat_model(temperature=0)`` 抛 ValueError
- ``--no-rubric`` 标志（``EvalRunner`` 不进入 L3 分支，此处不检查）

> ``RubricMiddleware(model=None)`` 会抛 ``ValueError``，所以降级检查必须在
> 构造中间件之前。
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

from app.eval.models import CaseResult, EvalCase, JudgeResult

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = ["SelfCorrectionRunner"]


def _get_chat_model() -> "BaseChatModel":
    """获取 agent 用的 ChatModel。失败抛 ValueError。"""
    from app.llm import get_chat_model

    return get_chat_model()


def _get_grader_model() -> "BaseChatModel":
    """获取 grader 用的 ChatModel（temperature=0）。失败抛 ValueError。"""
    from app.llm import get_chat_model

    return get_chat_model(temperature=0)


def _skipped_case(case: EvalCase, reason: str, duration_ms: int = 0) -> CaseResult:
    """构造 L3 skipped 结果（passed=True, score=5.0，不惩罚用例）。

    返回的 ``CaseResult`` 已含一条 L3 ``JudgeResult``（skipped），``passed=True``、
    ``avg_score=5.0``，避免外层 ``apply_judge_results`` 再次计算。
    """
    skipped_jr = JudgeResult(
        case_id=case.id,
        passed=True,
        score=5.0,
        reason=f"L3 skipped: {reason}",
        layer="L3",
    )
    return CaseResult(
        case=case,
        events=[],
        judge_results=[skipped_jr],
        passed=True,
        avg_score=5.0,
        duration_ms=duration_ms,
    )


def _normalize_event(event: Any) -> dict[str, Any]:
    """把 ``astream`` 产出的 event 归一化为 dict（eval 框架统一事件格式）。

    ``create_deep_agent`` 的 ``astream`` 默认 yield state dict（含 ``messages`` 等），
    本函数提取关键字段为 eval 事件格式：
    - 含 ``messages`` 且最后一条是 ``AIMessage`` → ``{"event": "token", "data": content}``
    - 含 ``messages`` 且最后一条是 ``ToolMessage`` → ``{"event": "tool_result", ...}``
    - 含 ``messages`` 且最后一条有 ``tool_calls`` → ``{"event": "tool_call", ...}``
    - 其他 → ``{"event": "state", "data": str(event)}``
    """
    if isinstance(event, dict):
        messages = event.get("messages")
        if messages:
            last = messages[-1]
            content = getattr(last, "content", "")
            tool_calls = getattr(last, "tool_calls", None) or []
            # 区分 AIMessage / ToolMessage
            msg_type = type(last).__name__
            if msg_type == "ToolMessage":
                return {
                    "event": "tool_result",
                    "tool_call_id": getattr(last, "tool_call_id", ""),
                    "result": content if isinstance(content, str) else str(content),
                }
            if tool_calls:
                # AIMessage with tool_calls
                tc = tool_calls[-1]
                return {
                    "event": "tool_call",
                    "tool": tc.get("name", ""),
                    "args": tc.get("args", {}),
                    "tool_call_id": tc.get("id", ""),
                }
            # AIMessage 纯文本
            return {
                "event": "token",
                "data": content if isinstance(content, str) else str(content),
            }
        # 非 messages 事件（如 rubric_evaluation）
        if "rubric_evaluation" in event:
            return {"event": "rubric_evaluation", "data": str(event)}
        return {"event": "state", "data": str(event)}
    return {"event": "unknown", "data": str(event)}


def _map_rubric_status(
    rubric_status: str | None,
    rubric_evaluations: list[dict],
    case: EvalCase,
) -> JudgeResult:
    """映射 ``_rubric_status`` → L3 ``JudgeResult``。

    | _rubric_status | passed | score | reason |
    |---|---|---|---|
    | satisfied | True | 5.0 | self-correction satisfied |
    | max_iterations_reached | False | 3.0 | exhausted N iterations |
    | failed | False | 0.0 | rubric malformed/impossible |
    | grader_error | False | 0.0 | grader error |
    | None / needs_revision | False | 0.0 | middleware not triggered |
    """
    iterations = len(rubric_evaluations)
    details: dict[str, Any] = {
        "rubric_status": rubric_status,
        "iterations": iterations,
        "evaluations": [
            {
                "iteration": ev.get("iteration", i),
                "result": ev.get("result", ""),
                "explanation": ev.get("explanation", ""),
                "criteria": ev.get("criteria", []),
            }
            for i, ev in enumerate(rubric_evaluations)
        ],
    }

    if rubric_status == "satisfied":
        return JudgeResult(
            case_id=case.id,
            passed=True,
            score=5.0,
            reason=f"self-correction satisfied after {iterations} iteration(s)",
            details=details,
            layer="L3",
        )

    if rubric_status == "max_iterations_reached":
        return JudgeResult(
            case_id=case.id,
            passed=False,
            score=3.0,
            reason=f"self-correction exhausted {iterations} iteration(s)",
            details=details,
            layer="L3",
        )

    if rubric_status == "failed":
        return JudgeResult(
            case_id=case.id,
            passed=False,
            score=0.0,
            reason="self-correction failed: rubric malformed or impossible",
            details=details,
            layer="L3",
        )

    if rubric_status == "grader_error":
        explanation = ""
        if rubric_evaluations:
            explanation = rubric_evaluations[-1].get("explanation", "")
        return JudgeResult(
            case_id=case.id,
            passed=False,
            score=0.0,
            reason=f"self-correction grader error: {explanation}",
            details=details,
            layer="L3",
        )

    # None / needs_revision（未达终态）/ 未知
    return JudgeResult(
        case_id=case.id,
        passed=False,
        score=0.0,
        reason=f"self-correction not triggered (status={rubric_status!r})",
        details=details,
        layer="L3",
    )


class SelfCorrectionRunner:
    """L3 in-loop 自纠评测：用 ``RubricMiddleware`` 驱动 agent 自我迭代。

    Args:
        chat_model: 可选注入的 agent ChatModel。None 时用 ``get_chat_model()``。
        grader_model: 可选注入的 grader ChatModel。None 时用
            ``get_chat_model(temperature=0)``。必须可用，否则降级 skipped。
        max_iterations: 自纠最大迭代次数（``RubricMiddleware`` hard cap 20）。
        checkpointer: 可选的 LangGraph checkpointer，透传到 ``create_deep_agent``。
            None 时用 ``MemorySaver()``（L3 需要读 ``_rubric_status``，必须传 checkpointer）。
    """

    def __init__(
        self,
        chat_model: "BaseChatModel | None" = None,
        grader_model: "BaseChatModel | None" = None,
        max_iterations: int = 3,
        checkpointer: Any = None,
    ) -> None:
        self.chat_model = chat_model
        self.grader_model = grader_model
        # RubricMiddleware hard cap 20
        self.max_iterations = min(max(1, max_iterations), 20)
        self.checkpointer = checkpointer

    async def run_case(self, case: EvalCase, tools: list | None = None) -> CaseResult:
        """执行 L3 自纠评测。

        Args:
            case: 评测用例（必须含 ``expect.rubric`` 与 ``expect.self_correct=True``）。
            tools: 可选工具列表（透传到 ``create_deep_agent``）。

        Returns:
            ``CaseResult``：含 L3 ``JudgeResult``（satisfied / max_iterations_reached /
            failed / grader_error / skipped）。
        """
        from langchain_core.messages import HumanMessage

        start = time.perf_counter()

        # 0. 降级检查：无 chat_model + 无 API key → skipped
        try:
            model = self.chat_model or _get_chat_model()
        except (ValueError, RuntimeError) as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return _skipped_case(case, f"no chat model: {exc}", duration_ms)

        # 降级检查：无 grader_model + 无 API key → skipped
        # 必须在构造 RubricMiddleware 之前（model=None 会抛 ValueError）
        try:
            grader_model = self.grader_model or _get_grader_model()
        except (ValueError, RuntimeError) as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return _skipped_case(case, f"no grader model: {exc}", duration_ms)

        # 1. 构建 agent with RubricMiddleware
        from deepagents import RubricMiddleware, create_deep_agent

        rubric_middleware = RubricMiddleware(
            model=grader_model,
            max_iterations=self.max_iterations,
        )

        # checkpointer 必须传入（L3 需要读 _rubric_status）
        checkpointer = self.checkpointer
        if checkpointer is None:
            try:
                from langgraph.checkpoint.memory import MemorySaver
            except ImportError:
                from langgraph.checkpoint.memory import (
                    MemorySaver as _MemorySaver,
                )

                MemorySaver = _MemorySaver  # type: ignore[assignment]
            checkpointer = MemorySaver()

        agent = create_deep_agent(
            model=model,
            tools=tools or [],
            middleware=[rubric_middleware],
            system_prompt="你是 AgentX 评测目标 agent。请根据用户请求完成任务。",
            checkpointer=checkpointer,
        )

        # 2. 调用 agent，传入 rubric（rubric 是 RubricState 的顶层字段）
        thread_id = f"eval-sc-{case.id}-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}

        events: list[dict[str, Any]] = []
        try:
            async for event in agent.astream(
                {
                    "messages": [HumanMessage(content=case.user_message)],
                    "rubric": case.expect.rubric or "",
                },
                config=config,
            ):
                events.append(_normalize_event(event))
        except Exception as exc:  # noqa: BLE001 - agent 执行异常隔离
            duration_ms = int((time.perf_counter() - start) * 1000)
            error_jr = JudgeResult(
                case_id=case.id,
                passed=False,
                score=0.0,
                reason=f"L3 agent error: {exc}",
                layer="L3",
            )
            return CaseResult(
                case=case,
                events=events,
                judge_results=[error_jr],
                passed=False,
                avg_score=0.0,
                duration_ms=duration_ms,
                error=str(exc),
            )

        # 3. 读取最终 rubric 状态（依赖 checkpointer 持久化）
        try:
            final_state = await agent.aget_state(config)
            state_values = final_state.values if final_state else {}
            rubric_status = state_values.get("_rubric_status")
            rubric_evaluations = state_values.get("_rubric_evaluations", []) or []
        except Exception as exc:  # noqa: BLE001 - aget_state 失败降级为 grader_error
            duration_ms = int((time.perf_counter() - start) * 1000)
            error_jr = JudgeResult(
                case_id=case.id,
                passed=False,
                score=0.0,
                reason=f"L3 aget_state error: {exc}",
                layer="L3",
            )
            return CaseResult(
                case=case,
                events=events,
                judge_results=[error_jr],
                passed=False,
                avg_score=0.0,
                duration_ms=duration_ms,
            )

        # 4. 映射 → CaseResult + JudgeResult
        duration_ms = int((time.perf_counter() - start) * 1000)
        jr = _map_rubric_status(rubric_status, rubric_evaluations, case)

        return CaseResult(
            case=case,
            events=events,
            judge_results=[jr],
            passed=jr.passed,
            avg_score=jr.score,
            duration_ms=duration_ms,
        )
