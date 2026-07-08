"""deepagents 集成测试：验证 harness 层配置正确组装并传递给 ``create_deep_agent``。

本模块不调用真实 LLM，通过 mock ``deepagents.create_deep_agent`` 验证：
- ``excluded_tools`` 隐藏内置 fs 工具 + 禁用默认 subagent
- ``interrupt_on`` 从 ``DANGEROUS_TOOLS`` 动态生成
- ``memory=`` 自动加载 ``.agentx/AGENTS.md`` + ``rules/*.md``
- ``skills=`` 指向 ``data/skills/`` 目录
- ``backend=`` 启用 ``FilesystemBackend`` Context Offloading
- 整体配置通过 ``create_agent`` 正确组装
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.deep.harness import (
    _EXCLUDED_BUILTIN_TOOLS,
    _registered_keys,
    build_interrupt_config,
    create_agent,
    ensure_harness_profile,
    resolve_backend,
    resolve_memory_paths,
    resolve_skills_dir,
)
from app.deep.tools import DANGEROUS_TOOLS


@pytest.fixture(autouse=True)
def _reset_harness_registry():
    """每个测试前清空已注册 profile key，避免幂等逻辑影响断言。"""
    _registered_keys.clear()
    yield


# ============================================================
# 中断配置
# ============================================================


def test_build_interrupt_config_from_dangerous_tools() -> None:
    """``build_interrupt_config`` 从 ``DANGEROUS_TOOLS`` 生成 ``dict[str, True]``。"""
    config = build_interrupt_config()
    assert config == {tool: True for tool in DANGEROUS_TOOLS}
    assert all(v is True for v in config.values())


# ============================================================
# HarnessProfile
# ============================================================


def test_harness_profile_excludes_builtin_tools() -> None:
    """``HarnessProfile`` 排除 deepagents 内置 fs 工具，避免与项目自研工具冲突。"""
    with patch("app.deep.harness.register_harness_profile") as mock_register:
        ensure_harness_profile("openai")
        assert mock_register.call_count == 1
        profile = mock_register.call_args[0][1]
        for tool in _EXCLUDED_BUILTIN_TOOLS:
            assert tool in profile.excluded_tools


def test_harness_profile_disables_default_subagent() -> None:
    """``HarnessProfile`` 禁用默认通用子代理，项目使用自研委派工具。"""
    with patch("app.deep.harness.register_harness_profile") as mock_register:
        ensure_harness_profile("openai")
        profile = mock_register.call_args[0][1]
        assert profile.general_purpose_subagent.enabled is False


def test_ensure_harness_profile_is_idempotent() -> None:
    """重复注册同一 key 不会再次调用 ``register_harness_profile``。"""
    with patch("app.deep.harness.register_harness_profile") as mock_register:
        ensure_harness_profile("openai")
        ensure_harness_profile("openai")
        assert mock_register.call_count == 1


# ============================================================
# memory= / skills= / backend= 解析
# ============================================================


def test_resolve_memory_paths_returns_agents_md_and_rules(tmp_path: Path) -> None:
    """``resolve_memory_paths`` 返回 ``AGENTS.md`` + 排序后的 ``rules/*.md``。"""
    agentx_dir = tmp_path / ".agentx"
    agentx_dir.mkdir()
    (agentx_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    rules_dir = agentx_dir / "rules"
    rules_dir.mkdir()
    (rules_dir / "b_rule.md").write_text("b", encoding="utf-8")
    (rules_dir / "a_rule.md").write_text("a", encoding="utf-8")

    paths = resolve_memory_paths(str(tmp_path))
    assert len(paths) == 3
    assert Path(paths[0]).name == "AGENTS.md"
    assert [Path(p).name for p in paths[1:]] == ["a_rule.md", "b_rule.md"]


def test_resolve_memory_paths_empty_when_no_agentx(tmp_path: Path) -> None:
    """``.agentx`` 不存在时返回空列表。"""
    assert resolve_memory_paths(str(tmp_path)) == []


def test_resolve_memory_paths_none_workspace() -> None:
    """``workspace_path`` 为 None 时返回空列表。"""
    assert resolve_memory_paths(None) == []


def test_resolve_skills_dir_returns_data_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``resolve_skills_dir`` 返回 ``DATA_DIR/skills`` 绝对路径。"""
    monkeypatch.setattr("app.deep.harness.DATA_DIR", tmp_path)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    assert resolve_skills_dir() == str(skills_dir)


def test_resolve_skills_dir_none_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``data/skills`` 不存在时返回 None。"""
    monkeypatch.setattr("app.deep.harness.DATA_DIR", tmp_path)
    assert resolve_skills_dir() is None


def test_resolve_backend_returns_filesystem_backend(tmp_path: Path) -> None:
    """``resolve_backend`` 返回 ``FilesystemBackend`` 实例。"""
    from deepagents.backends import FilesystemBackend

    backend = resolve_backend(str(tmp_path))
    assert isinstance(backend, FilesystemBackend)


def test_resolve_backend_none_when_no_workspace() -> None:
    """``workspace_path`` 为 None 时不启用 backend。"""
    assert resolve_backend(None) is None


# ============================================================
# create_agent 整体配置组装
# ============================================================


@pytest.mark.asyncio
async def test_create_agent_passes_correct_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``create_agent`` 正确组装所有 deepagents 参数。"""
    monkeypatch.setattr("app.deep.harness.DATA_DIR", tmp_path)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    agentx_dir = tmp_path / ".agentx"
    agentx_dir.mkdir()
    (agentx_dir / "AGENTS.md").write_text("agents", encoding="utf-8")

    with patch("app.deep.harness.create_deep_agent") as mock_create:
        mock_graph = MagicMock()
        mock_create.return_value = mock_graph

        model = MagicMock()
        tools = [MagicMock()]
        checkpointer = MagicMock()
        result = create_agent(
            model,
            tools,
            system_prompt="test system prompt",
            checkpointer=checkpointer,
            thread_id="thread-1",
            workspace_path=str(tmp_path),
            name="test_agent",
        )

        assert result is mock_graph
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args

        assert kwargs["model"] is model
        assert kwargs["tools"] is tools
        assert kwargs["system_prompt"] == "test system prompt"
        assert kwargs["checkpointer"] is checkpointer
        assert kwargs["name"] == "test_agent"
        assert kwargs["interrupt_on"] == build_interrupt_config()
        assert kwargs["memory"] == resolve_memory_paths(str(tmp_path))
        assert kwargs["skills"] == [str(skills_dir)]
        assert kwargs["backend"] is not None

        # 验证 memory 非空且 skills 只含一个目录
        assert len(kwargs["memory"]) == 1
        assert kwargs["memory"][0].endswith("AGENTS.md")


@pytest.mark.asyncio
async def test_create_agent_uses_default_name_and_none_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未传 name 时默认 ``deep_agent``；未传 workspace 时 backend 为 None。"""
    monkeypatch.setattr("app.deep.harness.DATA_DIR", tmp_path)

    with patch("app.deep.harness.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()
        create_agent(
            MagicMock(),
            [],
            system_prompt="prompt",
        )
        _, kwargs = mock_create.call_args
        assert kwargs["name"] == "deep_agent"
        assert kwargs["backend"] is None
        assert kwargs["memory"] is None
        assert kwargs["skills"] is None


# ============================================================
# 工具冲突排除清单
# ============================================================


def test_excluded_builtin_tools_covers_fs_and_task() -> None:
    """内置 fs 工具在排除清单中，避免绕过沙箱授权。"""
    assert {"ls", "read_file", "write_file", "edit_file", "glob", "grep"}.issubset(_EXCLUDED_BUILTIN_TOOLS)
