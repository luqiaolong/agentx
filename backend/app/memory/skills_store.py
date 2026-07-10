"""技能文件 CRUD：工作区级 + 全局级双层隔离。

存储结构（两级）::

    全局技能：data/skills/<name>/SKILL.md
    工作区技能：<workspace>/.agentx/skills/<name>/SKILL.md

读取（``list_skills_files`` / ``list_skills`` / ``get_skill_file``）合并工作区 +
全局；同 name 时工作区优先。写入 / 删除通过 ``scope`` 参数指定目标层级。

安全约束：
- 技能名严格校验正则 ``^[a-zA-Z0-9_-]+$``（长度 1-64），防目录逃逸与非法字符。
- ``save_skill_file`` / ``delete_skill_file`` 在执行前用 ``Path.resolve()`` 校验最终
  路径仍位于目标 skills 根目录内，防止符号链接逃逸。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from app.config import DATA_DIR
from app.memory.skills_loader import SkillDef, _parse_frontmatter, _tools_from_meta
from app.observability.logger import logger

# 全局技能文件目录（由 DATA_DIR 派生，测试时通过 monkeypatch DATA_DIR 隔离）
_SKILLS_DIR = DATA_DIR / "skills"

# 工作区技能子目录名（位于 <workspace>/.agentx/ 下）
_WORKSPACE_SKILLS_DIRNAME = "skills"
_WORKSPACE_AGENTX_DIRNAME = ".agentx"

# 技能文件名
_SKILL_FILENAME = "SKILL.md"

# 严格名称正则：仅字母/数字/下划线/连字符，长度 1-64
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# 列表接口的 content 预览长度
_PREVIEW_LEN = 200


class SkillFileInfo(BaseModel):
    """技能文件元信息（列表接口返回，不含完整 content）。

    ``scope`` 字段标注来源：``workspace`` 或 ``global``。
    """

    name: str
    size: int
    mtime: str  # ISO 格式时间戳
    content_preview: str  # 前 200 字符
    path: str = ""  # 真实文件绝对路径
    scope: str = "global"  # global | workspace


class SkillNameInvalid(ValueError):
    """技能文件名非法（正则不匹配或长度越界）。"""


class SkillPathEscape(ValueError):
    """resolve 后路径逃逸出目标 skills 根目录（防符号链接攻击）。"""


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


def _safe_path(name: str, base_dir: Path) -> Path:
    """构造 ``base_dir / name / SKILL.md`` 并校验 resolve 后仍在 ``base_dir`` 内。

    Args:
        name: 技能名。
        base_dir: 技能根目录（全局或工作区级）。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: resolve 后路径逃逸。
    """
    stem = _validate_name(name)
    base_dir.mkdir(parents=True, exist_ok=True)
    target = (base_dir / stem / _SKILL_FILENAME).resolve()
    # 用 resolve 后的 base_dir 比较父目录，避免符号链接逃逸
    base = base_dir.resolve()
    if base not in target.parents and target != base:
        raise SkillPathEscape("路径逃逸")
    return target


def _workspace_skills_dir(workspace_path: str | None) -> Path | None:
    """返回工作区技能目录 ``<workspace>/.agentx/skills/``。"""
    if not workspace_path:
        return None
    ws = Path(workspace_path)
    if not ws.is_absolute():
        ws = ws.resolve()
    return ws / _WORKSPACE_AGENTX_DIRNAME / _WORKSPACE_SKILLS_DIRNAME


def _scan_skills_dir(base_dir: Path, scope: str) -> list[SkillFileInfo]:
    """扫描单个技能目录，返回 SkillFileInfo 列表。"""
    if not base_dir.exists():
        return []
    files: list[SkillFileInfo] = []
    for skill_dir in sorted(base_dir.iterdir()):
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
                path=str(md_file),
                scope=scope,
            )
        )
    return files


def list_skills_files(workspace_path: str | None = None) -> list[SkillFileInfo]:
    """返回技能文件列表（合并工作区 + 全局，不含完整 content）。

    工作区技能优先：同 name 时工作区覆盖全局。

    Args:
        workspace_path: 工作区路径；None → 仅全局。
    """
    global_files = _scan_skills_dir(_SKILLS_DIR, "global")
    if workspace_path:
        ws_dir = _workspace_skills_dir(workspace_path)
        if ws_dir is not None:
            ws_files = _scan_skills_dir(ws_dir, "workspace")
            # 合并：工作区优先
            ws_names = {f.name for f in ws_files}
            merged = ws_files + [f for f in global_files if f.name not in ws_names]
            return merged
    return global_files


def list_skills(workspace_path: str | None = None) -> list[SkillDef]:
    """扫描技能目录（合并工作区 + 全局），解析 frontmatter 返回技能定义列表。

    工作区技能优先：同 name 时工作区覆盖全局。
    目录不存在或解析失败时跳过，不抛错。结果按技能目录名排序。

    Args:
        workspace_path: 工作区路径；None → 仅全局。
    """
    # 收集 name → (path, scope) 映射，工作区优先
    name_to_path: dict[str, Path] = {}
    for skill_dir in sorted(_SKILLS_DIR.iterdir()) if _SKILLS_DIR.exists() else []:
        if skill_dir.is_dir():
            md_file = skill_dir / _SKILL_FILENAME
            if md_file.exists():
                name_to_path[skill_dir.name] = md_file

    if workspace_path:
        ws_dir = _workspace_skills_dir(workspace_path)
        if ws_dir is not None and ws_dir.exists():
            for skill_dir in sorted(ws_dir.iterdir()):
                if skill_dir.is_dir():
                    md_file = skill_dir / _SKILL_FILENAME
                    if md_file.exists():
                        name_to_path[skill_dir.name] = md_file  # 工作区覆盖全局

    skills: list[SkillDef] = []
    for name in sorted(name_to_path.keys()):
        md_file = name_to_path[name]
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取技能文件失败", file=str(md_file), error=str(exc))
            continue
        parsed = _parse_frontmatter(text)
        md_abs_path = str(md_file)
        if parsed is None:
            skills.append(SkillDef(name=name, content=text, path=md_abs_path))
            continue
        meta, body = parsed
        try:
            skills.append(
                SkillDef(
                    name=str(meta.get("name", name)),
                    description=str(meta.get("description", "")),
                    trigger=str(meta.get("trigger", "")),
                    tools=_tools_from_meta(meta.get("tools")),
                    content=body,
                    path=md_abs_path,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("构建 SkillDef 失败", file=str(md_file), error=str(exc))
    return skills


def get_skill_file(name: str, workspace_path: str | None = None) -> str:
    """返回完整文件内容（工作区优先查找）。

    Args:
        name: 技能名。
        workspace_path: 工作区路径；None → 仅全局。

    Returns:
        文件完整文本。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: 路径逃逸。
        FileNotFoundError: 文件不存在。
    """
    # 先查工作区
    if workspace_path:
        ws_dir = _workspace_skills_dir(workspace_path)
        if ws_dir is not None:
            try:
                target = _safe_path(name, ws_dir)
                if target.exists():
                    return target.read_text(encoding="utf-8")
            except (SkillNameInvalid, SkillPathEscape):
                raise  # 名称/路径校验错误直接抛出

    # 回退全局
    target = _safe_path(name, _SKILLS_DIR)
    if not target.exists():
        raise FileNotFoundError(f"技能文件不存在: {name}")
    return target.read_text(encoding="utf-8")


def save_skill_file(
    name: str,
    content: str,
    workspace_path: str | None = None,
) -> None:
    """写入技能文件。

    Args:
        name: 技能名。
        content: 文件完整内容（YAML frontmatter + Markdown body）。
        workspace_path: 工作区路径；非空 → 写入工作区级，None → 写入全局级。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: 路径逃逸。
    """
    if workspace_path:
        ws_dir = _workspace_skills_dir(workspace_path)
        if ws_dir is not None:
            target = _safe_path(name, ws_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            logger.info("技能文件已保存", name=name, path=str(target), scope="workspace")
            return

    target = _safe_path(name, _SKILLS_DIR)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    logger.info("技能文件已保存", name=name, path=str(target), scope="global")


def delete_skill_file(
    name: str,
    workspace_path: str | None = None,
) -> bool:
    """删除技能文件（优先工作区，回退全局）。

    Args:
        name: 技能名。
        workspace_path: 工作区路径。

    Returns:
        True 表示已删除；False 表示文件不存在（无操作）。

    Raises:
        SkillNameInvalid: 名称非法。
        SkillPathEscape: 路径逃逸。
    """
    # 先尝试工作区
    if workspace_path:
        ws_dir = _workspace_skills_dir(workspace_path)
        if ws_dir is not None:
            try:
                target = _safe_path(name, ws_dir)
                if target.exists():
                    target.unlink()
                    logger.info("技能文件已删除", name=name, path=str(target), scope="workspace")
                    return True
            except (SkillNameInvalid, SkillPathEscape):
                raise

    # 回退全局
    target = _safe_path(name, _SKILLS_DIR)
    if not target.exists():
        return False
    target.unlink()
    logger.info("技能文件已删除", name=name, path=str(target), scope="global")
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
