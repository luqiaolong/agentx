"""长期用户画像：``data/config/profile.json``。

存储结构::

    {
      "entries": [
        {
          "key": "prefers_concise_reply",
          "category": "preference",  # preference | project | fact | custom
          "content": "用户喜欢简洁回复",
          "source": "manual",        # manual | llm_extracted
          "created_at": "2026-07-04T10:00:00+00:00",
          "updated_at": "2026-07-04T10:00:00+00:00"
        }
      ]
    }

安全约束：
- ``key`` 正则 ``^[a-zA-Z0-9_-]{1,64}$``（与技能名一致）
- ``content`` 限 500 字符
- 文件损坏时返回空 store，不阻塞启动

注入契约（``build_profile_prompt``）：
- 按 ``updated_at`` 降序取前 30 条
- 格式：``用户画像（请遵循以下偏好与约定）:\\n- [category] content\\n...``
- 无画像返回空字符串
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.config import DATA_DIR
from app.observability.logger import logger

# 画像文件路径（由 DATA_DIR 派生，测试时通过 monkeypatch DATA_DIR 隔离）
_PROFILE_DIR = DATA_DIR / "config"
_PROFILE_FILE = _PROFILE_DIR / "profile.json"

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


class ProfileEntry(BaseModel):
    """单条用户画像。"""

    key: str
    category: str  # preference | project | fact | custom
    content: str
    source: str  # manual | llm_extracted
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


def _load() -> ProfileStore:
    """读 ``profile.json``，损坏时返回空 store。

    - 文件不存在 → 空 store
    - JSON 解析失败 → 记 warning，返回空 store
    - 结构不匹配 → 记 warning，返回空 store
    """
    if not _PROFILE_FILE.exists():
        return ProfileStore()
    try:
        raw = _PROFILE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("profile.json 解析失败，返回空 store", error=str(exc))
        return ProfileStore()
    if not isinstance(data, dict):
        logger.warning("profile.json 顶层非 object，返回空 store")
        return ProfileStore()
    try:
        return ProfileStore.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — pydantic 校验失败兜底
        logger.warning("profile.json 结构非法，返回空 store", error=str(exc))
        return ProfileStore()


def _save(store: ProfileStore) -> None:
    """写 ``profile.json``，自动创建目录。"""
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    payload = store.model_dump(mode="json")
    # 原子写：先写临时文件再 rename，避免半写状态
    tmp = _PROFILE_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(_PROFILE_FILE)


def get_all() -> list[ProfileEntry]:
    """返回全部画像条目。"""
    return _load().entries


def get(key: str) -> ProfileEntry | None:
    """按 key 查找画像条目；不存在返回 None。"""
    store = _load()
    for entry in store.entries:
        if entry.key == key:
            return entry
    return None


def add(entry: ProfileEntry) -> ProfileEntry:
    """新建画像条目。

    Args:
        entry: 待新建的画像（key/category/content/source 已构造）。

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
        # 防御性：source 必须合法
        raise ValueError(f"source 必须为 {sorted(_VALID_SOURCES)} 之一")

    store = _load()
    if any(e.key == entry.key for e in store.entries):
        raise ValueError(f"key 已存在: {entry.key}，请用 PUT 更新")

    now = _now_iso()
    new_entry = ProfileEntry(
        key=entry.key,
        category=entry.category,
        content=entry.content,
        source=entry.source,
        created_at=entry.created_at or now,
        updated_at=now,
    )
    store.entries.append(new_entry)
    _save(store)
    logger.info("画像条目已新建", key=new_entry.key, category=new_entry.category)
    return new_entry


def update(key: str, content: str, category: str | None = None) -> ProfileEntry:
    """更新画像条目的 content（可选 category）。

    Args:
        key: 待更新的 key。
        content: 新 content。
        category: 可选新 category。

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

    store = _load()
    for idx, entry in enumerate(store.entries):
        if entry.key == key:
            new_entry = entry.model_copy(update={
                "content": content,
                "category": category if category is not None else entry.category,
                "updated_at": _now_iso(),
            })
            store.entries[idx] = new_entry
            _save(store)
            logger.info("画像条目已更新", key=key)
            return new_entry
    raise KeyError(f"画像 key 不存在: {key}")


def delete(key: str) -> bool:
    """删除画像条目。

    Returns:
        True 表示已删除；False 表示 key 不存在。

    Raises:
        ProfileKeyInvalid: key 非法。
    """
    _validate_key(key)
    store = _load()
    for idx, entry in enumerate(store.entries):
        if entry.key == key:
            del store.entries[idx]
            _save(store)
            logger.info("画像条目已删除", key=key)
            return True
    return False


def upsert_from_llm(entries: list[dict[str, Any]]) -> int:
    """LLM 抽取的条目批量写入。

    - key 已存在 → 更新 content 与 source=``llm_extracted``、刷新 updated_at
    - key 不存在 → 新建条目（source=``llm_extracted``）
    - 非法条目（key/content/category 不合法）跳过并记 warning
    - 单次写入上限 ``_MAX_LLM_EXTRACT_ENTRIES`` 条，超出截断并记 warning

    Args:
        entries: LLM 抽取的 dict 列表，每项含 ``key`` / ``category`` / ``content``。

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
    store = _load()
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

        # 查找是否已存在
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
                "updated_at": now,
            })
        else:
            store.entries.append(ProfileEntry(
                key=key,
                category=category,
                content=content,
                source="llm_extracted",
                created_at=now,
                updated_at=now,
            ))
        written += 1

    if written > 0:
        _save(store)
        logger.info("LLM 抽取画像批量写入完成", written=written)
    return written


def build_profile_prompt() -> str:
    """构造 system prompt 前缀。

    - 无画像返回空字符串
    - 有画像按 ``updated_at`` 降序取前 30 条，格式::

          用户画像（请遵循以下偏好与约定）:
          - [category] content
          - [category] content
          ...
    """
    store = _load()
    if not store.entries:
        return ""
    # 按 updated_at 降序（最近更新优先），取前 N 条
    sorted_entries = sorted(
        store.entries,
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
