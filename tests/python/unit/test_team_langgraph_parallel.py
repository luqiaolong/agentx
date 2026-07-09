"""AgentTeam LangGraph Send 并行编排回归测试。

覆盖：
1. 多子任务通过 Send fan-out 并行执行，结果自动聚合到黑板。
2. 各子任务节点完成后发出 ``team_progress(done)`` + ``team_result``。
3. 中止事件在子任务节点入口处被检查，触发 ``team_progress(error)``。
4. ``approval_request`` / ``token`` / ``tool_result`` 等 passthrough 事件实时透传。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.security.approval import set_abort
from app.team.orchestrator import (
    TeamPlan,
    TeamPlanItem,
    run_team_path,
)
from app.sse.events import make_team_event


def _make_fake_llm(plan: TeamPlan) -> MagicMock:
    """构造 mock LLM：with_structured_output().ainvoke 返回 TeamPlan；astream 返回汇总 chunk。"""
    mock = MagicMock()
    structured_mock = MagicMock()
    structured_mock.ainvoke = AsyncMock(return_value=plan)
    mock.with_structured_output = MagicMock(return_value=structured_mock)

    async def _fake_astream(messages: Any) -> AsyncIterator:
        yield SimpleNamespace(content="最终汇总")

    mock.astream = _fake_astream
    return mock


async def _collect_events(gen: AsyncIterator[dict]) -> list[dict]:
    events: list[dict] = []
    async for event in gen:
        events.append(event)
    return events


@pytest.fixture(autouse=True)
def _clear_abort_state():
    """每个用例前后清理全局 abort 状态。"""
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    yield
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()


async def test_team_parallel_fan_out_and_aggregates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个子任务并行执行，结果都进入黑板并触发 Aggregator。"""
    started = {}

    async def _fake_run_coding_expert(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "standard",
        workspace_path: str | None = None,
        parent_thread_id: str | None = None,
        chat_model=None,
    ) -> AsyncIterator[dict]:
        started["code"] = True
        await asyncio.sleep(0.01)
        yield {"event": "token", "data": "代码结果"}

    async def _fake_run_rag_agent(
        thread_id: str,
        message: str,
        history: list | None = None,
        workspace_path: str | None = None,
    ) -> AsyncIterator[dict]:
        started["rag"] = True
        await asyncio.sleep(0.01)
        yield {"type": "token", "content": "检索结果"}

    plan = TeamPlan(
        reasoning="并行读代码和文档",
        plan=[
            TeamPlanItem(agent="code", input="读 main.py", purpose="入口"),
            TeamPlanItem(agent="rag", input="Router 设计", purpose="文档"),
        ],
    )
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: _make_fake_llm(plan))

    events = await _collect_events(
        run_team_path(
            "分析项目入口文件和文档结构",
            "t-parallel",
            {"thread_id": "t-parallel", "messages": []},
            subtask_runners={"code": _fake_run_coding_expert, "rag": _fake_run_rag_agent},
        )
    )

    # 两个子任务都启动了，证明 fan-out
    assert started.get("code") is True
    assert started.get("rag") is True

    event_types = [e["event"] for e in events]

    # team_plan 在最前
    assert event_types[0] == "team_plan"
    plan_data = json.loads(events[0]["data"])
    assert len(plan_data["plan"]) == 2

    # running 事件
    running = [e for e in events if e["event"] == "team_progress" and json.loads(e["data"])["status"] == "running"]
    assert len(running) == 2

    # done 事件 + team_result 事件
    done_progress = [e for e in events if e["event"] == "team_progress" and json.loads(e["data"])["status"] == "done"]
    assert len(done_progress) == 2

    results = [e for e in events if e["event"] == "team_result"]
    assert len(results) == 2
    result_agents = {json.loads(e["data"])["agent"] for e in results}
    assert result_agents == {"code", "rag"}

    # Aggregator 输出 token
    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) >= 1
    assert "".join(e["data"] for e in tokens) == "最终汇总"

    # team_done 收尾
    assert any(e["event"] == "team_done" for e in events)


async def test_team_parallel_aborts_at_subtask_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在子任务启动前触发中止，子任务节点应返回错误并不执行 runner。"""
    executed: dict[str, bool] = {}

    async def _fake_run_coding_expert(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "standard",
        workspace_path: str | None = None,
        parent_thread_id: str | None = None,
        chat_model=None,
    ) -> AsyncIterator[dict]:
        executed["code"] = True
        yield {"event": "token", "data": "should not see"}

    async def _fake_run_rag_agent(
        thread_id: str,
        message: str,
        history: list | None = None,
        workspace_path: str | None = None,
    ) -> AsyncIterator[dict]:
        executed["rag"] = True
        yield {"type": "token", "content": "should not see"}

    plan = TeamPlan(
        reasoning="并行任务",
        plan=[
            TeamPlanItem(agent="code", input="读 main.py", purpose="入口"),
            TeamPlanItem(agent="rag", input="Router 设计", purpose="文档"),
        ],
    )
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: _make_fake_llm(plan))

    # 在启动 run_team_path 前设置中止
    await set_abort("t-abort-start")

    events = await _collect_events(
        run_team_path(
            "分析项目入口文件和文档结构",
            "t-abort-start",
            {"thread_id": "t-abort-start", "messages": []},
            subtask_runners={"code": _fake_run_coding_expert, "rag": _fake_run_rag_agent},
        )
    )

    # runner 不应被执行
    assert "code" not in executed
    assert "rag" not in executed

    # 至少有一个 error 状态的 team_progress
    progress_errors = [
        e
        for e in events
        if e["event"] == "team_progress"
        and json.loads(e["data"]).get("status") == "error"
    ]
    assert len(progress_errors) >= 1
    assert any("中止" in json.loads(e["data"]).get("message", "") for e in progress_errors)

    # 不应有成功 done
    done_progress = [e for e in events if e["event"] == "team_progress" and json.loads(e["data"]).get("status") == "done"]
    assert len(done_progress) == 0


async def test_team_parallel_passthrough_events_not_buffered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deep 子任务的 approval_request / token / tool_result 必须实时透传。"""
    plan = TeamPlan(
        reasoning="危险任务",
        plan=[
            TeamPlanItem(agent="deep", input="写入文件", purpose="改配置"),
        ],
    )
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: _make_fake_llm(plan))

    async def _fake_run_deep_path(state, message: str, **kwargs: Any) -> AsyncIterator[dict]:
        yield {"event": "approval_request", "data": json.dumps({"tool_name": "write_file", "preview": "test"})}
        yield {"event": "token", "data": "deep 结果"}
        yield {"event": "tool_result", "data": json.dumps({"name": "write_file", "result": "ok"})}

    events = await _collect_events(
        run_team_path(
            "请修改配置文件",
            "t-passthrough",
            {"thread_id": "t-passthrough", "messages": []},
            subtask_runners={"deep": _fake_run_deep_path},
        )
    )

    # approval_request / token / tool_result 都出现在事件流中
    assert any(e["event"] == "approval_request" for e in events)
    assert any(e["event"] == "token" for e in events)
    assert any(e["event"] == "tool_result" for e in events)

    # deep 子任务成功完成
    done_progress = [e for e in events if e["event"] == "team_progress" and json.loads(e["data"]).get("status") == "done"]
    assert len(done_progress) == 1


def test_team_event_helpers() -> None:
    """make_team_event 正确序列化 dict / 保留字符串 token。"""
    ev = make_team_event("team_plan", {"plan": [], "reasoning": "r"})
    assert ev["event"] == "team_plan"
    data = json.loads(ev["data"])
    assert data["reasoning"] == "r"

    token_ev = make_team_event("token", "你好")
    assert token_ev["event"] == "token"
    assert token_ev["data"] == "你好"
