"""memory 模块单元测试：checkpointer 单例 + skills 文件解析。

使用 ``tmp_path`` + ``monkeypatch`` 隔离 ``DATA_DIR``，避免污染真实 ``data/`` 目录。
技能加载已由 deepagents ``skills=`` 参数接管，本模块测试 skills_store 的文件解析能力。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.memory.checkpointer as cp_module
import app.memory.skills_store as ss_module
from app.memory.checkpointer import close_checkpointer, get_checkpointer
from app.memory.skills_loader import SkillDef, _parse_frontmatter
from app.memory.skills_store import list_skills


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试将 ``DATA_DIR`` 隔离到临时目录，并重置单例。"""
    monkeypatch.setattr(cp_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "_SKILLS_DIR", tmp_path / "skills")
    # 重置 checkpointer 单例
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None
    yield
    # 测试后关闭可能打开的连接
    close_checkpointer()


# ---- 1. checkpointer 单例 ----
def test_checkpointer_singleton() -> None:
    a = get_checkpointer()
    b = get_checkpointer()
    assert a is b


# ---- 2. checkpointer 创建 db 文件 ----
def test_checkpointer_creates_db_file(tmp_path: Path) -> None:
    db_file = tmp_path / "agentx.db"
    assert not db_file.exists()
    get_checkpointer()
    assert db_file.exists()


# ---- 3. skills 目录不存在时返回空列表 ----
def test_list_skills_empty_dir() -> None:
    assert list_skills() == []


# ---- 4. frontmatter 解析 ----
def test_list_skills_parses_frontmatter(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills" / "search_and_summarize"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\n"
        "name: search_and_summarize\n"
        "description: 搜索网页并总结\n"
        "trigger: 当用户要求搜索时\n"
        "tools: [web_search, rag_retrieve]\n"
        "---\n"
        "# 技能内容\n"
        "搜索并总结。",
        encoding="utf-8",
    )

    skills = list_skills()

    assert len(skills) == 1
    s = skills[0]
    assert isinstance(s, SkillDef)
    assert s.name == "search_and_summarize"
    assert s.description == "搜索网页并总结"
    assert s.trigger == "当用户要求搜索时"
    assert s.tools == ["web_search", "rag_retrieve"]
    assert "# 技能内容" in s.content
    assert "搜索并总结。" in s.content


# ---- 5. 缺省 name 回退到目录名；无 frontmatter 文件按整体内容处理 ----
def test_list_skills_name_fallback_and_plain(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # 无 name 字段，回退到目录名
    fallback_dir = skills_dir / "fallback"
    fallback_dir.mkdir()
    (fallback_dir / "SKILL.md").write_text(
        "---\ndescription: no name here\n---\nbody", encoding="utf-8"
    )

    # 无 frontmatter，按整体内容处理
    plain_dir = skills_dir / "plain"
    plain_dir.mkdir()
    (plain_dir / "SKILL.md").write_text(
        "# 纯 Markdown 无 frontmatter", encoding="utf-8"
    )

    skills = list_skills()

    assert len(skills) == 2
    by_name = {s.name: s for s in skills}
    assert by_name["fallback"].description == "no name here"
    assert by_name["fallback"].tools == []
    assert by_name["plain"].content == "# 纯 Markdown 无 frontmatter"


# ---- 6. _parse_frontmatter 辅助函数 ----
def test_parse_frontmatter_returns_meta_and_body() -> None:
    text = "---\nname: test\ndescription: desc\n---\n# body"
    parsed = _parse_frontmatter(text)
    assert parsed is not None
    meta, body = parsed
    assert meta["name"] == "test"
    assert meta["description"] == "desc"
    assert body == "# body"


def test_parse_frontmatter_invalid_yaml_returns_none() -> None:
    text = "---\nname: [unclosed\n---\nbody"
    assert _parse_frontmatter(text) is None
