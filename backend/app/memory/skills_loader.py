"""技能加载器：从 ``data/skills/`` 目录加载 Markdown 技能定义。

技能文件格式（YAML frontmatter + Markdown body）::

    ---
    name: search_and_summarize
    description: 搜索网页并总结
    trigger: 当用户要求搜索时
    tools: [web_search, rag_retrieve]
    ---
    # 技能内容
    ...

frontmatter 用 ``yaml`` 库解析（pyyaml 已安装）；body 作为 ``content`` 字段。
若 ``data/skills/`` 目录不存在，返回空列表（不报错）。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.config import DATA_DIR
from app.observability.logger import logger

# frontmatter 分隔正则：文件以 --- 开头，到下一个 --- 行结束，剩余为 body
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class SkillDef(BaseModel):
    """技能定义：frontmatter 元数据 + Markdown 正文。"""

    name: str
    description: str = ""
    trigger: str = ""
    tools: list[str] = Field(default_factory=list)
    content: str = ""


def _parse_frontmatter(text: str) -> tuple[dict, str] | None:
    """解析 YAML frontmatter，返回 ``(meta_dict, body_str)``；无 frontmatter 返回 None。"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    frontmatter_text, body = match.group(1), match.group(2)
    try:
        meta = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        logger.warning("frontmatter YAML 解析失败", error=str(exc))
        return None
    if not isinstance(meta, dict):
        return None
    return meta, body


def load_skills(skills_dir: Path | None = None) -> list[SkillDef]:
    """扫描 ``skills_dir`` 下所有 ``*.md``，解析 frontmatter 返回技能列表。

    - ``skills_dir`` 缺省为 ``DATA_DIR / "skills"``
    - 目录不存在时返回空列表（不报错）
    - 解析失败的文件跳过并告警，不影响其他文件
    """
    if skills_dir is None:
        skills_dir = DATA_DIR / "skills"

    if not skills_dir.exists():
        return []

    skills: list[SkillDef] = []
    for md_file in sorted(skills_dir.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取技能文件失败", file=str(md_file), error=str(exc))
            continue

        parsed = _parse_frontmatter(text)
        if parsed is None:
            logger.warning("技能文件缺少合法 frontmatter", file=str(md_file))
            continue
        meta, body = parsed

        # tools 允许 YAML 列表或逗号分隔字符串，统一归一为 list[str]
        raw_tools = meta.get("tools", [])
        if isinstance(raw_tools, str):
            tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
        else:
            tools = list(raw_tools or [])

        try:
            skill = SkillDef(
                name=str(meta.get("name", md_file.stem)),
                description=str(meta.get("description", "")),
                trigger=str(meta.get("trigger", "")),
                tools=tools,
                content=body.strip(),
            )
            skills.append(skill)
        except Exception as exc:  # noqa: BLE001
            logger.warning("构建 SkillDef 失败", file=str(md_file), error=str(exc))

    logger.info("技能加载完成", count=len(skills), skills_dir=str(skills_dir))
    return skills


# ---- 缓存层 ----
_skills_cache: list[SkillDef] | None = None


def get_skills() -> list[SkillDef]:
    """返回缓存的技能列表（首次调用触发加载，后续返回缓存）。"""
    global _skills_cache
    if _skills_cache is None:
        _skills_cache = load_skills()
    return _skills_cache


def reload_skills() -> list[SkillDef]:
    """强制重新加载技能列表并刷新缓存。"""
    global _skills_cache
    _skills_cache = load_skills()
    return _skills_cache


__all__ = [
    "SkillDef",
    "get_skills",
    "load_skills",
    "reload_skills",
]
