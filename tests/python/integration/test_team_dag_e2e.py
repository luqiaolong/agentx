"""AgentTeam DAG 端到端集成测试。

通过 ASGITransport 直连 FastAPI app，调用 ``POST /api/chat`` 的
``coding_team`` 模式，验证 DAG 依赖编排、分层 fan-out、结果注入、
replan 和 fallback 在完整 HTTP/SSE 链路中的行为。

所有 LLM 调用和子代理 runner 均被 mock，不依赖外部 API。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def _clear_team_state():
    """清理 abort 状态，避免用例间污染。"""
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    yield
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()


async def _post_chat(
    client: AsyncClient,
    message: str,
    thread_id: str,
    agent_mode: str = "coding_team",
) -> list[dict]:
    """发送 /api/chat 请求并收集 SSE 事件 dict 列表（带总超时）。

    正确解析 SSE ``event:`` + ``data:`` 行，保留事件类型；data 保持原始字符串，
    由调用方按需 ``json.loads`` 解析。
    """
    body = {
        "message": message,
        "thread_id": thread_id,
        "agent_mode": agent_mode,
    }
    events: list[dict] = []

    async def _read_stream() -> None:
        async with client.stream("POST", "/api/chat", json=body) as resp:
            assert resp.status_code == 200
            current_event: dict[str, Any] = {}
            data_parts: list[str] = []
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    if current_event and data_parts:
                        current_event["data"] = "\n".join(data_parts)
                        events.append(current_event)
                    current_event = {"event": line[6:].strip()}
                    data_parts = []
                elif line.startswith("data:"):
                    data_parts.append(line[5:].strip())
                elif line.strip() == "" and current_event and data_parts:
                    current_event["data"] = "\n".join(data_parts)
                    events.append(current_event)
                    current_event = {}
                    data_parts = []
            # 流末尾未以空行结尾的事件
            if current_event and data_parts:
                current_event["data"] = "\n".join(data_parts)
                events.append(current_event)

    try:
        await asyncio.wait_for(_read_stream(), timeout=15)
    except asyncio.TimeoutError:
        events.append({"event": "test_timeout", "data": "SSE 流未在 15s 内结束"})

    return events


def _find_event(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e.get("event") == event_type]


@pytest.mark.integration
async def test_team_dag_sequential_dependency_injection(
    monkeypatch: pytest.MonkeyPatch,
    _clear_team_state: Any,
) -> None:
    """顺序依赖：[code] 读取 → [deep][after:0] 修改，deep input 包含 code findings。"""
    plan_text = "[agent:code] 读取 src/main.py\n[agent:deep][after:0] 基于上述结果修改 src/main.py"

    llm = AsyncMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(content=plan_text),
    )

    captured_inputs: list[tuple[str, str]] = []

    async def _fake_code_runner(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        captured_inputs.append(("code", message))
        yield {"event": "token", "data": "代码读取结果：包含 foo() 函数"}

    async def _fake_deep_runner(
        state: Any,
        message: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        captured_inputs.append(("deep", message))
        # 验证依赖上下文已注入
        assert "[依赖任务结果]" in message
        assert "#0" in message
        assert "代码读取结果" in message
        yield {"event": "token", "data": "已基于依赖完成修改"}

    with (
        patch("app.team.orchestrator.get_chat_model", return_value=llm),
        patch("app.team.orchestrator._get_runner") as mock_get_runner,
    ):
        mock_get_runner.side_effect = lambda name, _sub: {
            "code": _fake_code_runner,
            "deep": _fake_deep_runner,
        }.get(name)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            events = await _post_chat(
                client,
                message="读取并修改 main.py",
                thread_id="e2e-dag-seq",
            )

    assert not any(e.get("event") == "test_timeout" for e in events)

    team_init = _find_event(events, "team_init")
    assert len(team_init) >= 1
    init_data = json.loads(team_init[0]["data"])
    assert len(init_data["plan"]) == 2
    assert init_data["plan"][1].get("deps") == [0]

    team_done = _find_event(events, "team_done")
    assert len(team_done) >= 1

    # 验证两个 runner 都被调用，且 deep 在 code 之后
    assert captured_inputs[0][0] == "code"
    assert any(name == "deep" for name, _ in captured_inputs)


@pytest.mark.integration
async def test_team_dag_parallel_no_dependency(
    monkeypatch: pytest.MonkeyPatch,
    _clear_team_state: Any,
) -> None:
    """无依赖任务并行执行：两个 code 任务同时派发。"""
    plan_text = "[agent:code] 读取 A.py\n[agent:code] 读取 B.py"

    llm = AsyncMock()
    # 初始计划返回两个并行 code 任务；replan 调用返回 NO_NEW_TASKS，避免循环追加。
    llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(content=plan_text),
            *[MagicMock(content="NO_NEW_TASKS")] * 3,
        ],
    )

    call_order: list[str] = []

    async def _fake_code_runner(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        call_order.append(message)
        yield {"event": "token", "data": "ok"}

    with (
        patch("app.team.orchestrator.get_chat_model", return_value=llm),
        patch("app.team.orchestrator._get_runner") as mock_get_runner,
    ):
        mock_get_runner.side_effect = lambda name, _sub: {
            "code": _fake_code_runner,
        }.get(name)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            events = await _post_chat(
                client,
                message="并行读取两个文件",
                thread_id="e2e-dag-par",
            )

    assert not any(e.get("event") == "test_timeout" for e in events)

    team_init = _find_event(events, "team_init")
    init_data = json.loads(team_init[0]["data"])
    assert all(p.get("deps") == [] for p in init_data["plan"])

    team_done = _find_event(events, "team_done")
    assert len(team_done) >= 1
    assert len(call_order) == 2


@pytest.mark.integration
async def test_team_dag_unknown_agent_fallback_to_code(
    monkeypatch: pytest.MonkeyPatch,
    _clear_team_state: Any,
) -> None:
    """未知 agent 类型经 default fallback 走 code runner，不直接失败。"""
    plan_text = "[agent:unknown_specialist] 执行某项任务"

    llm = AsyncMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(content=plan_text),
    )

    fallback_called = False

    async def _fake_code_runner(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        nonlocal fallback_called
        fallback_called = True
        yield {"event": "token", "data": "fallback 执行结果"}

    with (
        patch("app.team.orchestrator.get_chat_model", return_value=llm),
        patch("app.team.orchestrator._get_runner") as mock_get_runner,
    ):
        mock_get_runner.side_effect = lambda name, _sub: {
            "code": _fake_code_runner,
        }.get(name)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            events = await _post_chat(
                client,
                message="用未知 agent 做件事",
                thread_id="e2e-dag-fallback",
            )

    assert not any(e.get("event") == "test_timeout" for e in events)
    assert fallback_called is True
    team_done = _find_event(events, "team_done")
    assert len(team_done) >= 1


@pytest.mark.integration
async def test_team_dag_replan_appends_tasks(
    monkeypatch: pytest.MonkeyPatch,
    _clear_team_state: Any,
) -> None:
    """第一轮完成后 replan 追加新任务，新任务依赖已完成任务。"""
    first_plan = "[agent:code] 读取 src/main.py"
    replan_text = "[agent:deep][after:0] 基于读取结果重构"

    llm = AsyncMock()
    # 第一次调用返回初始计划，第二次调用返回 replan 追加任务
    responses = [
        MagicMock(content=first_plan),
        MagicMock(content=replan_text),
    ]
    llm.ainvoke = AsyncMock(side_effect=responses)

    captured_inputs: list[tuple[str, str]] = []

    async def _fake_code_runner(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        captured_inputs.append(("code", message))
        yield {"event": "token", "data": "读取结果"}

    async def _fake_deep_runner(
        state: Any,
        message: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        captured_inputs.append(("deep", message))
        assert "读取结果" in message
        yield {"event": "token", "data": "重构完成"}

    with (
        patch("app.team.orchestrator.get_chat_model", return_value=llm),
        patch("app.team.orchestrator._get_runner") as mock_get_runner,
    ):
        mock_get_runner.side_effect = lambda name, _sub: {
            "code": _fake_code_runner,
            "deep": _fake_deep_runner,
        }.get(name)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            events = await _post_chat(
                client,
                message="读取并重构 main.py",
                thread_id="e2e-dag-replan",
            )

    assert not any(e.get("event") == "test_timeout" for e in events)

    team_done = _find_event(events, "team_done")
    assert len(team_done) >= 1

    # code 跑一次，deep 在 replan 后跑一次
    assert any(name == "code" for name, _ in captured_inputs)
    assert any(name == "deep" for name, _ in captured_inputs)


@pytest.mark.integration
async def test_team_dag_replan_limit_stops_loop(
    monkeypatch: pytest.MonkeyPatch,
    _clear_team_state: Any,
) -> None:
    """replan 次数达到上限后不再追加，直接结束。"""
    first_plan = "[agent:code] 读取 src/main.py"
    # 每次 replan 都尝试追加，验证 max_replans 限制生效
    replan_text = "[agent:deep][after:0] 继续修改"

    llm = AsyncMock()
    llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(content=first_plan),
            *[MagicMock(content=replan_text)] * 5,
        ],
    )

    call_count = 0

    async def _fake_code_runner(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        nonlocal call_count
        call_count += 1
        yield {"event": "token", "data": "ok"}

    async def _fake_deep_runner(
        state: Any,
        message: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        nonlocal call_count
        call_count += 1
        yield {"event": "token", "data": "ok"}

    with (
        patch("app.team.orchestrator.get_chat_model", return_value=llm),
        patch("app.team.orchestrator._get_runner") as mock_get_runner,
    ):
        mock_get_runner.side_effect = lambda name, _sub: {
            "code": _fake_code_runner,
            "deep": _fake_deep_runner,
        }.get(name)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            events = await _post_chat(
                client,
                message="反复修改文件",
                thread_id="e2e-dag-replan-limit",
            )

    assert not any(e.get("event") == "test_timeout" for e in events)
    team_done = _find_event(events, "team_done")
    assert len(team_done) >= 1
    # 初始 code 1 次 + replan deep 最多 max_replans(默认 2) 次 = 3 次 runner 调用
    assert call_count <= 3
