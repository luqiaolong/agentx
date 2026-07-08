"""技能系统 API 单元测试：GET /api/skills、POST /api/skills/reload、@skill 注入。

技能文件已迁移到 ``data/skills/<name>/SKILL.md`` 目录结构，技能加载由
deepagents ``skills=`` 参数接管。本测试通过创建临时技能文件验证 API 行为。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.memory.skills_store as ss_module
from app.router.graph import _parse_skill_tag


# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """FastAPI TestClient（不触发 lifespan），隔离 skills 目录。"""
    monkeypatch.setattr(ss_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "_SKILLS_DIR", tmp_path / "skills")
    from app.main import app

    return TestClient(app)


@pytest.fixture
def skills_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """返回临时 skills 目录，并 patch 全局 _SKILLS_DIR。"""
    monkeypatch.setattr(ss_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "_SKILLS_DIR", tmp_path / "skills")
    return tmp_path / "skills"


def _write_skill(
    tmp_path: Path,
    name: str,
    content: str,
    description: str = "",
    trigger: str = "",
    tools: str | None = None,
) -> None:
    """在临时 skills 目录下写入 SKILL.md。"""
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    body = content
    if description or trigger or tools is not None:
        front_parts = [f"name: {name}"]
        if description:
            front_parts.append(f"description: {description}")
        if trigger:
            front_parts.append(f"trigger: {trigger}")
        if tools is not None:
            front_parts.append(f"tools: {tools}")
        body = f"---\n{'\n'.join(front_parts)}\n---\n\n{content}"
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


# ============================================================
# GET /api/skills
# ============================================================


def test_get_skills_returns_structure(client: TestClient, tmp_path: Path) -> None:
    """GET /api/skills 返回 {skills: [...]} 结构，每项含 5 个字段。"""
    _write_skill(
        tmp_path,
        "search_and_summarize",
        "步骤1 搜索\n步骤2 总结",
        description="搜索并总结",
        trigger="当用户要求搜索时",
        tools="[web_search, rag_retrieve]",
    )
    _write_skill(tmp_path, "code_review", "代码审查内容", description="代码审查")

    resp = client.get("/api/skills")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "skills" in body
    assert isinstance(body["skills"], list)
    assert len(body["skills"]) == 2

    # list_skills 按目录名排序：code_review < search_and_summarize
    first = body["skills"][0]
    assert first["name"] == "code_review"
    assert first["description"] == "代码审查"

    second = body["skills"][1]
    assert second["name"] == "search_and_summarize"
    assert second["description"] == "搜索并总结"
    assert second["trigger"] == "当用户要求搜索时"
    assert second["tools"] == ["web_search", "rag_retrieve"]
    assert second["content_preview"] == "步骤1 搜索\n步骤2 总结"


def test_get_skills_empty_list(client: TestClient) -> None:
    """data/skills/ 不存在时返回空列表。"""
    resp = client.get("/api/skills")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"skills": []}


def test_get_skills_content_preview_truncated(client: TestClient, tmp_path: Path) -> None:
    """content_preview 截 body 前 200 字符。"""
    long_content = "x" * 300
    _write_skill(tmp_path, "big", long_content)

    resp = client.get("/api/skills")
    body = resp.json()
    assert len(body["skills"][0]["content_preview"]) == 200
    assert body["skills"][0]["content_preview"] == "x" * 200


# ============================================================
# POST /api/skills/reload
# ============================================================


def test_reload_skills_returns_ok_and_count(client: TestClient, tmp_path: Path) -> None:
    """POST /api/skills/reload 返回当前技能数量。"""
    _write_skill(tmp_path, "a", "A")
    _write_skill(tmp_path, "b", "B")
    _write_skill(tmp_path, "c", "C")

    resp = client.post("/api/skills/reload")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["count"] == 3


# ============================================================
# _parse_skill_tag 单元测试
# ============================================================


def test_parse_skill_tag_no_tag() -> None:
    """无 @skill 标记 → (原消息, None)。"""
    msg, content = _parse_skill_tag("帮我分析这个文件")
    assert msg == "帮我分析这个文件"
    assert content is None


def test_parse_skill_tag_with_valid_skill(skills_dir: Path) -> None:
    """@skill:<existing> → 移除标记 + 返回 skill content。"""
    _write_skill(skills_dir.parent, "search_and_summarize", "搜索并总结的技能内容")

    msg, content = _parse_skill_tag(
        "@skill:search_and_summarize 帮我搜索 LangGraph"
    )
    assert msg == "帮我搜索 LangGraph"
    assert content == "搜索并总结的技能内容"


def test_parse_skill_tag_skill_not_found() -> None:
    """@skill:<nonexistent> → 移除标记（避免 LLM 困惑），不注入 content。"""
    msg, content = _parse_skill_tag("@skill:nonexistent 做某事")
    assert msg == "做某事"
    assert content is None


def test_parse_skill_tag_truncates_long_content(skills_dir: Path) -> None:
    """skill content 超过 4000 字符 → 截断并追加标记。"""
    long_content = "A" * 5000
    _write_skill(skills_dir.parent, "big_skill", long_content)

    msg, content = _parse_skill_tag("@skill:big_skill 做任务")
    assert msg == "做任务"
    assert content is not None
    assert len(content) <= 4000 + len("\n[skill content truncated]")
    assert content.startswith("A" * 4000)
    assert "[skill content truncated]" in content


def test_parse_skill_tag_multiple_tags(skills_dir: Path) -> None:
    """多个 @skill: 标记 → 首个存在技能注入 content，所有标记从消息移除。"""
    _write_skill(skills_dir.parent, "a", "A技能")
    _write_skill(skills_dir.parent, "b", "B技能")

    msg, content = _parse_skill_tag("@skill:a @skill:b 任务")
    assert msg == "任务"
    assert content == "A技能"


def test_parse_skill_tag_multiple_all_not_found() -> None:
    """多个不存在的 @skill: 标记 → 全部移除，不注入 content。"""
    msg, content = _parse_skill_tag("@skill:x @skill:y 做事")
    assert msg == "做事"
    assert content is None


def test_parse_skill_tag_tag_in_middle(skills_dir: Path) -> None:
    """@skill 标记在消息中间 → 正确移除标记，保留其余文本。"""
    _write_skill(skills_dir.parent, "helper", "help content")

    msg, content = _parse_skill_tag("请 @skill:helper 帮我做事")
    assert "helper" not in msg.replace("@skill:helper", "")  # 标记被移除
    assert content == "help content"
