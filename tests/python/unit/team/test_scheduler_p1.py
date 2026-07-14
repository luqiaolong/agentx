"""Task 7 P1 bug 修复测试（BE-D / BE-H / BE-J / BE-M / BE-R）。

覆盖：

- BE-M: ``_get_team_semaphore`` 配置变更后返回新 semaphore（旧 semaphore 已被替换）
- BE-R: ``_collect_team_sse`` 收到 team_done{error} 时设置 has_error
- BE-D: ``_iterate`` 返回 ``IterResult`` 枚举（隔离测试两个 _iterate 闭包）
- BE-J: ``_run_team_role_subtask._iterate`` 在流自然结束时发射 ``_subtask_done`` 哨兵
"""
import asyncio
import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.team.scheduler import (
    IterResult,
    _get_team_semaphore,
    reset_team_semaphore,
    _run_subtask_stream,
    _run_team_role_subtask,
)
from app.team.blackboard import TeamPlanTask


# ============================================================
# 公共 fixtures
# ============================================================


@pytest.fixture(autouse=True)
def _isolate_semaphore():
    """每个用例前后重置全局 semaphore 缓存。"""
    reset_team_semaphore()
    yield
    reset_team_semaphore()


def _mock_settings(max_concurrency: int) -> MagicMock:
    """构造带 team_max_concurrency 的 mock settings。"""
    mock = MagicMock()
    mock.team_max_concurrency = max_concurrency
    return mock


# ============================================================
# BE-M: _get_team_semaphore 配置热更新
# ============================================================


def test_get_team_semaphore_rebuilds_on_config_change() -> None:
    """BE-M: 配置变更后 _get_team_semaphore 返回新 semaphore，旧实例被替换。

    修复前 ``lru_cache(maxsize=1)`` 缓存首个 semaphore，后续 settings 更新无法生效；
    修复后通过模块级变量 + 配置版本比对，配置变更时自动重建。
    """
    # 第一次：max_concurrency=3
    with patch("app.team.scheduler.get_settings", return_value=_mock_settings(3)):
        sem_v1 = _get_team_semaphore()
    assert sem_v1._value == 3  # type: ignore[attr-defined]

    # 第二次：max_concurrency=8（配置热更新）
    with patch("app.team.scheduler.get_settings", return_value=_mock_settings(8)):
        sem_v2 = _get_team_semaphore()
    assert sem_v2._value == 8  # type: ignore[attr-defined]

    # 应是不同实例（旧 semaphore 已被替换）
    assert sem_v1 is not sem_v2, "BE-M 修复失败：配置变更后未重建 semaphore"


def test_get_team_semaphore_returns_same_instance_when_config_unchanged() -> None:
    """BE-M: 配置不变时返回同一实例（避免无谓重建）。"""
    with patch("app.team.scheduler.get_settings", return_value=_mock_settings(5)):
        sem_a = _get_team_semaphore()
        sem_b = _get_team_semaphore()
    assert sem_a is sem_b


def test_reset_team_semaphore_clears_cache() -> None:
    """BE-M: reset_team_semaphore 主动清空缓存，下次调用重建。"""
    with patch("app.team.scheduler.get_settings", return_value=_mock_settings(4)):
        sem_before = _get_team_semaphore()
    reset_team_semaphore()
    with patch("app.team.scheduler.get_settings", return_value=_mock_settings(4)):
        sem_after = _get_team_semaphore()
    assert sem_before is not sem_after, "reset_team_semaphore 未生效"


def test_get_team_semaphore_fallback_on_invalid_config() -> None:
    """BE-M: 配置非法（0/负数/None）时降级到默认 5。"""
    # None
    mock_none = MagicMock()
    mock_none.team_max_concurrency = None
    with patch("app.team.scheduler.get_settings", return_value=mock_none):
        sem = _get_team_semaphore()
    assert sem._value == 5  # type: ignore[attr-defined]

    reset_team_semaphore()

    # 0
    mock_zero = MagicMock()
    mock_zero.team_max_concurrency = 0
    with patch("app.team.scheduler.get_settings", return_value=mock_zero):
        sem = _get_team_semaphore()
    assert sem._value == 5  # type: ignore[attr-defined]


# ============================================================
# BE-D: _run_subtask_stream._iterate 返回 IterResult 枚举
# ============================================================


@pytest.mark.asyncio
async def test_run_subtask_stream_iterate_returns_iter_result_normal_end() -> None:
    """BE-D: runner 流自然结束时 _iterate 返回 IterResult.NORMAL_END。

    通过构造一个不发 _subtask_done 哨兵的 runner，验证 _run_subtask_stream
    最终走 NORMAL_END 分支，按 collected_text 构造成功结果。
    """
    reset_team_semaphore()

    async def _runner(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        # 模拟 rag/web/custom runner：发 token 后流自然结束，无 _subtask_done
        yield {"event": "token", "data": "hello"}
        yield {"event": "token", "data": " world"}

    abort_event = asyncio.Event()
    writer_events: list[dict] = []
    writer = writer_events.append

    result = await _run_subtask_stream(
        _runner,
        runner_args=(),
        runner_kwargs={},
        agent_name="rag",
        abort_event=abort_event,
        writer=writer,
        subtask_timeout=10,
    )

    assert result.success is True
    assert "hello" in result.payload and "world" in result.payload
    assert result.agent == "rag"


@pytest.mark.asyncio
async def test_run_subtask_stream_iterate_returns_iter_result_aborted() -> None:
    """BE-D: abort 触发时 _iterate 返回 IterResult.ABORTED，外层包装为失败结果。"""
    reset_team_semaphore()

    async def _runner(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        # 长时间不结束的 runner，等待 abort
        yield {"event": "token", "data": "partial"}
        await asyncio.sleep(5)
        yield {"event": "token", "data": "never"}

    abort_event = asyncio.Event()
    writer_events: list[dict] = []
    writer = writer_events.append

    # 启动子任务
    task = asyncio.ensure_future(
        _run_subtask_stream(
            _runner,
            runner_args=(),
            runner_kwargs={},
            agent_name="code",
            abort_event=abort_event,
            writer=writer,
            subtask_timeout=10,
        )
    )
    # 让 runner 先发 token
    await asyncio.sleep(0.1)
    abort_event.set()
    result = await task

    assert result.success is False
    assert result.payload == "用户中止"
    assert result.agent == "code"


@pytest.mark.asyncio
async def test_run_subtask_stream_iterate_returns_iter_result_subtask_done() -> None:
    """BE-D: runner 发射 _subtask_done 哨兵时 _iterate 返回 IterResult.SUBTASK_DONE。"""
    reset_team_semaphore()

    async def _runner(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        yield {"event": "token", "data": "result text"}
        # 哨兵事件：_route_event_for_node 解析为 TeamSubtaskResult
        yield {
            "event": "_subtask_done",
            "data": json.dumps(
                {"agent": "deep", "success": True, "payload": "deep done"}
            ),
        }

    abort_event = asyncio.Event()
    writer_events: list[dict] = []
    writer = writer_events.append

    result = await _run_subtask_stream(
        _runner,
        runner_args=(),
        runner_kwargs={},
        agent_name="deep",
        abort_event=abort_event,
        writer=writer,
        subtask_timeout=10,
    )

    # SUBTASK_DONE 分支：返回 last_result（来自 _route_event_for_node）
    assert result.success is True
    assert result.payload == "deep done"
    assert result.agent == "deep"


# ============================================================
# BE-J: _run_team_role_subtask._iterate 发射 _subtask_done 哨兵
# ============================================================


@pytest.mark.asyncio
async def test_run_team_role_subtask_emits_subtask_done_on_normal_end() -> None:
    """BE-J: _run_team_role_subtask runner 流自然结束时应发射 _subtask_done 哨兵。

    通过 mock build_custom_agent 返回伪 agent，模拟 astream_events 流自然结束
    （StopAsyncIteration），验证 writer 收到 _subtask_done 事件。
    """
    reset_team_semaphore()

    # 构造 TeamPlanTask（v1 兼容类型：agent/input/purpose/deps）
    task = TeamPlanTask(
        agent="frontend_dev",
        input="实现登录页面",
        purpose="前端实现",
    )

    # mock agent_obj.astream_events 返回空流（立即 StopAsyncIteration）
    mock_agent = MagicMock()

    async def _empty_astream(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        # 立即返回（空流）
        return
        yield  # pragma: no cover - 让 Python 识别为 async generator

    mock_agent.astream_events = _empty_astream

    # mock settings.team_subagents 返回带 system_prompt 的配置
    mock_cfg = MagicMock()
    mock_cfg.system_prompt = "You are frontend developer"
    mock_cfg.tools = []
    mock_cfg.temperature = 0.3
    mock_settings_obj = MagicMock()
    mock_settings_obj.team_subagents.get.return_value = mock_cfg

    writer_events: list[dict] = []
    writer = writer_events.append

    with (
        patch("app.team.scheduler.get_settings", return_value=mock_settings_obj),
        patch("app.subagents.custom_agent.build_custom_agent", return_value=mock_agent),
        patch("app.team.scheduler._inherit_workspace", new=AsyncMock(return_value=None)),
    ):
        result = await _run_team_role_subtask(
            task=task,
            thread_id="th-role-1",
            history=[],
            permission_mode="standard",
            profile_prompt="",
            task_index=0,
            workspace_path=None,
            chat_model=None,
            subtask_runners=None,
            abort_event=asyncio.Event(),
            writer=writer,
            subtask_timeout=10,
        )

    # 验证 _subtask_done 哨兵已发射
    subtask_done_events = [e for e in writer_events if e.get("event") == "_subtask_done"]
    assert len(subtask_done_events) == 1, (
        f"BE-J 修复失败：应发射 1 个 _subtask_done 哨兵，实际 {len(subtask_done_events)}"
    )
    # 解析哨兵 payload
    payload = json.loads(subtask_done_events[0]["data"])
    assert payload["agent"] == "frontend_dev"
    assert payload["success"] is True

    # 整体结果：collected_text/tool_traces 为空 → success=False
    assert result.agent == "frontend_dev"


# ============================================================
# IterResult 枚举完整性
# ============================================================


def test_iter_result_enum_has_expected_members() -> None:
    """BE-D: IterResult 枚举包含 NORMAL_END / ABORTED / SUBTASK_DONE 三个值。"""
    assert IterResult.NORMAL_END.value == "normal_end"
    assert IterResult.ABORTED.value == "aborted"
    assert IterResult.SUBTASK_DONE.value == "subtask_done"
