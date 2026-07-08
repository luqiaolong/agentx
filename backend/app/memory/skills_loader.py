"""技能定义模型与 frontmatter 解析。

自研技能扫描/缓存逻辑（原 ``load_skills`` / ``get_skills`` / ``reload_skills``）
已由 deepagents ``skills=`` 参数替代。本模块仅保留：

- ``SkillDef``：Pydantic 模型，用于 API 序列化和 ``@skill:<name>`` 标签解析。
- ``_parse_frontmatter``：解析 SKILL.md 的 YAML frontmatter + Markdown body。

技能文件目录结构为 ``data/skills/<name>/SKILL.md``，对齐 agentskills.io 规范。
"""

from __future__ import annotations

import re

import yaml
from pydantic import BaseModel, Field

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
    path: str = ""  # SKILL.md 真实绝对路径（DATA_DIR/skills/<name>/SKILL.md）


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
    return meta, body.strip()


def _tools_from_meta(raw_tools: object) -> list[str]:
    """统一 frontmatter ``tools`` 字段为 ``list[str]``。"""
    if isinstance(raw_tools, str):
        return [t.strip() for t in raw_tools.split(",") if t.strip()]
    return list(raw_tools or [])


__all__ = [
    "SkillDef",
    "_parse_frontmatter",
    "_tools_from_meta",
]
