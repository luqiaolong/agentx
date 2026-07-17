"""factory.py force_todo + AggressiveTodoMiddleware + ensure_harness_profile 单元测试。

覆盖（REQ-CP-6）：
- _build_aggressive_todo_middleware 返回 TodoListMiddleware，system_prompt 含 '强制'，
  不含默认劝退文本 'For simple objectives'
- ensure_harness_profile 默认返回 'openai'，excluded_middleware 非空时返回含 'mw-' 的 key
- profile key 唯一性：同 excluded_tools 不同 excluded_middleware → 不同 key；同两者 → 同 key
- create_agent force_todo=True：middleware 列表含强型 TodoListMiddleware，profile key 含 'mw-'
- create_agent force_todo=False：middleware 列表不含 TodoListMiddleware（默认由 harness 内部注入）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.deepagent.factory import (
    _build_aggressive_todo_middleware,
    _registered_keys,
    create_agent,
    ensure_harness_profile,
)


@pytest.fixture(autouse=True)
def _reset_harness_registry():
    """每个测试前清空已注册 profile key，避免幂等逻辑影响断言。"""
    _registered_keys.clear()
    yield


# ============================================================
# _build_aggressive_todo_middleware
# ============================================================


def test_build_aggressive_todo_middleware_returns_todo_list_middleware() -> None:
    """_build_aggressive_todo_middleware 返回 TodoListMiddleware 实例。"""
    from langchain.agents.middleware import TodoListMiddleware

    mw = _build_aggressive_todo_middleware()
    assert isinstance(mw, TodoListMiddleware)


def test_aggressive_todo_system_prompt_contains_force_text() -> None:
    """强型 system_prompt 包含 '强制'，不包含劝退文本 'For simple objectives'。"""
    mw = _build_aggressive_todo_middleware()
    assert "强制" in mw.system_prompt
    assert "For simple objectives" not in mw.system_prompt


# ============================================================
# ensure_harness_profile
# ============================================================


def test_ensure_harness_profile_default_returns_openai() -> None:
    """无 excluded_tools / excluded_middleware 时返回默认 key 'openai'。"""
    with patch("app.deepagent.factory.register_harness_profile"):
        key = ensure_harness_profile(None, None)
    assert key == "openai"


def test_ensure_harness_profile_with_excluded_middleware() -> None:
    """传入 excluded_middleware 时返回含 'mw-' 的 per-call key。"""
    with patch("app.deepagent.factory.register_harness_profile"):
        key = ensure_harness_profile(None, frozenset({"TodoListMiddleware"}))
    assert "mw-" in key
    assert key != "openai"


def test_profile_key_uniqueness_different_middleware() -> None:
    """同 excluded_tools 不同 excluded_middleware → 不同 key。"""
    with patch("app.deepagent.factory.register_harness_profile"):
        key1 = ensure_harness_profile(None, frozenset({"TodoListMiddleware"}))
        key2 = ensure_harness_profile(None, frozenset({"OtherMiddleware"}))
    assert key1 != key2


def test_profile_key_same_args_same_key() -> None:
    """同 excluded_tools + 同 excluded_middleware → 同 key（幂等）。"""
    with patch("app.deepagent.factory.register_harness_profile"):
        key1 = ensure_harness_profile(None, frozenset({"TodoListMiddleware"}))
        key2 = ensure_harness_profile(None, frozenset({"TodoListMiddleware"}))
    assert key1 == key2


# ============================================================
# create_agent force_todo
# ============================================================


@pytest.mark.asyncio
async def test_create_agent_force_todo_true_injects_aggressive_middleware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_todo=True 时 middleware 列表含强型 TodoListMiddleware（system_prompt 含 '强制'）。"""
    from langchain.agents.middleware import TodoListMiddleware

    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)

    with patch("app.deepagent.factory.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()
        create_agent(
            MagicMock(),
            [],
            system_prompt="prompt",
            workspace_path=str(tmp_path),
            force_todo=True,
        )

    _, kwargs = mock_create.call_args
    todo_mws = [m for m in kwargs["middleware"] if isinstance(m, TodoListMiddleware)]
    assert len(todo_mws) == 1
    assert "强制" in todo_mws[0].system_prompt
    # profile key 含 'mw-'（excluded_middleware 非空 → per-call key）
    assert any("mw-" in k for k in _registered_keys)


@pytest.mark.asyncio
async def test_create_agent_force_todo_false_no_aggressive_middleware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_todo=False 时 middleware 列表不含 TodoListMiddleware（默认由 harness 内部注入）。"""
    from langchain.agents.middleware import TodoListMiddleware

    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)

    with patch("app.deepagent.factory.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()
        create_agent(
            MagicMock(),
            [],
            system_prompt="prompt",
            workspace_path=str(tmp_path),
            force_todo=False,
        )

    _, kwargs = mock_create.call_args
    todo_mws = [m for m in kwargs["middleware"] if isinstance(m, TodoListMiddleware)]
    assert len(todo_mws) == 0
    # profile key 为 'openai'（默认，不排除中间件）
    assert "openai" in _registered_keys
