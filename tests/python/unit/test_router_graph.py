"""Router 图单元测试：mock LLM + mock subagents，不调真实服务。

覆盖：
1. build_router_graph 返回编译后的图实例
2. CHAT 路径：mock classify_message → "CHAT" + mock LLM，验证 yield token 事件
3. SINGLE_TOOL 路径：mock classify_message → "SINGLE_TOOL" + mock run_code_agent，验证透传事件
4. DEEP_TASK 路径：mock classify_message → "DEEP_TASK" + mock run_deep_path，验证透传事件
5. /reset 消息触发 checkpoint 清理 + 沙箱清理
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.router.graph import build_router_graph, run_router


# ============================================================
# 1. build_router_graph 返回编译后的图
# ============================================================


def test_build_router_graph_returns_compiled() -> None:
    """build_router_graph() 返回非 None，且具备 astream 方法。"""
    graph = build_router_graph()
    assert graph is not None
    # CompiledStateGraph 具备 astream / ainvoke 方法
    assert hasattr(graph, "astream")
    assert hasattr(graph, "ainvoke")


def test_build_router_graph_with_checkpointer() -> None:
    """build_router_graph(checkpointer=...) 接受 checkpointer 参数并编译成功。

    LangGraph 1.2.7 的 ``ensure_valid_checkpointer`` 会校验 checkpointer 必须是
    ``BaseCheckpointSaver`` 实例，故用真实 ``MemorySaver`` 而非 MagicMock。
    """
    from langgraph.checkpoint.memory import MemorySaver

    fake_cp = MemorySaver()
    graph = build_router_graph(checkpointer=fake_cp)
    assert graph is not None


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
    """构造 mock LLM，astream 返回含指定 token 的 chunk 流。"""

    async def _fake_astream(messages: Any) -> AsyncIterator:
        for text in tokens:
            yield SimpleNamespace(content=text)

    mock_llm = MagicMock()
    mock_llm.astream = _fake_astream
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
    monkeypatch.setattr("app.llm.get_chat_model", lambda **kw: fake_llm)

    events = await _collect_events(run_router("你好", "t1"))

    # 验证有 token 事件
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 4
    assert token_events[0]["data"] == "你好"
    assert token_events[1]["data"] == "！"

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

    monkeypatch.setattr("app.llm.get_chat_model", _fake_get_chat_model)

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
    """SINGLE_TOOL 路径：mock run_code_agent，验证透传事件 + 转为 SSE 格式。"""

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    # mock run_code_agent yield 标准化事件
    async def _fake_run_code_agent(thread_id: str, message: str) -> AsyncIterator[dict]:
        yield {"type": "token", "content": "文件内容"}
        yield {"type": "tool_call", "name": "read_file", "args": {"path": "/tmp/a.txt"}}
        yield {"type": "tool_result", "name": "read_file", "result": "content"}

    monkeypatch.setattr("app.router.graph.run_code_agent", _fake_run_code_agent)

    events = await _collect_events(run_router("读文件 /tmp/a.txt", "t1"))

    # 验证 token 事件透传
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 1
    assert token_events[0]["data"] == "文件内容"

    # 验证 tool_call → todo_update 转换
    todo_events = [e for e in events if e["event"] == "todo_update"]
    assert len(todo_events) == 2  # tool_call + tool_result

    # 验证 done 事件
    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1


async def test_router_tool_path_selects_web_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SINGLE_TOOL 路径：含 web 关键词时选择 web_agent。"""

    async def _fake_classify(message: str) -> str:
        return "SINGLE_TOOL"

    monkeypatch.setattr("app.router.graph.classify_message", _fake_classify)

    web_called = False

    async def _fake_run_web_agent(thread_id: str, message: str) -> AsyncIterator[dict]:
        nonlocal web_called
        web_called = True
        yield {"type": "token", "content": "web result"}

    async def _fake_run_code_agent(thread_id: str, message: str) -> AsyncIterator[dict]:
        yield {"type": "token", "content": "code result"}

    monkeypatch.setattr("app.router.graph.run_web_agent", _fake_run_web_agent)
    monkeypatch.setattr("app.router.graph.run_code_agent", _fake_run_code_agent)

    events = await _collect_events(run_router("搜索网页信息", "t1"))

    assert web_called
    token_events = [e for e in events if e["event"] == "token"]
    assert token_events[0]["data"] == "web result"


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
    async def _fake_run_deep_path(state: dict, message: str) -> AsyncIterator[dict]:
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

    async def _fake_run_deep_path(state: dict, message: str) -> AsyncIterator[dict]:
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
    monkeypatch.setenv("AGENT_PY_PERSIST_AUTHORIZED_DIRS", "false")
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
    monkeypatch.setenv("AGENT_PY_PERSIST_AUTHORIZED_DIRS", "true")
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
