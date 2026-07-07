"""GET /api/agents/mentionable 端点测试。

验证：
1. 默认配置返回 coding expert + rag/web 子代理
2. 禁用的 agent 不出现在列表中
3. 自定义子代理出现在列表中
4. Expert 排在子代理前面
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch):
    """每个用例前后清理 settings lru_cache，避免环境变量污染。"""
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_mentionable_returns_default_agents(client: TestClient) -> None:
    """默认配置返回 coding expert + rag/web 子代理。"""
    resp = client.get("/api/agents/mentionable")

    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    items = data["items"]

    # 应包含 coding（expert）、rag、web（subagent）
    keys = {item["key"] for item in items}
    assert "coding" in keys
    assert "rag" in keys
    assert "web" in keys

    # coding 应为 expert 类型
    coding_item = next(item for item in items if item["key"] == "coding")
    assert coding_item["type"] == "expert"

    # rag/web 应为 subagent 类型
    rag_item = next(item for item in items if item["key"] == "rag")
    assert rag_item["type"] == "subagent"
    web_item = next(item for item in items if item["key"] == "web")
    assert web_item["type"] == "subagent"


def test_mentionable_expert_before_subagent(client: TestClient) -> None:
    """Expert 排在子代理前面。"""
    resp = client.get("/api/agents/mentionable")
    items = resp.json()["items"]

    # 找到第一个 subagent 的位置
    first_subagent_idx = next(
        (i for i, item in enumerate(items) if item["type"] == "subagent"),
        None,
    )
    assert first_subagent_idx is not None, "应有至少一个子代理"

    # 第一个 subagent 之前的所有条目都应是 expert
    for item in items[:first_subagent_idx]:
        assert item["type"] == "expert", f"Expert 应排在子代理前面，但发现: {item}"


def test_mentionable_excludes_disabled_agents(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """禁用的 agent 不出现在列表中。"""
    # 禁用 rag 子代理和 coding expert
    monkeypatch.setenv(
        "AGENTX_SUBAGENTS_CONFIG",
        json.dumps({"rag": {"enabled": False}}),
    )
    monkeypatch.setenv(
        "AGENTX_AGENTS_CONFIG",
        json.dumps({"experts": {"coding": {"enabled": False}}}),
    )
    from app.config import get_settings

    get_settings.cache_clear()

    resp = client.get("/api/agents/mentionable")
    items = resp.json()["items"]
    keys = {item["key"] for item in items}

    # coding 和 rag 应被排除
    assert "coding" not in keys, "禁用的 coding expert 不应出现在列表中"
    assert "rag" not in keys, "禁用的 rag 子代理不应出现在列表中"
    # web 仍在
    assert "web" in keys


def test_mentionable_includes_custom_subagents(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自定义子代理出现在列表中。"""
    monkeypatch.setenv(
        "AGENTX_CUSTOM_SUBAGENTS_CONFIG",
        json.dumps(
            {
                "my_coder": {
                    "key": "my_coder",
                    "name": "我的代码助手",
                    "enabled": True,
                    "tools": ["read_file"],
                    "trigger_description": "用户问题涉及自定义代码任务时触发。",
                }
            }
        ),
    )
    from app.config import get_settings

    get_settings.cache_clear()

    resp = client.get("/api/agents/mentionable")
    items = resp.json()["items"]

    custom_item = next(
        (item for item in items if item["key"] == "my_coder"), None
    )
    assert custom_item is not None, "自定义子代理应出现在列表中"
    assert custom_item["type"] == "subagent"
    assert custom_item["display_name"] == "我的代码助手"
    assert "自定义代码任务" in custom_item["trigger_description"]


def test_mentionable_excludes_disabled_custom_subagents(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """禁用的自定义子代理不出现。"""
    monkeypatch.setenv(
        "AGENTX_CUSTOM_SUBAGENTS_CONFIG",
        json.dumps(
            {
                "disabled_agent": {
                    "key": "disabled_agent",
                    "name": "已禁用",
                    "enabled": False,
                    "tools": ["read_file"],
                }
            }
        ),
    )
    from app.config import get_settings

    get_settings.cache_clear()

    resp = client.get("/api/agents/mentionable")
    items = resp.json()["items"]
    keys = {item["key"] for item in items}

    assert "disabled_agent" not in keys


def test_mentionable_item_fields(client: TestClient) -> None:
    """每个条目包含 key / type / display_name / trigger_description 四个字段。"""
    resp = client.get("/api/agents/mentionable")
    items = resp.json()["items"]

    assert len(items) > 0
    for item in items:
        assert "key" in item
        assert "type" in item
        assert "display_name" in item
        assert "trigger_description" in item
        assert item["type"] in ("expert", "subagent")
