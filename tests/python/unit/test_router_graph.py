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
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

import app.memory.skills_store as ss_module
from app.router.graph import run_router


def _write_skill(tmp_path: Any, name: str, content: str) -> None:
    """在临时 skills 目录下写入 SKILL.md。"""
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


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
        permission_mode: str = "standard",
        workspace_path: str | None = None,
        chat_model=None,
        checkpointer: Any = None,
    ) -> AsyncIterator[dict]:
        captured["message"] = message
        captured["thread_id"] = thread_id
        captured["profile_prompt"] = profile_prompt
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
        permission_mode: str = "standard",
        workspace_path: str | None = None,
        chat_model=None,
        checkpointer: Any = None,
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
        chat_model=None,
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
# 6. /skill 标记解析（work 场景注入，coding 场景不注入）
# ============================================================


async def test_router_skill_tag_injected_in_work_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """work 场景下 /skill: 标记的 skill_content 拼到 profile_prompt 前。"""

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

    # 隔离 skills 目录并写入 coder 技能文件
    monkeypatch.setattr(ss_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "_SKILLS_DIR", tmp_path / "skills")
    _write_skill(tmp_path, "coder", "CODER SKILL CONTENT")
    monkeypatch.setattr(
        "app.router.graph.run_work_supervisor",
        _fake_run_work_supervisor,
    )

    events = await _collect_events(
        run_router("/skill:coder 帮我写代码", "t-skill", agent_mode="work")
    )

    # /skill: 标记被移除
    assert captured["message"] == "帮我写代码"
    # skill_content 拼到 profile_prompt 前
    assert "CODER SKILL CONTENT" in captured["profile_prompt"]
    # profile_prompt（用户画像）也在其中（可能为空字符串，但拼接后非空）
    assert captured["profile_prompt"].startswith("CODER SKILL CONTENT")

    assert any(e["event"] == "done" for e in events)


async def test_router_skill_tag_not_injected_in_coding_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """coding 场景下 /skill: 标记被移除但不注入 profile_prompt。"""

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

    # 隔离 skills 目录并写入 coder 技能文件
    monkeypatch.setattr(ss_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "_SKILLS_DIR", tmp_path / "skills")
    _write_skill(tmp_path, "coder", "CODER SKILL CONTENT")
    monkeypatch.setattr(
        "app.router.graph.run_coding_expert",
        _fake_run_coding_expert,
    )

    events = await _collect_events(
        run_router("/skill:coder 帮我写代码", "t-skill-coding", agent_mode="coding")
    )

    # /skill: 标记被移除
    assert captured["message"] == "帮我写代码"
    # coding 场景不注入 skill_content
    assert "CODER SKILL CONTENT" not in captured["profile_prompt"]

    assert any(e["event"] == "done" for e in events)


# ============================================================
# 7. workspace_path 透传 + 授权
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


# ============================================================
# 10. agent_mode normalize（大小写不敏感 + 旧值兼容）
# ============================================================


async def test_router_agent_mode_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """agent_mode 大小写不敏感：'Work' / 'CODING' / 'Coding_Team' 均接受。"""

    captured_modes: list[str] = []

    async def _fake_run_work_supervisor(message, thread_id, **kwargs):
        captured_modes.append("work")
        yield {"event": "token", "data": "ok"}

    async def _fake_run_coding_expert(message, thread_id, **kwargs):
        captured_modes.append("coding")
        yield {"event": "token", "data": "ok"}

    async def _fake_run_coding_team(message, thread_id, **kwargs):
        captured_modes.append("coding_team")
        yield {"event": "token", "data": "ok"}

    monkeypatch.setattr("app.router.graph.run_work_supervisor", _fake_run_work_supervisor)
    monkeypatch.setattr("app.router.graph.run_coding_expert", _fake_run_coding_expert)
    monkeypatch.setattr("app.router.graph.run_coding_team", _fake_run_coding_team)

    await _collect_events(run_router("hi", "t1", agent_mode="Work"))
    await _collect_events(run_router("hi", "t2", agent_mode="CODING"))
    await _collect_events(run_router("hi", "t3", agent_mode="Coding_Team"))

    assert captured_modes == ["work", "coding", "coding_team"]


async def test_router_agent_mode_legacy_values_compat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧值 'agent' / 'agent_team' 兼容映射到 'work' / 'coding_team'。"""

    captured_modes: list[str] = []

    async def _fake_run_work_supervisor(message, thread_id, **kwargs):
        captured_modes.append("work")
        yield {"event": "token", "data": "ok"}

    async def _fake_run_coding_team(message, thread_id, **kwargs):
        captured_modes.append("coding_team")
        yield {"event": "token", "data": "ok"}

    monkeypatch.setattr("app.router.graph.run_work_supervisor", _fake_run_work_supervisor)
    monkeypatch.setattr("app.router.graph.run_coding_team", _fake_run_coding_team)

    await _collect_events(run_router("hi", "t1", agent_mode="agent"))
    await _collect_events(run_router("hi", "t2", agent_mode="agent_team"))

    assert captured_modes == ["work", "coding_team"]


# ============================================================
# 11. /skill 非 work 模式警告 token
# ============================================================


async def test_router_skill_tag_warns_in_non_work_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """非 work 模式下 /skill 标记被清理时 yield 警告 token。"""

    async def _fake_run_coding_expert(message, thread_id, **kwargs):
        yield {"event": "token", "data": "ok"}

    monkeypatch.setattr(ss_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "_SKILLS_DIR", tmp_path / "skills")
    _write_skill(tmp_path, "coder", "CODER SKILL CONTENT")
    monkeypatch.setattr("app.router.graph.run_coding_expert", _fake_run_coding_expert)

    events = await _collect_events(
        run_router("/skill:coder 帮我写代码", "t-skill-warn", agent_mode="coding")
    )

    # 应该有一条 [skill 跳过] 警告 token
    token_events = [e for e in events if e["event"] == "token"]
    skill_warn = [e for e in token_events if "[skill 跳过]" in e.get("data", "")]
    assert len(skill_warn) == 1
    assert "coding" in skill_warn[0]["data"]


# ============================================================
# 12. workspace revoked 前端反馈 token
# ============================================================


async def test_router_workspace_revoked_yields_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workspace 在 revoked_paths 中时 yield [工作区跳过] token。"""

    async def _fake_run_work_supervisor(message, thread_id, **kwargs):
        yield {"event": "token", "data": "ok"}

    monkeypatch.setattr("app.router.graph.run_work_supervisor", _fake_run_work_supervisor)

    events = await _collect_events(
        run_router(
            "hi",
            "t-revoked",
            agent_mode="work",
            workspace_path="D:\\revoked_proj",
            revoked_paths=["D:\\revoked_proj"],
        )
    )

    token_events = [e for e in events if e["event"] == "token"]
    skipped_warn = [e for e in token_events if "[工作区跳过]" in e.get("data", "")]
    assert len(skipped_warn) == 1
    assert "D:\\revoked_proj" in skipped_warn[0]["data"]


# ============================================================
# 13. coding_team 写回 checkpointer 仅在 team_done 收到时
# ============================================================


async def test_router_team_no_done_no_checkpoint_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """coding_team 未收到 team_done 事件（SSE 中途断开）时不写回 checkpointer。"""

    from unittest.mock import AsyncMock, MagicMock

    async def _fake_run_coding_team_no_done(message, thread_id, **kwargs):
        # 模拟 SSE 中途断开：只 yield token，不 yield team_done
        yield {"event": "token", "data": "partial content"}
        # 生成器在此结束（模拟断连）

    monkeypatch.setattr("app.router.graph.run_coding_team", _fake_run_coding_team_no_done)

    # mock _append_messages_to_checkpointer 验证不调用
    append_called: list[list] = []

    async def _fake_append(checkpointer, thread_id, new_messages):
        append_called.append(new_messages)

    monkeypatch.setattr("app.router.graph._append_messages_to_checkpointer", _fake_append)

    mock_checkpointer = MagicMock()
    mock_checkpointer.aget = AsyncMock(return_value=None)

    await _collect_events(
        run_router(
            "hi",
            "t-no-done",
            agent_mode="coding_team",
            checkpointer=mock_checkpointer,
        )
    )

    # 不应该写回 checkpointer
    assert append_called == []


async def test_router_team_with_done_writes_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """coding_team 收到 team_done 且无 error 时正常写回 checkpointer。"""

    from unittest.mock import AsyncMock, MagicMock

    async def _fake_run_coding_team_with_done(message, thread_id, **kwargs):
        yield {"event": "token", "data": "final content"}
        yield {"event": "team_done", "data": json.dumps({"status": "done"})}

    monkeypatch.setattr("app.router.graph.run_coding_team", _fake_run_coding_team_with_done)

    # mock _append_messages_to_checkpointer 验证调用（避免 langgraph compile 校验）
    append_called: list[list] = []

    async def _fake_append(checkpointer, thread_id, new_messages):
        append_called.append(new_messages)

    monkeypatch.setattr("app.router.graph._append_messages_to_checkpointer", _fake_append)

    # mock checkpointer：aget 返回空 checkpoint（用于 _load_history_from_checkpointer）
    mock_checkpointer = MagicMock()
    mock_checkpointer.aget = AsyncMock(return_value=None)

    await _collect_events(
        run_router(
            "hi",
            "t-with-done",
            agent_mode="coding_team",
            checkpointer=mock_checkpointer,
        )
    )

    # 应该写回 checkpointer
    assert len(append_called) == 1
    new_msgs = append_called[0]
    assert len(new_msgs) == 2  # HumanMessage + AIMessage


# ============================================================
# 14. tokenizer 异常不再静默吞（warning 日志）
# ============================================================


async def test_router_tokenizer_exception_logged_not_silenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_model.get_num_tokens_from_messages 抛异常时记录 warning，不静默。"""

    from app.router.graph import logger as router_logger

    async def _fake_run_work_supervisor(message, thread_id, **kwargs):
        yield {"event": "token", "data": "ok"}

    monkeypatch.setattr("app.router.graph.run_work_supervisor", _fake_run_work_supervisor)

    # mock checkpointer 返回若干历史消息
    from unittest.mock import AsyncMock, MagicMock
    from langchain_core.messages import HumanMessage

    mock_cp = MagicMock()
    mock_cp.aget = AsyncMock(
        return_value={
            "channel_values": {
                "messages": [HumanMessage(content="history msg")] * 5,
            }
        }
    )

    # mock chat_model：get_num_tokens_from_messages 抛异常
    mock_model = MagicMock()
    mock_model.get_num_tokens_from_messages = MagicMock(
        side_effect=RuntimeError("tokenizer exploded")
    )

    # 拦截 logger.warning 调用（loguru 不走标准 logging，caplog 抓不到）
    warning_calls: list[tuple[str, dict]] = []

    def _capture_warning(msg, *args, **kwargs):
        warning_calls.append((str(msg), kwargs))

    monkeypatch.setattr(router_logger, "warning", _capture_warning)

    events = await _collect_events(
        run_router(
            "hi",
            "t-tokenizer-err",
            agent_mode="work",
            checkpointer=mock_cp,
            chat_model=mock_model,
        )
    )

    # 应该有 router.token_counter_failed warning 日志
    tokenizer_warnings = [
        (msg, kw) for msg, kw in warning_calls if "token_counter_failed" in msg
    ]
    assert len(tokenizer_warnings) == 1, (
        f"expected 1 token_counter_failed warning, got {tokenizer_warnings}"
    )
    # error 信息在结构化字段 error= 里
    _, kwargs = tokenizer_warnings[0]
    assert "tokenizer exploded" in str(kwargs.get("error", ""))

    # 仍然正常 done
    assert any(e["event"] == "done" for e in events)
