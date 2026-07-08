"""MockChatModel 单元测试。

覆盖：
1. 无 fixture 时返回 default_response
2. 命中 match 时返回 response
3. 命中含 tool_calls 的 fixture 时 AIMessage.tool_calls 正确
4. from_fixtures 从目录加载多个 YAML 文件
5. _agenerate 与 _generate 返回一致
6. 子串匹配（match="读取" 能匹配 "读取 pyproject.toml"）
7. 未命中任何 fixture 时返回 default_response
8. 自定义 default_response 生效
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.eval.mocks.llm import MockChatModel, MockFixture


def test_default_response_when_no_fixtures() -> None:
    """无 fixture 时返回 default_response，无 tool_calls。"""
    model = MockChatModel()
    result = model.invoke([HumanMessage(content="任意输入")])
    assert result.content == "（mock）暂无预录响应"
    assert result.tool_calls == []


def test_hit_match_returns_response() -> None:
    """命中 match 子串时返回 fixture.response。"""
    model = MockChatModel(
        fixtures=[
            MockFixture(match="你好", response="你好！我是 AgentX 助手。"),
        ]
    )
    result = model.invoke([HumanMessage(content="你好，请介绍自己")])
    assert result.content == "你好！我是 AgentX 助手。"
    assert result.tool_calls == []


def test_hit_fixture_with_tool_calls() -> None:
    """命中含 tool_calls 的 fixture 时，AIMessage.tool_calls 结构正确。"""
    model = MockChatModel(
        fixtures=[
            MockFixture(
                match="读取",
                response="我需要读取文件。",
                tool_calls=[{"name": "read_file", "args": {"path": "pyproject.toml"}}],
            )
        ]
    )
    result = model.invoke([HumanMessage(content="读取 pyproject.toml")])
    assert result.content == "我需要读取文件。"
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["name"] == "read_file"
    assert tc["args"] == {"path": "pyproject.toml"}
    assert tc["id"].startswith("call_")


def test_from_fixtures_loads_multiple_yaml(tmp_path: Path) -> None:
    """from_fixtures 从目录加载多个 YAML 文件并合并。"""
    (tmp_path / "a.yaml").write_text(
        """
- match: "hello"
  response: "Hi there"
  tool_calls: []
""",
        encoding="utf-8",
    )
    (tmp_path / "b.yaml").write_text(
        """
- match: "read"
  response: "reading"
  tool_calls:
    - name: "read_file"
      args: {"path": "a.txt"}
""",
        encoding="utf-8",
    )
    model = MockChatModel.from_fixtures(tmp_path)
    assert len(model.fixtures) == 2
    assert model.fixtures[0].match == "hello"
    assert model.fixtures[1].match == "read"

    r1 = model.invoke([HumanMessage(content="hello world")])
    assert r1.content == "Hi there"
    r2 = model.invoke([HumanMessage(content="please read the file")])
    assert r2.content == "reading"
    assert len(r2.tool_calls) == 1


def test_agenerate_equals_generate() -> None:
    """异步 _agenerate 与同步 _generate 返回一致。"""
    model = MockChatModel(
        fixtures=[
            MockFixture(match="你好", response="你好！", tool_calls=[]),
        ]
    )
    messages = [HumanMessage(content="你好啊")]
    sync_result = model._generate(messages)
    async_result = asyncio.run(model._agenerate(messages))
    assert sync_result.generations[0].message.content == "你好！"
    assert async_result.generations[0].message.content == "你好！"
    assert (
        sync_result.generations[0].message.content
        == async_result.generations[0].message.content
    )


def test_substring_match_partial_user_message() -> None:
    """match="读取" 能匹配 user message="读取 pyproject.toml"（子串匹配）。"""
    model = MockChatModel(
        fixtures=[
            MockFixture(match="读取", response="正在读取", tool_calls=[]),
        ]
    )
    result = model.invoke([HumanMessage(content="读取 pyproject.toml 并告诉我项目名")])
    assert result.content == "正在读取"


def test_no_match_returns_default_response() -> None:
    """未命中任何 fixture 时返回 default_response。"""
    model = MockChatModel(
        fixtures=[
            MockFixture(match="你好", response="你好！", tool_calls=[]),
        ]
    )
    result = model.invoke([HumanMessage(content="完全无关的内容")])
    assert result.content == "（mock）暂无预录响应"


def test_custom_default_response() -> None:
    """自定义 default_response 在未命中时生效。"""
    model = MockChatModel(
        fixtures=[],
        default_response="fallback text",
    )
    result = model.invoke([HumanMessage(content="anything")])
    assert result.content == "fallback text"


def test_match_uses_last_human_message() -> None:
    """匹配使用 messages 中最后一条 HumanMessage。"""
    model = MockChatModel(
        fixtures=[
            MockFixture(match="尾部", response="命中尾部", tool_calls=[]),
        ]
    )
    messages = [
        HumanMessage(content="尾部 前面的消息"),
        SystemMessage(content="system prompt"),
        HumanMessage(content="这是尾部 消息"),
    ]
    result = model.invoke(messages)
    assert result.content == "命中尾部"


def test_bundled_fixtures_loadable() -> None:
    """内置 fixtures 目录可被 from_fixtures 加载，且 smoke/coding/team 都有内容。"""
    fixtures_dir = Path(__file__).resolve().parents[3] / "backend" / "app" / "eval" / "mocks" / "fixtures"
    model = MockChatModel.from_fixtures(fixtures_dir)
    assert len(model.fixtures) >= 6
    matches = {f.match for f in model.fixtures}
    assert "你好" in matches
    assert "读取" in matches
    assert "团队" in matches

    r = model.invoke([HumanMessage(content="你好呀")])
    assert "AgentX" in r.content
