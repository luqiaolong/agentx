"""Router 编排单元测试：mock LLM + mock subagents，不调真实服务。

覆盖：
1. CHAT 路径：mock classify_message → "CHAT" + mock LLM，验证 yield token 事件
2. SINGLE_TOOL 路径：mock classify_message → "SINGLE_TOOL" + mock run_code_agent
   - 验证 delegation 事件（路径 B 入口）
   - 验证 tool_call/tool_result 透传为同名 SSE 事件（含 source 字段）
   - 验证 token 经 ThinkFilter 分离后 yield reasoning + token
3. DEEP_TASK 路径：mock classify_message → "DEEP_TASK" + mock run_deep_path，验证透传事件
4. /reset 消息触发 checkpoint 清理 + 沙箱清理
5. workspace_path 字段透传
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.router.graph import run_router
from app.utils.prompts import resolve_system_prompt


# ============================================================
# 辅助函数
# ============================================================


async def _collect_events(gen: AsyncIterator[dict]) -> list[dict]:
    """收集异步生成器的所有事件。"""
    events: list[dict] = []
    async for event in gen:
        events.append(event)
    return events


def _make_fake_llm(tokens: list[str]) -> MagicMock:
    """构造 mock LLM，astream 返回含指定 token 的 chunk 流；ainvoke 返回第一个 token。"""

    async def _fake_astream(messages: Any) -> AsyncIterator:
        for text in tokens:
            yield SimpleNamespace(content=text)

    async def _fake_ainvoke(messages: Any) -> Any:
        return SimpleNamespace(content=tokens[0] if tokens else "")

    mock_llm = MagicMock()
    mock_llm.astream = _fake_astream
    mock_llm.ainvoke = _fake_ainvoke
    return mock_llm


# ============================================================
# 2. CHAT 路径
# ============================================================


async def test_router_chat_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CHAT 路径：mock classify_message → "CHAT" + mock LLM，验证 yield token 事件。"""
    # mock classify_message 返回 CHAT
    async def _fake_classify(message: str) -> str:
        return "CHAT"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock get_chat_model 返回 fake LLM
    fake_llm = _make_fake_llm(["你好", "！", "我是", "助理"])
    monkeypatch.setattr("app.chat.run.get_chat_model", lambda **kw: fake_llm)

    events = await _collect_events(run_router("你好", "t1"))

    # 验证有 token 事件
    # 注意: ThinkFilter 会跨 chunk 缓冲最多 len("<think>")-1=6 字符以剥离 <think> 块，
    # 故 token 事件数可能少于 chunk 数（此处 4 chunk → 可能 1~2 token 事件）。
    # 契约层面只需验证: (1) 有 token 事件 (2) 拼接内容正确 (3) done 事件 data="{}"
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) >= 1
    combined = "".join(e["data"] for e in token_events)
    assert combined == "你好！我是助理"

    # 验证有 done 事件
    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["data"] == "{}"


async def test_router_chat_path_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CHAT 路径 LLM 不可用时 yield error 事件。"""

    async def _fake_classify(message: str) -> str:
        return "CHAT"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    def _fake_get_chat_model(**kw):
        raise ValueError("no API key")

    monkeypatch.setattr("app.chat.run.get_chat_model", _fake_get_chat_model)

    events = await _collect_events(run_router("你好", "t1"))

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert "LLM 不可用" in error_events[0]["data"]

    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1


# ============================================================
# 3. SINGLE_TOOL 路径
# ============================================================


async def test_router_tool_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SINGLE_TOOL 路径：mock run_code_agent，验证 tool_call/tool_result 透传为同名 SSE 事件。"""

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock LLM（_llm_select_subagent 内部调用 get_chat_model）
    monkeypatch.setattr("app.subagents.dispatch.get_chat_model", lambda **_: _make_fake_llm(["code"]))

    # mock run_code_agent yield 标准化事件（T3 后子代理已带 source 字段）
    async def _fake_run_code_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None, checkpointer: Any = None) -> AsyncIterator[dict]:
        yield {"type": "token", "content": "文件内容"}
        yield {
            "type": "tool_call",
            "name": "read_file",
            "args": {"path": "/tmp/a.txt"},
            "source": "code",
        }
        yield {
            "type": "tool_result",
            "name": "read_file",
            "result": "content",
            "source": "code",
        }

    monkeypatch.setattr("app.subagents.dispatch.run_code_agent", _fake_run_code_agent)

    events = await _collect_events(run_router("读文件 /tmp/a.txt", "t1"))

    # 验证 token 事件透传（ThinkFilter 对纯文本透传，受 max_hold 缓冲影响，
    # 可能拆为 feed 输出 + flush 输出，校验拼接文本）
    token_text = "".join(e["data"] for e in events if e["event"] == "token")
    assert token_text == "文件内容"

    # 验证 tool_call 透传为 tool_call SSE 事件（不再压扁为 todo_update）
    tool_call_events = [e for e in events if e["event"] == "tool_call"]
    assert len(tool_call_events) == 1
    tc_data = json.loads(tool_call_events[0]["data"])
    assert tc_data["name"] == "read_file"
    assert tc_data["args"] == {"path": "/tmp/a.txt"}
    assert tc_data["source"] == "code"
    assert "id" in tc_data and tc_data["id"]  # id 非空

    # 验证 tool_result 透传为 tool_result SSE 事件
    tool_result_events = [e for e in events if e["event"] == "tool_result"]
    assert len(tool_result_events) == 1
    tr_data = json.loads(tool_result_events[0]["data"])
    assert tr_data["name"] == "read_file"
    assert tr_data["result"] == "content"
    assert tr_data["source"] == "code"
    assert "id" in tr_data and tr_data["id"]

    # 验证 done 事件
    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1


async def test_router_tool_path_yields_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SINGLE_TOOL 路径入口 yield delegation 事件标识委派目标（spec D6）。"""

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock LLM（_llm_select_subagent 内部调用 get_chat_model）
    monkeypatch.setattr("app.subagents.dispatch.get_chat_model", lambda **_: _make_fake_llm(["code"]))

    async def _fake_run_code_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None, checkpointer: Any = None) -> AsyncIterator[dict]:
        yield {"type": "token", "content": "ok"}

    monkeypatch.setattr("app.subagents.dispatch.run_code_agent", _fake_run_code_agent)

    events = await _collect_events(run_router("读文件", "t-delegation"))

    # 验证 delegation 事件（应为第一个事件）
    delegation_events = [e for e in events if e["event"] == "delegation"]
    assert len(delegation_events) == 1
    assert events[0]["event"] == "delegation"  # delegation 应在流的最前面
    dlg_data = json.loads(delegation_events[0]["data"])
    assert dlg_data["target"] == "code"
    assert dlg_data["source"] == "router"
    assert "message" in dlg_data and dlg_data["message"]


async def test_router_tool_path_delegation_for_web_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SINGLE_TOOL 路径选 web 子代理时，delegation 事件 target="web"。"""

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock LLM（_llm_select_subagent 内部调用 get_chat_model）
    monkeypatch.setattr("app.subagents.dispatch.get_chat_model", lambda **_: _make_fake_llm(["web"]))

    async def _fake_run_web_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None, checkpointer: Any = None) -> AsyncIterator[dict]:
        yield {"type": "token", "content": "web result"}

    monkeypatch.setattr("app.subagents.dispatch.run_web_agent", _fake_run_web_agent)

    events = await _collect_events(run_router("搜索网页信息", "t-web"))

    delegation_events = [e for e in events if e["event"] == "delegation"]
    assert len(delegation_events) == 1
    dlg_data = json.loads(delegation_events[0]["data"])
    assert dlg_data["target"] == "web"


async def test_router_tool_path_source_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子代理事件未带 source 字段时（旧格式兼容），用 caller 传入的 source 兜底。"""

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock LLM（_llm_select_subagent 内部调用 get_chat_model）
    monkeypatch.setattr("app.subagents.dispatch.get_chat_model", lambda **_: _make_fake_llm(["code"]))

    # mock yield 旧格式事件（无 source 字段）
    async def _fake_run_code_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None, checkpointer: Any = None) -> AsyncIterator[dict]:
        yield {"type": "tool_call", "name": "read_file", "args": {"path": "/tmp"}}
        yield {"type": "tool_result", "name": "read_file", "result": "ok"}

    monkeypatch.setattr("app.subagents.dispatch.run_code_agent", _fake_run_code_agent)

    events = await _collect_events(run_router("读文件", "t-fallback"))

    tc_events = [e for e in events if e["event"] == "tool_call"]
    assert len(tc_events) == 1
    assert json.loads(tc_events[0]["data"])["source"] == "code"

    tr_events = [e for e in events if e["event"] == "tool_result"]
    assert len(tr_events) == 1
    assert json.loads(tr_events[0]["data"])["source"] == "code"


async def test_router_tool_path_reasoning_separation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SINGLE_TOOL 路径：token 含 <think> 块时，reasoning 走 reasoning SSE，text 走 token SSE。"""

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock LLM（_llm_select_subagent 内部调用 get_chat_model）
    monkeypatch.setattr("app.subagents.dispatch.get_chat_model", lambda **_: _make_fake_llm(["code"]))

    async def _fake_run_code_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None, checkpointer: Any = None) -> AsyncIterator[dict]:
        # 模拟推理模型输出：think 块 + 正文
        yield {"type": "token", "content": "<think>用户要读文件，我应该用 list_dir</think>"}
        yield {"type": "token", "content": "好的，我来读取文件内容。"}

    monkeypatch.setattr("app.subagents.dispatch.run_code_agent", _fake_run_code_agent)

    events = await _collect_events(run_router("列出 /tmp 目录", "t-reasoning"))

    # 验证 reasoning 事件下发（含 think 块内容）
    reasoning_events = [e for e in events if e["event"] == "reasoning"]
    reasoning_text = "".join(
        json.loads(e["data"])["content"] for e in reasoning_events
    )
    assert "用户要读文件" in reasoning_text
    assert "list_dir" in reasoning_text
    # 验证 reasoning 事件 source 字段
    for e in reasoning_events:
        assert json.loads(e["data"])["source"] == "code"

    # 验证 token 事件不含 think 标签
    token_text = "".join(e["data"] for e in events if e["event"] == "token")
    assert "<think>" not in token_text
    assert "</think>" not in token_text
    assert "好的，我来读取文件内容。" in token_text


async def test_router_tool_path_selects_web_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SINGLE_TOOL 路径：含 web 关键词时选择 web_agent。"""

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock LLM（_llm_select_subagent 内部调用 get_chat_model）
    monkeypatch.setattr("app.subagents.dispatch.get_chat_model", lambda **_: _make_fake_llm(["web"]))

    web_called = False

    async def _fake_run_web_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None, checkpointer: Any = None) -> AsyncIterator[dict]:
        nonlocal web_called
        web_called = True
        yield {"type": "token", "content": "web result"}

    async def _fake_run_code_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None, checkpointer: Any = None) -> AsyncIterator[dict]:
        yield {"type": "token", "content": "code result"}

    monkeypatch.setattr("app.subagents.dispatch.run_web_agent", _fake_run_web_agent)
    monkeypatch.setattr("app.subagents.dispatch.run_code_agent", _fake_run_code_agent)

    events = await _collect_events(run_router("搜索网页信息", "t1"))

    assert web_called
    # ThinkFilter 会跨 chunk 缓冲最多 max_hold 字符，单 chunk 长 content 会被拆分为
    # feed 立即输出 + flush 末尾输出。校验拼接后的完整文本而非单个事件。
    token_text = "".join(e["data"] for e in events if e["event"] == "token")
    assert token_text == "web result"


async def test_router_tool_path_strips_think_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回归测试：SINGLE_TOOL 路径子代理 token 中的 <think>...</think> 块必须被剥离。

    复现 claude.md §5 SSE 契约违反：子代理 yield 的 token 含推理模型 think 块时，
    _run_tool_path 经 ThinkFilter 过滤后再 yield，前端不应看到 <think> 标签。
    """

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock LLM（_llm_select_subagent 内部调用 get_chat_model）
    monkeypatch.setattr("app.subagents.dispatch.get_chat_model", lambda **_: _make_fake_llm(["code"]))

    async def _fake_run_code_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None, checkpointer: Any = None) -> AsyncIterator[dict]:
        # 模拟 MiniMax-M3 推理模型输出：think 块 + 正文
        yield {"type": "token", "content": "<think>用户要读文件，我应该用 list_dir</think>"}
        yield {"type": "token", "content": "好的，我来读取文件内容。"}
        yield {"type": "tool_call", "name": "list_dir", "args": {"path": "/tmp"}}
        yield {"type": "tool_result", "name": "list_dir", "result": ["a.txt"]}

    monkeypatch.setattr("app.subagents.dispatch.run_code_agent", _fake_run_code_agent)

    events = await _collect_events(run_router("列出 /tmp 目录", "t-think"))

    token_text = "".join(e["data"] for e in events if e["event"] == "token")
    # 必须不含 think 标签
    assert "<think>" not in token_text
    assert "</think>" not in token_text
    # 正文必须保留
    assert "好的，我来读取文件内容。" in token_text


# ============================================================
# 4. DEEP_TASK 路径
# ============================================================


async def test_router_deep_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEEP_TASK 路径：mock run_deep_path，验证透传事件。"""

    async def _fake_classify(message: str) -> str:
        return "DEEP_TASK"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock run_deep_path yield SSE 事件（注意：done 由 run_router 统一 yield）
    # 签名需与 run_deep_path 真实签名对齐（含 permission_mode / scene_prompt / workspace_path）
    async def _fake_run_deep_path(
        state: dict,
        message: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "workspace",
        scene_prompt: str | None = None,
        workspace_path: str | None = None,
    ) -> AsyncIterator[dict]:
        yield {"event": "token", "data": "deep response"}
        yield {"event": "todo_update", "data": '{"todos": [{"text": "step1", "done": true}]}'}

    monkeypatch.setattr("app.router.graph.run_deep_path", _fake_run_deep_path)

    events = await _collect_events(run_router("帮我分析这个模块", "t1"))

    # 验证透传事件
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert token_events[0]["data"] == "deep response"

    todo_events = [e for e in events if e["event"] == "todo_update"]
    assert len(todo_events) == 1

    # 验证 done 事件（由 run_router 统一 yield）
    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["data"] == "{}"


async def test_router_deep_path_error_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEEP_TASK 路径：run_deep_path yield error 后 run_router 仍 yield done。"""

    async def _fake_classify(message: str) -> str:
        return "DEEP_TASK"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    async def _fake_run_deep_path(
        state: dict,
        message: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "workspace",
        scene_prompt: str | None = None,
        workspace_path: str | None = None,
    ) -> AsyncIterator[dict]:
        yield {"event": "error", "data": "用户拒绝执行危险操作"}

    monkeypatch.setattr("app.router.graph.run_deep_path", _fake_run_deep_path)

    events = await _collect_events(run_router("帮我分析", "t1"))

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert "拒绝" in error_events[0]["data"]

    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1


# ============================================================
# 5. /reset 触发 checkpoint 清理
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
    mock_checkpointer = MagicMock()
    mock_checkpointer.adelete_thread = AsyncMock()
    monkeypatch.setattr(
        "app.main.get_async_checkpointer",
        AsyncMock(return_value=mock_checkpointer),
    )

    # Mock get_sandbox 返回带 clear 的 mock
    mock_sandbox = MagicMock()
    mock_sandbox.clear = MagicMock()
    monkeypatch.setattr("app.main.get_sandbox", MagicMock(return_value=mock_sandbox))

    # 调用 _event_generator 处理 /reset
    req = ChatRequest(message="/reset", thread_id="t-reset")
    events = await _collect_events(_event_generator(req))

    # 验证 checkpoint 被清理
    mock_checkpointer.adelete_thread.assert_awaited_once_with("t-reset")

    # 验证沙箱被清理（persist_authorized_dirs=False）
    mock_sandbox.clear.assert_called_once_with("t-reset")

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
    mock_checkpointer = MagicMock()
    mock_checkpointer.adelete_thread = AsyncMock()
    monkeypatch.setattr(
        "app.main.get_async_checkpointer",
        AsyncMock(return_value=mock_checkpointer),
    )

    # Mock get_sandbox
    mock_sandbox = MagicMock()
    mock_sandbox.clear = MagicMock()
    monkeypatch.setattr("app.main.get_sandbox", MagicMock(return_value=mock_sandbox))

    req = ChatRequest(message="/reset", thread_id="t-preserve")
    events = await _collect_events(_event_generator(req))

    # checkpoint 仍被清理
    mock_checkpointer.adelete_thread.assert_awaited_once_with("t-preserve")

    # 沙箱未被清理
    mock_sandbox.clear.assert_not_called()

    # token 事件提示授权目录已持久化
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert "持久化" in token_events[0]["data"]


# ============================================================
# 6. workspace_path 字段透传
# ============================================================


async def test_run_router_passes_workspace_path_to_deep_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEEP_TASK 路径：run_router 从请求字段透传 workspace_path 给 run_deep_path，不再解析消息正文 <workspace> 标签。"""

    async def _fake_classify(message: str) -> str:
        return "DEEP_TASK"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    captured: dict = {}

    async def _fake_run_deep_path(
        state: dict,
        message: str,
        profile_prompt: str = "",
        history: list | None = None,
        permission_mode: str = "workspace",
        scene_prompt: str | None = None,
        workspace_path: str | None = None,
    ) -> AsyncIterator[dict]:
        captured["workspace_path"] = workspace_path
        captured["message"] = message
        yield {"event": "token", "data": "ok"}

    monkeypatch.setattr("app.router.graph.run_deep_path", _fake_run_deep_path)

    events = await _collect_events(
        run_router("帮我分析", "t-ws", workspace_path="D:\\proj")
    )

    assert captured.get("workspace_path") == "D:\\proj"
    assert captured.get("message") == "帮我分析"
    assert any(e["event"] == "token" for e in events)


# ============================================================
# 7. resolve_system_prompt 工具函数
# ============================================================


def test_resolve_system_prompt_default_only() -> None:
    """无 scene_prompt 也无 skill_extra → 返回 default。"""
    assert resolve_system_prompt("default", None, None) == "default"


def test_resolve_system_prompt_scene_overrides_default() -> None:
    """scene_prompt 非空 → 覆盖 default。"""
    assert resolve_system_prompt("default", "coding-prompt", None) == "coding-prompt"


def test_resolve_system_prompt_skill_prepended() -> None:
    """skill_extra 始终拼在最前（即使 scene_prompt 也存在）。"""
    result = resolve_system_prompt("default", "coding-prompt", "skill-content")
    assert result == "skill-content\ncoding-prompt"


def test_resolve_system_prompt_skill_only() -> None:
    """只有 skill_extra → 拼到 default 前。"""
    result = resolve_system_prompt("default", None, "skill-content")
    assert result == "skill-content\ndefault"


def test_resolve_system_prompt_empty_scene_falls_back() -> None:
    """scene_prompt 为空字符串（非 None）→ 视为未设置，回退 default。

    防御 pydantic 把 "" 当 falsy 处理的边界情况。
    """
    assert resolve_system_prompt("default", "", None) == "default"
