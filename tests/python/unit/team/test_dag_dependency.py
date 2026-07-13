"""DAG 依赖注入测试 — 验证 _compose_input_with_upstream 返回值被实际使用。"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.team.blackboard import TeamSubtaskResult
from app.team.dispatcher import _compose_input_with_upstream
from app.team.nodes import execute_node
from app.team.state import Finding, SubtaskState, TeamTask


def test_compose_input_with_upstream_returns_enriched_str():
    """_compose_input_with_upstream 应返回包含上游 finding 的拼接字符串。"""
    task = TeamTask(
        id="t2", agent="code", description="实现功能 B",
        depends_on=["t1"],
    )
    upstream = {
        "code:t1:0": Finding(
            agent="code", task_id="t1", wave_index=0,
            content="功能 A 已完成", success=True,
        ),
    }
    result = _compose_input_with_upstream(task, upstream)
    assert "功能 A 已完成" in result
    assert "实现功能 B" in result
    assert "[依赖任务结果]" in result


def test_compose_input_with_upstream_no_upstream_returns_description():
    """无上游时返回原 description。"""
    task = TeamTask(id="t1", agent="code", description="实现功能 A")
    result = _compose_input_with_upstream(task, {})
    assert result == "实现功能 A"


@pytest.mark.asyncio
async def test_execute_node_uses_composed_input_for_code_runner():
    """execute_node 应把 composed_input 作为子任务输入传给 runner，而非原 task.description。"""
    task = TeamTask(
        id="t2", agent="code", description="实现功能 B",
        depends_on=["t1"], expected_output="功能 B 完成",
    )
    upstream = {
        "code:t1:0": Finding(
            agent="code", task_id="t1", wave_index=0,
            content="功能 A 已完成", success=True,
        ),
    }
    state: SubtaskState = {
        "task": task,
        "upstream_findings": upstream,
        "wave_index": 0,
        "parent_thread_id": "parent-1",
        "remaining_waves": [],
        "history": [],
        "permission_mode": "standard",
        "profile_prompt": "",
        "workspace_path": None,
        "chat_model": None,
        "subtask_runners": {},
        "subtask_timeout": 30,
        "abort_event": asyncio.Event(),
    }

    captured_args: list = []

    async def fake_runner(*args, **kwargs):
        # _run_subtask_stream 调用形如：
        #   _run_subtask_stream(runner, runner_args=(input, child_id), ...)
        # runner_args 是 kwarg，code runner 的 args 元组为 (actual_input, child_id)。
        captured_args.extend(kwargs.get("runner_args", ()))
        return TeamSubtaskResult(agent="code", success=True, payload="done")

    with patch("app.team.nodes._run_subtask_stream", new=AsyncMock(side_effect=fake_runner)), \
         patch("app.team.nodes._inherit_workspace", new=AsyncMock(return_value=None)), \
         patch("app.team.nodes.get_abort_event", new=AsyncMock(return_value=asyncio.Event())), \
         patch("app.team.nodes.get_stream_writer", return_value=lambda x: None), \
         patch("app.team.nodes.get_settings") as mock_settings:
        mock_settings.return_value.team_max_retries = 0
        mock_settings.return_value.team_subagents = {}
        await execute_node(state)

    # 验证 runner 收到的第一个位置参数（code runner 的 actual_input）包含上游 finding
    assert len(captured_args) >= 2, f"runner_args = {captured_args}"
    composed_description = captured_args[0]
    assert "功能 A 已完成" in composed_description, \
        f"DAG 依赖未注入：runner 收到 '{composed_description}'，应包含上游 finding"
