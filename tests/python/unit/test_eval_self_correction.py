"""L3 SelfCorrectionRunner 单元测试。

覆盖 design.md §7.2：
- 降级策略：无 chat_model / 无 grader_model → skipped（passed=True, score=5.0）
- RubricResult 映射：satisfied / max_iterations_reached / failed / grader_error / not_triggered
- agent 异常隔离：astream 抛异常 → CaseResult.error 填充 + L3 error JudgeResult

用 MockChatModel + monkeypatch create_deep_agent / RubricMiddleware 模拟 agent，
零外部 LLM 调用。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from app.eval.judges.self_correction import SelfCorrectionRunner
from app.eval.mocks.llm import MockChatModel
from app.eval.models import CaseExpect, EvalCase


def _make_case() -> EvalCase:
    """构造启用 L3 自纠的测试用 EvalCase。"""
    return EvalCase(
        id="c1",
        user_message="hi",
        agent_mode="coding",
        expect=CaseExpect(rubric="test rubric", self_correct=True),
    )


class _FakeState:
    """模拟 agent.aget_state 返回值，含 .values dict。"""

    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values


class _FakeAgent:
    """模拟 create_deep_agent 返回的 agent。

    - astream: async generator，yield 预设的 state dict（含 messages）
    - aget_state: async method，返回 _FakeState（含 _rubric_status / _rubric_evaluations）
    """

    def __init__(
        self,
        *,
        rubric_status: str | None = "satisfied",
        rubric_evaluations: list[dict[str, Any]] | None = None,
        stream_events: list[dict[str, Any]] | None = None,
        raise_on_stream: bool = False,
    ) -> None:
        self._rubric_status = rubric_status
        self._rubric_evaluations = rubric_evaluations or []
        self._stream_events = stream_events or [
            {"messages": [AIMessage(content="response")]}
        ]
        self._raise_on_stream = raise_on_stream

    async def astream(self, input: Any, config: Any = None) -> Any:
        if self._raise_on_stream:
            raise RuntimeError("agent boom")
        for event in self._stream_events:
            yield event

    async def aget_state(self, config: Any = None) -> _FakeState:
        return _FakeState(
            {
                "_rubric_status": self._rubric_status,
                "_rubric_evaluations": self._rubric_evaluations,
            }
        )


def _patch_deepagents(
    monkeypatch: pytest.MonkeyPatch,
    fake_agent: _FakeAgent,
) -> None:
    """monkeypatch deepagents.RubricMiddleware + create_deep_agent。

    - RubricMiddleware → MagicMock（避免真实中间件构造）
    - create_deep_agent → lambda 返回 fake_agent
    """
    monkeypatch.setattr("deepagents.RubricMiddleware", MagicMock())
    monkeypatch.setattr("deepagents.create_deep_agent", lambda **kw: fake_agent)


def _make_evaluations(count: int, result: str = "needs_revision") -> list[dict[str, Any]]:
    """构造 N 条 rubric_evaluations（iteration 0..N-1）。"""
    return [
        {
            "iteration": i,
            "result": result,
            "explanation": f"iteration {i} explanation",
            "criteria": [],
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# 降级测试
# ---------------------------------------------------------------------------


async def test_self_correction_skipped_no_chat_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_get_chat_model 抛 ValueError → skipped（passed=True, score=5.0）。"""

    def _raise() -> None:
        raise ValueError("no chat model configured")

    monkeypatch.setattr("app.eval.judges.self_correction._get_chat_model", _raise)

    runner = SelfCorrectionRunner()
    case = _make_case()
    result = await runner.run_case(case)

    assert result.passed is True
    assert result.avg_score == 5.0
    assert len(result.judge_results) == 1
    jr = result.judge_results[0]
    assert jr.layer == "L3"
    assert jr.passed is True
    assert jr.score == 5.0
    assert "skipped" in jr.reason
    assert "no chat model" in jr.reason


async def test_self_correction_skipped_no_grader_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_model 可用但 _get_grader_model 抛 ValueError → skipped。"""

    def _raise() -> None:
        raise ValueError("no grader model configured")

    monkeypatch.setattr("app.eval.judges.self_correction._get_grader_model", _raise)

    runner = SelfCorrectionRunner(chat_model=MockChatModel())
    case = _make_case()
    result = await runner.run_case(case)

    assert result.passed is True
    assert result.avg_score == 5.0
    assert len(result.judge_results) == 1
    jr = result.judge_results[0]
    assert jr.layer == "L3"
    assert jr.passed is True
    assert jr.score == 5.0
    assert "skipped" in jr.reason
    assert "no grader model" in jr.reason


# ---------------------------------------------------------------------------
# RubricResult 映射测试
# ---------------------------------------------------------------------------


async def test_self_correction_satisfied(monkeypatch: pytest.MonkeyPatch) -> None:
    """_rubric_status=satisfied → passed=True, score=5.0, layer=L3。"""
    evaluations = [
        {"iteration": 0, "result": "satisfied", "explanation": "ok", "criteria": []},
    ]
    fake_agent = _FakeAgent(
        rubric_status="satisfied",
        rubric_evaluations=evaluations,
    )
    _patch_deepagents(monkeypatch, fake_agent)

    runner = SelfCorrectionRunner(
        chat_model=MockChatModel(),
        grader_model=MockChatModel(
            grader_responses=[
                {"result": "satisfied", "explanation": "ok", "criteria": []},
            ]
        ),
        checkpointer=MagicMock(),
    )
    case = _make_case()
    result = await runner.run_case(case)

    assert result.passed is True
    assert result.avg_score == 5.0
    assert len(result.judge_results) == 1
    jr = result.judge_results[0]
    assert jr.layer == "L3"
    assert jr.passed is True
    assert jr.score == 5.0
    assert "satisfied" in jr.reason
    assert jr.details["rubric_status"] == "satisfied"
    assert jr.details["iterations"] == 1


async def test_self_correction_max_iterations_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_rubric_status=max_iterations_reached → passed=False, score=3.0。"""
    evaluations = _make_evaluations(3, "needs_revision")
    fake_agent = _FakeAgent(
        rubric_status="max_iterations_reached",
        rubric_evaluations=evaluations,
    )
    _patch_deepagents(monkeypatch, fake_agent)

    runner = SelfCorrectionRunner(
        chat_model=MockChatModel(),
        grader_model=MockChatModel(),
        checkpointer=MagicMock(),
    )
    case = _make_case()
    result = await runner.run_case(case)

    assert result.passed is False
    assert result.avg_score == 3.0
    assert len(result.judge_results) == 1
    jr = result.judge_results[0]
    assert jr.layer == "L3"
    assert jr.passed is False
    assert jr.score == 3.0
    assert "exhausted" in jr.reason
    assert jr.details["rubric_status"] == "max_iterations_reached"
    assert jr.details["iterations"] == 3


async def test_self_correction_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """_rubric_status=failed → passed=False, score=0.0。"""
    evaluations = [
        {"iteration": 0, "result": "failed", "explanation": "bad rubric", "criteria": []},
    ]
    fake_agent = _FakeAgent(
        rubric_status="failed",
        rubric_evaluations=evaluations,
    )
    _patch_deepagents(monkeypatch, fake_agent)

    runner = SelfCorrectionRunner(
        chat_model=MockChatModel(),
        grader_model=MockChatModel(),
        checkpointer=MagicMock(),
    )
    case = _make_case()
    result = await runner.run_case(case)

    assert result.passed is False
    assert result.avg_score == 0.0
    assert len(result.judge_results) == 1
    jr = result.judge_results[0]
    assert jr.layer == "L3"
    assert jr.passed is False
    assert jr.score == 0.0
    assert "failed" in jr.reason
    assert jr.details["rubric_status"] == "failed"


async def test_self_correction_grader_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_rubric_status=grader_error → passed=False, score=0.0。"""
    evaluations = [
        {
            "iteration": 0,
            "result": "grader_error",
            "explanation": "ConnectionError: timeout",
            "criteria": [],
        },
    ]
    fake_agent = _FakeAgent(
        rubric_status="grader_error",
        rubric_evaluations=evaluations,
    )
    _patch_deepagents(monkeypatch, fake_agent)

    runner = SelfCorrectionRunner(
        chat_model=MockChatModel(),
        grader_model=MockChatModel(),
        checkpointer=MagicMock(),
    )
    case = _make_case()
    result = await runner.run_case(case)

    assert result.passed is False
    assert result.avg_score == 0.0
    assert len(result.judge_results) == 1
    jr = result.judge_results[0]
    assert jr.layer == "L3"
    assert jr.passed is False
    assert jr.score == 0.0
    assert "grader error" in jr.reason
    assert "ConnectionError" in jr.reason
    assert jr.details["rubric_status"] == "grader_error"


async def test_self_correction_not_triggered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_rubric_status=None → passed=False, score=0.0（middleware 未触发）。"""
    fake_agent = _FakeAgent(
        rubric_status=None,
        rubric_evaluations=[],
    )
    _patch_deepagents(monkeypatch, fake_agent)

    runner = SelfCorrectionRunner(
        chat_model=MockChatModel(),
        grader_model=MockChatModel(),
        checkpointer=MagicMock(),
    )
    case = _make_case()
    result = await runner.run_case(case)

    assert result.passed is False
    assert result.avg_score == 0.0
    assert len(result.judge_results) == 1
    jr = result.judge_results[0]
    assert jr.layer == "L3"
    assert jr.passed is False
    assert jr.score == 0.0
    assert "not triggered" in jr.reason
    assert jr.details["rubric_status"] is None
    assert jr.details["iterations"] == 0


# ---------------------------------------------------------------------------
# agent 异常隔离测试
# ---------------------------------------------------------------------------


async def test_self_correction_agent_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """astream 抛异常 → CaseResult.error 填充 + L3 error JudgeResult。"""
    fake_agent = _FakeAgent(raise_on_stream=True)
    _patch_deepagents(monkeypatch, fake_agent)

    runner = SelfCorrectionRunner(
        chat_model=MockChatModel(),
        grader_model=MockChatModel(),
        checkpointer=MagicMock(),
    )
    case = _make_case()
    result = await runner.run_case(case)

    assert result.passed is False
    assert result.avg_score == 0.0
    assert result.error is not None
    assert "agent boom" in result.error
    assert len(result.judge_results) == 1
    jr = result.judge_results[0]
    assert jr.layer == "L3"
    assert jr.passed is False
    assert jr.score == 0.0
    assert "L3 agent error" in jr.reason
    assert "agent boom" in jr.reason
