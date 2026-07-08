"""组合评分器：逐个执行多个 Judge，异常隔离。

单个 Judge 抛异常时，记录失败的 JudgeResult（layer=L1），不影响其他 Judge 执行。
注意：``CompositeJudge.evaluate`` 返回 ``list[JudgeResult]``，不实现 ``Judge`` 协议。
"""

from __future__ import annotations

from app.eval.judges.base import Judge
from app.eval.models import EvalCase, JudgeResult


class CompositeJudge:
    """组合多个 Judge，逐个执行，异常隔离。"""

    def __init__(self, judges: list[Judge]):
        self.judges = judges

    async def evaluate(self, events: list[dict], case: EvalCase) -> list[JudgeResult]:
        results: list[JudgeResult] = []
        for judge in self.judges:
            try:
                result = await judge.evaluate(events, case)
                results.append(result)
            except Exception as exc:  # noqa: BLE001 - 单 Judge 异常隔离
                results.append(
                    JudgeResult(
                        case_id=case.id,
                        passed=False,
                        score=0.0,
                        reason=f"judge error: {exc}",
                        layer="L1",
                    )
                )
        return results
