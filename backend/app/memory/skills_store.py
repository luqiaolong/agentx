"""技能文件 CRUD：直接操作 ``data/skills/<name>/SKILL.md``。

提供列表 / 读取 / 写入 / 删除四个原子操作。技能加载由 deepagents ``skills=``
参数自动接管，本模块不再维护自研缓存。

安全约束：
- 技能名严格校验正则 ``^[a-zA-Z0-9_-]+$``（长度 1-64），防目录逃逸与非法字符。
- ``save_skill_file`` / ``delete_skill_file`` 在执行前用 ``Path.resolve()`` 校验最终
  路径仍位于 ``DATA_DIR / "skills"`` 内，防止符号链接逃逸。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from app.config import DATA_DIR
from app.memory.skills_loader import SkillDef, _parse_frontmatter, _tools_from_meta
from app.observability.logger import logger

# 技能文件目录（由 DATA_DIR 派生，测试时通过 monkeypatch DATA_DIR 隔离）
_SKILLS_DIR = DATA_DIR / "skills"

# 技能文件名
_SKILL_FILENAME = "SKILL.md"

# 严格名称正则：仅字母/数字/下划线/连字符，长度 1-64
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# 列表接口的 content 预览长度
_PREVIEW_LEN = 200


class SkillFileInfo(BaseModel):
    """技能文件元信息（列表接口返回，不含完整 content）。"""

    name: str
    size: int
    mtime: str  # ISO 格式时间戳
    content_preview: str  # 前 200 字符


class SkillNameInvalid(ValueError):
    """技能文件名非法（正则不匹配或长度越界）。"""


class SkillPathEscape(ValueError):
    """resolve 后路径逃逸出 ``_SKILLS_DIR``（防符号链接攻击）。"""


def _validate_name(name: str) -> str:
    """校验名称合法，返回安全的技能名（不含路径）。

    Args:
        name: 用户提供的技能名。

    Returns:
        校验通过后的技能名字符串。

    Raises:
        SkillNameInvalid: 名称为空、长度越界或含非法字符。
    """
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise SkillNameInvalid(
            "名称只能含字母、数字、下划线、连字符，长度 1-64"
        )
    return name


def _safe_path(name: str) -> Path:
    """构造 ``_SKILLS_DIR / name / SKILL.md`` 并校验 resolve 后仍在 ``_SKILLS_DIR`` 内。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: resolve 后路径逃逸。
    """
    stem = _validate_name(name)
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    target = (_SKILLS_DIR / stem / _SKILL_FILENAME).resolve()
    # 用 resolve 后的 _SKILLS_DIR 比较父目录，避免符号链接逃逸
    base = _SKILLS_DIR.resolve()
    if base not in target.parents and target != base:
        raise SkillPathEscape("路径逃逸")
    return target


def list_skills_files() -> list[SkillFileInfo]:
    """返回技能文件列表（不含完整 content）。

    遍历 ``_SKILLS_DIR`` 下每个子目录中的 ``SKILL.md``。
    目录不存在时返回空列表（不报错）。
    """
    if not _SKILLS_DIR.exists():
        return []
    files: list[SkillFileInfo] = []
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        md_file = skill_dir / _SKILL_FILENAME
        if not md_file.exists():
            continue
        try:
            stat = md_file.stat()
            text = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取技能文件失败", file=str(md_file), error=str(exc))
            continue
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        files.append(
            SkillFileInfo(
                name=skill_dir.name,
                size=stat.st_size,
                mtime=mtime,
                content_preview=text[:_PREVIEW_LEN],
            )
        )
    return files


def list_skills() -> list[SkillDef]:
    """扫描 ``data/skills/<name>/SKILL.md``，解析 frontmatter 返回技能定义列表。

    目录不存在或解析失败时跳过，不抛错。结果按技能目录名排序。
    """
    if not _SKILLS_DIR.exists():
        return []
    skills: list[SkillDef] = []
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        md_file = skill_dir / _SKILL_FILENAME
        if not md_file.exists():
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取技能文件失败", file=str(md_file), error=str(exc))
            continue
        parsed = _parse_frontmatter(text)
        if parsed is None:
            skills.append(SkillDef(name=skill_dir.name, content=text))
            continue
        meta, body = parsed
        try:
            skills.append(
                SkillDef(
                    name=str(meta.get("name", skill_dir.name)),
                    description=str(meta.get("description", "")),
                    trigger=str(meta.get("trigger", "")),
                    tools=_tools_from_meta(meta.get("tools")),
                    content=body,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("构建 SkillDef 失败", file=str(md_file), error=str(exc))
    return skills


def get_skill_file(name: str) -> str:
    """返回完整文件内容。

    Args:
        name: 技能名。

    Returns:
        文件完整文本。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: 路径逃逸。
        FileNotFoundError: 文件不存在。
    """
    target = _safe_path(name)
    if not target.exists():
        raise FileNotFoundError(f"技能文件不存在: {name}")
    return target.read_text(encoding="utf-8")


def save_skill_file(name: str, content: str) -> None:
    """写入 ``data/skills/{name}/SKILL.md``。

    若文件已存在则覆盖；不存在则新建目录和文件。deepagents ``skills=`` 参数
    会在下次构建 agent 时自动重新扫描，无需手动刷新缓存。

    Args:
        name: 技能名。
        content: 文件完整内容（YAML frontmatter + Markdown body）。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: 路径逃逸。
    """
    target = _safe_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    logger.info("技能文件已保存", name=name, path=str(target))


def delete_skill_file(name: str) -> bool:
    """删除 ``data/skills/{name}/SKILL.md``。

    Args:
        name: 技能名。

    Returns:
        True 表示已删除；False 表示文件不存在（无操作）。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: 路径逃逸。
    """
    target = _safe_path(name)
    if not target.exists():
        return False
    target.unlink()
    logger.info("技能文件已删除", name=name, path=str(target))
    return True


__all__ = [
    "SkillFileInfo",
    "SkillNameInvalid",
    "SkillPathEscape",
    "delete_skill_file",
    "get_skill_file",
    "list_skills",
    "list_skills_files",
    "save_skill_file",
]
