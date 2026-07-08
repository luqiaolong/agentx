"""L1 规则断言评分器：4 级断言（events / tools_called / tools_not_called / assertions）。

L1 必跑，无外部依赖（不调 LLM），纯规则匹配。
SSE 事件结构：``{"event": "tool_call", "data": '{"name": "read_file", "args": {...}}'}``，
``data`` 字段为 JSON 字符串时需解析后再做子集匹配。
"""

from __future__ import annotations

import json
from typing import Any

from app.eval.models import EvalCase, JudgeResult


def _parse_data(data: Any) -> Any:
    """解析 SSE 事件的 data 字段：JSON 字符串 → dict/list/标量；非 JSON 原样返回。"""
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return data
    return data


def _is_subset(subset: dict[str, Any], superset: dict[str, Any]) -> bool:
    """dict 子集匹配：subset 的每个 key/value 都在 superset 中存在且相等（浅层）。"""
    return all(superset.get(k) == v for k, v in subset.items())


class AssertJudge:
    """L1 必跑规则断言评分器，无外部依赖。

    4 级断言：
    1. events：按 type 匹配，校验 count_min/count_max，可选 data_contains（dict 子集）。
    2. tools_called：tool_call 事件中 name 列表需包含期望子集。
    3. tools_not_called：tool_call 事件中 name 列表需不含期望工具。
    4. assertions：对每个表达式用 eval() 执行，命名空间含 events/case，返回 bool。

    全部通过 → passed=True, score=5.0；任一失败 → passed=False, score=0.0。
    """

    async def evaluate(self, events: list[dict], case: EvalCase) -> JudgeResult:
        failures: list[str] = []

        self._check_events(events, case, failures)
        self._check_tools(events, case, failures)
        self._check_assertions(events, case, failures)

        if failures:
            return JudgeResult(
                case_id=case.id,
                passed=False,
                score=0.0,
                reason="; ".join(failures),
                layer="L1",
            )
        return JudgeResult(
            case_id=case.id,
            passed=True,
            score=5.0,
            reason="all assertions passed",
            layer="L1",
        )

    def _check_events(
        self, events: list[dict], case: EvalCase, failures: list[str]
    ) -> None:
        for assertion in case.expect.events:
            matched = [e for e in events if e.get("event") == assertion.type]
            if assertion.data_contains is not None:
                filtered: list[dict] = []
                for e in matched:
                    parsed = _parse_data(e.get("data"))
                    if isinstance(parsed, dict) and _is_subset(
                        assertion.data_contains, parsed
                    ):
                        filtered.append(e)
                matched = filtered
            count = len(matched)
            if count < assertion.count_min:
                failures.append(
                    f"event {assertion.type!r} count {count} < count_min {assertion.count_min}"
                )
            if assertion.count_max is not None and count > assertion.count_max:
                failures.append(
                    f"event {assertion.type!r} count {count} > count_max {assertion.count_max}"
                )

    def _check_tools(
        self, events: list[dict], case: EvalCase, failures: list[str]
    ) -> None:
        called_names: list[str] = []
        for e in events:
            if e.get("event") != "tool_call":
                continue
            data = _parse_data(e.get("data"))
            if isinstance(data, dict) and "name" in data:
                called_names.append(data["name"])

        for name in case.expect.tools_called:
            if name not in called_names:
                failures.append(f"expected tool {name!r} not called")

        for name in case.expect.tools_not_called:
            if name in called_names:
                failures.append(f"unexpected tool {name!r} was called")

    def _check_assertions(
        self, events: list[dict], case: EvalCase, failures: list[str]
    ) -> None:
        namespace = {"events": events, "case": case}
        for idx, expr in enumerate(case.expect.assertions):
            try:
                result = eval(expr, namespace)  # noqa: S307 - 评测框架受信表达式
            except Exception as exc:  # noqa: BLE001 - 表达式异常视为断言失败
                failures.append(f"assertion[{idx}] {expr!r} raised: {exc}")
                continue
            if not result:
                failures.append(f"assertion[{idx}] {expr!r} returned False")
