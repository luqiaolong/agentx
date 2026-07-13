"""planner replan 测试 — 验证 replan 后 task_id 与 previous_plan 不冲突。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.team.planner import Planner
from app.team.state import Finding, TeamPlan, TeamTask


@pytest.mark.asyncio
async def test_replan_task_ids_unique_against_previous_plan():
    """replan 返回的新任务 id 不应与 previous_plan 的 id 重复。"""
    previous_plan = [
        TeamTask(id="task_1", agent="code", description="任务1"),
        TeamTask(id="task_2", agent="web", description="任务2"),
    ]
    findings = {
        "code:task_1:0": Finding(
            agent="code", task_id="task_1", wave_index=0,
            content="完成", success=True,
        ),
    }
    errors = []

    # 模拟 LLM 返回包含重复 id 的新 plan
    fake_plan = TeamPlan(tasks=[
        TeamTask(id="task_1", agent="code", description="重做任务1"),  # 重复 id
        TeamTask(id="task_3", agent="web", description="新任务3"),
    ])

    planner = Planner(chat_model=MagicMock())
    with patch.object(planner, "_resolve_llm", return_value=MagicMock()):
        planner._structured = AsyncMock()
        planner._structured.ainvoke = AsyncMock(return_value=fake_plan)
        with patch("app.team.planner.get_settings") as mock_settings:
            mock_settings.return_value.team_max_tasks = 10
            result = await planner.replan(
                original_message="用户请求",
                previous_plan=previous_plan,
                findings=findings,
                errors=errors,
                hint=False,
            )

    # task_1 应被重命名为带后缀的唯一 id
    result_ids = {t.id for t in result.tasks}
    assert "task_1" not in result_ids, f"replan 后仍含重复 id: {result_ids}"
    assert len(result.tasks) == 2
    # 新 id 应保留可读性（如 task_1_r1）
    assert any(t.id.startswith("task_1") for t in result.tasks)
