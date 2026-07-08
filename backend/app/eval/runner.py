"""EvalRunner：in-process 调用 ``run_router`` 执行评测 case 并收集 SSE 事件。

设计要点（见 design.md §3）：
- **顺序执行**（非并行）：避免 checkpointer 写入冲突；case 间无状态共享。
- **chat_model 透传**：注入到 ``run_router``，支持离线 MockChatModel 评测。
- **独立 thread_id**：每个 case 用独立 thread_id，避免历史污染。
- **异常隔离**：单个 case 抛异常 → 记录 ``CaseResult.error``，不阻塞 suite。
- **超时控制**：用 ``asyncio.wait_for`` 包装 ``run_router`` 调用，超时算失败。

本模块仅负责"执行 + 收集事件"，不打分。打分由 ``judges/`` 模块负责，
``apply_judge_results`` 仅把 Judge 结果回填到 ``CaseResult``（passed/avg_score）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.eval.models import CaseResult, EvalCase, EvalResult, EvalSuite, JudgeResult
from app.observability.logger import logger

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

__all__ = ["EvalRunner"]


class EvalRunner:
    """评测执行器：in-process 调 ``run_router``，收集 SSE 事件。

    Args:
        checkpointer: 可选的 LangGraph checkpointer，透传到 ``run_router`` 和
            L3 ``SelfCorrectionRunner``。
        chat_model: 可选注入的 ChatModel（如 ``MockChatModel``）。None 时
            ``run_router`` 内部走 ``get_chat_model`` 调真实 LLM。
        grader_model: 可选注入的 grader ChatModel，供 L3 ``SelfCorrectionRunner`` 透传。
            None 时 ``SelfCorrectionRunner`` 内部调 ``_get_grader_model()``。
        workspace_path: 默认工作区路径，case 自身未指定时使用。
        default_timeout: 单 case 默认超时秒数，case.timeout 优先。
        permission_mode: 权限模式，透传到 ``run_router``。默认 ``"full_trust"``
            以避免 eval 模式下危险工具在审批处死锁（无人审批）。
        no_rubric: ``--no-rubric`` 标志，True 时 L3 分支不触发（避免无 API key
            时构造 ``RubricMiddleware`` 失败）。
    """

    def __init__(
        self,
        checkpointer: Any = None,
        chat_model: "BaseChatModel | None" = None,
        grader_model: "BaseChatModel | None" = None,
        workspace_path: str | None = None,
        default_timeout: float = 60.0,
        permission_mode: str = "full_trust",
        no_rubric: bool = False,
    ) -> None:
        self.checkpointer = checkpointer
        self.chat_model = chat_model
        self.grader_model = grader_model
        self.workspace_path = workspace_path
        self.default_timeout = default_timeout
        self.permission_mode = permission_mode
        self.no_rubric = no_rubric

    async def run_case(self, case: EvalCase) -> CaseResult:
        """执行单个 case。

        L3 自纠模式（``case.expect.self_correct and case.expect.rubric and not self.no_rubric``）
        走 ``SelfCorrectionRunner``；否则走 ``run_router``（默认路径）。

        异常 / 超时不抛出，而是记录到 ``CaseResult.error`` 返回。
        返回的 ``CaseResult.passed`` 默认 False、``avg_score`` 默认 0.0，
        由外层（``run_suite`` 或调用方）调 ``apply_judge_results`` 填充。

        Args:
            case: 评测用例。

        Returns:
            ``CaseResult``：含收集到的 events 列表；失败时 ``error`` 非空。
        """
        # L3 自纠模式：走 SelfCorrectionRunner
        if case.expect.self_correct and case.expect.rubric and not self.no_rubric:
            from app.eval.judges.self_correction import SelfCorrectionRunner

            sc_runner = SelfCorrectionRunner(
                chat_model=self.chat_model,
                grader_model=self.grader_model,
                max_iterations=case.expect.self_correct_max_iterations,
                checkpointer=self.checkpointer,
            )
            return await sc_runner.run_case(case)

        return await self._run_case_via_router(case)

    async def _run_case_via_router(self, case: EvalCase) -> CaseResult:
        """默认模式：in-process 调 ``run_router`` 收集 SSE 事件。"""
        # 延迟 import 避免循环依赖（run_router 依赖链较深）
        from app.router.graph import run_router

        thread_id = f"eval-{case.id}-{uuid.uuid4().hex[:8]}"
        workspace = case.workspace_path or self.workspace_path
        timeout = case.timeout if case.timeout > 0 else self.default_timeout

        events: list[dict[str, Any]] = []
        start = time.perf_counter()
        try:
            # 用 wait_for 包装整个事件流消费，超时算失败
            async def _consume() -> None:
                async for event in run_router(
                    message=case.user_message,
                    thread_id=thread_id,
                    agent_mode=case.agent_mode,
                    checkpointer=self.checkpointer,
                    workspace_path=workspace,
                    chat_model=self.chat_model,
                    permission_mode=self.permission_mode,
                ):
                    events.append(event)

            await asyncio.wait_for(_consume(), timeout=timeout)
        except asyncio.TimeoutError:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.warning("eval case timeout", case_id=case.id, timeout=timeout)
            return CaseResult(
                case=case,
                events=events,
                duration_ms=duration_ms,
                error=f"timeout after {timeout}s",
            )
        except Exception as exc:  # noqa: BLE001 - 单 case 异常隔离，不阻塞 suite
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.warning("eval case failed", case_id=case.id, error=str(exc))
            return CaseResult(
                case=case,
                events=events,
                duration_ms=duration_ms,
                error=str(exc),
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        return CaseResult(
            case=case,
            events=events,
            duration_ms=duration_ms,
        )

    async def run_suite(self, suite: EvalSuite) -> EvalResult:
        """顺序执行 suite 中所有 case（非并行，避免 checkpointer 写入冲突）。

        单个 case 失败（异常/超时）不影响其他 case，结果汇总到 ``EvalResult``。
        Judge 打分不在本方法职责内——调用方需自行调 ``apply_judge_results``
        或外部 ``CompositeJudge`` 填充 ``passed`` / ``avg_score``。

        Args:
            suite: 评测集。

        Returns:
            ``EvalResult``：含每个 case 的 ``CaseResult``（未经 Judge 打分）。
        """
        started_at = time.time()
        start_perf = time.perf_counter()
        case_results: list[CaseResult] = []
        for case in suite.cases:
            result = await self.run_case(case)
            case_results.append(result)
        duration_ms = int((time.perf_counter() - start_perf) * 1000)

        # started_at 用 datetime，但避免 import 顶部 datetime（models 已用）
        from datetime import datetime

        return EvalResult(
            suite_id=suite.id,
            started_at=datetime.fromtimestamp(started_at),
            duration_ms=duration_ms,
            case_results=case_results,
        )

    @staticmethod
    def apply_judge_results(
        case_result: CaseResult,
        judge_results: list[JudgeResult],
    ) -> CaseResult:
        """把 Judge 结果回填到 ``CaseResult``，返回更新后的新对象。

        规则（与 design.md §2 一致）：
        - ``passed`` = 所有 Judge 都 passed（空列表视为 False，保守策略）
        - ``avg_score`` = L2 Judge 的 score 平均值；无 L2 时 L1 全 passed → 5.0，
          否则 0.0
        - 有 ``error`` 的 case（执行失败）直接 passed=False、avg_score=0.0，
          Judge 结果仍保留在 ``judge_results`` 中供报告展示

        Args:
            case_result: 原始执行结果（passed/avg_score 为默认值）。
            judge_results: Judge 列表产出的结果。

        Returns:
            更新后的 ``CaseResult``（新实例，原对象不变）。
        """
        # 执行失败的 case 直接判失败，但仍保留 judge_results
        if case_result.error:
            return case_result.model_copy(
                update={
                    "judge_results": judge_results,
                    "passed": False,
                    "avg_score": 0.0,
                }
            )

        if not judge_results:
            # 无 Judge 结果：保守判失败
            return case_result.model_copy(
                update={
                    "judge_results": judge_results,
                    "passed": False,
                    "avg_score": 0.0,
                }
            )

        passed = all(jr.passed for jr in judge_results)
        # L2+L3 的 score 参与均值计算；无 L2/L3 时 L1 全 passed → 5.0，否则 0.0
        scored_results = [jr for jr in judge_results if jr.layer in ("L2", "L3")]
        if scored_results:
            avg_score = sum(jr.score for jr in scored_results) / len(scored_results)
        else:
            # 无 L2/L3：L1 全 passed → 5.0，否则 0.0
            avg_score = 5.0 if passed else 0.0

        return case_result.model_copy(
            update={
                "judge_results": judge_results,
                "passed": passed,
                "avg_score": avg_score,
            }
        )
