"""memory 模块单元测试：checkpointer 单例 + skills 加载/缓存/重载。

使用 ``tmp_path`` + ``monkeypatch`` 隔离 ``DATA_DIR``，避免污染真实 ``data/`` 目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.memory.checkpointer as cp_module
import app.memory.skills_loader as sl_module
from app.memory.checkpointer import close_checkpointer, get_checkpointer
from app.memory.skills_loader import SkillDef, get_skills, load_skills, reload_skills


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试将 ``DATA_DIR`` 隔离到临时目录，并重置单例/缓存。"""
    monkeypatch.setattr(cp_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sl_module, "DATA_DIR", tmp_path)
    # 重置 checkpointer 单例
    cp_module._sync_saver = None
    cp_module._sync_conn = None
    cp_module._async_saver = None
    cp_module._async_conn = None
    # 重置 skills 缓存
    sl_module._skills_cache = None
    yield
    # 测试后关闭可能打开的连接并清理缓存
    close_checkpointer()
    sl_module._skills_cache = None


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
def test_skills_loader_empty_dir() -> None:
    # data/skills 不存在
    assert load_skills() == []


# ---- 4. frontmatter 解析 ----
def test_skills_loader_parses_frontmatter(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "search.md").write_text(
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

    skills = load_skills(skills_dir)

    assert len(skills) == 1
    s = skills[0]
    assert isinstance(s, SkillDef)
    assert s.name == "search_and_summarize"
    assert s.description == "搜索网页并总结"
    assert s.trigger == "当用户要求搜索时"
    assert s.tools == ["web_search", "rag_retrieve"]
    assert "# 技能内容" in s.content
    assert "搜索并总结。" in s.content


# ---- 5. get_skills 缓存：第二次返回同一对象 ----
def test_skills_loader_caches(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "a.md").write_text(
        "---\nname: a\n---\nbody a", encoding="utf-8"
    )

    first = get_skills()
    assert len(first) == 1

    # 第二次应返回缓存（同一列表对象，不重新加载）
    second = get_skills()
    assert second is first


# ---- 6. reload_skills 强制重新加载 ----
def test_skills_reload(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "a.md").write_text(
        "---\nname: a\n---\nbody a", encoding="utf-8"
    )

    first = get_skills()
    assert len(first) == 1

    # 新增技能文件
    (skills_dir / "b.md").write_text(
        "---\nname: b\n---\nbody b", encoding="utf-8"
    )
    # 不 reload：仍返回缓存（只有 1 个）
    assert len(get_skills()) == 1

    # reload 后应包含新技能
    reloaded = reload_skills()
    assert len(reloaded) == 2
    assert {s.name for s in reloaded} == {"a", "b"}


# ---- 额外：缺省 name 回退到文件名；无 frontmatter 文件被跳过 ----
def test_skills_loader_name_fallback_and_skip(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    # 无 name 字段，回退到文件名（去扩展名）
    (skills_dir / "fallback.md").write_text(
        "---\ndescription: no name here\n---\nbody", encoding="utf-8"
    )
    # 无 frontmatter，应被跳过
    (skills_dir / "plain.md").write_text(
        "# 纯 Markdown 无 frontmatter", encoding="utf-8"
    )

    skills = load_skills(skills_dir)

    assert len(skills) == 1
    assert skills[0].name == "fallback"
    assert skills[0].description == "no name here"
    assert skills[0].tools == []
