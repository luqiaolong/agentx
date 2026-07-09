"""subagents 模块单元测试：mock LLM + mock 工具，不调真实服务。

场景化架构下 code 子代理已被 coding Expert 取代，本测试仅覆盖 rag/web 子代理。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.subagents import rag_agent as rag_agent_mod
from app.subagents import web_agent as web_agent_mod


@pytest.fixture
def mock_create_agent(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock ``get_chat_model`` + ``create_agent``，避免真实 LLM/deepagents 调用。

    - ``get_chat_model``：mock 为返回 MagicMock，避免 API key 检查失败
    - ``create_agent``：mock 为返回带 ``astream_events`` 的 fake agent，
      避免 ``create_deep_agent`` 处理 MagicMock model spec 时报错

    注：``build_rag_agent`` / ``build_web_agent`` 实现已收敛到
    ``app.subagents.base.build_builtin_subagent``，故 ``get_chat_model``
    在 ``base`` 模块命名空间中被引用，需 mock ``app.subagents.base`` 而非
    rag_agent / web_agent 模块。
    """
    fake_model = MagicMock(name="fake_chat_model")
    from app.subagents import base as base_mod

    monkeypatch.setattr(base_mod, "get_chat_model", lambda **kw: fake_model)

    fake_agent = MagicMock(name="fake_compiled_graph")
    fake_agent.astream_events = MagicMock()
    fake_create = MagicMock(return_value=fake_agent, name="fake_create_agent")
    monkeypatch.setattr("app.deepagent.factory.create_agent", fake_create)
    return fake_create


# 1. build_rag_agent 返回非 None
def test_build_rag_agent_returns_agent(mock_create_agent: MagicMock) -> None:
    agent = rag_agent_mod.build_rag_agent("t1")
    assert agent is not None
    assert hasattr(agent, "astream_events")


# 2. build_web_agent 返回非 None
def test_build_web_agent_returns_agent(mock_create_agent: MagicMock) -> None:
    agent = web_agent_mod.build_web_agent("t1")
    assert agent is not None
    assert hasattr(agent, "astream_events")


# 3. _make_fs_tools 绑定 thread_id：调用 read_file 工具时内部传入正确的 thread_id
# 安全约束：subagent 工具列表仅含只读工具（read_file/list_dir/glob/grep），
# 危险工具（write_file/edit_file）仅由 DeepAgent/Expert 暴露并经 interrupt_on 审批。
async def test_fs_tools_bind_thread_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # mock filesystem.read_file，捕获调用参数
    fake_read = AsyncMock(return_value="file-content")
    monkeypatch.setattr(
        "app.tools.filesystem.read_file", fake_read, raising=True
    )

    # _make_fs_tools 在 base.py 有同名别名，供 rag/web 子代理使用
    from app.subagents.base import _make_fs_tools

    tools = _make_fs_tools("t1")
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
    # 验证闭包正确绑定了 thread_id（base 为 workspace 上下文，未传时为 None）
    fake_read.assert_awaited_once_with("t1", "d:/docs/x.txt", base=None)


# 4. 无 TAVILY_API_KEY 时 web_search 返回错误字符串
async def test_web_search_no_key_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTX_TAVILY_API_KEY", raising=False)

    tools = web_agent_mod._make_web_tools("t1")
    assert len(tools) == 1
    web_search = tools[0]

    result = await web_search.ainvoke({"query": "hello"})
    assert isinstance(result, str)
    assert "不可用" in result
    assert "AGENTX_TAVILY_API_KEY" in result
