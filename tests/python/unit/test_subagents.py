"""subagents 模块单元测试：mock LLM + mock 工具，不调真实服务。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.subagents import code_agent as code_agent_mod
from app.subagents import rag_agent as rag_agent_mod
from app.subagents import web_agent as web_agent_mod


@pytest.fixture
def mock_chat_model(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """用 MagicMock 替换三个子代理模块中的 ``get_chat_model``，避免真实 LLM 调用。"""
    fake = MagicMock(name="fake_chat_model")
    for mod in (code_agent_mod, rag_agent_mod, web_agent_mod):
        monkeypatch.setattr(mod, "get_chat_model", fake)
    return fake


# 1. build_code_agent 返回非 None
def test_build_code_agent_returns_agent(mock_chat_model: MagicMock) -> None:
    agent = code_agent_mod.build_code_agent("t1")
    assert agent is not None
    # create_react_agent 返回 CompiledStateGraph，具备 astream_events 方法
    assert hasattr(agent, "astream_events")


# 2. build_rag_agent 返回非 None
def test_build_rag_agent_returns_agent(mock_chat_model: MagicMock) -> None:
    agent = rag_agent_mod.build_rag_agent("t1")
    assert agent is not None
    assert hasattr(agent, "astream_events")


# 3. build_web_agent 返回非 None
def test_build_web_agent_returns_agent(mock_chat_model: MagicMock) -> None:
    agent = web_agent_mod.build_web_agent("t1")
    assert agent is not None
    assert hasattr(agent, "astream_events")


# 4. _make_fs_tools 绑定 thread_id：调用 read_file 工具时内部传入正确的 thread_id
# 安全约束：subagent 工具列表仅含只读工具（read_file/list_dir/glob/grep），
# 危险工具（write_file/edit_file）仅由 DeepAgent 暴露并经 interrupt_before 审批。
async def test_fs_tools_bind_thread_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # mock filesystem.read_file，捕获调用参数
    fake_read = AsyncMock(return_value="file-content")
    monkeypatch.setattr(
        "app.tools.filesystem.read_file", fake_read, raising=True
    )

    tools = code_agent_mod._make_fs_tools("t1")
    # 只读工具集：read_file, list_dir, glob_files, grep_files
    assert len(tools) == 4
    # 验证不包含危险工具
    tool_names = {t.name for t in tools}
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names

    # 找到 read_file 工具（@tool 装饰后名为 read_file）
    read_tool = next(t for t in tools if t.name == "read_file")
    result = await read_tool.ainvoke({"path": "d:/docs/x.txt"})

    assert result == "file-content"
    # 验证闭包正确绑定了 thread_id
    fake_read.assert_awaited_once_with("t1", "d:/docs/x.txt")


# 5. 无 TAVILY_API_KEY 时 web_search 返回错误字符串
async def test_web_search_no_key_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_PY_TAVILY_API_KEY", raising=False)

    tools = web_agent_mod._make_web_tools("t1")
    assert len(tools) == 1
    web_search = tools[0]

    result = await web_search.ainvoke({"query": "hello"})
    assert isinstance(result, str)
    assert "不可用" in result
    assert "AGENT_PY_TAVILY_API_KEY" in result
