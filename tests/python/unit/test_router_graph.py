"""Router 场景分发单元测试：mock 三种场景 runner，不调真实服务。

覆盖（Phase 2e 新架构）：
1. work 场景分发：mock run_work_supervisor，验证 token 事件透传 + done 事件
2. coding 场景分发：mock run_coding_expert，验证 token 事件透传 + done 事件
3. coding_team 场景分发：mock run_coding_team，验证 token 事件透传 + done 事件
4. 无效 agent_mode：yield error + done 事件
5. 默认 agent_mode 为 "work"
6. @skill 标记解析后调用 work runner
7. checkpointer 历史加载与截断
8. assistant 内容写回 checkpointer
9. workspace_path 透传
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

from app.router.graph import run_router


# ============================================================
# 辅助函数
# ============================================================


async def _collect_events(gen: AsyncIterator[dict]) -> list[dict]:
    """收集异步生成器的所有事件。"""
    events: list[dict] = []
    async for event in gen:
        events.append(event)
    return events


def _make_token_stream(tokens: list[str]) -> Any:
    """构造 fake async generator，yield 指定 token 事件。"""

    async def _gen(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        for tok in tokens:
            yield {"event": "token", "data": tok}

    return _gen


# ============================================================
# 1. work 场景分发
# ============================================================


async def test_router_work_mode_dispatches_to_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """work 模式 → run_work_supervisor 被调用，token 事件透传。"""

    captured: dict = {}

    async def _fake_run_work_supervisor(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "standard",
        workspace_path: str | None = None,
    ) -> AsyncIterator[dict]:
        captured["message"] = message
        captured["thread_id"] = thread_id
        captured["profile_prompt"] = profile_prompt
        captured["history"] = history
        captured["permission_mode"] = permission_mode
        captured["workspace_path"] = workspace_path
        yield {"event": "token", "data": "你好"}
        yield {"event": "token", "data": "！"}
        yield {"event": "tool_call", "data": json.dumps({"name": "read_file", "args": {"path": "/tmp"}})}

    monkeypatch.setattr(
        "app.router.graph.run_work_supervisor",
        _fake_run_work_supervisor,
    )

    events = await _collect_events(
        run_router("你好", "t-work", agent_mode="work")
    )

    # 验证参数透传
    assert captured["message"] == "你好"
    assert captured["thread_id"] == "t-work"
    assert captured["permission_mode"] == "standard"

    # 验证 token 事件透传
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 2
    assert token_events[0]["data"] == "你好"
    assert token_events[1]["data"] == "！"

    # 验证 tool_call 事件透传
    tool_call_events = [e for e in events if e["event"] == "tool_call"]
    assert len(tool_call_events) == 1

    # 验证 done 事件
    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["data"] == "{}"


# ============================================================
# 2. coding 场景分发
# ============================================================


async def test_router_coding_mode_dispatches_to_expert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """coding 模式 → run_coding_expert 被调用。"""

    captured: dict = {}

    async def _fake_run_coding_expert(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "standard",
        workspace_path: str | None = None,
    ) -> AsyncIterator[dict]:
        captured["message"] = message
        captured["thread_id"] = thread_id
        captured["workspace_path"] = workspace_path
        yield {"event": "token", "data": "code response"}

    monkeypatch.setattr(
        "app.router.graph.run_coding_expert",
        _fake_run_coding_expert,
    )

    events = await _collect_events(
        run_router(
            "帮我写代码",
            "t-coding",
            agent_mode="coding",
            workspace_path="D:\\proj",
        )
    )

    assert captured["message"] == "帮我写代码"
    assert captured["thread_id"] == "t-coding"
    assert captured["workspace_path"] == "D:\\proj"

    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert token_events[0]["data"] == "code response"

    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1


# ============================================================
# 3. coding_team 场景分发
# ============================================================


async def test_router_coding_team_mode_dispatches_to_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """coding_team 模式 → run_coding_team 被调用。"""

    captured: dict = {}

    async def _fake_run_coding_team(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "standard",
        workspace_path: str | None = None,
    ) -> AsyncIterator[dict]:
        captured["message"] = message
        captured["thread_id"] = thread_id
        yield {"event": "token", "data": "team summary"}
        yield {"event": "team_plan", "data": json.dumps({"plan": [], "reasoning": "test"})}
        yield {"event": "team_done", "data": json.dumps({"status": "done"})}

    monkeypatch.setattr(
        "app.router.graph.run_coding_team",
        _fake_run_coding_team,
    )

    events = await _collect_events(
        run_router("复杂任务", "t-team", agent_mode="coding_team")
    )

    assert captured["message"] == "复杂任务"
    assert captured["thread_id"] == "t-team"

    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert token_events[0]["data"] == "team summary"

    team_plan_events = [e for e in events if e["event"] == "team_plan"]
    assert len(team_plan_events) == 1

    team_done_events = [e for e in events if e["event"] == "team_done"]
    assert len(team_done_events) == 1

    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1


# ============================================================
# 4. 无效 agent_mode
# ============================================================


async def test_router_invalid_agent_mode_yields_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无效 agent_mode → yield error + done 事件。"""

    # 确保场景 runner 不会被调用
    supervisor_called = False
    expert_called = False
    team_called = False

    async def _no_call_supervisor(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        nonlocal supervisor_called
        supervisor_called = True
        if False:  # pragma: no cover
            yield {}

    async def _no_call_expert(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        nonlocal expert_called
        expert_called = True
        if False:  # pragma: no cover
            yield {}

    async def _no_call_team(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        nonlocal team_called
        team_called = True
        if False:  # pragma: no cover
            yield {}

    monkeypatch.setattr("app.router.graph.run_work_supervisor", _no_call_supervisor)
    monkeypatch.setattr("app.router.graph.run_coding_expert", _no_call_expert)
    monkeypatch.setattr("app.router.graph.run_coding_team", _no_call_team)

    events = await _collect_events(
        run_router("hi", "t-invalid", agent_mode="invalid_mode")
    )

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert "无效的 agent_mode" in error_events[0]["data"]
    assert "invalid_mode" in error_events[0]["data"]

    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1

    assert not supervisor_called
    assert not expert_called
    assert not team_called


# ============================================================
# 5. 默认 agent_mode 为 "work"
# ============================================================


async def test_router_default_agent_mode_is_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不传 agent_mode → 默认 "work"，调用 run_work_supervisor。"""

    captured_mode: dict = {}

    async def _fake_run_work_supervisor(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        captured_mode["called"] = True
        yield {"event": "token", "data": "ok"}

    monkeypatch.setattr(
        "app.router.graph.run_work_supervisor",
        _fake_run_work_supervisor,
    )

    events = await _collect_events(run_router("hi", "t-default"))

    assert captured_mode.get("called") is True
    assert any(e["event"] == "token" for e in events)
    assert any(e["event"] == "done" for e in events)


# ============================================================
# 6. @skill 标记解析（work 场景注入，coding 场景不注入）
# ============================================================


async def test_router_skill_tag_injected_in_work_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """work 场景下 @skill: 标记的 skill_content 拼到 profile_prompt 前。"""

    captured: dict = {}

    async def _fake_run_work_supervisor(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        captured["message"] = message
        captured["profile_prompt"] = profile_prompt
        yield {"event": "token", "data": "ok"}

    # mock get_skills 返回一个名为 "coder" 的技能
    fake_skill = SimpleNamespace(name="coder", content="CODER SKILL CONTENT")
    monkeypatch.setattr(
        "app.router.graph.get_skills",
        lambda: [fake_skill],
    )
    monkeypatch.setattr(
        "app.router.graph.run_work_supervisor",
        _fake_run_work_supervisor,
    )

    events = await _collect_events(
        run_router("@skill:coder 帮我写代码", "t-skill", agent_mode="work")
    )

    # @skill: 标记被移除
    assert captured["message"] == "帮我写代码"
    # skill_content 拼到 profile_prompt 前
    assert "CODER SKILL CONTENT" in captured["profile_prompt"]
    # profile_prompt（用户画像）也在其中（可能为空字符串，但拼接后非空）
    assert captured["profile_prompt"].startswith("CODER SKILL CONTENT")

    assert any(e["event"] == "done" for e in events)


async def test_router_skill_tag_not_injected_in_coding_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """coding 场景下 @skill: 标记被移除但不注入 profile_prompt。"""

    captured: dict = {}

    async def _fake_run_coding_expert(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        captured["message"] = message
        captured["profile_prompt"] = profile_prompt
        yield {"event": "token", "data": "ok"}

    fake_skill = SimpleNamespace(name="coder", content="CODER SKILL CONTENT")
    monkeypatch.setattr(
        "app.router.graph.get_skills",
        lambda: [fake_skill],
    )
    monkeypatch.setattr(
        "app.router.graph.run_coding_expert",
        _fake_run_coding_expert,
    )

    events = await _collect_events(
        run_router("@skill:coder 帮我写代码", "t-skill-coding", agent_mode="coding")
    )

    # @skill: 标记被移除
    assert captured["message"] == "帮我写代码"
    # coding 场景不注入 skill_content
    assert "CODER SKILL CONTENT" not in captured["profile_prompt"]

    assert any(e["event"] == "done" for e in events)


# ============================================================
# 7. checkpointer 历史加载与写回
# ============================================================


async def test_router_checkpointer_history_loaded_and_written_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """checkpointer 历史被加载，assistant 内容被写回。"""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver

    checkpointer = InMemorySaver()

    # 预写入一条历史消息
    from langgraph.graph import END, START, MessagesState, StateGraph

    async def _passthrough(state: MessagesState) -> dict:
        return {"messages": []}

    graph = StateGraph(MessagesState)
    graph.add_node("passthrough", _passthrough)
    graph.add_edge(START, "passthrough")
    graph.add_edge("passthrough", END)
    compiled = graph.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "t-cp"}}
    await compiled.ainvoke(
        {"messages": [HumanMessage(content="历史问题"), AIMessage(content="历史回答")]},
        config=config,
    )

    captured: dict = {}

    async def _fake_run_work_supervisor(
        message: str,
        thread_id: str,
        profile_prompt: str = "",
        history: list | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        captured["history"] = history
        yield {"event": "token", "data": "新回答"}

    monkeypatch.setattr(
        "app.router.graph.run_work_supervisor",
        _fake_run_work_supervisor,
    )

    events = await _collect_events(
        run_router("新问题", "t-cp", checkpointer=checkpointer, agent_mode="work")
    )

    # 历史被加载（至少 2 条）
    assert len(captured["history"]) >= 2

    # 写回后，checkpointer 中应包含新问题和新回答
    checkpoint = checkpointer.get({"configurable": {"thread_id": "t-cp"}})
    messages = checkpoint.get("channel_values", {}).get("messages", [])
    contents = [getattr(m, "content", "") for m in messages]
    assert "新问题" in contents
    assert "新回答" in contents

    assert any(e["event"] == "done" for e in events)


async def test_router_empty_assistant_content_skips_writeback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assistant 内容为空（仅 error）时不写回 checkpointer。"""
    from langgraph.checkpoint.memory import InMemorySaver

    checkpointer = InMemorySaver()

    async def _fake_run_work_supervisor(
        message: str,
        thread_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        yield {"event": "error", "data": "LLM 不可用"}
        # 无 token 事件 → assistant_content_parts 为空

    monkeypatch.setattr(
        "app.router.graph.run_work_supervisor",
        _fake_run_work_supervisor,
    )

    events = await _collect_events(
        run_router("hi", "t-empty", checkpointer=checkpointer, agent_mode="work")
    )

    # checkpointer 中无新消息
    checkpoint = checkpointer.get({"configurable": {"thread_id": "t-empty"}})
    if checkpoint:
        messages = checkpoint.get("channel_values", {}).get("messages", [])
        assert len(messages) == 0, f"expected 0 messages, got {len(messages)}"

    assert any(e["event"] == "done" for e in events)


# ============================================================
# 8. workspace_path 透传 + 授权
# ============================================================


async def test_router_workspace_path_passed_to_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workspace_path 被透传给场景 runner。"""

    captured: dict = {}

    async def _fake_run_work_supervisor(
        message: str,
        thread_id: str,
        workspace_path: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        captured["workspace_path"] = workspace_path
        yield {"event": "token", "data": "ok"}

    # mock sandbox.authorize 避免真实文件系统操作
    from unittest.mock import AsyncMock

    mock_sandbox = MagicMock()
    mock_sandbox.authorize = AsyncMock()
    monkeypatch.setattr(
        "app.sandbox.get_sandbox",
        lambda: mock_sandbox,
    )

    monkeypatch.setattr(
        "app.router.graph.run_work_supervisor",
        _fake_run_work_supervisor,
    )

    events = await _collect_events(
        run_router(
            "帮我分析",
            "t-ws",
            agent_mode="work",
            workspace_path="D:\\proj",
        )
    )

    assert captured["workspace_path"] == "D:\\proj"
    # sandbox.authorize 被调用
    mock_sandbox.authorize.assert_called_once()

    assert any(e["event"] == "done" for e in events)


# ============================================================
# 9. /reset 消息触发 checkpoint 清理（保留原测试，从 _event_generator 入口测试）
# ============================================================


async def test_router_reset_clears_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/reset 消息触发 checkpoint 清理 + 沙箱清理（当 persist_authorized_dirs=False）。"""
    from app.config import get_settings
    from app.main import ChatRequest, _event_generator

    # 设置 persist_authorized_dirs = False
    monkeypatch.setenv("AGENTX_PERSIST_AUTHORIZED_DIRS", "false")
    get_settings.cache_clear()

    # Mock get_async_checkpointer 返回带 adelete_thread 的 mock
    from unittest.mock import AsyncMock

    mock_checkpointer = MagicMock()
    mock_checkpointer.adelete_thread = AsyncMock()
    monkeypatch.setattr(
        "app.main.get_async_checkpointer",
        AsyncMock(return_value=mock_checkpointer),
    )

    # Mock get_sandbox 返回带 clear 的 mock
    mock_sandbox = MagicMock()
    mock_sandbox.clear = AsyncMock()
    monkeypatch.setattr("app.main.get_sandbox", MagicMock(return_value=mock_sandbox))

    # 调用 _event_generator 处理 /reset
    req = ChatRequest(message="/reset", thread_id="t-reset")
    events = await _collect_events(_event_generator(req))

    # 验证 checkpoint 被清理
    mock_checkpointer.adelete_thread.assert_awaited_once_with("t-reset")

    # 验证沙箱被清理（persist_authorized_dirs=False）
    mock_sandbox.clear.assert_awaited_once_with("t-reset")

    # 验证事件：有 token 和 done
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert "清空" in token_events[0]["data"]

    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1


async def test_router_reset_preserves_authorized_dirs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/reset 消息当 persist_authorized_dirs=True 时仅清 checkpoint，不清沙箱。"""
    from app.config import get_settings
    from app.main import ChatRequest, _event_generator

    # 设置 persist_authorized_dirs = True（默认）
    monkeypatch.setenv("AGENTX_PERSIST_AUTHORIZED_DIRS", "true")
    get_settings.cache_clear()

    # Mock get_async_checkpointer
    from unittest.mock import AsyncMock

    mock_checkpointer = MagicMock()
    mock_checkpointer.adelete_thread = AsyncMock()
    monkeypatch.setattr(
        "app.main.get_async_checkpointer",
        AsyncMock(return_value=mock_checkpointer),
    )

    # Mock get_sandbox
    mock_sandbox = MagicMock()
    mock_sandbox.clear = AsyncMock()
    monkeypatch.setattr("app.main.get_sandbox", MagicMock(return_value=mock_sandbox))

    req = ChatRequest(message="/reset", thread_id="t-preserve")
    events = await _collect_events(_event_generator(req))

    # checkpoint 仍被清理
    mock_checkpointer.adelete_thread.assert_awaited_once_with("t-preserve")

    # 沙箱未被清理
    mock_sandbox.clear.assert_not_awaited()

    # token 事件提示授权目录已持久化
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert "持久化" in token_events[0]["data"]
