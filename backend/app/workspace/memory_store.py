"""工作区记忆文件操作：直接读写 ``<workspace>/.agentx/memory/*.md``。

每个记忆条目是一个独立的 ``.md`` 文件，格式为 YAML frontmatter + Markdown content：

    ---
    key: workspace_tech
    category: project
    source: manual
    updated_at: "2026-07-10T16:00:00+00:00"
    ---

    项目使用 FastAPI + React + Tauri 2.x 技术栈...

设计原则：
- 不依赖数据库，纯文件操作，与 DeepAgents ``memory=`` 参数天然兼容
- 前端通过现有 ``/api/memory/profile`` 端点读写，后端透明替换为文件操作
- 全局画像（preference/fact/custom）仍走 ``data/config/profile.json``，不受影响
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.observability.logger import logger

# key 严格校验正则（与技能名一致）
_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# content 最大字符数（工作区记忆比全局画像放宽到 2000）
_CONTENT_MAX = 2000

# 合法 category / source 取值
_VALID_CATEGORIES = {"preference", "project", "fact", "custom"}
_VALID_SOURCES = {"manual", "llm_extracted"}

# 并发安全：每个 workspace_path 一个独立锁
_workspace_locks: dict[str, asyncio.Lock] = {}


def _get_workspace_lock(workspace_path: str) -> asyncio.Lock:
    """获取工作区级锁（每个 workspace_path 一个独立锁）。"""
    key = str(Path(workspace_path).resolve())
    if key not in _workspace_locks:
        _workspace_locks[key] = asyncio.Lock()
    return _workspace_locks[key]


def _memory_dir(workspace_path: str) -> Path:
    """返回 ``<workspace>/.agentx/memory/`` 目录路径。"""
    ws = Path(workspace_path)
    if not ws.is_absolute():
        ws = ws.resolve()
    return ws / ".agentx" / "memory"


def _entry_file_path(workspace_path: str, key: str) -> Path:
    """返回单条记忆的文件路径 ``<workspace>/.agentx/memory/<key>.md``。"""
    return _memory_dir(workspace_path) / f"{key}.md"


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(tz=timezone.utc).isoformat()


def _validate_key(key: str) -> str:
    """校验 key 合法。"""
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise ValueError("key 只能含字母、数字、下划线、连字符，长度 1-64")
    return key


def _validate_category(category: str) -> str:
    """校验 category 取值。"""
    if category not in _VALID_CATEGORIES:
        raise ValueError(f"category 必须为 {sorted(_VALID_CATEGORIES)} 之一")
    return category


def _validate_source(source: str) -> str:
    """校验 source 取值。"""
    if source not in _VALID_SOURCES:
        raise ValueError(f"source 必须为 {sorted(_VALID_SOURCES)} 之一")
    return source


def _validate_content(content: str) -> str:
    """校验 content 长度。"""
    if not isinstance(content, str):
        raise ValueError("content 必须为字符串")
    if len(content) > _CONTENT_MAX:
        raise ValueError(f"content 超过 {_CONTENT_MAX} 字符")
    return content


@dataclass
class MemoryEntry:
    """单条工作区记忆条目。"""

    key: str
    category: str
    content: str
    source: str
    updated_at: str
    created_at: str = ""
    title: str | None = None
    keywords: list[str] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)
    # scope 固定为 "workspace"（memory_store 只管工作区级），与 profile_store 对齐
    scope: str = "workspace"
    # T5.2/T5.3：敏感等级（public 可注入 prompt，private 仅存储不注入）
    sensitivity: str = "public"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category,
            "content": self.content,
            "source": self.source,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "title": self.title,
            "keywords": list(self.keywords),
            "scenarios": list(self.scenarios),
            "scope": self.scope,
            "sensitivity": self.sensitivity,
        }


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 YAML frontmatter + Markdown content。

    Returns:
        (frontmatter_dict, content)
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        import yaml
        frontmatter = yaml.safe_load(parts[1]) or {}
    except Exception:  # noqa: BLE001
        frontmatter = {}
    content = parts[2].strip()
    return frontmatter, content


def _serialize_entry(entry: MemoryEntry) -> str:
    """将 MemoryEntry 序列化为 ``.md`` 文件内容。"""
    frontmatter: dict[str, Any] = {
        "key": entry.key,
        "category": entry.category,
        "source": entry.source,
        "updated_at": entry.updated_at,
    }
    if entry.created_at:
        frontmatter["created_at"] = entry.created_at
    if entry.title is not None:
        frontmatter["title"] = entry.title
    if entry.keywords:
        frontmatter["keywords"] = list(entry.keywords)
    if entry.scenarios:
        frontmatter["scenarios"] = list(entry.scenarios)
    if entry.sensitivity and entry.sensitivity != "public":
        frontmatter["sensitivity"] = entry.sensitivity
    try:
        import yaml
        yaml_block = yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    except Exception:  # noqa: BLE001
        # yaml 不可用时的降级（理论上 PyYAML 已安装）
        yaml_block = json.dumps(frontmatter, ensure_ascii=False, indent=2) + "\n"
    return f"---\n{yaml_block}---\n\n{entry.content}\n"


def _read_entry_file(file_path: Path) -> MemoryEntry | None:
    """读取单个 ``.md`` 文件并解析为 MemoryEntry。"""
    if not file_path.exists():
        return None
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    frontmatter, content = _parse_frontmatter(text)
    key = str(frontmatter.get("key", file_path.stem))
    category = str(frontmatter.get("category", "custom"))
    source = str(frontmatter.get("source", "manual"))
    updated_at = str(frontmatter.get("updated_at", _now_iso()))
    # created_at：旧文件缺失时回退为 updated_at
    created_at = str(frontmatter.get("created_at", updated_at))
    # 新字段：缺失时补默认值（兼容旧 frontmatter）
    title_raw = frontmatter.get("title")
    title = str(title_raw) if title_raw is not None else None
    keywords_raw = frontmatter.get("keywords")
    if isinstance(keywords_raw, list):
        keywords = [str(k) for k in keywords_raw]
    else:
        keywords = []
    scenarios_raw = frontmatter.get("scenarios")
    if isinstance(scenarios_raw, list):
        scenarios = [str(s) for s in scenarios_raw]
    else:
        scenarios = []
    # T5.2/T5.3：读取 sensitivity（旧文件缺失时默认 public）
    sensitivity = str(frontmatter.get("sensitivity", "public"))
    return MemoryEntry(
        key=key,
        category=category,
        content=content,
        source=source,
        updated_at=updated_at,
        created_at=created_at,
        title=title,
        keywords=keywords,
        scenarios=scenarios,
        sensitivity=sensitivity,
    )


def list_entries(
    workspace_path: str,
    category: str | None = None,
) -> list[MemoryEntry]:
    """列出工作区记忆条目。

    Args:
        workspace_path: 工作区根目录绝对路径。
        category: 可选 category 过滤。

    Returns:
        MemoryEntry 列表，按 updated_at 降序。
    """
    mem_dir = _memory_dir(workspace_path)
    if not mem_dir.exists():
        return []
    entries: list[MemoryEntry] = []
    for md_file in sorted(mem_dir.glob("*.md")):
        entry = _read_entry_file(md_file)
        if entry is not None:
            entries.append(entry)
    if category is not None and category in _VALID_CATEGORIES:
        entries = [e for e in entries if e.category == category]
    entries.sort(key=lambda e: e.updated_at, reverse=True)
    return entries


def get_entry(workspace_path: str, key: str) -> MemoryEntry | None:
    """按 key 读取单条工作区记忆。"""
    _validate_key(key)
    file_path = _entry_file_path(workspace_path, key)
    return _read_entry_file(file_path)


async def save_entry(
    workspace_path: str,
    key: str,
    category: str,
    content: str,
    source: str = "manual",
    title: str | None = None,
    keywords: list[str] | None = None,
    scenarios: list[str] | None = None,
    sensitivity: str = "public",
) -> MemoryEntry:
    """写入/覆盖工作区记忆条目。

    Args:
        workspace_path: 工作区根目录绝对路径。
        key: 条目唯一键。
        category: 分类。
        content: Markdown 内容。
        source: 来源。
        title: 可读标题。
        keywords: 关键词标签列表。
        scenarios: 应用场景列表。
        sensitivity: 敏感等级（public / private）。

    Returns:
        写入后的 MemoryEntry。

    Raises:
        ValueError: content 中检测到密钥/凭证（T5.2）。
    """
    _validate_key(key)
    _validate_category(category)
    _validate_source(source)
    _validate_content(content)
    # T5.2：密钥/凭证检测 → 拒绝入库
    from app.memory.profile_store import detect_secret
    secret_type = detect_secret(content)
    if secret_type:
        raise ValueError(
            f"content 中检测到 {secret_type}，拒绝入库以防止密钥泄露"
        )

    lock = _get_workspace_lock(workspace_path)
    async with lock:
        mem_dir = _memory_dir(workspace_path)
        mem_dir.mkdir(parents=True, exist_ok=True)

        # 更新时保留 created_at；新建时 created_at = now
        existing = _read_entry_file(_entry_file_path(workspace_path, key))
        now = _now_iso()
        created_at = existing.created_at if existing is not None and existing.created_at else now

        entry = MemoryEntry(
            key=key,
            category=category,
            content=content,
            source=source,
            updated_at=now,
            created_at=created_at,
            title=title,
            keywords=list(keywords) if keywords is not None else [],
            scenarios=list(scenarios) if scenarios is not None else [],
            sensitivity=sensitivity,
        )
        file_path = _entry_file_path(workspace_path, key)
        text = _serialize_entry(entry)
        # 原子写：先写临时文件再 rename
        tmp = file_path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(file_path)
        logger.info(
            "workspace_memory.saved",
            workspace=workspace_path,
            key=key,
            category=category,
            sensitivity=sensitivity,
        )
        return entry


async def delete_entry(workspace_path: str, key: str) -> bool:
    """删除工作区记忆条目。

    Returns:
        True 表示已删除；False 表示 key 不存在。
    """
    _validate_key(key)

    lock = _get_workspace_lock(workspace_path)
    async with lock:
        file_path = _entry_file_path(workspace_path, key)
        if not file_path.exists():
            return False
        try:
            file_path.unlink()
            logger.info("workspace_memory.deleted", workspace=workspace_path, key=key)
            return True
        except OSError:
            return False


async def upsert_from_llm(
    workspace_path: str,
    entries: list[dict[str, Any]],
) -> int:
    """LLM 抽取的条目批量写入工作区记忆。

    - key 已存在 → 更新 content / category / source=llm_extracted / updated_at
    - key 不存在 → 新建条目
    - 非法条目跳过并记 warning
    - 单次写入上限 20 条

    Args:
        workspace_path: 工作区根目录绝对路径。
        entries: LLM 抽取的 dict 列表，每项含 ``key`` / ``category`` / ``content``。

    Returns:
        实际写入的条目数（新建 + 更新）。
    """
    if not isinstance(entries, list):
        return 0
    _MAX_LLM_EXTRACT_ENTRIES = 20
    if len(entries) > _MAX_LLM_EXTRACT_ENTRIES:
        logger.warning(
            "workspace_memory.llm_extract_truncated",
            original=len(entries),
            limit=_MAX_LLM_EXTRACT_ENTRIES,
        )
        entries = entries[:_MAX_LLM_EXTRACT_ENTRIES]

    lock = _get_workspace_lock(workspace_path)
    async with lock:
        mem_dir = _memory_dir(workspace_path)
        mem_dir.mkdir(parents=True, exist_ok=True)

        now = _now_iso()
        written = 0
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            try:
                key = _validate_key(str(raw.get("key", "")))
                content = _validate_content(str(raw.get("content", "")))
                category = _validate_category(str(raw.get("category", "custom")))
            except ValueError as exc:
                logger.warning("workspace_memory.llm_extract_invalid", entry=raw, error=str(exc))
                continue
            # T5.2：密钥/凭证检测 → 跳过（批量操作不抛异常，仅记日志）
            from app.memory.profile_store import detect_secret
            secret_type = detect_secret(content)
            if secret_type:
                logger.warning(
                    "workspace_memory.llm_extract_secret_detected_skip",
                    key=key,
                    secret_type=secret_type,
                )
                continue

            file_path = _entry_file_path(workspace_path, key)
            existing = _read_entry_file(file_path)
            # 优先保留 existing 字段值，除非 raw 中显式提供新值
            title = raw.get("title") or (existing.title if existing is not None else None)
            keywords = raw.get("keywords") or (existing.keywords if existing is not None else [])
            scenarios = raw.get("scenarios") or (existing.scenarios if existing is not None else [])
            created_at = existing.created_at if existing is not None and existing.created_at else now
            # T5.2/T5.3：读取 sensitivity（LLM 返回的 dict 可能包含此字段）
            sensitivity = str(raw.get("sensitivity", "public"))
            entry = MemoryEntry(
                key=key,
                category=category,
                content=content,
                source="llm_extracted",
                updated_at=now,
                created_at=created_at,
                title=title,
                keywords=list(keywords),
                scenarios=list(scenarios),
                sensitivity=sensitivity,
            )
            text = _serialize_entry(entry)
            tmp = file_path.with_suffix(".md.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(file_path)
            written += 1
            if existing is None:
                logger.info("workspace_memory.llm_created", key=key, category=category)
            else:
                logger.info("workspace_memory.llm_updated", key=key, category=category)

        return written


async def migrate_legacy_profile(workspace_path: str) -> dict[str, Any]:
    """将旧格式 ``<workspace>/.agentx/profile.json`` 迁移到新格式 ``.agentx/memory/*.md``。

    迁移策略：
    1. 检查 ``<workspace>/.agentx/profile.json`` 是否存在
    2. 若存在且 ``<workspace>/.agentx/memory/`` 不存在或为空，执行迁移
    3. 将每条 entry 写为独立的 ``.md`` 文件（YAML frontmatter + content）
    4. 迁移成功后将旧文件重命名为 ``profile.json.bak``（不删除，保留兜底）
    5. 若 memory 目录已有 .md 文件，跳过迁移（避免覆盖用户已编辑的新格式）

    Args:
        workspace_path: 工作区根目录绝对路径。

    Returns:
        ``{"migrated": int, "skipped": bool, "backup": str | None}``
        - migrated: 实际迁移的条目数
        - skipped: 是否因 memory 目录非空而跳过
        - backup: 备份文件路径（迁移成功时），或 None
    """
    ws = Path(workspace_path)
    if not ws.is_absolute():
        ws = ws.resolve()
    agentx_dir = ws / ".agentx"
    legacy_file = agentx_dir / "profile.json"
    memory_dir = agentx_dir / "memory"

    if not legacy_file.exists():
        return {"migrated": 0, "skipped": False, "backup": None}

    # 若 memory 目录已有 .md 文件，跳过迁移（用户已在使用新格式）
    if memory_dir.exists() and any(memory_dir.glob("*.md")):
        return {"migrated": 0, "skipped": True, "backup": None}

    # 读取旧 profile.json
    try:
        raw = legacy_file.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "migrate_legacy_profile.read_failed",
            workspace=workspace_path,
            error=str(exc),
        )
        return {"migrated": 0, "skipped": False, "backup": None}

    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        logger.warning(
            "migrate_legacy_profile.invalid_structure",
            workspace=workspace_path,
        )
        return {"migrated": 0, "skipped": False, "backup": None}

    entries = data["entries"]
    migrated = 0
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        try:
            key = _validate_key(str(raw_entry.get("key", "")))
            content = _validate_content(str(raw_entry.get("content", "")))
            category = _validate_category(str(raw_entry.get("category", "custom")))
        except ValueError as exc:
            logger.warning(
                "migrate_legacy_profile.skip_invalid",
                workspace=workspace_path,
                entry=raw_entry,
                error=str(exc),
            )
            continue
        source = str(raw_entry.get("source", "manual"))
        if source not in _VALID_SOURCES:
            source = "manual"
        title = raw_entry.get("title")
        keywords = raw_entry.get("keywords", [])
        scenarios = raw_entry.get("scenarios", [])
        # 复用 save_entry 写入 .md 文件；单条失败不阻塞其余迁移
        try:
            await save_entry(
                workspace_path,
                key,
                category,
                content,
                source=source,
                title=title if isinstance(title, str) else None,
                keywords=list(keywords) if isinstance(keywords, list) else [],
                scenarios=list(scenarios) if isinstance(scenarios, list) else [],
            )
        except Exception as exc:  # noqa: BLE001 — 单条失败继续迁移其余
            logger.warning(
                "migrate_legacy_profile.save_failed",
                workspace=workspace_path,
                key=key,
                error=str(exc),
            )
            continue
        migrated += 1

    # 迁移成功后重命名旧文件为 .bak（不删除）
    backup_path = None
    if migrated > 0:
        backup_path = str(legacy_file.with_suffix(".json.bak"))
        try:
            legacy_file.rename(backup_path)
        except OSError as exc:
            logger.warning(
                "migrate_legacy_profile.backup_failed",
                workspace=workspace_path,
                error=str(exc),
            )
            backup_path = None
        else:
            logger.info(
                "migrate_legacy_profile.done",
                workspace=workspace_path,
                migrated=migrated,
                backup=backup_path,
            )

    return {"migrated": migrated, "skipped": False, "backup": backup_path}


__all__ = [
    "MemoryEntry",
    "delete_entry",
    "get_entry",
    "list_entries",
    "migrate_legacy_profile",
    "save_entry",
    "upsert_from_llm",
]
