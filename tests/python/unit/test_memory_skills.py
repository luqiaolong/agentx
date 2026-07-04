"""技能文件 CRUD 单元测试：list / get / save / delete + 名称校验 + 路径逃逸 + reload 触发。

用 ``tmp_path`` + ``monkeypatch`` 隔离 ``DATA_DIR``，避免污染真实 ``data/`` 目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.memory.skills_loader as sl_module
import app.memory.skills_store as ss_module
from app.memory.skills_store import (
    SkillFileInfo,
    SkillNameInvalid,
    SkillPathEscape,
    delete_skill_file,
    get_skill_file,
    list_skills_files,
    save_skill_file,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试隔离 ``DATA_DIR`` 与 ``_SKILLS_DIR``，并重置 skills 缓存。"""
    monkeypatch.setattr(sl_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "_SKILLS_DIR", tmp_path / "skills")
    sl_module._skills_cache = None
    yield
    sl_module._skills_cache = None


# ============================================================
# list_skills_files
# ============================================================


def test_list_skills_files_empty_dir(tmp_path: Path) -> None:
    """目录不存在时返回空列表。"""
    assert list_skills_files() == []


def test_list_skills_files_returns_metadata(tmp_path: Path) -> None:
    """列表返回 name/size/mtime/content_preview 四字段。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    content = "---\nname: coder\n---\n# 技能内容\n搜索代码"
    # 用二进制写入，避免平台差异（Windows write_text 转 \r\n）
    target = skills_dir / "coder.md"
    target.write_bytes(content.encode("utf-8"))

    files = list_skills_files()
    assert len(files) == 1
    f = files[0]
    assert isinstance(f, SkillFileInfo)
    assert f.name == "coder"
    assert f.size == len(content.encode("utf-8"))
    assert isinstance(f.mtime, str)
    assert f.content_preview == content


def test_list_skills_files_preview_truncated(tmp_path: Path) -> None:
    """content_preview 截前 200 字符。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    long_content = "x" * 300
    (skills_dir / "big.md").write_text(long_content, encoding="utf-8")

    files = list_skills_files()
    assert len(files[0].content_preview) == 200


# ============================================================
# get_skill_file
# ============================================================


def test_get_skill_file_returns_content(tmp_path: Path) -> None:
    """读取返回完整文件内容。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "search.md").write_text("body content", encoding="utf-8")

    assert get_skill_file("search") == "body content"


def test_get_skill_file_not_found(tmp_path: Path) -> None:
    """文件不存在抛 FileNotFoundError。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        get_skill_file("nonexistent")


def test_get_skill_file_invalid_name() -> None:
    """名称含非法字符抛 SkillNameInvalid。"""
    with pytest.raises(SkillNameInvalid):
        get_skill_file("../etc/passwd")


def test_get_skill_file_name_with_space() -> None:
    """名称含空格抛 SkillNameInvalid。"""
    with pytest.raises(SkillNameInvalid):
        get_skill_file("a b c")


# ============================================================
# save_skill_file
# ============================================================


def test_save_skill_file_creates_new(tmp_path: Path) -> None:
    """新建文件成功，触发 reload_skills。"""
    content = "---\nname: new_skill\n---\n# 新技能\n内容"
    save_skill_file("new_skill", content)

    target = tmp_path / "skills" / "new_skill.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == content

    # 验证 skills 缓存已刷新（reload_skills 被触发）
    from app.memory.skills_loader import get_skills

    skills = get_skills()
    assert any(s.name == "new_skill" for s in skills)


def test_save_skill_file_overwrites_existing(tmp_path: Path) -> None:
    """已存在文件覆盖。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "old.md").write_text("old content", encoding="utf-8")

    save_skill_file("old", "new content")
    assert get_skill_file("old") == "new content"


def test_save_skill_file_invalid_name() -> None:
    """名称非法抛 SkillNameInvalid，不写文件。"""
    with pytest.raises(SkillNameInvalid):
        save_skill_file("../escape", "content")


def test_save_skill_file_name_too_long() -> None:
    """名称超 64 字符抛 SkillNameInvalid。"""
    long_name = "a" * 65
    with pytest.raises(SkillNameInvalid):
        save_skill_file(long_name, "content")


def test_save_skill_file_name_with_slash() -> None:
    """名称含路径分隔符抛 SkillNameInvalid。"""
    with pytest.raises(SkillNameInvalid):
        save_skill_file("a/b", "content")


# ============================================================
# delete_skill_file
# ============================================================


def test_delete_skill_file_success(tmp_path: Path) -> None:
    """删除已存在文件返回 True，触发 reload_skills。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "to_delete.md").write_text("content", encoding="utf-8")

    assert delete_skill_file("to_delete") is True
    assert not (skills_dir / "to_delete.md").exists()


def test_delete_skill_file_not_exists(tmp_path: Path) -> None:
    """删除不存在的文件返回 False（不抛错）。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    assert delete_skill_file("nonexistent") is False


def test_delete_skill_file_invalid_name() -> None:
    """名称非法抛 SkillNameInvalid。"""
    with pytest.raises(SkillNameInvalid):
        delete_skill_file("../etc/passwd")


# ============================================================
# 路径逃逸防御
# ============================================================


def test_save_skill_file_path_escape_via_mocked_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """路径逃逸防御：mock ``_safe_path`` 让 resolve 后路径在 _SKILLS_DIR 之外，
    验证抛 ``SkillPathEscape``。

    正常情况下 ``_validate_name`` 的正则已过滤 ``..`` / ``/``，路径逃逸不可能发生；
    此测试覆盖符号链接场景的防御性逻辑（resolve 后路径不在 _SKILLS_DIR）。
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_target = outside_dir / "escape.md"

    # mock Path.resolve：让目标文件 resolve 到 _SKILLS_DIR 之外
    real_resolve = Path.resolve

    def _fake_resolve(self: Path) -> Path:
        if self.name == "escape.md" and self.parent == skills_dir:
            return outside_target
        return real_resolve(self)

    monkeypatch.setattr(Path, "resolve", _fake_resolve)

    with pytest.raises(SkillPathEscape):
        save_skill_file("escape", "content")


# ============================================================
# reload_skills 触发
# ============================================================


def test_save_triggers_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save_skill_file 必须调用 reload_skills 刷新缓存。"""
    reload_called = False

    def _fake_reload():
        nonlocal reload_called
        reload_called = True
        return []

    monkeypatch.setattr(ss_module, "reload_skills", _fake_reload)

    save_skill_file("trigger_test", "content")
    assert reload_called


def test_delete_triggers_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delete_skill_file 必须调用 reload_skills 刷新缓存。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "to_reload.md").write_text("content", encoding="utf-8")

    reload_called = False

    def _fake_reload():
        nonlocal reload_called
        reload_called = True
        return []

    monkeypatch.setattr(ss_module, "reload_skills", _fake_reload)

    delete_skill_file("to_reload")
    assert reload_called
