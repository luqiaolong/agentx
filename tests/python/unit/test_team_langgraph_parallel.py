"""AgentTeam LangGraph Send 并行编排回归测试。

覆盖：
1. 多子任务通过 Send fan-out 并行执行，结果自动聚合到黑板。
2. 各子任务节点完成后通过 ``_merge_todos`` reducer 更新 ``state.todos``，
   最终 ``todo_update`` 事件反映所有 todo 为 ``completed``。
3. 中止事件在子任务节点入口处被检查，子任务返回失败 payload，
   Aggregator 检测到 ``findings={}`` 后发出 ``error`` + ``team_done(error)``。
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
from app.team.orchestrator import run_team_path


def _make_fake_llm_for_aggregator() -> MagicMock:
    """构造 mock LLM：astream 返回汇总 chunk（供 Aggregator 使用）。"""
    mock = MagicMock()

    async def _fake_astream(messages: Any) -> AsyncIterator:
        yield SimpleNamespace(content="最终汇总")

    mock.astream = _fake_astream
    return mock


def _patch_orchestrator_to_return_todos(
    monkeypatch: pytest.MonkeyPatch,
    todos: list[dict],
) -> MagicMock:
    """patch get_chat_model 返回 mock LLM（ainvoke 返回 [agent:xxx] 任务行文本）。

    新方案 _plan_node 直接用 llm.ainvoke 调用 LLM，从回复正文解析 [agent:xxx]
    任务行。mock LLM 的 ainvoke 返回 SimpleNamespace(content=任务行文本)，
    astream 返回汇总 chunk 供 Aggregator 使用。
    """
    fake_llm = _make_fake_llm_for_aggregator()

    # 构造 ainvoke 响应：把 todos 的 content 拼成文本（模拟 LLM 输出 [agent:xxx] 任务行）
    todo_lines = "\n".join(t["content"] for t in todos)
    fake_response = SimpleNamespace(content=todo_lines)
    fake_llm.ainvoke = AsyncMock(return_value=fake_response)

    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: fake_llm)
    return fake_llm


def _todo(content: str, status: str = "pending") -> dict:
    """构造 deepagents 原生 Todo dict。"""
    return {"content": content, "status": status}


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

    todos = [
        _todo("[agent:code] 读 main.py"),
        _todo("[agent:rag] 检索 Router 设计"),
    ]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

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

    # todo_update 事件存在（替代旧的 team_plan / team_progress）
    assert "todo_update" in event_types
    todo_updates = [e for e in events if e["event"] == "todo_update"]
    assert len(todo_updates) >= 1

    # 最终 todo_update：所有 todo 应为 completed
    final_todos = json.loads(todo_updates[-1]["data"]).get("todos", [])
    assert len(final_todos) == 2
    for todo in final_todos:
        assert todo["status"] == "completed", f"Expected completed, got {todo['status']}"

    # Aggregator 输出 token
    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) >= 1
    assert "".join(e["data"] for e in tokens) == "最终汇总"

    # team_done 收尾
    assert any(e["event"] == "team_done" for e in events)


async def test_team_parallel_aborts_at_subtask_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在子任务启动前触发中止，子任务节点应返回失败并不执行 runner。"""
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

    todos = [
        _todo("[agent:code] 读 main.py"),
        _todo("[agent:rag] 检索 Router 设计"),
    ]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

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

    # 中止后子任务失败 → findings 为空 → Aggregator 发 error + team_done(error)
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) >= 1
    assert any("所有专家任务均失败" in e["data"] for e in error_events)

    # team_done 收尾
    done_events = [e for e in events if e["event"] == "team_done"]
    assert len(done_events) >= 1


async def test_team_parallel_passthrough_events_not_buffered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deep 子任务的 approval_request / token / tool_result 必须实时透传。"""
    todos = [
        _todo("[agent:deep] 写入文件"),
    ]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

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

    # deep 子任务完成后 todo_update 最终为 completed
    todo_updates = [e for e in events if e["event"] == "todo_update"]
    assert len(todo_updates) >= 1
    final_todos = json.loads(todo_updates[-1]["data"]).get("todos", [])
    assert any(t["status"] == "completed" for t in final_todos)
