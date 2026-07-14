"""Phase 3 + Phase 5a：scope 分类 / confidence 过滤 / 密钥检测 / sensitivity 注入测试。

覆盖：
- T3.4: ProfileEntry 新增 scope/confidence/sensitivity 字段
- T3.5: 低置信度（< 0.6）条目不被自动写入
- T3.6: coding_team 触发抽取（集成层验证见 e2e，此处验证 enqueue 调用路径）
- T5.2: 密钥/凭证检测 → 拒绝入库
- T5.3: sensitivity="private" 条目不注入 prompt
- scope 分类规则：preference → global，project → workspace
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.memory.checkpointer as cp_module
import app.memory.extract_queue as eq_module
import app.memory.profile_store as ps_module
from app.memory.extract_queue import enqueue, queue_size, start_worker
from app.memory.profile_extractor import (
    ExtractStatus,
    ProfileEntry,
    ProfileResult,
    extract_profile_via_llm,
)
from app.memory.profile_store import (
    ProfileSecretDetected,
    add,
    build_profile_prompt,
    detect_secret,
    get,
    upsert_from_llm,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个测试隔离 ``DATA_DIR``，避免污染真实数据库与 profile.json。"""
    monkeypatch.setattr(eq_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cp_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ps_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ps_module, "_PROFILE_DIR", tmp_path / "config")
    monkeypatch.setattr(ps_module, "_PROFILE_FILE", tmp_path / "config" / "profile.json")
    # 隔离 workspace.memory_store 的锁缓存
    import app.workspace.memory_store as ms_module
    monkeypatch.setattr(ms_module, "_workspace_locks", {})
    yield


def _make_llm_mock(entries: list[ProfileEntry] | None) -> MagicMock:
    """构造返回指定 entries 的 mock LLM。"""
    result = ProfileResult(entries=entries or [])
    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(return_value=result)
    fake_llm = MagicMock()
    fake_llm.with_structured_output = MagicMock(return_value=structured_llm)
    return fake_llm


async def _wait_for_empty_queue(timeout: float = 3.0) -> None:
    """辅助：等待队列清空。"""
    import asyncio
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await queue_size() == 0:
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("queue not empty in time")


# ============================================================
# T3.4: scope 分类规则
# ============================================================


async def test_preference_scope_is_global(tmp_path: Path) -> None:
    """T3.4-1: preference 类条目 scope 应为 global。"""
    entries = [
        {
            "key": "prefers_concise",
            "category": "preference",
            "content": "用户喜欢简洁回复",
            "scope": "global",
            "confidence": 0.9,
            "sensitivity": "public",
        }
    ]
    written = await upsert_from_llm(entries)
    assert written == 1
    entry = get("prefers_concise")
    assert entry is not None
    assert entry.scope == "global"
    assert entry.sensitivity == "public"


async def test_project_scope_is_workspace(tmp_path: Path) -> None:
    """T3.4-2: project 类条目 scope 应为 workspace（需 workspace_path）。"""
    ws_path = tmp_path / "myproject"
    ws_path.mkdir()
    entries = [
        {
            "key": "project_framework",
            "category": "project",
            "content": "项目使用 FastAPI 框架",
            "scope": "workspace",
            "confidence": 0.95,
            "sensitivity": "public",
        }
    ]
    written = await upsert_from_llm(entries, workspace_path=str(ws_path))
    assert written == 1
    # profile_store 写入 <workspace>/.agentx/profile.json
    entry = get("project_framework", workspace_path=str(ws_path))
    assert entry is not None
    assert entry.scope == "workspace"


async def test_workspace_preference_is_global_not_workspace(tmp_path: Path) -> None:
    """T3.4-6: 用户在工作区内说\"我喜欢中文回复\"，scope 应为 global 而非 workspace。

    用户偏好（如语言偏好）是跨工作区的个人偏好，不应绑定到当前工作区。
    即使 LLM 在工作区上下文中抽取，scope 仍应为 global。
    """
    # 模拟 LLM 返回的条目：preference 类，scope=global（即使在工作区中抽取）
    entries = [
        {
            "key": "prefers_chinese_reply",
            "category": "preference",
            "content": "用户喜欢中文回复",
            "scope": "global",
            "confidence": 0.9,
            "sensitivity": "public",
        }
    ]
    # 写入全局画像（因为 scope=global，不写工作区）
    written = await upsert_from_llm(entries, workspace_path=None)
    assert written == 1
    entry = get("prefers_chinese_reply")
    assert entry is not None
    assert entry.scope == "global"
    assert entry.category == "preference"


# ============================================================
# T3.5: 低置信度过滤
# ============================================================


async def test_low_confidence_not_auto_written(tmp_path: Path) -> None:
    """T3.5-3: confidence < 0.6 的条目不被自动写入。"""
    fake_llm = _make_llm_mock(
        [
            ProfileEntry(
                key="low_conf_key",
                category="fact",
                content="模糊的推断",
                confidence=0.4,
            ),
            ProfileEntry(
                key="high_conf_key",
                category="fact",
                content="明确的事实",
                confidence=0.9,
            ),
        ]
    )

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        await enqueue(
            message="测试消息",
            assistant_reply="测试回复",
            workspace_path=None,
        )
        worker_task = start_worker(poll_interval=0.05)
        await _wait_for_empty_queue()
        worker_task.cancel()
        try:
            import asyncio
            await worker_task
        except asyncio.CancelledError:
            pass

    assert await queue_size() == 0
    # 低置信度条目未写入
    assert get("low_conf_key") is None
    # 高置信度条目已写入
    high_entry = get("high_conf_key")
    assert high_entry is not None
    assert high_entry.content == "明确的事实"


async def test_all_low_confidence_nothing_written(tmp_path: Path) -> None:
    """T3.5 补充：全部条目都低置信度时，不写入任何条目。"""
    fake_llm = _make_llm_mock(
        [
            ProfileEntry(
                key="guess1",
                category="fact",
                content="可能是这样",
                confidence=0.3,
            ),
            ProfileEntry(
                key="guess2",
                category="fact",
                content="也许是这样",
                confidence=0.5,
            ),
        ]
    )

    with patch("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm):
        await enqueue(
            message="模糊消息",
            assistant_reply="模糊回复",
            workspace_path=None,
        )
        worker_task = start_worker(poll_interval=0.05)
        await _wait_for_empty_queue()
        worker_task.cancel()
        try:
            import asyncio
            await worker_task
        except asyncio.CancelledError:
            pass

    assert await queue_size() == 0
    assert get("guess1") is None
    assert get("guess2") is None


# ============================================================
# T5.2: 密钥/凭证检测
# ============================================================


def test_detect_secret_api_key() -> None:
    """T5.2-4a: detect_secret 检测到 API key。"""
    # 注意：不含 sk- 前缀，否则会匹配 OpenAI-style key 模式
    content = "用户的 API key 是 api_key=abcdefghij1234567890xyz"
    result = detect_secret(content)
    assert result is not None
    assert result == "API key"


def test_detect_secret_private_key() -> None:
    """T5.2 补充: detect_secret 检测到私钥。"""
    content = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
    result = detect_secret(content)
    assert result is not None
    assert result == "Private key"


def test_detect_secret_openai_key() -> None:
    """T5.2 补充: detect_secret 检测到 OpenAI 风格 key。"""
    content = "使用 sk-abcdefghijklmnopqrstuvwxyz1234567890 作为密钥"
    result = detect_secret(content)
    assert result is not None
    assert result == "OpenAI-style key"


def test_detect_secret_connection_string() -> None:
    """T5.2 补充: detect_secret 检测到带凭证的连接字符串。"""
    content = "数据库连接：postgres://user:password@localhost:5432/db"
    result = detect_secret(content)
    assert result is not None
    assert result == "Connection string with credentials"


def test_detect_secret_none_for_normal_content() -> None:
    """T5.2 补充: 普通内容不触发密钥检测。"""
    assert detect_secret("用户喜欢 TypeScript") is None
    assert detect_secret("项目使用 FastAPI 框架") is None
    assert detect_secret("") is None
    assert detect_secret(None) is None  # type: ignore[arg-type]


async def test_add_rejects_api_key(tmp_path: Path) -> None:
    """T5.2-4b: add 拒绝含密钥的 content。"""
    from app.memory.profile_store import ProfileEntry as StoreEntry
    # 使用不含 sk- 前缀的 API key，确保匹配 API key 模式
    entry = StoreEntry(
        key="leaked_key",
        category="fact",
        content="api_key=abcdefghij1234567890xyz",
        source="manual",
        created_at="",
        updated_at="",
    )
    with pytest.raises(ProfileSecretDetected, match="API key"):
        await add(entry)
    # 确认未写入
    assert get("leaked_key") is None


async def test_upsert_from_llm_skips_secret(tmp_path: Path) -> None:
    """T5.2 补充: upsert_from_llm 跳过含密钥的条目，写入合法条目。"""
    entries = [
        {
            "key": "secret_key",
            "category": "fact",
            "content": "password=secret123",
        },
        {
            "key": "normal_fact",
            "category": "fact",
            "content": "用户是前端工程师",
        },
    ]
    written = await upsert_from_llm(entries)
    # 只有合法条目被写入
    assert written == 1
    assert get("secret_key") is None
    assert get("normal_fact") is not None


# ============================================================
# T5.3: sensitivity 影响注入
# ============================================================


async def test_private_sensitivity_not_in_profile_prompt(tmp_path: Path) -> None:
    """T5.3-5: sensitivity='private' 的条目不出现在 build_profile_prompt 中。"""
    from app.memory.profile_store import ProfileEntry as StoreEntry
    # 写入 public 条目
    await add(StoreEntry(
        key="public_pref",
        category="preference",
        content="喜欢简洁回复",
        source="manual",
        created_at="",
        updated_at="",
        sensitivity="public",
    ))
    # 写入 private 条目
    await add(StoreEntry(
        key="private_info",
        category="fact",
        content="个人敏感信息",
        source="manual",
        created_at="",
        updated_at="",
        sensitivity="private",
    ))

    prompt = build_profile_prompt()
    # public 条目出现在 prompt 中
    assert "喜欢简洁回复" in prompt
    # private 条目不出现在 prompt 中
    assert "个人敏感信息" not in prompt


async def test_only_public_entries_in_prompt(tmp_path: Path) -> None:
    """T5.3 补充: 全部 private 时 prompt 为空。"""
    from app.memory.profile_store import ProfileEntry as StoreEntry
    await add(StoreEntry(
        key="secret1",
        category="fact",
        content="秘密信息A",
        source="manual",
        created_at="",
        updated_at="",
        sensitivity="private",
    ))
    await add(StoreEntry(
        key="secret2",
        category="fact",
        content="秘密信息B",
        source="manual",
        created_at="",
        updated_at="",
        sensitivity="private",
    ))
    prompt = build_profile_prompt()
    assert prompt == ""


async def test_sensitivity_stored_correctly(tmp_path: Path) -> None:
    """T5.3 补充: sensitivity 字段正确存储与读取。"""
    from app.memory.profile_store import ProfileEntry as StoreEntry
    await add(StoreEntry(
        key="pub_entry",
        category="preference",
        content="公开偏好",
        source="manual",
        created_at="",
        updated_at="",
        sensitivity="public",
    ))
    entry = get("pub_entry")
    assert entry is not None
    assert entry.sensitivity == "public"

    await add(StoreEntry(
        key="priv_entry",
        category="fact",
        content="私有信息",
        source="manual",
        created_at="",
        updated_at="",
        sensitivity="private",
    ))
    entry = get("priv_entry")
    assert entry is not None
    assert entry.sensitivity == "private"


# ============================================================
# T3.4: ProfileEntry 模型字段验证
# ============================================================


def test_profile_entry_has_scope_confidence_sensitivity() -> None:
    """T3.4 补充: ProfileEntry 模型包含 scope/confidence/sensitivity 字段。"""
    entry = ProfileEntry(
        key="test_key",
        category="preference",
        content="测试内容",
    )
    # 默认值
    assert entry.scope == "global"
    assert entry.confidence == 0.8
    assert entry.sensitivity == "public"


def test_profile_entry_confidence_bounds() -> None:
    """T3.4 补充: confidence 有 0.0-1.0 边界约束。"""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ProfileEntry(key="k", category="fact", content="c", confidence=1.5)
    with pytest.raises(ValidationError):
        ProfileEntry(key="k", category="fact", content="c", confidence=-0.1)
    # 边界值合法
    entry = ProfileEntry(key="k", category="fact", content="c", confidence=0.0)
    assert entry.confidence == 0.0
    entry = ProfileEntry(key="k", category="fact", content="c", confidence=1.0)
    assert entry.confidence == 1.0


async def test_extract_returns_scope_confidence_sensitivity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T3.4 补充: extract_profile_via_llm 返回的 dict 包含新字段。"""
    fake_llm = _make_llm_mock(
        [
            ProfileEntry(
                key="uses_ts",
                category="project",
                content="用户用 TypeScript",
                scope="workspace",
                confidence=0.95,
                sensitivity="public",
            )
        ]
    )
    monkeypatch.setattr("app.memory.profile_extractor.get_chat_model", lambda **kw: fake_llm)

    result = await extract_profile_via_llm("我用 TypeScript", "好的")
    assert result.status == ExtractStatus.SUCCESS_WRITTEN
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry["scope"] == "workspace"
    assert entry["confidence"] == 0.95
    assert entry["sensitivity"] == "public"
