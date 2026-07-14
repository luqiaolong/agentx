"""AgentTeam v2 核心数据结构与派发逻辑单元测试（T1-T4 + T12）。

覆盖：
- T1: ``TeamTask`` / ``TeamPlan`` / ``Finding`` / ``ClassificationResult`` schema
- T2: ``_merge_findings`` / ``_merge_warnings`` / ``_merge_pending_waves`` / ``_merge_completed_task_ids`` reducers
- T3: ``_parse_plan_from_text_fallback`` + ``Planner`` 类（structured output + fallback 路径）
- T4: ``resolve_waves`` Kahn 分层 + ``dispatch_node`` + ``build_dispatch_sends``
- T12: ``_inject_upstream_findings`` + ``_compose_input_with_upstream``
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.team.blackboard import (
    _merge_completed_task_ids,
    _merge_findings,
    _merge_pending_waves,
    _merge_warnings,
)
from app.team.dispatcher import (
    _compose_input_with_upstream,
    _inject_upstream_findings,
    build_dispatch_sends,
    dispatch_node,
    resolve_waves,
)
from app.team.planner import Planner, _parse_plan_from_text_fallback
from app.team.state import (
    ClassificationResult,
    Finding,
    TeamPlan,
    TeamState,
    TeamTask,
)


# ============================================================
# 公共 helpers
# ============================================================


def _task(
    task_id: str = "t1",
    agent: str = "code",
    description: str = "do something",
    depends_on: list[str] | None = None,
) -> TeamTask:
    """构造 TeamTask（便于测试）。"""
    return TeamTask(
        id=task_id,
        agent=agent,
        description=description,
        depends_on=list(depends_on) if depends_on is not None else [],
    )


def _finding(
    agent: str = "code",
    task_id: str = "t1",
    wave_index: int = 0,
    content: str = "result",
    success: bool = True,
) -> Finding:
    """构造 Finding（便于测试）。"""
    return Finding(
        agent=agent,
        task_id=task_id,
        wave_index=wave_index,
        content=content,
        success=success,
    )


def _mock_settings(
    max_tasks: int = 10,
    result_max_chars: int = 2000,
    temperature: float = 0.3,
) -> Any:
    """构造 mock settings（供 Planner / dispatcher 使用）。"""
    return SimpleNamespace(
        team_max_tasks=max_tasks,
        team_result_max_chars=result_max_chars,
        llm_temperature_orchestrator=temperature,
        team_subagents={},
        subagents={},
        custom_subagents={},
        tools_enabled={},
    )


# ============================================================
# T1: Schema 测试
# ============================================================


class TestTeamTaskSchema:
    """``TeamTask`` Pydantic schema 验证。"""

    def test_team_task_defaults(self) -> None:
        """``depends_on`` / ``expected_output`` / ``is_dangerous_hint`` 默认值。"""
        task = TeamTask(id="t1", agent="code", description="read file")
        assert task.depends_on == []
        assert task.expected_output == ""
        assert task.is_dangerous_hint is False

    def test_team_task_with_deps(self) -> None:
        """带 ``depends_on`` 的 TeamTask。"""
        task = TeamTask(
            id="t2",
            agent="deep",
            description="modify file",
            depends_on=["t1"],
            expected_output="modified",
            is_dangerous_hint=True,
        )
        assert task.depends_on == ["t1"]
        assert task.expected_output == "modified"
        assert task.is_dangerous_hint is True

    def test_team_task_id_required(self) -> None:
        """``id`` 为必填字段。"""
        with pytest.raises(Exception):
            TeamTask(agent="code", description="missing id")  # type: ignore[call-arg]


class TestTeamPlanSchema:
    """``TeamPlan`` Pydantic schema 验证。"""

    def test_team_plan_defaults(self) -> None:
        """``summary`` / ``needs_iterative`` 默认值。"""
        plan = TeamPlan(tasks=[])
        assert plan.summary == ""
        assert plan.needs_iterative is False

    def test_team_plan_with_tasks(self) -> None:
        """带 tasks 列表的 TeamPlan。"""
        tasks = [_task("t1"), _task("t2", depends_on=["t1"])]
        plan = TeamPlan(tasks=tasks, summary="plan", needs_iterative=True)
        assert len(plan.tasks) == 2
        assert plan.summary == "plan"
        assert plan.needs_iterative is True


class TestFindingSchema:
    """``Finding`` Pydantic schema 验证。"""

    def test_finding_defaults(self) -> None:
        """``success`` / ``error`` / ``retries`` 默认值。"""
        f = Finding(agent="code", task_id="t1", wave_index=0, content="ok")
        assert f.success is True
        assert f.error is None
        assert f.retries == 0

    def test_finding_failure(self) -> None:
        """失败 Finding。"""
        f = Finding(
            agent="deep",
            task_id="t2",
            wave_index=1,
            content="",
            success=False,
            error="timeout",
            retries=2,
        )
        assert f.success is False
        assert f.error == "timeout"
        assert f.retries == 2


class TestClassificationResultSchema:
    """``ClassificationResult`` Pydantic schema 验证。"""

    def test_classification_safe(self) -> None:
        """非危险任务。"""
        r = ClassificationResult(is_dangerous=False, reason="read-only", suggested_agent="code")
        assert r.is_dangerous is False
        assert r.suggested_agent == "code"

    def test_classification_dangerous(self) -> None:
        """危险任务。"""
        r = ClassificationResult(is_dangerous=True, reason="write file", suggested_agent="deep")
        assert r.is_dangerous is True
        assert r.suggested_agent == "deep"


# ============================================================
# T2: Reducer 测试
# ============================================================


class TestMergeFindings:
    """``_merge_findings`` reducer：同 key 收集为 list（BE-N 修复）。"""

    def test_merge_empty(self) -> None:
        """空 dict 合并。"""
        assert _merge_findings({}, {}) == {}

    def test_merge_disjoint(self) -> None:
        """不同 key 合并。"""
        left = {"code:t1:0": _finding("code", "t1")}
        right = {"rag:t2:1": _finding("rag", "t2", wave_index=1)}
        merged = _merge_findings(left, right)
        assert set(merged.keys()) == {"code:t1:0", "rag:t2:1"}

    def test_merge_same_key_collects_to_list(self) -> None:
        """相同 key 时收集为 list，不丢失（BE-N 修复）。"""
        left = {"code:t1:0": _finding("code", "t1", content="old")}
        right = {"code:t1:0": _finding("code", "t1", content="new")}
        merged = _merge_findings(left, right)
        val = merged["code:t1:0"]
        assert isinstance(val, list), f"同 key 应收集为 list，实际 {type(val)}"
        assert len(val) == 2
        assert val[0].content == "old"
        assert val[1].content == "new"

    def test_merge_none(self) -> None:
        """None 输入降级为空 dict。"""
        assert _merge_findings(None, None) == {}
        merged = _merge_findings(None, {"k": _finding()})
        assert "k" in merged


class TestMergeWarnings:
    """``_merge_warnings`` reducer：拼接去重，保序。"""

    def test_merge_empty(self) -> None:
        assert _merge_warnings([], []) == []

    def test_merge_disjoint(self) -> None:
        """不同 warning 拼接。"""
        assert _merge_warnings(["a"], ["b"]) == ["a", "b"]

    def test_merge_dedup(self) -> None:
        """重复 warning 去重。"""
        assert _merge_warnings(["a", "b"], ["b", "c"]) == ["a", "b", "c"]

    def test_merge_none(self) -> None:
        """None 输入降级为空 list。"""
        assert _merge_warnings(None, None) == []
        assert _merge_warnings(None, ["x"]) == ["x"]


class TestMergePendingWaves:
    """``_merge_pending_waves`` reducer：right 覆盖 left。"""

    def test_merge_empty(self) -> None:
        assert _merge_pending_waves([], []) == []

    def test_merge_override(self) -> None:
        """right 覆盖 left（wave 弹出机制）。"""
        left = [[_task("t1")], [_task("t2")]]
        right = [[_task("t2")]]
        assert _merge_pending_waves(left, right) == [[_task("t2")]]

    def test_merge_right_none(self) -> None:
        """right 为 None 时保留 left。"""
        left = [[_task("t1")]]
        assert _merge_pending_waves(left, None) == [[_task("t1")]]


class TestMergeCompletedTaskIds:
    """``_merge_completed_task_ids`` reducer：去重合并，保序。"""

    def test_merge_empty(self) -> None:
        assert _merge_completed_task_ids([], []) == []

    def test_merge_disjoint(self) -> None:
        assert _merge_completed_task_ids(["t1"], ["t2"]) == ["t1", "t2"]

    def test_merge_dedup(self) -> None:
        """重复 id 去重。"""
        assert _merge_completed_task_ids(["t1", "t2"], ["t2", "t3"]) == ["t1", "t2", "t3"]

    def test_merge_none(self) -> None:
        assert _merge_completed_task_ids(None, None) == []
        assert _merge_completed_task_ids(None, ["t1"]) == ["t1"]


# ============================================================
# T3: _parse_plan_from_text_fallback 测试
# ============================================================


class TestParsePlanFromTextFallback:
    """``_parse_plan_from_text_fallback`` 正则解析 fallback。"""

    def test_parse_single_task(self) -> None:
        """单任务无依赖。"""
        text = "[agent:code] 读取 main.py"
        plan = _parse_plan_from_text_fallback(text)
        assert len(plan.tasks) == 1
        assert plan.tasks[0].id == "t1"
        assert plan.tasks[0].agent == "code"
        assert plan.tasks[0].description == "读取 main.py"
        assert plan.tasks[0].depends_on == []

    def test_parse_with_after(self) -> None:
        """``[after:0]`` 依赖映射为 task id ``t1``。"""
        text = (
            "[agent:code] 读取 main.py\n"
            "[agent:deep][after:0] 修改 main.py"
        )
        plan = _parse_plan_from_text_fallback(text)
        assert len(plan.tasks) == 2
        assert plan.tasks[0].id == "t1"
        assert plan.tasks[1].id == "t2"
        assert plan.tasks[1].depends_on == ["t1"]

    def test_parse_multi_deps(self) -> None:
        """``[after:0,1]`` 多依赖映射。"""
        text = (
            "[agent:code] task A\n"
            "[agent:code] task B\n"
            "[agent:deep][after:0,1] task C"
        )
        plan = _parse_plan_from_text_fallback(text)
        assert plan.tasks[2].depends_on == ["t1", "t2"]

    def test_parse_markdown_prefix(self) -> None:
        """兼容 markdown 列表前缀（- / * / 1.）。"""
        text = (
            "1. [agent:code] task A\n"
            "- [agent:code] task B\n"
            "* [agent:deep][after:0,1] task C"
        )
        plan = _parse_plan_from_text_fallback(text)
        assert len(plan.tasks) == 3
        assert plan.tasks[2].depends_on == ["t1", "t2"]

    def test_parse_empty_text(self) -> None:
        """空文本 → 空 plan。"""
        assert _parse_plan_from_text_fallback("").tasks == []
        assert _parse_plan_from_text_fallback("解释性文字，无任务行").tasks == []

    def test_parse_code_fence_markers_skipped(self) -> None:
        """跳过 ``` 代码块标记行本身（与 ``_parse_todos_from_text`` 一致）。"""
        text = "```\n[agent:code] real task\n```"
        plan = _parse_plan_from_text_fallback(text)
        # ``` 行被跳过，但 [agent:code] 行被解析
        assert len(plan.tasks) == 1
        assert plan.tasks[0].description == "real task"

    def test_parse_out_of_bounds_dep(self) -> None:
        """``[after:99]`` 越界索引过滤。"""
        text = "[agent:code] task A\n[agent:deep][after:99] task B"
        plan = _parse_plan_from_text_fallback(text)
        assert plan.tasks[1].depends_on == []

    def test_parse_agent_lowercased(self) -> None:
        """agent 名转小写。"""
        text = "[agent:CODE] task A"
        plan = _parse_plan_from_text_fallback(text)
        assert plan.tasks[0].agent == "code"


# ============================================================
# T3: Planner 类测试
# ============================================================


class TestPlannerPlanWithLlm:
    """``Planner.plan_with_llm`` 测试（structured output + fallback）。"""

    @pytest.mark.asyncio
    async def test_structured_output_success(self) -> None:
        """structured output 路径：LLM 返回 TeamPlan。"""
        settings = _mock_settings()
        expected_plan = TeamPlan(
            tasks=[_task("t1", "code", "read file")],
            summary="plan",
        )
        structured_mock = AsyncMock()
        structured_mock.ainvoke = AsyncMock(return_value=expected_plan)
        chat_model = MagicMock()
        chat_model.with_structured_output.return_value = structured_mock

        with patch("app.team.planner.get_settings", return_value=settings):
            planner = Planner(chat_model)
            plan = await planner.plan_with_llm("read main.py")

        assert plan.tasks == expected_plan.tasks
        chat_model.with_structured_output.assert_called_once_with(TeamPlan)

    @pytest.mark.asyncio
    async def test_structured_output_fallback_to_text(self) -> None:
        """structured output 失败 → 回退到文本解析。"""
        settings = _mock_settings()
        structured_mock = AsyncMock()
        structured_mock.ainvoke = AsyncMock(side_effect=RuntimeError("LLM error"))

        # fallback llm.ainvoke 返回文本
        response_mock = MagicMock()
        response_mock.content = "[agent:code] 读取 main.py"
        chat_model = MagicMock()
        chat_model.with_structured_output.return_value = structured_mock
        chat_model.ainvoke = AsyncMock(return_value=response_mock)

        with patch("app.team.planner.get_settings", return_value=settings):
            planner = Planner(chat_model)
            plan = await planner.plan_with_llm("read main.py")

        assert len(plan.tasks) == 1
        assert plan.tasks[0].agent == "code"
        assert plan.tasks[0].description == "读取 main.py"

    @pytest.mark.asyncio
    async def test_no_structured_output_uses_fallback(self) -> None:
        """LLM 不支持 structured output → 直接走文本解析。"""
        settings = _mock_settings()
        response_mock = MagicMock()
        response_mock.content = "[agent:deep] 修改 main.py"
        chat_model = MagicMock()
        chat_model.with_structured_output.side_effect = NotImplementedError("not supported")
        chat_model.ainvoke = AsyncMock(return_value=response_mock)

        with patch("app.team.planner.get_settings", return_value=settings):
            planner = Planner(chat_model)
            plan = await planner.plan_with_llm("modify main.py")

        assert len(plan.tasks) == 1
        assert plan.tasks[0].agent == "deep"

    @pytest.mark.asyncio
    async def test_llm_invoke_failure_returns_empty(self) -> None:
        """LLM 调用失败 → 返回空 plan。"""
        settings = _mock_settings()
        chat_model = MagicMock()
        chat_model.with_structured_output.side_effect = NotImplementedError
        chat_model.ainvoke = AsyncMock(side_effect=RuntimeError("network"))

        with patch("app.team.planner.get_settings", return_value=settings):
            planner = Planner(chat_model)
            plan = await planner.plan_with_llm("test")

        assert plan.tasks == []

    @pytest.mark.asyncio
    async def test_post_filter_dangerous_rewrite(self) -> None:
        """``_post_filter``：非 deep 任务含危险关键词 → 改写为 deep。"""
        settings = _mock_settings()
        plan = TeamPlan(
            tasks=[_task("t1", "code", "写入文件内容")]
        )
        structured_mock = AsyncMock()
        structured_mock.ainvoke = AsyncMock(return_value=plan)
        chat_model = MagicMock()
        chat_model.with_structured_output.return_value = structured_mock

        with patch("app.team.planner.get_settings", return_value=settings):
            planner = Planner(chat_model)
            result = await planner.plan_with_llm("write file")

        assert result.tasks[0].agent == "deep"

    @pytest.mark.asyncio
    async def test_post_filter_truncation(self) -> None:
        """``_post_filter``：超过 max_tasks → 截断 + 清理悬空 depends_on。"""
        settings = _mock_settings(max_tasks=2)
        tasks = [
            _task("t1", "code", "task A"),
            _task("t2", "code", "task B"),
            _task("t3", "deep", "task C", depends_on=["t1", "t2"]),
        ]
        plan = TeamPlan(tasks=tasks)
        structured_mock = AsyncMock()
        structured_mock.ainvoke = AsyncMock(return_value=plan)
        chat_model = MagicMock()
        chat_model.with_structured_output.return_value = structured_mock

        with patch("app.team.planner.get_settings", return_value=settings):
            planner = Planner(chat_model)
            result = await planner.plan_with_llm("test")

        assert len(result.tasks) == 2
        # t3 被截断，t2 的 depends_on 引用 t1（未被截断），保留
        assert result.tasks[0].id == "t1"
        assert result.tasks[1].id == "t2"


class TestPlannerReplan:
    """``Planner.replan`` 测试。"""

    @pytest.mark.asyncio
    async def test_replan_structured_output(self) -> None:
        """replan structured output 路径。"""
        settings = _mock_settings()
        new_plan = TeamPlan(
            tasks=[_task("t3", "deep", "fix bug", depends_on=["t1"])]
        )
        structured_mock = AsyncMock()
        structured_mock.ainvoke = AsyncMock(return_value=new_plan)
        chat_model = MagicMock()
        chat_model.with_structured_output.return_value = structured_mock

        prev_plan = [_task("t1", "code", "read file")]
        findings = {"code:t1:0": _finding("code", "t1", content="found bug")}

        with patch("app.team.planner.get_settings", return_value=settings):
            planner = Planner(chat_model)
            result = await planner.replan(
                original_message="fix the bug",
                previous_plan=prev_plan,
                findings=findings,
                errors=[],
            )

        assert len(result.tasks) == 1
        assert result.tasks[0].id == "t3"

    @pytest.mark.asyncio
    async def test_replan_filters_existing_task_ids(self) -> None:
        """``_post_filter_replan``：过滤已在 previous_plan 中的 task id。"""
        settings = _mock_settings()
        # LLM 返回 t1（已存在）+ t3（新任务）
        new_plan = TeamPlan(
            tasks=[
                _task("t1", "code", "read file"),
                _task("t3", "deep", "fix bug", depends_on=["t1"]),
            ]
        )
        structured_mock = AsyncMock()
        structured_mock.ainvoke = AsyncMock(return_value=new_plan)
        chat_model = MagicMock()
        chat_model.with_structured_output.return_value = structured_mock

        prev_plan = [_task("t1", "code", "read file")]

        with patch("app.team.planner.get_settings", return_value=settings):
            planner = Planner(chat_model)
            result = await planner.replan(
                original_message="test",
                previous_plan=prev_plan,
                findings={},
                errors=[],
            )

        # t1 被过滤，只保留 t3
        assert len(result.tasks) == 1
        assert result.tasks[0].id == "t3"

    @pytest.mark.asyncio
    async def test_replan_fallback_no_new_tasks(self) -> None:
        """fallback 路径：LLM 输出 NO_NEW_TASKS → 空 plan。"""
        settings = _mock_settings()
        response_mock = MagicMock()
        response_mock.content = "NO_NEW_TASKS"
        chat_model = MagicMock()
        chat_model.with_structured_output.side_effect = NotImplementedError
        chat_model.ainvoke = AsyncMock(return_value=response_mock)

        with patch("app.team.planner.get_settings", return_value=settings):
            planner = Planner(chat_model)
            result = await planner.replan(
                original_message="test",
                previous_plan=[],
                findings={},
                errors=[],
            )

        assert result.tasks == []


# ============================================================
# T4: resolve_waves 测试
# ============================================================


class TestResolveWaves:
    """``resolve_waves`` Kahn 算法分层。"""

    def test_empty(self) -> None:
        """空任务列表 → 空 waves。"""
        assert resolve_waves([]) == []

    def test_single_task(self) -> None:
        """单任务 → 单层单任务。"""
        t = _task("t1")
        waves = resolve_waves([t])
        assert waves == [[t]]

    def test_no_deps_single_layer(self) -> None:
        """所有任务无依赖 → 单层（等价并行 fan-out）。"""
        tasks = [_task("t1"), _task("t2"), _task("t3")]
        waves = resolve_waves(tasks)
        assert len(waves) == 1
        assert set(t.id for t in waves[0]) == {"t1", "t2", "t3"}

    def test_linear_chain(self) -> None:
        """t1→t2→t3 线性链 → 三层。"""
        tasks = [
            _task("t1"),
            _task("t2", depends_on=["t1"]),
            _task("t3", depends_on=["t2"]),
        ]
        waves = resolve_waves(tasks)
        assert len(waves) == 3
        assert [t.id for t in waves[0]] == ["t1"]
        assert [t.id for t in waves[1]] == ["t2"]
        assert [t.id for t in waves[2]] == ["t3"]

    def test_diamond(self) -> None:
        """菱形：t1→t2, t1→t3, t2→t4, t3→t4 → 三层。"""
        tasks = [
            _task("t1"),
            _task("t2", depends_on=["t1"]),
            _task("t3", depends_on=["t1"]),
            _task("t4", depends_on=["t2", "t3"]),
        ]
        waves = resolve_waves(tasks)
        assert len(waves) == 3
        assert [t.id for t in waves[0]] == ["t1"]
        assert set(t.id for t in waves[1]) == {"t2", "t3"}
        assert [t.id for t in waves[2]] == ["t4"]

    def test_self_loop_dropped(self) -> None:
        """自环 t1.depends_on=[t1] → 丢弃，单层。"""
        tasks = [_task("t1", depends_on=["t1"])]
        waves = resolve_waves(tasks)
        assert len(waves) == 1
        assert waves[0][0].id == "t1"

    def test_out_of_bounds_dropped(self) -> None:
        """越界 dep_id（t99 不存在）→ 丢弃。"""
        tasks = [_task("t1", depends_on=["t99"])]
        waves = resolve_waves(tasks)
        assert len(waves) == 1
        assert waves[0][0].id == "t1"

    def test_cycle_broken(self) -> None:
        """循环 t1→t2→t1 → 强制打破，所有循环节点放入同一 wave（并行）。"""
        tasks = [
            _task("t1", depends_on=["t2"]),
            _task("t2", depends_on=["t1"]),
        ]
        waves = resolve_waves(tasks)
        # 循环被打破：两个入度相同的节点放入同一 wave（避免无限循环）
        assert len(waves) == 1
        assert set(t.id for t in waves[0]) == {"t1", "t2"}


# ============================================================
# T12: _inject_upstream_findings 测试
# ============================================================


class TestInjectUpstreamFindings:
    """``_inject_upstream_findings`` 上游结果提取。"""

    def test_no_deps_returns_empty(self) -> None:
        """无 depends_on → 空 dict。"""
        task = _task("t1")
        findings = {"code:t1:0": _finding("code", "t1")}
        assert _inject_upstream_findings(task, findings) == {}

    def test_extract_by_depends_on(self) -> None:
        """按 depends_on 提取上游 findings。"""
        task = _task("t2", depends_on=["t1"])
        findings = {
            "code:t1:0": _finding("code", "t1", content="result A"),
            "rag:t3:1": _finding("rag", "t3", content="result B"),
        }
        upstream = _inject_upstream_findings(task, findings)
        assert len(upstream) == 1
        assert "code:t1:0" in upstream
        assert upstream["code:t1:0"].content == "result A"

    def test_missing_finding_warning(self) -> None:
        """依赖的 finding 不存在 → 空 upstream（logger.warning）。"""
        task = _task("t2", depends_on=["t1", "t99"])
        findings = {"code:t1:0": _finding("code", "t1")}
        upstream = _inject_upstream_findings(task, findings)
        assert len(upstream) == 1
        assert "code:t1:0" in upstream

    def test_empty_findings(self) -> None:
        """空 findings + 有 deps → 空 upstream。"""
        task = _task("t2", depends_on=["t1"])
        assert _inject_upstream_findings(task, {}) == {}

    def test_multiple_deps_all_found(self) -> None:
        """多依赖全部找到。"""
        task = _task("t3", depends_on=["t1", "t2"])
        findings = {
            "code:t1:0": _finding("code", "t1"),
            "rag:t2:0": _finding("rag", "t2"),
        }
        upstream = _inject_upstream_findings(task, findings)
        assert len(upstream) == 2


# ============================================================
# T12: _compose_input_with_upstream 测试
# ============================================================


class TestComposeInputWithUpstream:
    """``_compose_input_with_upstream`` 上游结果拼接。"""

    def test_no_upstream_returns_description(self) -> None:
        """无上游 → 原样返回 description。"""
        task = _task("t1", description="read file")
        assert _compose_input_with_upstream(task, {}) == "read file"

    def test_with_upstream_composed(self) -> None:
        """有上游 → 拼接 [依赖任务结果] + 内容 + [当前任务]。"""
        task = _task("t2", description="modify file")
        upstream = {
            "code:t1:0": _finding("code", "t1", content="file content"),
        }
        result = _compose_input_with_upstream(task, upstream)
        assert "[依赖任务结果]" in result
        assert "file content" in result
        assert "[当前任务]" in result
        assert "modify file" in result

    def test_truncation(self) -> None:
        """上游内容超过 max_chars → 截断。"""
        settings = _mock_settings(result_max_chars=10)
        task = _task("t2", description="task")
        upstream = {
            "code:t1:0": _finding("code", "t1", content="x" * 100),
        }
        with patch("app.team.dispatcher.get_settings", return_value=settings):
            result = _compose_input_with_upstream(task, upstream)
        assert "[结果已截断]" in result

    def test_multiple_upstream_all_included(self) -> None:
        """多上游全部拼接。"""
        task = _task("t3", description="task C")
        upstream = {
            "code:t1:0": _finding("code", "t1", content="result A"),
            "rag:t2:1": _finding("rag", "t2", content="result B"),
        }
        result = _compose_input_with_upstream(task, upstream)
        assert "result A" in result
        assert "result B" in result


# ============================================================
# T4: dispatch_node + build_dispatch_sends 测试
# ============================================================


class TestDispatchNode:
    """``dispatch_node`` 节点函数。"""

    def test_returns_empty_dict(self) -> None:
        """dispatch_node 返回空 dict（fan-out 由 _dispatch_router 完成）。"""
        state: TeamState = {}  # type: ignore[assignment]
        assert dispatch_node(state) == {}


class TestBuildDispatchSends:
    """``build_dispatch_sends`` Send 列表构造。"""

    def test_empty_waves_routes_to_aggregate(self) -> None:
        """空 pending_waves → Send("aggregate", {})。"""
        state: TeamState = {"pending_waves": []}  # type: ignore[assignment]
        sends = build_dispatch_sends(state)
        assert len(sends) == 1
        assert sends[0].node == "aggregate"

    def test_single_wave_single_task(self) -> None:
        """单 wave 单任务 → 1 个 Send("execute", ...)。"""
        task = _task("t1")
        state: TeamState = {"pending_waves": [[task]], "thread_id": "th-1"}  # type: ignore[assignment]
        sends = build_dispatch_sends(state)
        assert len(sends) == 1
        assert sends[0].node == "execute"

    def test_single_wave_multi_tasks(self) -> None:
        """单 wave 多任务 → 多个 Send（并行 fan-out）。"""
        tasks = [_task("t1"), _task("t2"), _task("t3")]
        state: TeamState = {"pending_waves": [tasks], "thread_id": "th-1"}  # type: ignore[assignment]
        sends = build_dispatch_sends(state)
        assert len(sends) == 3
        for s in sends:
            assert s.node == "execute"

    def test_remaining_waves_passed(self) -> None:
        """``remaining_waves`` 传递到 execute 节点。"""
        wave1 = [_task("t1")]
        wave2 = [_task("t2", depends_on=["t1"])]
        state: TeamState = {"pending_waves": [wave1, wave2], "thread_id": "th-1"}  # type: ignore[assignment]
        sends = build_dispatch_sends(state)
        # wave1 被派发，remaining = [wave2]（list[list[TeamTask]]）
        assert len(sends) == 1
        # Send 的 arg 是 SubtaskState dict
        assert sends[0].arg["remaining_waves"] == [wave2]

    def test_upstream_findings_injected(self) -> None:
        """有 findings 时注入到 execute 节点的 upstream_findings。"""
        wave2 = [_task("t2", depends_on=["t1"])]
        findings = {"code:t1:0": _finding("code", "t1", content="result")}
        state: TeamState = {  # type: ignore[assignment]
            "pending_waves": [wave2],  # 派发 wave2（依赖 wave1 已完成）
            "findings": findings,
            "thread_id": "th-1",
        }
        sends = build_dispatch_sends(state)
        assert len(sends) == 1
        upstream = sends[0].arg["upstream_findings"]
        assert "code:t1:0" in upstream
        assert upstream["code:t1:0"].content == "result"
