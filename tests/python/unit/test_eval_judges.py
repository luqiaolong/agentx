"""eval 评分器（Judge）单元测试。

覆盖：
- AssertJudge（L1）：events 断言 pass/fail、tools_called 子集匹配、tools_not_called、
  assertions 表达式 pass/fail、data_contains dict 子集匹配。
- RubricJudge（L2）：--no-rubric 降级、无 API key 降级（基础 smoke 测试，
  完整成功路径见 test_eval_rubric_judge.py）。
- CompositeJudge：多 Judge 组合、单 Judge 异常隔离、全部通过。

AssertJudge 无外部依赖；RubricJudge 降级逻辑用 MonkeyPatch mock 环境变量。
"""

from __future__ import annotations

import pytest

from app.eval.judges import AssertJudge, CompositeJudge, RubricJudge
from app.eval.models import (
    CaseExpect,
    EvalCase,
    EventAssertion,
    JudgeResult,
)

_LLM_KEY_ENVS = (
    "AGENTX_OPENAI_API_KEY",
    "AGENTX_DEEPSEEK_API_KEY",
    "AGENTX_KIMI_API_KEY",
    "AGENTX_GLM_API_KEY",
)


def _case(
    *,
    cid: str = "c1",
    user_message: str = "hi",
    agent_mode: str = "work",
    expect: CaseExpect | None = None,
) -> EvalCase:
    """构造测试用 EvalCase，expect 默认空。"""
    return EvalCase(
        id=cid,
        user_message=user_message,
        agent_mode=agent_mode,  # type: ignore[arg-type]
        expect=expect or CaseExpect(),
    )


# ---------------------------------------------------------------------------
# AssertJudge: events 断言
# ---------------------------------------------------------------------------


async def test_assert_judge_events_type_count_pass() -> None:
    """events 断言 pass：type 匹配 + count_min/count_max 满足。"""
    events = [
        {"event": "token", "data": "hello"},
        {"event": "token", "data": "world"},
        {"event": "done", "data": "{}"},
    ]
    case = _case(
        expect=CaseExpect(
            events=[
                EventAssertion(type="token", count_min=2),
                EventAssertion(type="done", count_min=1, count_max=1),
            ]
        )
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.case_id == "c1"
    assert result.layer == "L1"
    assert result.passed is True
    assert result.score == 5.0


async def test_assert_judge_events_count_below_min_fail() -> None:
    """events 断言 fail：count 不足 count_min。"""
    events = [{"event": "token", "data": "hi"}]
    case = _case(
        expect=CaseExpect(
            events=[EventAssertion(type="token", count_min=2)]
        )
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.passed is False
    assert result.score == 0.0
    assert "count 1 < count_min 2" in result.reason


async def test_assert_judge_events_count_exceeds_max_fail() -> None:
    """events 断言 fail：count 超过 count_max。"""
    events = [
        {"event": "done", "data": "{}"},
        {"event": "done", "data": "{}"},
    ]
    case = _case(
        expect=CaseExpect(
            events=[EventAssertion(type="done", count_min=1, count_max=1)]
        )
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.passed is False
    assert "count 2 > count_max 1" in result.reason


# ---------------------------------------------------------------------------
# AssertJudge: tools_called / tools_not_called 断言
# ---------------------------------------------------------------------------


async def test_assert_judge_tools_called_subset_pass() -> None:
    """tools_called 子集匹配 pass + tools_not_called pass。"""
    events = [
        {"event": "tool_call", "data": '{"name": "read_file", "args": {}}'},
        {"event": "tool_call", "data": '{"name": "list_files", "args": {}}'},
        {"event": "token", "data": "done"},
    ]
    case = _case(
        expect=CaseExpect(
            tools_called=["read_file"],
            tools_not_called=["write_file"],
        )
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.passed is True
    assert result.score == 5.0


async def test_assert_judge_tools_called_missing_fail() -> None:
    """tools_called 缺失 fail：期望 read_file 但未调用。"""
    events = [
        {"event": "tool_call", "data": '{"name": "list_files", "args": {}}'},
    ]
    case = _case(
        expect=CaseExpect(tools_called=["read_file"])
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.passed is False
    assert "'read_file' not called" in result.reason


async def test_assert_judge_tools_not_called_violation_fail() -> None:
    """tools_not_called 违规 fail：期望不调用 write_file 但调用了。"""
    events = [
        {"event": "tool_call", "data": '{"name": "write_file", "args": {}}'},
    ]
    case = _case(
        expect=CaseExpect(tools_not_called=["write_file"])
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.passed is False
    assert "'write_file' was called" in result.reason


# ---------------------------------------------------------------------------
# AssertJudge: assertions 表达式
# ---------------------------------------------------------------------------


async def test_assert_judge_assertions_pass() -> None:
    """assertions 表达式 pass：any/len 在命名空间中正确求值。"""
    events = [{"event": "token", "data": "hi"}, {"event": "done", "data": "{}"}]
    case = _case(
        expect=CaseExpect(
            assertions=[
                "any(e['event'] == 'token' for e in events)",
                "len(events) == 2",
                "case.id == 'c1'",
            ]
        )
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.passed is True


async def test_assert_judge_assertions_fail() -> None:
    """assertions 表达式 fail：返回 False 与抛异常都计入 failures。"""
    events = []
    case = _case(
        expect=CaseExpect(
            assertions=[
                "len(events) > 0",
                "undefined_name",  # NameError
            ]
        )
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.passed is False
    assert "returned False" in result.reason
    assert "raised" in result.reason


# ---------------------------------------------------------------------------
# AssertJudge: data_contains dict 子集匹配
# ---------------------------------------------------------------------------


async def test_assert_judge_data_contains_match_pass() -> None:
    """data_contains 子集匹配 pass：data JSON 解析后含期望键值。"""
    events = [
        {
            "event": "tool_call",
            "data": '{"name": "read_file", "args": {"path": "/tmp/a.txt"}}',
        },
    ]
    case = _case(
        expect=CaseExpect(
            events=[
                EventAssertion(
                    type="tool_call",
                    count_min=1,
                    data_contains={"name": "read_file"},
                )
            ]
        )
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.passed is True


async def test_assert_judge_data_contains_no_match_fail() -> None:
    """data_contains 不匹配 fail：data 中 name 不是期望值，filtered count=0。"""
    events = [
        {
            "event": "tool_call",
            "data": '{"name": "list_files", "args": {}}',
        },
    ]
    case = _case(
        expect=CaseExpect(
            events=[
                EventAssertion(
                    type="tool_call",
                    count_min=1,
                    data_contains={"name": "read_file"},
                )
            ]
        )
    )
    result = await AssertJudge().evaluate(events, case)
    assert result.passed is False
    assert "count 0 < count_min 1" in result.reason


# ---------------------------------------------------------------------------
# RubricJudge: 降级策略（基础 smoke 测试；完整成功路径见 test_eval_rubric_judge.py）
# ---------------------------------------------------------------------------


async def test_rubric_judge_no_rubric_flag_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--no-rubric 标志 → skipped（passed=True, score=5.0）。"""
    # 即使有 API key 也应 skipped
    monkeypatch.setenv("AGENTX_OPENAI_API_KEY", "fake-key")
    judge = RubricJudge(no_rubric=True)
    case = _case(expect=CaseExpect(rubric="some rubric"))
    result = await judge.evaluate([{"event": "token", "data": "hi"}], case)
    assert result.layer == "L2"
    assert result.passed is True
    assert result.score == 5.0
    assert "skipped" in result.reason
    assert "--no-rubric" in result.reason


async def test_rubric_judge_no_api_key_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """无任一 AGENTX_*_API_KEY → skipped。"""
    for key in _LLM_KEY_ENVS:
        monkeypatch.delenv(key, raising=False)
    judge = RubricJudge()
    case = _case(expect=CaseExpect(rubric="some rubric"))
    result = await judge.evaluate([{"event": "token", "data": "hi"}], case)
    assert result.layer == "L2"
    assert result.passed is True
    assert result.score == 5.0
    assert "skipped" in result.reason
    assert "no API key" in result.reason


# ---------------------------------------------------------------------------
# CompositeJudge: 组合 + 异常隔离
# ---------------------------------------------------------------------------


class _PassingJudge:
    """始终通过的 mock Judge。"""

    async def evaluate(self, events: list[dict], case: EvalCase) -> JudgeResult:
        return JudgeResult(
            case_id=case.id, passed=True, score=5.0, reason="ok", layer="L1"
        )


class _RaisingJudge:
    """总是抛异常的 mock Judge。"""

    async def evaluate(self, events: list[dict], case: EvalCase) -> JudgeResult:
        raise RuntimeError("boom")


async def test_composite_judge_exception_isolation() -> None:
    """单 Judge 异常隔离：抛异常的 Judge 记录失败，不影响其他 Judge。"""
    case = _case()
    events = [{"event": "token", "data": "hi"}]
    composite = CompositeJudge([_PassingJudge(), _RaisingJudge()])
    results = await composite.evaluate(events, case)
    assert len(results) == 2
    assert results[0].passed is True
    assert results[0].reason == "ok"
    assert results[1].passed is False
    assert results[1].score == 0.0
    assert "judge error: boom" in results[1].reason
    assert results[1].layer == "L1"


async def test_composite_judge_all_pass() -> None:
    """全部通过：AssertJudge + _PassingJudge 组合，结果数 = Judge 数，全 passed。"""
    events = [
        {"event": "token", "data": "hi"},
        {"event": "tool_call", "data": '{"name": "read_file", "args": {}}'},
    ]
    case = _case(
        expect=CaseExpect(
            events=[EventAssertion(type="token", count_min=1)],
            tools_called=["read_file"],
        )
    )
    composite = CompositeJudge([AssertJudge(), _PassingJudge()])
    results = await composite.evaluate(events, case)
    assert len(results) == 2
    assert all(r.passed for r in results)
    assert all(r.score == 5.0 for r in results)


async def test_composite_judge_with_rubric_judge_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CompositeJudge 组合 L1 + L2：无 API key 时 L2 skipped，L1 仍正常执行。"""
    for key in _LLM_KEY_ENVS:
        monkeypatch.delenv(key, raising=False)
    events = [{"event": "token", "data": "hi"}]
    case = _case(
        expect=CaseExpect(
            events=[EventAssertion(type="token", count_min=1)],
            rubric="some rubric",
        )
    )
    composite = CompositeJudge([AssertJudge(), RubricJudge()])
    results = await composite.evaluate(events, case)
    assert len(results) == 2
    assert results[0].layer == "L1"
    assert results[0].passed is True
    assert results[1].layer == "L2"
    assert results[1].passed is True  # skipped 不惩罚
    assert results[1].score == 5.0
