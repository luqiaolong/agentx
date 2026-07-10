"""用户画像存储：工作区级 + 全局级双层隔离。

存储结构（两级）::

    全局画像：data/config/profile.json
    工作区画像：<workspace>/.agentx/profile.json

    {
      "entries": [
        {
          "key": "prefers_concise_reply",
          "category": "preference",  # preference | project | fact | custom
          "content": "用户喜欢简洁回复",
          "source": "manual",        # manual | llm_extracted
          "scope": "global",         # global | workspace（注入时标注来源）
          "created_at": "2026-07-04T10:00:00+00:00",
          "updated_at": "2026-07-04T10:00:00+00:00"
        }
      ]
    }

安全约束：
- ``key`` 正则 ``^[a-zA-Z0-9_-]{1,64}$``（与技能名一致）
- ``content`` 限 500 字符
- 文件损坏时返回空 store，不阻塞启动
- 工作区画像路径限定在 ``<workspace>/.agentx/`` 内，防路径逃逸

注入契约（``build_profile_prompt``）：
- 合并工作区画像 + 全局画像（工作区优先，同 key 覆盖全局）
- 按 ``updated_at`` 降序取前 30 条
- 格式：``用户画像（请遵循以下偏好与约定）:\\n- [category] content\\n...``
- 无画像返回空字符串

并发安全：
- ``_PROFILE_LOCK`` 异步锁保护 load → modify → save 事务，防 lost-update
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config import DATA_DIR
from app.observability.logger import logger

# 全局画像路径（由 DATA_DIR 派生，测试时通过 monkeypatch DATA_DIR 隔离）
_PROFILE_DIR = DATA_DIR / "config"
_PROFILE_FILE = _PROFILE_DIR / "profile.json"

# 工作区画像子目录名（位于 <workspace>/.agentx/ 下）
_WORKSPACE_PROFILE_DIRNAME = ".agentx"
_WORKSPACE_PROFILE_FILENAME = "profile.json"

# key 严格校验正则（与技能文件名一致）
_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# content 最大字符数
_CONTENT_MAX = 500

# system prompt 注入上限（按 updated_at 降序取前 N 条）
_PROFILE_MAX_INJECT = 30

# LLM 单次抽取条目上限（防止恶意 LLM 返回超长列表导致大量写盘）
_MAX_LLM_EXTRACT_ENTRIES = 20

# 合法 category / source 取值
_VALID_CATEGORIES = {"preference", "project", "fact", "custom"}
_VALID_SOURCES = {"manual", "llm_extracted"}

# 全局异步锁：保护 _load → modify → _save 事务（防并发 lost-update）
# 单例锁覆盖全局画像；工作区画像各用独立锁（见 _workspace_locks）
_PROFILE_LOCK: asyncio.Lock = asyncio.Lock()
_workspace_locks: dict[str, asyncio.Lock] = {}


def _get_workspace_lock(workspace_path: str) -> asyncio.Lock:
    """获取工作区级锁（每个 workspace_path 一个独立锁）。"""
    key = str(Path(workspace_path).resolve())
    if key not in _workspace_locks:
        _workspace_locks[key] = asyncio.Lock()
    return _workspace_locks[key]


class ProfileEntry(BaseModel):
    """单条用户画像。"""

    key: str
    category: str  # preference | project | fact | custom
    content: str
    source: str  # manual | llm_extracted
    scope: str = "global"  # global | workspace
    created_at: str  # ISO 格式
    updated_at: str  # ISO 格式


class ProfileStore(BaseModel):
    """画像集合（对应 profile.json 顶层结构）。"""

    entries: list[ProfileEntry] = Field(default_factory=list)


class ProfileKeyInvalid(ValueError):
    """画像 key 非法（正则不匹配或长度越界）。"""


class ProfileContentTooLong(ValueError):
    """画像 content 超 500 字符。"""


class ProfileCategoryInvalid(ValueError):
    """画像 category 取值非法。"""


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(tz=timezone.utc).isoformat()


def _validate_key(key: str) -> str:
    """校验 key 合法。"""
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise ProfileKeyInvalid("key 只能含字母、数字、下划线、连字符，长度 1-64")
    return key


def _validate_content(content: str) -> str:
    """校验 content 长度。"""
    if not isinstance(content, str):
        raise ProfileContentTooLong("content 必须为字符串")
    if len(content) > _CONTENT_MAX:
        raise ProfileContentTooLong(f"content 超过 {_CONTENT_MAX} 字符")
    return content


def _validate_category(category: str) -> str:
    """校验 category 取值。"""
    if category not in _VALID_CATEGORIES:
        raise ProfileCategoryInvalid(
            f"category 必须为 {sorted(_VALID_CATEGORIES)} 之一"
        )
    return category


def _workspace_profile_path(workspace_path: str | None) -> Path | None:
    """返回工作区画像文件路径。

    Args:
        workspace_path: 工作区根目录绝对路径。None 或空 → 返回 None。

    Returns:
        ``<workspace>/.agentx/profile.json`` 路径，或 None。
    """
    if not workspace_path:
        return None
    ws = Path(workspace_path)
    if not ws.is_absolute():
        ws = ws.resolve()
    return ws / _WORKSPACE_PROFILE_DIRNAME / _WORKSPACE_PROFILE_FILENAME


def _load_from_file(file_path: Path, scope: str) -> ProfileStore:
    """从指定路径读 profile.json，损坏时返回空 store。

    Args:
        file_path: 画像文件路径。
        scope: 标注条目来源（global / workspace）。
    """
    if not file_path.exists():
        return ProfileStore()
    try:
        raw = file_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("profile.json 解析失败，返回空 store", file=str(file_path), error=str(exc))
        return ProfileStore()
    if not isinstance(data, dict):
        logger.warning("profile.json 顶层非 object，返回空 store", file=str(file_path))
        return ProfileStore()
    try:
        store = ProfileStore.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — pydantic 校验失败兜底
        logger.warning("profile.json 结构非法，返回空 store", file=str(file_path), error=str(exc))
        return ProfileStore()
    # 标注 scope（文件内可能无此字段）
    for entry in store.entries:
        if not entry.scope:
            entry.scope = scope
    return store


def _load(scope: str = "global") -> ProfileStore:
    """读全局 profile.json，损坏时返回空 store。"""
    return _load_from_file(_PROFILE_FILE, scope)


def _load_workspace(workspace_path: str | None) -> ProfileStore:
    """读工作区 profile.json，无工作区或损坏时返回空 store。"""
    ws_file = _workspace_profile_path(workspace_path)
    if ws_file is None:
        return ProfileStore()
    return _load_from_file(ws_file, "workspace")


def _save(store: ProfileStore, file_path: Path | None = None) -> None:
    """写 profile.json，自动创建目录。

    Args:
        store: 待写入的 store。
        file_path: 目标路径；None → 全局 _PROFILE_FILE。
    """
    target = file_path or _PROFILE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = store.model_dump(mode="json")
    # 原子写：先写临时文件再 rename，避免半写状态
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(target)


def _save_global(store: ProfileStore) -> None:
    """写全局 profile.json。"""
    _save(store, _PROFILE_FILE)


def _save_workspace(store: ProfileStore, workspace_path: str) -> None:
    """写工作区 profile.json。"""
    ws_file = _workspace_profile_path(workspace_path)
    if ws_file is None:
        return
    _save(store, ws_file)


def get_all(
    category: str | None = None,
    workspace_path: str | None = None,
) -> list[ProfileEntry]:
    """返回全部画像条目（合并工作区 + 全局）；可选按 category 过滤。

    Args:
        category: 可选 category 过滤。
        workspace_path: 工作区路径；None → 仅全局。
    """
    global_entries = _load("global").entries
    ws_entries = _load_workspace(workspace_path).entries
    # 合并：工作区优先，同 key 覆盖全局
    merged: dict[str, ProfileEntry] = {e.key: e for e in global_entries}
    for e in ws_entries:
        merged[e.key] = e
    entries = list(merged.values())
    if category is not None and category in _VALID_CATEGORIES:
        return [e for e in entries if e.category == category]
    return entries


def get(key: str, workspace_path: str | None = None) -> ProfileEntry | None:
    """按 key 查找画像条目（工作区优先）；不存在返回 None。"""
    _validate_key(key)
    # 先查工作区
    if workspace_path:
        ws_store = _load_workspace(workspace_path)
        for entry in ws_store.entries:
            if entry.key == key:
                return entry
    # 再查全局
    global_store = _load("global")
    for entry in global_store.entries:
        if entry.key == key:
            return entry
    return None


async def add(
    entry: ProfileEntry,
    workspace_path: str | None = None,
) -> ProfileEntry:
    """新建画像条目。

    Args:
        entry: 待新建的画像（key/category/content/source 已构造）。
        workspace_path: 工作区路径；非空 → 写入工作区画像，None → 写入全局画像。

    Returns:
        写入后的 ProfileEntry（含 created_at/updated_at）。

    Raises:
        ProfileKeyInvalid: key 非法。
        ProfileContentTooLong: content 越界。
        ProfileCategoryInvalid: category 非法。
        ValueError: key 已存在（用 409 上报前端）。
    """
    _validate_key(entry.key)
    _validate_content(entry.content)
    _validate_category(entry.category)
    if entry.source not in _VALID_SOURCES:
        raise ValueError(f"source 必须为 {sorted(_VALID_SOURCES)} 之一")

    scope = "workspace" if workspace_path else "global"
    lock = _get_workspace_lock(workspace_path) if workspace_path else _PROFILE_LOCK

    async with lock:
        if workspace_path:
            store = _load_workspace(workspace_path)
            file_path = _workspace_profile_path(workspace_path)
            save_fn = lambda s: _save_workspace(s, workspace_path)  # noqa: E731
        else:
            store = _load("global")
            file_path = _PROFILE_FILE
            save_fn = _save_global

        if any(e.key == entry.key for e in store.entries):
            raise ValueError(f"key 已存在: {entry.key}，请用 PUT 更新")

        now = _now_iso()
        new_entry = ProfileEntry(
            key=entry.key,
            category=entry.category,
            content=entry.content,
            source=entry.source,
            scope=scope,
            created_at=entry.created_at or now,
            updated_at=now,
        )
        store.entries.append(new_entry)
        save_fn(store)
        logger.info(
            "画像条目已新建",
            key=new_entry.key,
            category=new_entry.category,
            scope=scope,
        )
        return new_entry


async def update(
    key: str,
    content: str,
    category: str | None = None,
    workspace_path: str | None = None,
) -> ProfileEntry:
    """更新画像条目的 content（可选 category）。

    优先更新工作区画像中的条目；若工作区无此 key 则更新全局画像。

    Args:
        key: 待更新的 key。
        content: 新 content。
        category: 可选新 category。
        workspace_path: 工作区路径。

    Returns:
        更新后的 ProfileEntry。

    Raises:
        ProfileKeyInvalid: key 非法。
        ProfileContentTooLong: content 越界。
        ProfileCategoryInvalid: category 非法。
        KeyError: key 不存在（用 404 上报前端）。
    """
    _validate_key(key)
    _validate_content(content)
    if category is not None:
        _validate_category(category)

    # 先尝试工作区
    if workspace_path:
        lock = _get_workspace_lock(workspace_path)
        async with lock:
            store = _load_workspace(workspace_path)
            for idx, entry in enumerate(store.entries):
                if entry.key == key:
                    new_entry = entry.model_copy(update={
                        "content": content,
                        "category": category if category is not None else entry.category,
                        "updated_at": _now_iso(),
                    })
                    store.entries[idx] = new_entry
                    _save_workspace(store, workspace_path)
                    logger.info("画像条目已更新", key=key, scope="workspace")
                    return new_entry

    # 回退全局
    async with _PROFILE_LOCK:
        store = _load("global")
        for idx, entry in enumerate(store.entries):
            if entry.key == key:
                new_entry = entry.model_copy(update={
                    "content": content,
                    "category": category if category is not None else entry.category,
                    "updated_at": _now_iso(),
                })
                store.entries[idx] = new_entry
                _save_global(store)
                logger.info("画像条目已更新", key=key, scope="global")
                return new_entry
    raise KeyError(f"画像 key 不存在: {key}")


async def delete(
    key: str,
    workspace_path: str | None = None,
) -> bool:
    """删除画像条目。

    优先从工作区画像删除；若工作区无此 key 则从全局删除。

    Returns:
        True 表示已删除；False 表示 key 不存在。

    Raises:
        ProfileKeyInvalid: key 非法。
    """
    _validate_key(key)

    # 先尝试工作区
    if workspace_path:
        lock = _get_workspace_lock(workspace_path)
        async with lock:
            store = _load_workspace(workspace_path)
            for idx, entry in enumerate(store.entries):
                if entry.key == key:
                    del store.entries[idx]
                    _save_workspace(store, workspace_path)
                    logger.info("画像条目已删除", key=key, scope="workspace")
                    return True

    # 回退全局
    async with _PROFILE_LOCK:
        store = _load("global")
        for idx, entry in enumerate(store.entries):
            if entry.key == key:
                del store.entries[idx]
                _save_global(store)
                logger.info("画像条目已删除", key=key, scope="global")
                return True
    return False


async def upsert_from_llm(
    entries: list[dict[str, Any]],
    workspace_path: str | None = None,
) -> int:
    """LLM 抽取的条目批量写入。

    - key 已存在 → 更新 content 与 source=``llm_extracted``、刷新 updated_at
    - key 不存在 → 新建条目（source=``llm_extracted``）
    - 非法条目（key/content/category 不合法）跳过并记 warning
    - 单次写入上限 ``_MAX_LLM_EXTRACT_ENTRIES`` 条，超出截断并记 warning

    Args:
        entries: LLM 抽取的 dict 列表，每项含 ``key`` / ``category`` / ``content``。
        workspace_path: 工作区路径；非空 → 写入工作区画像，None → 写入全局画像。

    Returns:
        实际写入的条目数（新建 + 更新）。
    """
    if not isinstance(entries, list):
        return 0
    if len(entries) > _MAX_LLM_EXTRACT_ENTRIES:
        logger.warning(
            "LLM 抽取条目数超限，截断",
            original=len(entries),
            limit=_MAX_LLM_EXTRACT_ENTRIES,
        )
        entries = entries[:_MAX_LLM_EXTRACT_ENTRIES]

    scope = "workspace" if workspace_path else "global"
    lock = _get_workspace_lock(workspace_path) if workspace_path else _PROFILE_LOCK

    async with lock:
        if workspace_path:
            store = _load_workspace(workspace_path)
            save_fn = lambda s: _save_workspace(s, workspace_path)  # noqa: E731
        else:
            store = _load("global")
            save_fn = _save_global

        now = _now_iso()
        written = 0
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            try:
                key = _validate_key(str(raw.get("key", "")))
                content = _validate_content(str(raw.get("content", "")))
                category = _validate_category(str(raw.get("category", "custom")))
            except (ProfileKeyInvalid, ProfileContentTooLong, ProfileCategoryInvalid) as exc:
                logger.warning("LLM 抽取条目非法，跳过", entry=raw, error=str(exc))
                continue

            existing_idx: int | None = None
            for idx, e in enumerate(store.entries):
                if e.key == key:
                    existing_idx = idx
                    break

            if existing_idx is not None:
                old = store.entries[existing_idx]
                store.entries[existing_idx] = old.model_copy(update={
                    "content": content,
                    "category": category,
                    "source": "llm_extracted",
                    "scope": scope,
                    "updated_at": now,
                })
            else:
                store.entries.append(ProfileEntry(
                    key=key,
                    category=category,
                    content=content,
                    source="llm_extracted",
                    scope=scope,
                    created_at=now,
                    updated_at=now,
                ))
            written += 1

        if written > 0:
            save_fn(store)
            logger.info("LLM 抽取画像批量写入完成", written=written, scope=scope)
    return written


def build_profile_prompt(workspace_path: str | None = None) -> str:
    """构造 system prompt 前缀（合并工作区 + 全局画像）。

    - 工作区画像优先：同 key 覆盖全局
    - 无画像返回空字符串
    - 按 ``updated_at`` 降序取前 30 条，格式::

          用户画像（请遵循以下偏好与约定）:
          - [category] content
          - [category] content
          ...
    """
    entries = get_all(workspace_path=workspace_path)
    if not entries:
        return ""
    sorted_entries = sorted(
        entries,
        key=lambda e: e.updated_at,
        reverse=True,
    )[:_PROFILE_MAX_INJECT]
    lines = ["用户画像（请遵循以下偏好与约定）:"]
    for entry in sorted_entries:
        lines.append(f"- [{entry.category}] {entry.content}")
    return "\n".join(lines)


__all__ = [
    "ProfileCategoryInvalid",
    "ProfileContentTooLong",
    "ProfileEntry",
    "ProfileKeyInvalid",
    "ProfileStore",
    "add",
    "build_profile_prompt",
    "delete",
    "get",
    "get_all",
    "update",
    "upsert_from_llm",
]
