"""Judge 协议定义。

所有评分器（AssertJudge / RubricJudge 等）需符合 ``Judge`` 协议。
CompositeJudge 是组合器，返回 ``list[JudgeResult]``，不实现本协议。已重命名为 ``JudgeChain``。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.eval.models import EvalCase, JudgeResult


@runtime_checkable
class Judge(Protocol):
    """评测评分器协议。"""

    async def evaluate(self, events: list[dict], case: EvalCase) -> JudgeResult:
        """评估事件列表，返回评分结果。"""
        ...
