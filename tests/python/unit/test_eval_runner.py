"""EvalRunner 单元测试。

覆盖（design.md §3 + §13）：
1. ``run_case`` 收集 SSE 事件（mock run_router）
2. ``run_case`` 异常 → ``CaseResult.error`` 填充，不抛出
3. ``run_case`` 超时 → ``CaseResult.error`` 填充 timeout 信息
4. ``run_suite`` 顺序执行所有 case，汇总到 ``EvalResult``
5. ``chat_model=None`` 时不注入（透传 None 到 run_router）
6. ``chat_model`` 非 None 时透传到 run_router
7. ``apply_judge_results`` 回填 passed/avg_score（L1 全 passed、含 L2、含 L3、error case）
8. ``run_case`` L3 自纠分支：``self_correct=True`` + ``rubric`` + 非 ``no_rubric`` 时
   走 ``SelfCorrectionRunner``；``no_rubric=True`` 时回退到 ``run_router``

所有测试通过 ``monkeypatch`` 替换 ``app.router.graph.run_router`` 为 mock 异步生成器，
不调真实 LLM / 沙箱 / 工具。
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.eval.models import (
    CaseExpect,
    CaseResult,
    EvalCase,
    EvalSuite,
    JudgeResult,
)
from app.eval.runner import EvalRunner


# ============================================================
# 辅助函数
# ============================================================


def _case(
    *,
    cid: str = "c1",
    user_message: str = "hi",
    agent_mode: str = "work",
    timeout: float = 60.0,
) -> EvalCase:
    """构造测试用 EvalCase。"""
    return EvalCase(
        id=cid,
        user_message=user_message,
        agent_mode=agent_mode,  # type: ignore[arg-type]
        expect=CaseExpect(),
        timeout=timeout,
    )


def _make_fake_run_router(
    events: list[dict[str, str]],
    captured: dict | None = None,
    raise_exc: Exception | None = None,
    delay: float = 0.0,
):
    """构造 fake run_router：yield 指定 events，可记录入参，可抛异常。

    Args:
        events: 要 yield 的事件列表。
        captured: 若提供，记录 message/thread_id/agent_mode/workspace_path/chat_model。
        raise_exc: 若提供，在 yield 前抛出该异常。
        delay: 每个 event 前的 sleep 秒数（用于测超时）。
    """

    async def _fake_run_router(
        message: str,
        thread_id: str,
        checkpointer: Any = None,
        permission_mode: str = "standard",
        agent_mode: str = "work",
        workspace_path: str | None = None,
        revoked_paths: list[str] | None = None,
        chat_model: Any = None,
    ) -> AsyncIterator[dict[str, str]]:
        if captured is not None:
            captured["message"] = message
            captured["thread_id"] = thread_id
            captured["agent_mode"] = agent_mode
            captured["workspace_path"] = workspace_path
            captured["chat_model"] = chat_model
        if raise_exc is not None:
            raise raise_exc
        for ev in events:
            if delay > 0:
                import asyncio

                await asyncio.sleep(delay)
            yield ev

    return _fake_run_router


@pytest.fixture
def base_case_result() -> CaseResult:
    """构造一个基础 CaseResult（events 含 done），供 apply_judge_results 测试用。"""
    return CaseResult(
        case=_case(),
        events=[{"event": "done", "data": "{}"}],
    )


# ============================================================
# 1. run_case 收集 SSE 事件
# ============================================================


async def test_run_case_collects_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_case 收集 run_router 产出的所有 SSE 事件。"""
    events = [
        {"event": "token", "data": "你好"},
        {"event": "token", "data": "世界"},
        {"event": "done", "data": "{}"},
    ]
    monkeypatch.setattr("app.router.graph.run_router", _make_fake_run_router(events))

    runner = EvalRunner()
    result = await runner.run_case(_case())

    assert result.error is None
    assert len(result.events) == 3
    assert result.events[0]["event"] == "token"
    assert result.events[2]["event"] == "done"
    assert result.duration_ms >= 0
    # 未经 Judge 打分：passed 默认 False、avg_score 默认 0.0
    assert result.passed is False
    assert result.avg_score == 0.0


async def test_run_case_passes_case_fields_to_run_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_case 把 case.user_message / agent_mode / workspace_path 透传到 run_router。"""
    captured: dict = {}
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([{"event": "done", "data": "{}"}], captured=captured),
    )

    runner = EvalRunner(workspace_path="/default/ws")
    case = EvalCase(
        id="c-fields",
        user_message="读取文件",
        agent_mode="coding",
        workspace_path="/custom/ws",
        expect=CaseExpect(),
    )
    await runner.run_case(case)

    assert captured["message"] == "读取文件"
    assert captured["agent_mode"] == "coding"
    assert captured["workspace_path"] == "/custom/ws"
    # thread_id 应以 eval- 前缀开头
    assert captured["thread_id"].startswith("eval-c-fields-")


async def test_run_case_uses_runner_workspace_when_case_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """case.workspace_path 为 None 时，用 runner.workspace_path。"""
    captured: dict = {}
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([{"event": "done", "data": "{}"}], captured=captured),
    )

    runner = EvalRunner(workspace_path="/runner/ws")
    case = EvalCase(
        id="c-ws",
        user_message="hi",
        agent_mode="work",
        workspace_path=None,
        expect=CaseExpect(),
    )
    await runner.run_case(case)

    assert captured["workspace_path"] == "/runner/ws"


# ============================================================
# 2. run_case 异常 / 超时 → CaseResult.error
# ============================================================


async def test_run_case_exception_recorded_in_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_router 抛异常 → CaseResult.error 填充，不抛出。"""
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([], raise_exc=RuntimeError("boom")),
    )

    runner = EvalRunner()
    result = await runner.run_case(_case())

    assert result.error is not None
    assert "boom" in result.error
    assert result.events == []
    assert result.passed is False


async def test_run_case_timeout_recorded_in_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """case 超时 → CaseResult.error 含 timeout 信息。"""
    # fake run_router 在 yield 前 sleep，触发 wait_for 超时
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router(
            [{"event": "token", "data": "partial"}],
            delay=0.5,
        ),
    )

    runner = EvalRunner()
    case = EvalCase(
        id="c-timeout",
        user_message="hi",
        agent_mode="work",
        expect=CaseExpect(),
        timeout=0.1,  # 100ms 超时
    )
    result = await runner.run_case(case)

    assert result.error is not None
    assert "timeout" in result.error.lower()


# ============================================================
# 3. chat_model 透传
# ============================================================


async def test_run_case_chat_model_none_transparently_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_model=None 时，透传 None 到 run_router（即用真实 LLM）。"""
    captured: dict = {}
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([{"event": "done", "data": "{}"}], captured=captured),
    )

    runner = EvalRunner(chat_model=None)
    await runner.run_case(_case())

    assert captured["chat_model"] is None


async def test_run_case_chat_model_injected_to_run_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_model 非 None 时，透传到 run_router。"""
    captured: dict = {}
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([{"event": "done", "data": "{}"}], captured=captured),
    )

    mock_model = object()  # 任意非 None 标记
    runner = EvalRunner(chat_model=mock_model)  # type: ignore[arg-type]
    await runner.run_case(_case())

    assert captured["chat_model"] is mock_model


# ============================================================
# 4. run_suite 顺序执行
# ============================================================


async def test_run_suite_executes_cases_sequentially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_suite 顺序执行所有 case，汇总到 EvalResult。"""
    call_order: list[str] = []

    def _fake(events: list[dict[str, str]]):
        async def _gen(
            message: str,
            thread_id: str,
            **kwargs: Any,
        ) -> AsyncIterator[dict[str, str]]:
            call_order.append(thread_id)
            for ev in events:
                yield ev

        return _gen

    monkeypatch.setattr(
        "app.router.graph.run_router",
        _fake([{"event": "done", "data": "{}"}]),
    )

    suite = EvalSuite(
        id="s1",
        name="test suite",
        description="sequential",
        cases=[_case(cid="c1"), _case(cid="c2"), _case(cid="c3")],
    )
    runner = EvalRunner()
    result = await runner.run_suite(suite)

    assert result.suite_id == "s1"
    assert len(result.case_results) == 3
    # 顺序执行：c1 先于 c2 先于 c3
    assert len(call_order) == 3
    assert call_order[0].startswith("eval-c1-")
    assert call_order[1].startswith("eval-c2-")
    assert call_order[2].startswith("eval-c3-")
    assert result.duration_ms >= 0


async def test_run_suite_case_failure_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单个 case 异常不阻塞 suite 中后续 case。"""

    def _fake(events: list[dict[str, str]], raise_on_msg: str | None = None):
        async def _gen(
            message: str,
            thread_id: str,
            **kwargs: Any,
        ) -> AsyncIterator[dict[str, str]]:
            if raise_on_msg and message == raise_on_msg:
                raise RuntimeError("case failed")
            for ev in events:
                yield ev

        return _gen

    monkeypatch.setattr(
        "app.router.graph.run_router",
        _fake([{"event": "done", "data": "{}"}], raise_on_msg="fail"),
    )

    suite = EvalSuite(
        id="s2",
        name="partial fail",
        description="",
        cases=[
            _case(cid="ok1", user_message="ok"),
            _case(cid="boom", user_message="fail"),
            _case(cid="ok2", user_message="ok"),
        ],
    )
    runner = EvalRunner()
    result = await runner.run_suite(suite)

    assert len(result.case_results) == 3
    assert result.case_results[0].error is None
    assert result.case_results[1].error is not None
    assert "case failed" in result.case_results[1].error
    assert result.case_results[2].error is None


# ============================================================
# 5. apply_judge_results 回填 passed / avg_score
# ============================================================


def test_apply_judge_results_l1_all_passed(base_case_result: CaseResult) -> None:
    """L1 全 passed → passed=True, avg_score=5.0。"""
    judge_results = [
        JudgeResult(case_id="c1", passed=True, score=0.0, layer="L1"),
    ]
    out = EvalRunner.apply_judge_results(base_case_result, judge_results)
    assert out.passed is True
    assert out.avg_score == 5.0
    assert out.judge_results == judge_results


def test_apply_judge_results_l1_failed(base_case_result: CaseResult) -> None:
    """L1 有 failed → passed=False, avg_score=0.0。"""
    judge_results = [
        JudgeResult(case_id="c1", passed=False, score=0.0, layer="L1", reason="缺 token"),
    ]
    out = EvalRunner.apply_judge_results(base_case_result, judge_results)
    assert out.passed is False
    assert out.avg_score == 0.0


def test_apply_judge_results_with_l2(base_case_result: CaseResult) -> None:
    """含 L2 → avg_score = L2 score 平均值。"""
    judge_results = [
        JudgeResult(case_id="c1", passed=True, score=0.0, layer="L1"),
        JudgeResult(case_id="c1", passed=True, score=4.0, layer="L2"),
        JudgeResult(case_id="c1", passed=True, score=5.0, layer="L2"),
    ]
    out = EvalRunner.apply_judge_results(base_case_result, judge_results)
    assert out.passed is True
    assert out.avg_score == pytest.approx(4.5)


def test_apply_judge_results_l2_fail_makes_passed_false(base_case_result: CaseResult) -> None:
    """L2 failed → passed=False（即使 L1 passed），avg_score = L2 平均。"""
    judge_results = [
        JudgeResult(case_id="c1", passed=True, score=0.0, layer="L1"),
        JudgeResult(case_id="c1", passed=False, score=2.0, layer="L2"),
    ]
    out = EvalRunner.apply_judge_results(base_case_result, judge_results)
    assert out.passed is False
    assert out.avg_score == pytest.approx(2.0)


def test_apply_judge_results_with_l3(base_case_result: CaseResult) -> None:
    """L1 + L3（无 L2）→ avg_score = L3 score。"""
    judge_results = [
        JudgeResult(case_id="c1", passed=True, score=0.0, layer="L1"),
        JudgeResult(case_id="c1", passed=True, score=5.0, layer="L3"),
    ]
    out = EvalRunner.apply_judge_results(base_case_result, judge_results)
    assert out.passed is True
    assert out.avg_score == pytest.approx(5.0)


def test_apply_judge_results_l2_and_l3(base_case_result: CaseResult) -> None:
    """L2 + L3 → avg_score = L2 与 L3 score 的均值。"""
    judge_results = [
        JudgeResult(case_id="c1", passed=True, score=0.0, layer="L1"),
        JudgeResult(case_id="c1", passed=True, score=4.0, layer="L2"),
        JudgeResult(case_id="c1", passed=True, score=5.0, layer="L3"),
    ]
    out = EvalRunner.apply_judge_results(base_case_result, judge_results)
    assert out.passed is True
    # (4.0 + 5.0) / 2 = 4.5
    assert out.avg_score == pytest.approx(4.5)


def test_apply_judge_results_l3_fail_makes_passed_false(base_case_result: CaseResult) -> None:
    """L3 failed → passed=False（即使 L1 passed），avg_score = L3 score。"""
    judge_results = [
        JudgeResult(case_id="c1", passed=True, score=0.0, layer="L1"),
        JudgeResult(case_id="c1", passed=False, score=3.0, layer="L3"),
    ]
    out = EvalRunner.apply_judge_results(base_case_result, judge_results)
    assert out.passed is False
    assert out.avg_score == pytest.approx(3.0)


def test_apply_judge_results_error_case_always_fails(base_case_result: CaseResult) -> None:
    """有 error 的 case 直接 passed=False, avg_score=0.0。"""
    cr = base_case_result.model_copy(update={"error": "timeout"})
    judge_results = [
        JudgeResult(case_id="c1", passed=True, score=5.0, layer="L1"),
        JudgeResult(case_id="c1", passed=True, score=5.0, layer="L2"),
    ]
    out = EvalRunner.apply_judge_results(cr, judge_results)
    assert out.passed is False
    assert out.avg_score == 0.0
    # judge_results 仍保留
    assert len(out.judge_results) == 2


def test_apply_judge_results_empty_list_fails(base_case_result: CaseResult) -> None:
    """无 Judge 结果 → 保守判失败。"""
    out = EvalRunner.apply_judge_results(base_case_result, [])
    assert out.passed is False
    assert out.avg_score == 0.0


def test_apply_judge_results_original_unchanged(base_case_result: CaseResult) -> None:
    """apply_judge_results 返回新对象，原 CaseResult 不变。"""
    original_passed = base_case_result.passed
    original_score = base_case_result.avg_score
    judge_results = [JudgeResult(case_id="c1", passed=True, score=5.0, layer="L2")]
    out = EvalRunner.apply_judge_results(base_case_result, judge_results)
    # 原对象不变
    assert base_case_result.passed == original_passed
    assert base_case_result.avg_score == original_score
    assert base_case_result.judge_results == []
    # 新对象更新
    assert out.passed is True
    assert out.avg_score == 5.0


# ============================================================
# 6. run_case L3 自纠分支
# ============================================================


async def test_run_case_l3_branch_calls_self_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_correct=True + rubric 设定 + no_rubric=False → 调 SelfCorrectionRunner。"""
    case = EvalCase(
        id="c-l3",
        user_message="hi",
        agent_mode="coding",
        expect=CaseExpect(rubric="test rubric", self_correct=True),
    )
    expected_result = CaseResult(
        case=case,
        events=[],
        passed=True,
        avg_score=5.0,
    )

    mock_instance = MagicMock()
    mock_instance.run_case = AsyncMock(return_value=expected_result)
    mock_class = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("app.eval.judges.self_correction.SelfCorrectionRunner", mock_class)

    runner = EvalRunner()
    result = await runner.run_case(case)

    assert mock_class.called
    assert result == expected_result


async def test_run_case_no_rubric_skips_l3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """no_rubric=True 时即使 self_correct=True 也走 run_router，不调 SelfCorrectionRunner。"""
    # mock run_router 以走默认路径
    monkeypatch.setattr(
        "app.router.graph.run_router",
        _make_fake_run_router([{"event": "done", "data": "{}"}]),
    )

    mock_instance = MagicMock()
    mock_instance.run_case = AsyncMock()
    mock_class = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("app.eval.judges.self_correction.SelfCorrectionRunner", mock_class)

    case = EvalCase(
        id="c-no-rubric",
        user_message="hi",
        agent_mode="work",
        expect=CaseExpect(rubric="test rubric", self_correct=True),
    )
    runner = EvalRunner(no_rubric=True)
    result = await runner.run_case(case)

    # SelfCorrectionRunner 不应被实例化
    assert not mock_class.called
    assert not mock_instance.run_case.called
    # 走了默认路径：收集到 done 事件，无 error
    assert result.error is None
    assert len(result.events) == 1
    assert result.events[0]["event"] == "done"


async def test_run_case_l3_branch_passes_grader_model_and_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L3 分支：SelfCorrectionRunner 接收 chat_model/grader_model/max_iterations/checkpointer。"""
    case = EvalCase(
        id="c-l3-args",
        user_message="hi",
        agent_mode="coding",
        expect=CaseExpect(
            rubric="test rubric",
            self_correct=True,
            self_correct_max_iterations=5,
        ),
    )
    expected_result = CaseResult(case=case, events=[], passed=True, avg_score=5.0)

    mock_instance = MagicMock()
    mock_instance.run_case = AsyncMock(return_value=expected_result)
    mock_class = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("app.eval.judges.self_correction.SelfCorrectionRunner", mock_class)

    sentinel_chat = object()
    sentinel_grader = object()
    sentinel_checkpointer = object()
    runner = EvalRunner(
        chat_model=sentinel_chat,  # type: ignore[arg-type]
        grader_model=sentinel_grader,  # type: ignore[arg-type]
        checkpointer=sentinel_checkpointer,
    )
    await runner.run_case(case)

    mock_class.assert_called_once()
    _, kwargs = mock_class.call_args
    assert kwargs["chat_model"] is sentinel_chat
    assert kwargs["grader_model"] is sentinel_grader
    assert kwargs["max_iterations"] == 5
    assert kwargs["checkpointer"] is sentinel_checkpointer
