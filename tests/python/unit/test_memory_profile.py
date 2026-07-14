"""用户画像 CRUD 单元测试：add / get / update / delete / upsert_from_llm + 注入 prompt。

用 ``tmp_path`` + ``monkeypatch`` 隔离 ``DATA_DIR``，避免污染真实 ``data/config/profile.json``。
LLM 抽取用 mock，不调真实服务。

注意：``add`` / ``update`` / ``delete`` / ``upsert_from_llm`` 为 async 函数（持锁防并发）。
``get`` / ``get_all`` / ``build_profile_prompt`` 为同步函数（只读）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.memory.profile_store as ps_module
from app.memory.profile_store import (
    ProfileCategoryInvalid,
    ProfileContentTooLong,
    ProfileEntry,
    ProfileKeyInvalid,
    add,
    build_profile_prompt,
    delete,
    get,
    get_all,
    update,
    upsert_from_llm,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试隔离 ``DATA_DIR`` 与 ``_PROFILE_FILE``。"""
    monkeypatch.setattr(ps_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ps_module, "_PROFILE_DIR", tmp_path / "config")
    monkeypatch.setattr(ps_module, "_PROFILE_FILE", tmp_path / "config" / "profile.json")
    # 隔离 workspace.memory_store 的锁缓存，避免测试间交叉污染
    import app.workspace.memory_store as ms_module
    monkeypatch.setattr(ms_module, "_workspace_locks", {})
    yield


def _make_entry(
    key: str = "prefers_concise",
    category: str = "preference",
    content: str = "用户喜欢简洁回复",
    source: str = "manual",
) -> ProfileEntry:
    """构造测试用 ProfileEntry。"""
    return ProfileEntry(
        key=key,
        category=category,
        content=content,
        source=source,
        created_at="",
        updated_at="",
    )


# ============================================================
# add / get / get_all
# ============================================================


async def test_add_creates_entry(tmp_path: Path) -> None:
    """add 写入 profile.json 并返回带时间戳的 entry。"""
    entry = await add(_make_entry())
    assert entry.key == "prefers_concise"
    assert entry.created_at  # 自动填充
    assert entry.updated_at
    assert entry.source == "manual"
    assert entry.scope == "global"

    # 文件已写入
    assert (tmp_path / "config" / "profile.json").exists()

    # get_all 能读到
    all_entries = get_all()
    assert len(all_entries) == 1
    assert all_entries[0].key == "prefers_concise"


async def test_add_duplicate_key_raises_value_error(tmp_path: Path) -> None:
    """key 重复抛 ValueError（端点层映射为 409）。"""
    await add(_make_entry(key="dup"))
    with pytest.raises(ValueError, match="key 已存在"):
        await add(_make_entry(key="dup"))


async def test_add_invalid_key() -> None:
    """key 非法抛 ProfileKeyInvalid。"""
    with pytest.raises(ProfileKeyInvalid):
        await add(_make_entry(key="../etc"))


async def test_add_content_too_long() -> None:
    """content 超 500 字符抛 ProfileContentTooLong。"""
    long_content = "x" * 501
    with pytest.raises(ProfileContentTooLong):
        await add(_make_entry(content=long_content))


async def test_add_invalid_category() -> None:
    """category 非法抛 ProfileCategoryInvalid。"""
    with pytest.raises(ProfileCategoryInvalid):
        await add(_make_entry(category="invalid_cat"))


async def test_get_returns_entry(tmp_path: Path) -> None:
    """get 按 key 查找。"""
    await add(_make_entry(key="find_me", content="hello"))
    entry = get("find_me")
    assert entry is not None
    assert entry.content == "hello"


async def test_get_returns_none_when_not_exists(tmp_path: Path) -> None:
    """get 不存在返回 None。"""
    assert get("nonexistent") is None


# ============================================================
# update
# ============================================================


async def test_update_modifies_content(tmp_path: Path) -> None:
    """update 修改 content 与 updated_at，source 保持原值。"""
    original = await add(_make_entry(key="up", content="old", source="manual"))
    updated = await update("up", "new content")
    assert updated.content == "new content"
    assert updated.source == "manual"  # source 保持
    assert updated.updated_at >= original.updated_at


async def test_update_category(tmp_path: Path) -> None:
    """update 同时修改 category。"""
    await add(_make_entry(key="cat", category="preference"))
    updated = await update("cat", "new content", category="fact")
    assert updated.category == "fact"


async def test_update_nonexistent_raises_key_error(tmp_path: Path) -> None:
    """update 不存在抛 KeyError（端点层映射为 404）。"""
    with pytest.raises(KeyError):
        await update("nonexistent", "content")


async def test_update_content_too_long(tmp_path: Path) -> None:
    """update content 越界抛 ProfileContentTooLong。"""
    await add(_make_entry(key="long"))
    with pytest.raises(ProfileContentTooLong):
        await update("long", "x" * 501)


# ============================================================
# delete
# ============================================================


async def test_delete_removes_entry(tmp_path: Path) -> None:
    """delete 移除条目。"""
    await add(_make_entry(key="del"))
    assert await delete("del") is True
    assert get("del") is None


async def test_delete_nonexistent_returns_false(tmp_path: Path) -> None:
    """delete 不存在返回 False。"""
    assert await delete("nonexistent") is False


async def test_delete_invalid_key() -> None:
    """delete 非法 key 抛 ProfileKeyInvalid。"""
    with pytest.raises(ProfileKeyInvalid):
        await delete("../etc")


# ============================================================
# 文件不存在 / 损坏兜底
# ============================================================


def test_get_all_empty_when_file_not_exists(tmp_path: Path) -> None:
    """文件不存在时返回空列表。"""
    assert get_all() == []


def test_get_all_empty_when_json_corrupted(
    tmp_path: Path,
) -> None:
    """JSON 损坏时返回空列表（不报错）。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "profile.json").write_text("{invalid json", encoding="utf-8")
    assert get_all() == []


def test_build_profile_prompt_empty_when_no_file(tmp_path: Path) -> None:
    """无画像返回空字符串。"""
    assert build_profile_prompt() == ""


# ============================================================
# upsert_from_llm
# ============================================================


async def test_upsert_from_llm_creates_new(tmp_path: Path) -> None:
    """LLM 抽取条目新建。"""
    entries = [
        {"key": "uses_typescript", "category": "fact", "content": "用户用 TypeScript"}
    ]
    written = await upsert_from_llm(entries)
    assert written == 1
    saved = get("uses_typescript")
    assert saved is not None
    assert saved.source == "llm_extracted"
    assert saved.content == "用户用 TypeScript"


async def test_upsert_from_llm_updates_existing(tmp_path: Path) -> None:
    """key 重复时更新 content 与 source=llm_extracted。"""
    await add(_make_entry(key="uses_ts", content="old", source="manual"))
    entries = [
        {"key": "uses_ts", "category": "fact", "content": "new TS usage"}
    ]
    written = await upsert_from_llm(entries)
    assert written == 1
    updated = get("uses_ts")
    assert updated is not None
    assert updated.content == "new TS usage"
    assert updated.source == "llm_extracted"  # source 被覆盖


async def test_upsert_from_llm_skips_invalid_entries(tmp_path: Path) -> None:
    """非法条目跳过，不写入。"""
    entries = [
        {"key": "../invalid", "category": "fact", "content": "bad"},  # key 非法
        {"key": "valid", "category": "fact", "content": "good"},
        {"key": "too_long", "category": "fact", "content": "x" * 501},  # content 越界
    ]
    written = await upsert_from_llm(entries)
    assert written == 1
    assert get("valid") is not None
    # 非法 key 在 get 时也会校验抛异常
    with pytest.raises(ProfileKeyInvalid):
        get("../invalid")
    # too_long 的 content 越界，upsert 跳过，key 本身合法所以 get 不抛但返回 None
    assert get("too_long") is None


async def test_upsert_from_llm_empty_list(tmp_path: Path) -> None:
    """空列表不写入。"""
    assert await upsert_from_llm([]) == 0
    assert await upsert_from_llm(None) == 0  # type: ignore[arg-type]


async def test_upsert_from_llm_truncates_over_limit(tmp_path: Path) -> None:
    """超出 ``_MAX_LLM_EXTRACT_ENTRIES`` 的条目被截断，只写前 20 条。"""
    import app.memory.profile_store as ps

    entries = [
        {"key": f"k{i:02d}", "category": "fact", "content": f"v{i}"}
        for i in range(ps._MAX_LLM_EXTRACT_ENTRIES + 5)
    ]
    written = await upsert_from_llm(entries)
    assert written == ps._MAX_LLM_EXTRACT_ENTRIES
    # 前 20 条已写入
    assert get("k00") is not None
    assert get(f"k{ps._MAX_LLM_EXTRACT_ENTRIES - 1:02d}") is not None
    # 第 21 条及之后被截断
    assert get(f"k{ps._MAX_LLM_EXTRACT_ENTRIES:02d}") is None
    assert get(f"k{ps._MAX_LLM_EXTRACT_ENTRIES + 4:02d}") is None


# ============================================================
# build_profile_prompt
# ============================================================


async def test_build_profile_prompt_format(tmp_path: Path) -> None:
    """画像注入 prompt 格式正确。"""
    await add(_make_entry(key="pref1", category="preference", content="简洁回复"))
    await add(_make_entry(key="proj1", category="fact", content="用 FastAPI"))

    prompt = build_profile_prompt()
    assert "用户画像（请遵循以下偏好与约定）:" in prompt
    assert "- [preference] 简洁回复" in prompt
    assert "- [fact] 用 FastAPI" in prompt


async def test_build_profile_prompt_truncates_to_30(tmp_path: Path) -> None:
    """超 30 条截断到前 30 条。"""
    # 写 35 条
    for i in range(35):
        await add(_make_entry(key=f"k{i:02d}", content=f"content_{i}"))
    prompt = build_profile_prompt()
    # 1 行前缀 + 30 行条目 = 31 行
    lines = prompt.split("\n")
    assert len(lines) == 31


async def test_build_profile_prompt_orders_by_updated_at_desc(tmp_path: Path) -> None:
    """按 updated_at 降序排列（最近更新优先）。"""
    import time

    await add(_make_entry(key="old", content="old content"))
    time.sleep(0.01)  # 确保时间戳不同
    # 直接 update 一个 entry 让它成为最新
    await add(_make_entry(key="new", content="new content"))

    prompt = build_profile_prompt()
    lines = prompt.split("\n")
    # 第一条（前缀后）应是 new
    assert "new content" in lines[1]
    assert "old content" in lines[2]


# ============================================================
# LLM 抽取（mock）
# ============================================================


async def test_extract_profile_via_llm_returns_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 抽取：mock with_structured_output 返回 ProfileResult。"""
    from app.memory.profile_extractor import (
        ExtractStatus,
        ProfileEntry,
        ProfileResult,
        extract_profile_via_llm,
    )

    result = ProfileResult(
        entries=[
            ProfileEntry(
                key="uses_ts",
                category="project",
                content="用户用 TypeScript 写前端",
            )
        ]
    )
    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(return_value=result)
    fake_llm = MagicMock()
    fake_llm.with_structured_output = MagicMock(return_value=structured_llm)
    monkeypatch.setattr("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm)

    extract_result = await extract_profile_via_llm("我用 TypeScript", "好的")
    assert extract_result.status == ExtractStatus.SUCCESS_WRITTEN
    assert len(extract_result.entries) == 1
    assert extract_result.entries[0]["key"] == "uses_ts"
    assert extract_result.entries[0]["category"] == "project"


async def test_extract_profile_via_llm_no_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 返回空 entries → SUCCESS_EMPTY。"""
    from app.memory.profile_extractor import (
        ExtractStatus,
        ProfileResult,
        extract_profile_via_llm,
    )

    result = ProfileResult(entries=[])
    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(return_value=result)
    fake_llm = MagicMock()
    fake_llm.with_structured_output = MagicMock(return_value=structured_llm)
    monkeypatch.setattr("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm)

    extract_result = await extract_profile_via_llm("你好", "你好")
    assert extract_result.status == ExtractStatus.SUCCESS_EMPTY
    assert extract_result.entries == []


async def test_extract_profile_via_llm_handles_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 调用抛异常时返回 FAILED 状态（不报错，但区分失败与空成功）。"""
    from app.memory.profile_extractor import (
        ExtractStatus,
        extract_profile_via_llm,
    )

    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM 不可用"))
    fake_llm = MagicMock()
    fake_llm.with_structured_output = MagicMock(return_value=structured_llm)
    monkeypatch.setattr("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm)

    extract_result = await extract_profile_via_llm("msg", "reply")
    assert extract_result.status == ExtractStatus.FAILED
    assert extract_result.entries == []
    assert extract_result.error is not None
    assert "LLM 不可用" in extract_result.error


async def test_extract_profile_via_llm_multiple_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 返回多条目时全部转换为 dict。"""
    from app.memory.profile_extractor import (
        ExtractStatus,
        ProfileEntry,
        ProfileResult,
        extract_profile_via_llm,
    )

    result = ProfileResult(
        entries=[
            ProfileEntry(key="k1", category="fact", content="重要事实"),
            ProfileEntry(key="k2", category="preference", content="偏好简洁"),
        ]
    )
    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(return_value=result)
    fake_llm = MagicMock()
    fake_llm.with_structured_output = MagicMock(return_value=structured_llm)
    monkeypatch.setattr("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm)

    extract_result = await extract_profile_via_llm("msg", "reply")
    assert extract_result.status == ExtractStatus.SUCCESS_WRITTEN
    assert len(extract_result.entries) == 2
    assert extract_result.entries[0]["key"] == "k1"
    assert extract_result.entries[1]["key"] == "k2"


# ============================================================
# 文件格式容错：边界
# ============================================================


def test_get_all_empty_when_top_level_not_dict(tmp_path: Path) -> None:
    """profile.json 顶层是 list/str/数字等非 object 时，返回空 store。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "profile.json").write_text('[1, 2, 3]', encoding="utf-8")
    assert get_all() == []


def test_get_all_empty_when_entries_not_list(tmp_path: Path) -> None:
    """profile.json 顶层合法但 entries 不是 list 时，返回空 store。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "profile.json").write_text(
        '{"entries": "not a list"}', encoding="utf-8"
    )
    assert get_all() == []


def test_get_all_empty_when_entry_missing_required_field(
    tmp_path: Path,
) -> None:
    """entry 缺必填字段时整个文件按空 store 处理。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    # 缺 content 字段
    (config_dir / "profile.json").write_text(
        '{"entries": [{"key": "k1", "category": "fact", "source": "manual", '
        '"created_at": "x", "updated_at": "y"}]}',
        encoding="utf-8",
    )
    assert get_all() == []


# ============================================================
# upsert_from_llm 边界
# ============================================================


async def test_upsert_from_llm_skips_non_dict_entries(tmp_path: Path) -> None:
    """非 dict 条目（字符串、列表等）跳过。"""
    entries: list[Any] = [
        "not a dict",
        ["also", "not"],
        None,
        {"key": "valid", "category": "fact", "content": "ok"},
    ]
    written = await upsert_from_llm(entries)  # type: ignore[arg-type]
    assert written == 1
    assert get("valid") is not None


async def test_upsert_from_llm_default_category_is_custom(tmp_path: Path) -> None:
    """LLM 抽取条目缺 category 时默认 custom。"""
    entries = [{"key": "k_no_cat", "content": "no category given"}]
    written = await upsert_from_llm(entries)
    assert written == 1
    assert get("k_no_cat").category == "custom"


# ============================================================
# build_profile_prompt 边界
# ============================================================


async def test_build_profile_prompt_with_single_entry(tmp_path: Path) -> None:
    """单条画像也正确格式化。"""
    await add(_make_entry(key="only", content="only one"))
    prompt = build_profile_prompt()
    assert prompt == "用户画像（请遵循以下偏好与约定）:\n- [preference] only one"


# ============================================================
# 校验顺序：key 非法 vs key 不存在
# ============================================================


async def test_update_invalid_key_format_raises_first(tmp_path: Path) -> None:
    """update 非法 key 抛 ProfileKeyInvalid（不抛 KeyError）。"""
    with pytest.raises(ProfileKeyInvalid):
        await update("../bad", "content")


async def test_delete_invalid_key_format_raises_first(tmp_path: Path) -> None:
    """delete 非法 key 抛 ProfileKeyInvalid。"""
    with pytest.raises(ProfileKeyInvalid):
        await delete("../bad")


# ============================================================
# 工作区级画像隔离
# ============================================================


async def test_add_to_workspace(tmp_path: Path) -> None:
    """workspace_path 非空时写入工作区画像。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()
    entry = await add(_make_entry(key="ws_pref"), workspace_path=str(ws_path))
    assert entry.scope == "workspace"
    # 工作区画像文件存在
    assert (ws_path / ".agentx" / "profile.json").exists()
    # 全局画像文件不存在
    assert not (tmp_path / "config" / "profile.json").exists()


async def test_get_all_merges_workspace_and_global(tmp_path: Path) -> None:
    """get_all 合并工作区 + 全局，同 key 工作区覆盖全局。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()
    # 全局写入
    await add(_make_entry(key="shared", content="global value"))
    await add(_make_entry(key="global_only", content="only in global"))
    # 工作区写入（覆盖 shared）
    await add(
        _make_entry(key="shared", content="workspace value"),
        workspace_path=str(ws_path),
    )
    await add(
        _make_entry(key="ws_only", content="only in workspace"),
        workspace_path=str(ws_path),
    )

    # 不传 workspace_path → 仅全局
    global_entries = {e.key: e for e in get_all()}
    assert "shared" in global_entries
    assert global_entries["shared"].content == "global value"
    assert "global_only" in global_entries
    assert "ws_only" not in global_entries

    # 传 workspace_path → 合并
    merged_entries = {e.key: e for e in get_all(workspace_path=str(ws_path))}
    assert merged_entries["shared"].content == "workspace value"  # 工作区覆盖
    assert merged_entries["global_only"].content == "only in global"  # 全局保留
    assert merged_entries["ws_only"].content == "only in workspace"  # 工作区独有

    # 传 workspace_path + scope=workspace → 只读工作区级，不掺全局
    ws_only_entries = {
        e.key: e for e in get_all(workspace_path=str(ws_path), scope="workspace")
    }
    assert "shared" in ws_only_entries
    assert ws_only_entries["shared"].content == "workspace value"
    assert "global_only" not in ws_only_entries  # 全局条目被排除
    assert "ws_only" in ws_only_entries

    # scope=workspace 但 workspace_path=None → 空列表
    assert get_all(scope="workspace") == []


async def test_build_profile_prompt_merges_workspace(tmp_path: Path) -> None:
    """build_profile_prompt 合并工作区 + 全局。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()
    await add(_make_entry(key="g_pref", content="全局偏好"))
    await add(
        _make_entry(key="w_pref", content="工作区偏好"),
        workspace_path=str(ws_path),
    )

    # 仅全局
    prompt_global = build_profile_prompt()
    assert "全局偏好" in prompt_global
    assert "工作区偏好" not in prompt_global

    # 合并
    prompt_merged = build_profile_prompt(workspace_path=str(ws_path))
    assert "全局偏好" in prompt_merged
    assert "工作区偏好" in prompt_merged


async def test_update_fallback_to_global(tmp_path: Path) -> None:
    """update 工作区无此 key 时回退全局。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()
    await add(_make_entry(key="g_key", content="old"))
    updated = await update("g_key", "new", workspace_path=str(ws_path))
    assert updated.content == "new"
    assert updated.scope == "global"  # 实际更新了全局


async def test_delete_fallback_to_global(tmp_path: Path) -> None:
    """delete 工作区无此 key 时回退全局。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()
    await add(_make_entry(key="g_key"))
    assert await delete("g_key", workspace_path=str(ws_path)) is True
    assert get("g_key") is None
