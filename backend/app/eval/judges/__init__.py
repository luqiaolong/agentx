"""eval 评测评分器：Judge 协议 + AssertJudge + RubricJudge + SelfCorrectionRunner + JudgeChain。

- ``Judge``：评分器协议（Protocol）。
- ``AssertJudge``：L1 必跑规则断言（events / tools_called / assertions）。
- ``RubricJudge``：L2 可选 rubric 评分（deepagents GraderResponse，无 API key 降级 skipped）。
- ``SelfCorrectionRunner``：L3 in-loop 自纠（deepagents RubricMiddleware 驱动迭代）。
- ``JudgeChain``：Judge 链，逐个执行 Judge 并异常隔离，返回 ``list[JudgeResult]``。
"""

from __future__ import annotations

from app.eval.judges.assert_judge import AssertJudge
from app.eval.judges.base import Judge
from app.eval.judges.composite import JudgeChain
from app.eval.judges.rubric_judge import RubricJudge
from app.eval.judges.self_correction import SelfCorrectionRunner

__all__ = [
    "Judge",
    "AssertJudge",
    "RubricJudge",
    "SelfCorrectionRunner",
    "JudgeChain",
]