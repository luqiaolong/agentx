"""AgentTeam planner task_id 唯一性测试（I4.4 / REQ-TEAM-PLANNER-1）。

覆盖：
- 初始计划（结构化输出路径）重复 task_id 去重（``_post_filter``）
- 初始计划（文本 fallback 路径）task_id 唯一性保持
- replan 追加任务不与 previous_plan id 冲突（回归）
- replan 多轮追加时新任务间也不冲突（回归）
- depends_on 引用在 id 去重后仍指向首个同 id 任务
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.team.planner import Planner, _parse_plan_from_text_fallback
from app.team.state import TeamPlan, TeamTask

__all__ = [
    "TestPostFilterUniqueId",
    "TestTextFallbackUniqueId",
    "TestReplanUniqueId",
]


def _mock_settings(**overrides: Any) -> Any:
    """构造模拟 settings（team_max_tasks 默认足够大，不触发截断）。"""
    defaults = {
        "team_max_tasks": 10,
        "team_subagents": {},
        "subagents": {},
        "custom_subagents": {},
        "tools_enabled": {},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _task(
    task_id: str,
    agent: str = "code",
    description: str = "do something",
    depends_on: list[str] | None = None,
) -> TeamTask:
    return TeamTask(
        id=task_id, agent=agent, description=description, depends_on=depends_on or []
    )


# ============================================================
# _post_filter task_id 唯一性校验（初始计划路径，REQ-TEAM-PLANNER-1）
# ============================================================


class TestPostFilterUniqueId:
    """``_post_filter`` 应保证初始计划内 task_id 唯一。

    结构化输出路径（``with_structured_output(TeamPlan)``）可能返回重复 id，
    ``_post_filter`` 作为初始计划的统一后处理入口，必须去重。
    """

    def test_duplicate_ids_deduplicated(self) -> None:
        """两个相同 id 的任务应被去重，两个任务都保留。"""
        planner = Planner(chat_model=None)
        plan = TeamPlan(
            tasks=[
                _task("t1", description="task A"),
                _task("t1", description="task B"),  # 重复 id
            ]
        )
        result = planner._post_filter(plan, _mock_settings())
        ids = [t.id for t in result.tasks]
        assert len(ids) == len(set(ids)), f"存在重复 task_id: {ids}"
        assert len(result.tasks) == 2, "两个任务都应保留，不应丢弃"

    def test_multiple_duplicates_all_deduplicated(self) -> None:
        """多个重复 id（3 个 t1 + 2 个 t2）都应被唯一化。"""
        planner = Planner(chat_model=None)
        plan = TeamPlan(
            tasks=[
                _task("t1", description="A"),
                _task("t1", description="B"),
                _task("t1", description="C"),
                _task("t2", description="D"),
                _task("t2", description="E"),
            ]
        )
        result = planner._post_filter(plan, _mock_settings())
        ids = [t.id for t in result.tasks]
        assert len(ids) == len(set(ids)), f"存在重复 task_id: {ids}"
        assert len(result.tasks) == 5, "所有任务都应保留"

    def test_unique_ids_unchanged(self) -> None:
        """id 已唯一时不应被修改。"""
        planner = Planner(chat_model=None)
        plan = TeamPlan(
            tasks=[
                _task("t1", description="A"),
                _task("t2", description="B"),
                _task("t3", description="C"),
            ]
        )
        result = planner._post_filter(plan, _mock_settings())
        ids = [t.id for t in result.tasks]
        assert ids == ["t1", "t2", "t3"], "唯一 id 不应被修改"

    def test_depends_on_points_to_first_occurrence_after_dedup(self) -> None:
        """去重后 depends_on 仍指向首个同 id 任务。

        场景：t1(A) + t1(B, 重复) + t3(depends_on=[t1])
        去重后：t1(A) + t1_2(B) + t3(depends_on=[t1])
        t3 的 depends_on 仍指向首个 t1（A），无需更新。
        """
        planner = Planner(chat_model=None)
        plan = TeamPlan(
            tasks=[
                _task("t1", description="A"),
                _task("t1", description="B"),  # 重复，应被重命名
                _task("t3", description="C", depends_on=["t1"]),
            ]
        )
        result = planner._post_filter(plan, _mock_settings())
        ids = [t.id for t in result.tasks]
        assert len(ids) == len(set(ids)), f"存在重复 task_id: {ids}"
        # t3 的 depends_on 仍包含 t1（首个同 id 任务）
        t3 = [t for t in result.tasks if t.id.startswith("t3")][0]
        assert "t1" in t3.depends_on, "depends_on 应指向首个 t1"

    def test_empty_plan_no_error(self) -> None:
        """空 plan 不应触发错误。"""
        planner = Planner(chat_model=None)
        plan = TeamPlan(tasks=[])
        result = planner._post_filter(plan, _mock_settings())
        assert result.tasks == []


# ============================================================
# _parse_plan_from_text_fallback task_id 唯一性（文本 fallback 路径）
# ============================================================


class TestTextFallbackUniqueId:
    """``_parse_plan_from_text_fallback`` 生成的 task_id 应天然唯一。

    文本 fallback 路径按行序生成 ``t{idx+1}``，天然唯一。作为回归测试
    确保 _post_filter 不破坏这一不变式。
    """

    def test_text_fallback_ids_unique(self) -> None:
        """文本 fallback 路径生成的 id 应唯一（t1, t2, ...）。"""
        text = (
            "[agent:code] 任务 A\n"
            "[agent:code] 任务 B\n"
            "[agent:code] 任务 C\n"
        )
        plan = _parse_plan_from_text_fallback(text)
        ids = [t.id for t in plan.tasks]
        assert len(ids) == len(set(ids)), f"文本 fallback 生成重复 id: {ids}"
        assert ids == ["t1", "t2", "t3"]

    def test_text_fallback_then_post_filter_ids_unique(self) -> None:
        """文本 fallback + _post_filter 后 id 仍唯一。"""
        text = (
            "[agent:code] 任务 A\n"
            "[agent:code] 任务 B\n"
        )
        plan = _parse_plan_from_text_fallback(text)
        planner = Planner(chat_model=None)
        result = planner._post_filter(plan, _mock_settings())
        ids = [t.id for t in result.tasks]
        assert len(ids) == len(set(ids))


# ============================================================
# replan 路径 task_id 唯一性（_post_filter_replan，回归测试）
# ============================================================


class TestReplanUniqueId:
    """``_post_filter_replan`` 应保证追加任务 id 不与 previous_plan 冲突。

    replan 路径已有 ``_r{replan_round}`` 后缀唯一化逻辑，作为回归测试
    确保 I4.4 修改 _post_filter 后不破坏此路径。
    """

    def test_replan_no_conflict_with_previous(self) -> None:
        """replan 追加任务 id 与 previous_plan 冲突时应被重命名。"""
        planner = Planner(chat_model=None)
        previous = [_task("t1", description="old task")]
        new_plan = TeamPlan(
            tasks=[
                _task("t1", description="new task"),  # 与 previous 冲突
            ]
        )
        result = planner._post_filter_replan(new_plan, _mock_settings(), previous)
        ids = [t.id for t in result.tasks]
        assert len(ids) == len(set(ids)), f"replan 后存在重复: {ids}"
        prev_ids = {t.id for t in previous}
        for new_id in ids:
            assert new_id not in prev_ids, f"replan 新任务 id '{new_id}' 与 previous 冲突"

    def test_replan_multi_round_new_tasks_no_conflict(self) -> None:
        """replan 多轮追加时新任务间也不冲突。

        场景：previous 已有 t1, t2_r1；新一轮 replan 返回两个都叫 t1 的任务。
        """
        planner = Planner(chat_model=None)
        previous = [
            _task("t1", description="original"),
            _task("t2_r1", description="first replan"),
        ]
        new_plan = TeamPlan(
            tasks=[
                _task("t1", description="replan A"),
                _task("t1", description="replan B"),
            ]
        )
        result = planner._post_filter_replan(new_plan, _mock_settings(), previous)
        ids = [t.id for t in result.tasks]
        assert len(ids) == len(set(ids)), f"replan 多轮后存在重复: {ids}"
        # 不应与 previous 冲突
        prev_ids = {t.id for t in previous}
        for new_id in ids:
            assert new_id not in prev_ids, f"replan 新任务 id '{new_id}' 与 previous 冲突"
        # 两个任务都应保留
        assert len(result.tasks) == 2

    def test_replan_completely_duplicate_task_filtered(self) -> None:
        """与 previous 完全相同（id + description）的任务应被过滤。"""
        planner = Planner(chat_model=None)
        previous = [_task("t1", description="same task")]
        new_plan = TeamPlan(
            tasks=[
                _task("t1", description="same task"),  # 完全重复
            ]
        )
        result = planner._post_filter_replan(new_plan, _mock_settings(), previous)
        assert len(result.tasks) == 0, "完全重复的任务应被过滤"
