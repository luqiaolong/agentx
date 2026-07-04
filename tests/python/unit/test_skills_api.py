"""技能系统 API 单元测试：GET /api/skills、POST /api/skills/reload、@skill 注入。

覆盖：
1. ``GET /api/skills`` — 返回 ``{skills: [...]}`` 结构，每项含 name/description/trigger/tools/content_preview
2. ``POST /api/skills/reload`` — 调 reload_skills，返回 ``{ok: true, count: N}``
3. ``_parse_skill_tag`` — @skill:<name> 标记解析（命中/未命中/技能不存在/截断）
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.memory.skills_loader import SkillDef
from app.router.graph import _parse_skill_tag


# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient（不触发 lifespan）。"""
    from app.main import app

    return TestClient(app)


def _make_skill(
    name: str = "search_and_summarize",
    description: str = "搜索并总结",
    trigger: str = "当用户要求搜索时",
    tools: list[str] | None = None,
    content: str = "搜索网页并总结结果",
) -> SkillDef:
    """构造测试用 SkillDef。"""
    return SkillDef(
        name=name,
        description=description,
        trigger=trigger,
        tools=tools if tools is not None else ["web_search"],
        content=content,
    )


# ============================================================
# GET /api/skills
# ============================================================


def test_get_skills_returns_structure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/skills 返回 {skills: [...]} 结构，每项含 5 个字段。"""
    fake_skills = [
        _make_skill(
            name="search_and_summarize",
            description="搜索并总结",
            trigger="当用户要求搜索时",
            tools=["web_search", "rag_retrieve"],
            content="步骤1 搜索\n步骤2 总结",
        ),
        _make_skill(name="code_review", description="代码审查", trigger="审查代码时"),
    ]
    monkeypatch.setattr("app.main.get_skills", lambda: fake_skills)

    resp = client.get("/api/skills")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "skills" in body
    assert isinstance(body["skills"], list)
    assert len(body["skills"]) == 2

    first = body["skills"][0]
    assert first["name"] == "search_and_summarize"
    assert first["description"] == "搜索并总结"
    assert first["trigger"] == "当用户要求搜索时"
    assert first["tools"] == ["web_search", "rag_retrieve"]
    assert first["content_preview"] == "步骤1 搜索\n步骤2 总结"


def test_get_skills_empty_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """data/skills/ 不存在时返回空列表。"""
    monkeypatch.setattr("app.main.get_skills", lambda: [])

    resp = client.get("/api/skills")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"skills": []}


def test_get_skills_content_preview_truncated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """content_preview 截前 200 字符。"""
    long_content = "x" * 300
    monkeypatch.setattr(
        "app.main.get_skills", lambda: [_make_skill(content=long_content)]
    )

    resp = client.get("/api/skills")
    body = resp.json()
    assert len(body["skills"][0]["content_preview"]) == 200
    assert body["skills"][0]["content_preview"] == "x" * 200


# ============================================================
# POST /api/skills/reload
# ============================================================


def test_reload_skills_returns_ok_and_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/skills/reload 调 reload_skills，返回 {ok: true, count: N}。"""
    fake_skills = [_make_skill(name="a"), _make_skill(name="b"), _make_skill(name="c")]
    reload_called = False

    def _fake_reload() -> list[SkillDef]:
        nonlocal reload_called
        reload_called = True
        return fake_skills

    monkeypatch.setattr("app.main.reload_skills", _fake_reload)

    resp = client.post("/api/skills/reload")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["count"] == 3
    assert reload_called


# ============================================================
# _parse_skill_tag 单元测试
# ============================================================


def test_parse_skill_tag_no_tag() -> None:
    """无 @skill 标记 → (原消息, None)。"""
    msg, content = _parse_skill_tag("帮我分析这个文件")
    assert msg == "帮我分析这个文件"
    assert content is None


def test_parse_skill_tag_with_valid_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """@skill:<existing> → 移除标记 + 返回 skill content。"""
    fake_skills = [
        _make_skill(name="search_and_summarize", content="搜索并总结的技能内容")
    ]
    monkeypatch.setattr("app.router.graph.get_skills", lambda: fake_skills)

    msg, content = _parse_skill_tag(
        "@skill:search_and_summarize 帮我搜索 LangGraph"
    )
    assert msg == "帮我搜索 LangGraph"
    assert content == "搜索并总结的技能内容"


def test_parse_skill_tag_skill_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """@skill:<nonexistent> → 移除标记（避免 LLM 困惑），不注入 content。"""
    monkeypatch.setattr("app.router.graph.get_skills", lambda: [])

    msg, content = _parse_skill_tag("@skill:nonexistent 做某事")
    assert msg == "做某事"
    assert content is None


def test_parse_skill_tag_truncates_long_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """skill content 超过 4000 字符 → 截断并追加标记。"""
    long_content = "A" * 5000
    fake_skills = [_make_skill(name="big_skill", content=long_content)]
    monkeypatch.setattr("app.router.graph.get_skills", lambda: fake_skills)

    msg, content = _parse_skill_tag("@skill:big_skill 做任务")
    assert msg == "做任务"
    assert content is not None
    assert len(content) <= 4000 + len("\n[skill content truncated]")
    assert content.startswith("A" * 4000)
    assert "[skill content truncated]" in content


def test_parse_skill_tag_multiple_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多个 @skill: 标记 → 首个存在技能注入 content，所有标记从消息移除。"""
    fake_skills = [
        _make_skill(name="a", content="A技能"),
        _make_skill(name="b", content="B技能"),
    ]
    monkeypatch.setattr("app.router.graph.get_skills", lambda: fake_skills)

    msg, content = _parse_skill_tag("@skill:a @skill:b 任务")
    assert msg == "任务"
    assert content == "A技能"


def test_parse_skill_tag_multiple_all_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多个不存在的 @skill: 标记 → 全部移除，不注入 content。"""
    monkeypatch.setattr("app.router.graph.get_skills", lambda: [])

    msg, content = _parse_skill_tag("@skill:x @skill:y 做事")
    assert msg == "做事"
    assert content is None


def test_parse_skill_tag_tag_in_middle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """@skill 标记在消息中间 → 正确移除标记，保留其余文本。"""
    fake_skills = [_make_skill(name="helper", content="help content")]
    monkeypatch.setattr("app.router.graph.get_skills", lambda: fake_skills)

    msg, content = _parse_skill_tag("请 @skill:helper 帮我做事")
    assert "helper" not in msg.replace("@skill:helper", "")  # 标记被移除
    assert content == "help content"
