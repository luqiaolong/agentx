"""技能文件 CRUD 单元测试：list / get / save / delete + 名称校验 + 路径逃逸 + 工作区隔离。

用 ``tmp_path`` + ``monkeypatch`` 隔离 ``DATA_DIR``，避免污染真实 ``data/`` 目录。
技能加载已由 deepagents ``skills=`` 参数接管，本模块不再测试缓存刷新。
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
    """每个测试隔离 ``DATA_DIR`` 与 ``_SKILLS_DIR``。"""
    monkeypatch.setattr(ss_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ss_module, "_SKILLS_DIR", tmp_path / "skills")
    yield


# ============================================================
# list_skills_files
# ============================================================


def test_list_skills_files_empty_dir(tmp_path: Path) -> None:
    """目录不存在时返回空列表。"""
    assert list_skills_files() == []


def test_list_skills_files_returns_metadata(tmp_path: Path) -> None:
    """列表返回 name/size/mtime/content_preview 四字段。"""
    skills_dir = tmp_path / "skills" / "coder"
    skills_dir.mkdir(parents=True)
    content = "---\nname: coder\n---\n# 技能内容\n搜索代码"
    target = skills_dir / "SKILL.md"
    target.write_bytes(content.encode("utf-8"))

    files = list_skills_files()
    assert len(files) == 1
    f = files[0]
    assert isinstance(f, SkillFileInfo)
    assert f.name == "coder"
    assert f.size == len(content.encode("utf-8"))
    assert isinstance(f.mtime, str)
    assert f.content_preview == content
    assert f.scope == "global"


def test_list_skills_files_preview_truncated(tmp_path: Path) -> None:
    """content_preview 截前 200 字符。"""
    skills_dir = tmp_path / "skills" / "big"
    skills_dir.mkdir(parents=True)
    long_content = "x" * 300
    (skills_dir / "SKILL.md").write_text(long_content, encoding="utf-8")

    files = list_skills_files()
    assert len(files[0].content_preview) == 200


# ============================================================
# get_skill_file
# ============================================================


def test_get_skill_file_returns_content(tmp_path: Path) -> None:
    """读取返回完整文件内容。"""
    skills_dir = tmp_path / "skills" / "search"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("body content", encoding="utf-8")

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
    """新建文件成功。"""
    content = "---\nname: new_skill\n---\n# 新技能\n内容"
    save_skill_file("new_skill", content)

    target = tmp_path / "skills" / "new_skill" / "SKILL.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == content


def test_save_skill_file_overwrites_existing(tmp_path: Path) -> None:
    """已存在文件覆盖。"""
    skills_dir = tmp_path / "skills" / "old"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("old content", encoding="utf-8")

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
    """删除已存在文件返回 True。"""
    skills_dir = tmp_path / "skills" / "to_delete"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("content", encoding="utf-8")

    assert delete_skill_file("to_delete") is True
    assert not (skills_dir / "SKILL.md").exists()


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
    outside_target = outside_dir / "SKILL.md"

    # mock Path.resolve：让目标文件 resolve 到 _SKILLS_DIR 之外
    real_resolve = Path.resolve

    def _fake_resolve(self: Path) -> Path:
        if self.name == "SKILL.md" and self.parent == skills_dir / "escape":
            return outside_target
        return real_resolve(self)

    monkeypatch.setattr(Path, "resolve", _fake_resolve)

    with pytest.raises(SkillPathEscape):
        save_skill_file("escape", "content")


# ============================================================
# 工作区级技能隔离
# ============================================================


def test_save_skill_file_to_workspace(tmp_path: Path) -> None:
    """workspace_path 非空时写入工作区级技能。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()
    content = "---\nname: ws_skill\n---\n# 工作区技能"
    save_skill_file("ws_skill", content, workspace_path=str(ws_path))

    # 工作区技能文件存在
    ws_target = ws_path / ".agentx" / "skills" / "ws_skill" / "SKILL.md"
    assert ws_target.exists()
    assert ws_target.read_text(encoding="utf-8") == content

    # 全局技能文件不存在
    assert not (tmp_path / "skills" / "ws_skill" / "SKILL.md").exists()


def test_list_skills_files_merges_workspace_and_global(tmp_path: Path) -> None:
    """list_skills_files 合并工作区 + 全局，同 name 工作区覆盖全局。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()

    # 全局技能
    save_skill_file("shared", "global content")
    save_skill_file("global_only", "only global")

    # 工作区技能（覆盖 shared）
    save_skill_file("shared", "workspace content", workspace_path=str(ws_path))
    save_skill_file("ws_only", "only workspace", workspace_path=str(ws_path))

    # 不传 workspace_path → 仅全局
    global_files = {f.name: f for f in list_skills_files()}
    assert "shared" in global_files
    assert global_files["shared"].content_preview == "global content"
    assert global_files["shared"].scope == "global"
    assert "global_only" in global_files
    assert "ws_only" not in global_files

    # 传 workspace_path → 合并
    merged_files = {f.name: f for f in list_skills_files(workspace_path=str(ws_path))}
    assert merged_files["shared"].content_preview == "workspace content"
    assert merged_files["shared"].scope == "workspace"
    assert merged_files["global_only"].scope == "global"
    assert merged_files["ws_only"].scope == "workspace"


def test_get_skill_file_workspace_priority(tmp_path: Path) -> None:
    """get_skill_file 工作区优先查找。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()

    # 全局和工作区都有 shared
    save_skill_file("shared", "global content")
    save_skill_file("shared", "workspace content", workspace_path=str(ws_path))

    # 不传 workspace_path → 全局
    assert get_skill_file("shared") == "global content"

    # 传 workspace_path → 工作区优先
    assert get_skill_file("shared", workspace_path=str(ws_path)) == "workspace content"


def test_get_skill_file_fallback_to_global(tmp_path: Path) -> None:
    """get_skill_file 工作区无此技能时回退全局。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()

    save_skill_file("g_skill", "global content")

    # 工作区无此技能，回退全局
    assert get_skill_file("g_skill", workspace_path=str(ws_path)) == "global content"


def test_delete_skill_file_workspace_priority(tmp_path: Path) -> None:
    """delete_skill_file 优先删除工作区技能。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()

    # 全局和工作区都有 shared
    save_skill_file("shared", "global content")
    save_skill_file("shared", "workspace content", workspace_path=str(ws_path))

    # 删除工作区技能
    assert delete_skill_file("shared", workspace_path=str(ws_path)) is True

    # 工作区技能已删
    ws_target = ws_path / ".agentx" / "skills" / "shared" / "SKILL.md"
    assert not ws_target.exists()

    # 全局技能仍在
    assert get_skill_file("shared") == "global content"


def test_delete_skill_file_fallback_to_global(tmp_path: Path) -> None:
    """delete_skill_file 工作区无此技能时回退全局。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()

    save_skill_file("g_skill", "global content")

    # 工作区无此技能，回退全局
    assert delete_skill_file("g_skill", workspace_path=str(ws_path)) is True
    with pytest.raises(FileNotFoundError):
        get_skill_file("g_skill")
