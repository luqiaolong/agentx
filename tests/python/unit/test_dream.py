"""Dream 记忆整理单元测试。

测试覆盖（对应 OpenSpec P0 修复）：
1. dream 模块导入成功 + dream_all_memory 可调用（修复 delete_entry → delete 导入 bug）
2. 非法 target_scope 被拒绝，不应用任何变更
3. LLM 遗漏 key 不导致删除（仅显式 operation=delete 才删除）
4. global delete 正确 await（无未等待协程）
5. snapshot/rollback：workspace 写入失败时回滚 global 变更

mock 策略：
- ``app.llm.get_chat_model`` / ``make_structured_llm`` / ``get_settings`` 在 dream 模块级 patch
- ``profile_store`` / ``memory_store`` 的函数在源模块级 patch（dream 函数体内 import）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.dream import DreamEntry, DreamOperation, DreamPatch, DreamResult, dream_all_memory
from app.memory.profile_store import ProfileEntry


# ============================================================
# helpers
# ============================================================


def _make_profile_entry(
    key: str = "test_key",
    category: str = "preference",
    content: str = "test content",
    source: str = "manual",
) -> ProfileEntry:
    """构造测试用 ProfileEntry。"""
    return ProfileEntry(
        key=key,
        category=category,
        content=content,
        source=source,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _make_dream_entry(
    key: str = "test_key",
    category: str = "preference",
    content: str = "test content",
) -> DreamEntry:
    """构造测试用 DreamEntry（不含 target，由 DreamPatch.target_scope 指定）。"""
    return DreamEntry(key=key, category=category, content=content)


def _make_patch(
    operation: DreamOperation,
    key: str,
    target_scope: str | None = None,
    entry: DreamEntry | None = None,
    reason: str = "test",
) -> DreamPatch:
    """构造测试用 DreamPatch。"""
    return DreamPatch(
        operation=operation,
        key=key,
        target_scope=target_scope,
        entry=entry,
        reason=reason,
    )


@pytest.fixture
def mock_dream_env():
    """Mock LLM 链 + 配置 + 存储层，返回可定制的 mock 对象字典。

    默认：LLM 返回空 patches，全局/工作区均无条目。
    测试可通过修改 ``dream_result`` 或设置各 mock 的 ``return_value`` / ``side_effect`` 定制场景。
    """
    dream_result = DreamResult(patches=[], summary="test")

    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(return_value=dream_result)

    with (
        patch("app.memory.dream.get_chat_model", return_value=MagicMock()) as mock_get_chat,
        patch("app.memory.dream.make_structured_llm", return_value=structured_llm),
        patch("app.memory.dream.get_settings") as mock_get_settings,
        patch("app.memory.profile_store.get_all") as mock_get_all,
        patch("app.memory.profile_store.upsert_from_llm", new_callable=AsyncMock) as mock_upsert,
        patch("app.memory.profile_store.delete", new_callable=AsyncMock) as mock_delete,
        patch("app.workspace.memory_store.list_entries") as mock_list_ws,
        patch("app.workspace.memory_store.save_entry", new_callable=AsyncMock) as mock_ws_save,
        patch("app.workspace.memory_store.delete_entry", new_callable=AsyncMock) as mock_ws_delete,
    ):
        mock_get_settings.return_value.llm_temperature_extraction = 0.0
        mock_get_all.return_value = []
        mock_list_ws.return_value = []

        yield {
            "dream_result": dream_result,
            "structured_llm": structured_llm,
            "get_chat_model": mock_get_chat,
            "get_settings": mock_get_settings,
            "get_all": mock_get_all,
            "upsert_from_llm": mock_upsert,
            "delete": mock_delete,
            "list_entries": mock_list_ws,
            "save_entry": mock_ws_save,
            "delete_entry": mock_ws_delete,
        }


# ============================================================
# Test 1: dream 模块导入成功 + dream_all_memory 可调用
# ============================================================


async def test_dream_imports_and_runs_without_import_error(mock_dream_env) -> None:
    """dream 模块导入成功，dream_all_memory 可正常调用。

    修复前：profile_store 无 ``delete_entry``，函数体内 ``from ... import delete_entry`` 会 ImportError。
    修复后：改为 ``delete as global_delete``，导入 + 调用正常。
    """
    mock_dream_env["dream_result"].patches = []
    mock_dream_env["dream_result"].summary = "无变更"

    result = await dream_all_memory(workspace_path=None)

    assert isinstance(result, dict)
    assert "summary" in result


# ============================================================
# Test 2: 非法 target_scope 被拒绝
# ============================================================


async def test_invalid_target_scope_rejected(mock_dream_env) -> None:
    """target_scope 非 global/workspace 时拒绝应用全部变更。"""
    # 需要有条目才能触发 LLM 调用 + patch 校验
    mock_dream_env["get_all"].return_value = [
        _make_profile_entry(key="existing", content="exists"),
    ]

    mock_dream_env["dream_result"].patches = [
        _make_patch(
            operation=DreamOperation.UPSERT,
            key="bad",
            target_scope="invalid_scope",
            entry=_make_dream_entry(key="bad"),
        ),
    ]

    result = await dream_all_memory(workspace_path=None)

    # 无任何存储操作被调用
    mock_dream_env["upsert_from_llm"].assert_not_called()
    mock_dream_env["delete"].assert_not_called()
    mock_dream_env["save_entry"].assert_not_called()
    mock_dream_env["delete_entry"].assert_not_called()
    # 结果标记未应用
    assert result.get("applied", 0) == 0


# ============================================================
# Test 3: LLM 遗漏 key 不导致删除
# ============================================================


async def test_omitted_key_not_deleted(mock_dream_env) -> None:
    """LLM 输出中遗漏的 key 不会被删除 — 仅 operation=delete 才删除。"""
    # 初始全局有 key_a 和 key_b
    mock_dream_env["get_all"].return_value = [
        _make_profile_entry(key="key_a", content="a"),
        _make_profile_entry(key="key_b", content="b"),
    ]

    # LLM 只返回 key_a 的 upsert patch（完全遗漏 key_b，无 delete patch）
    mock_dream_env["dream_result"].patches = [
        _make_patch(
            operation=DreamOperation.UPSERT,
            key="key_a",
            target_scope="global",
            entry=_make_dream_entry(key="key_a", content="a updated"),
        ),
    ]

    await dream_all_memory(workspace_path=None)

    # key_b 没有被删除（delete 未被调用）
    mock_dream_env["delete"].assert_not_called()


# ============================================================
# Test 4: global delete 正确 await
# ============================================================


async def test_global_delete_properly_awaited(mock_dream_env) -> None:
    """global delete 被正确 await — 不产生未等待协程。"""
    mock_dream_env["get_all"].return_value = [
        _make_profile_entry(key="to_delete", content="delete me"),
    ]

    mock_dream_env["dream_result"].patches = [
        _make_patch(
            operation=DreamOperation.DELETE,
            key="to_delete",
            target_scope="global",
        ),
    ]

    await dream_all_memory(workspace_path=None)

    # AsyncMock.assert_awaited_once_with 验证被 await 调用（而非仅创建协程）
    mock_dream_env["delete"].assert_awaited_once_with("to_delete", workspace_path=None)


# ============================================================
# Test 5: snapshot/rollback — workspace 写入失败回滚 global
# ============================================================


async def test_snapshot_rollback_on_workspace_failure(mock_dream_env) -> None:
    """workspace 写入失败时，已应用的 global 变更被回滚。"""
    # 初始全局只有 key_orig
    # 用 side_effect 让 get_all 第一次返回初始状态、第二次（rollback 时）返回 upsert 后状态
    mock_dream_env["get_all"].side_effect = [
        [_make_profile_entry(key="key_orig", content="original")],
        [
            _make_profile_entry(key="key_orig", content="original"),
            _make_profile_entry(key="key_new", content="new global"),
        ],
    ]

    mock_dream_env["list_entries"].return_value = []

    # LLM: upsert key_new 到 global + upsert key_ws 到 workspace
    mock_dream_env["dream_result"].patches = [
        _make_patch(
            operation=DreamOperation.UPSERT,
            key="key_new",
            target_scope="global",
            entry=_make_dream_entry(key="key_new", content="new global"),
        ),
        _make_patch(
            operation=DreamOperation.UPSERT,
            key="key_ws",
            target_scope="workspace",
            entry=_make_dream_entry(key="key_ws", content="ws entry"),
        ),
    ]

    # workspace save_entry 抛异常
    mock_dream_env["save_entry"].side_effect = RuntimeError("disk full")

    result = await dream_all_memory(workspace_path="/test/workspace")

    # global upsert 被调用（应用阶段）
    mock_dream_env["upsert_from_llm"].assert_awaited()

    # 回滚：global delete 被调用以删除新增的 key_new（不在原始 snapshot 中）
    mock_dream_env["delete"].assert_any_await("key_new", workspace_path=None)

    # 结果标记已回滚
    assert result.get("rolled_back", 0) > 0


# ============================================================
# Test 6: rollback 部分失败时返回 rollback_errors > 0
# ============================================================


async def test_rollback_partial_failure_reports_errors(mock_dream_env) -> None:
    """回滚过程中部分失败（如 global delete 抛异常）时，返回值包含 rollback_errors > 0。

    修复前：``_rollback_global`` / ``_rollback_workspace`` 静默吞掉异常，
    ``dream_all_memory`` 仍返回成功，违反原子性承诺。
    修复后：回滚函数返回错误计数，调用方在返回值中暴露 ``rollback_errors``。
    """
    # 初始全局只有 key_orig；第二次（rollback 时）返回 upsert 后状态
    mock_dream_env["get_all"].side_effect = [
        [_make_profile_entry(key="key_orig", content="original")],
        [
            _make_profile_entry(key="key_orig", content="original"),
            _make_profile_entry(key="key_new", content="new global"),
        ],
    ]

    mock_dream_env["list_entries"].return_value = []

    # LLM: upsert key_new 到 global + upsert key_ws 到 workspace
    mock_dream_env["dream_result"].patches = [
        _make_patch(
            operation=DreamOperation.UPSERT,
            key="key_new",
            target_scope="global",
            entry=_make_dream_entry(key="key_new", content="new global"),
        ),
        _make_patch(
            operation=DreamOperation.UPSERT,
            key="key_ws",
            target_scope="workspace",
            entry=_make_dream_entry(key="key_ws", content="ws entry"),
        ),
    ]

    # workspace save_entry 抛异常 → 触发回滚
    mock_dream_env["save_entry"].side_effect = RuntimeError("disk full")
    # 回滚阶段：global delete 也抛异常 → rollback 部分失败
    mock_dream_env["delete"].side_effect = RuntimeError("rollback delete failed")

    result = await dream_all_memory(workspace_path="/test/workspace")

    # 回滚被触发：global delete 被调用以删除新增的 key_new
    mock_dream_env["delete"].assert_awaited()

    # 返回值包含 rollback_errors > 0
    assert result.get("rollback_errors", 0) > 0
    # summary 中也体现回滚错误数
    assert "回滚错误" in result["summary"]
