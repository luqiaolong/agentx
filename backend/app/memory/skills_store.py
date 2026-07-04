"""技能文件 CRUD：直接操作 ``data/skills/*.md``。

提供列表 / 读取 / 写入 / 删除四个原子操作，写入或删除后自动触发
``skills_loader.reload_skills`` 刷新缓存，确保 ``@skill:<name>`` 标记解析立即可见。

安全约束：
- 文件名严格校验正则 ``^[a-zA-Z0-9_-]+$``（长度 1-64），防目录逃逸与非法字符。
- ``save_skill_file`` / ``delete_skill_file`` 在执行前用 ``Path.resolve()`` 校验最终
  路径仍位于 ``DATA_DIR / "skills"`` 内，防止符号链接逃逸。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from app.config import DATA_DIR
from app.memory.skills_loader import reload_skills
from app.observability.logger import logger

# 技能文件目录（由 DATA_DIR 派生，测试时通过 monkeypatch DATA_DIR 隔离）
_SKILLS_DIR = DATA_DIR / "skills"

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
    """校验名称合法，返回安全的 stem（不含扩展名）。

    Args:
        name: 用户提供的技能名（不含 ``.md`` 扩展名）。

    Returns:
        校验通过后的 stem 字符串。

    Raises:
        SkillNameInvalid: 名称为空、长度越界或含非法字符。
    """
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise SkillNameInvalid(
            "名称只能含字母、数字、下划线、连字符，长度 1-64"
        )
    return name


def _safe_path(name: str) -> Path:
    """构造 ``_SKILLS_DIR / f"{name}.md"`` 并校验 resolve 后仍在 ``_SKILLS_DIR`` 内。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: resolve 后路径逃逸。
    """
    stem = _validate_name(name)
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    target = (_SKILLS_DIR / f"{stem}.md").resolve()
    # 用 resolve 后的 _SKILLS_DIR 比较父目录，避免符号链接逃逸
    base = _SKILLS_DIR.resolve()
    if base not in target.parents and target != base:
        raise SkillPathEscape("路径逃逸")
    if target.suffix != ".md":
        # 二次防御：resolve 后扩展名被改写（理论上不会发生，因 stem 已正则校验）
        raise SkillNameInvalid("扩展名非法")
    return target


def list_skills_files() -> list[SkillFileInfo]:
    """返回技能文件列表（不含完整 content）。

    目录不存在时返回空列表（不报错）。
    """
    if not _SKILLS_DIR.exists():
        return []
    files: list[SkillFileInfo] = []
    for md_file in sorted(_SKILLS_DIR.glob("*.md")):
        try:
            stat = md_file.stat()
            text = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取技能文件失败", file=str(md_file), error=str(exc))
            continue
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        files.append(
            SkillFileInfo(
                name=md_file.stem,
                size=stat.st_size,
                mtime=mtime,
                content_preview=text[:_PREVIEW_LEN],
            )
        )
    return files


def get_skill_file(name: str) -> str:
    """返回完整文件内容。

    Args:
        name: 技能名（不含 ``.md`` 扩展名）。

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
    """写入 ``data/skills/{name}.md``，触发 ``reload_skills()``。

    若文件已存在则覆盖；不存在则新建。写入后立即刷新技能缓存。

    Args:
        name: 技能名（不含 ``.md`` 扩展名）。
        content: 文件完整内容（YAML frontmatter + Markdown body）。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: 路径逃逸。
    """
    target = _safe_path(name)
    target.write_text(content, encoding="utf-8")
    logger.info("技能文件已保存", name=name, path=str(target))
    reload_skills()


def delete_skill_file(name: str) -> bool:
    """删除 ``data/skills/{name}.md``，触发 ``reload_skills()``。

    Args:
        name: 技能名（不含 ``.md`` 扩展名）。

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
    reload_skills()
    return True


__all__ = [
    "SkillFileInfo",
    "SkillNameInvalid",
    "SkillPathEscape",
    "delete_skill_file",
    "get_skill_file",
    "list_skills_files",
    "save_skill_file",
]
