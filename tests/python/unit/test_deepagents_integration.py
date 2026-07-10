"""deepagents 集成测试：验证 harness 层配置正确组装并传递给 ``create_deep_agent``。

本模块不调用真实 LLM，通过 mock ``deepagents.create_deep_agent`` 验证：
- 默认 HarnessProfile 启用全部内置 fs 工具（excluded_tools 为空）
- per-call excluded_tools 机制：传入 FORBIDDEN_SUBAGENT_TOOLS 注册子代理专用 profile
- ``interrupt_on`` 从 ``DANGEROUS_TOOLS`` 动态生成
- ``memory=`` 自动加载 ``.agentx/AGENTS.md`` + ``rules/*.md``
- ``backend=`` 启用 ``AuthorizedLocalShellBackend``（继承 ``SafeLocalShellBackend``），
  提供 ``execute`` + 内置 fs 工具（带 SessionSandbox 动态授权）
- ``middleware=`` 注入 ``RubricMiddleware``（当 ``rubric=`` 非 None 时）
- 整体配置通过 ``create_agent`` 正确组装
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.deepagent.factory import (
    _registered_keys,
    build_interrupt_config,
    create_agent,
    ensure_harness_profile,
    resolve_backend,
    resolve_memory_paths,
    resolve_skills_sources,
)
from app.deepagent.tool_assembly import DANGEROUS_TOOLS
from app.security.dangerous_tools import FORBIDDEN_SUBAGENT_TOOLS


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


def test_harness_profile_default_enables_builtin_tools() -> None:
    """默认 HarnessProfile 启用全部内置 fs 工具（excluded_tools 为空）。

    Phase A 后内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由
    AuthorizedLocalShellBackend 自动注入，默认 profile 不排除任何工具。
    """
    with patch("app.deepagent.factory.register_harness_profile") as mock_register:
        ensure_harness_profile(None)
        assert mock_register.call_count == 1
        profile = mock_register.call_args[0][1]
        assert profile.excluded_tools == frozenset()


def test_harness_profile_subagent_excludes_forbidden_tools() -> None:
    """per-call excluded_tools 机制：传入 FORBIDDEN_SUBAGENT_TOOLS 注册子代理专用 profile。

    子代理无 interrupt_on 审批流，写/编辑/git/shell 工具必须通过 excluded_tools 隐藏。
    """
    with patch("app.deepagent.factory.register_harness_profile") as mock_register:
        ensure_harness_profile(FORBIDDEN_SUBAGENT_TOOLS)
        profile = mock_register.call_args[0][1]
        assert FORBIDDEN_SUBAGENT_TOOLS.issubset(profile.excluded_tools)
        # 至少包含 write_file / edit_file / execute
        assert "write_file" in profile.excluded_tools
        assert "edit_file" in profile.excluded_tools
        assert "execute" in profile.excluded_tools


def test_harness_profile_disables_default_subagent() -> None:
    """``HarnessProfile`` 禁用默认通用子代理，项目使用自研委派工具。"""
    with patch("app.deepagent.factory.register_harness_profile") as mock_register:
        ensure_harness_profile("openai")
        profile = mock_register.call_args[0][1]
        assert profile.general_purpose_subagent.enabled is False


def test_ensure_harness_profile_is_idempotent() -> None:
    """重复注册同一 key 不会再次调用 ``register_harness_profile``。"""
    with patch("app.deepagent.factory.register_harness_profile") as mock_register:
        ensure_harness_profile(None)
        ensure_harness_profile(None)
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


def test_resolve_skills_sources_returns_global_and_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``resolve_skills_sources`` 返回全局 ``DATA_DIR/skills`` + 工作区 ``.agentx/skills``。"""
    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    ws_skills = tmp_path / ".agentx" / "skills"
    ws_skills.mkdir(parents=True)
    sources = resolve_skills_sources(str(tmp_path))
    assert str(skills_dir) in sources
    assert str(ws_skills) in sources
    # 工作区在后（优先级更高）
    assert sources.index(str(ws_skills)) > sources.index(str(skills_dir))


def test_resolve_skills_sources_global_only_when_no_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """无 workspace 时仅返回全局 skills 目录。"""
    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    assert resolve_skills_sources(None) == [str(skills_dir)]


def test_resolve_skills_sources_empty_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """skills 目录不存在时返回空列表。"""
    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)
    assert resolve_skills_sources(None) == []
    assert resolve_skills_sources(str(tmp_path)) == []


def test_resolve_backend_returns_authorized_local_shell_backend(tmp_path: Path) -> None:
    """``resolve_backend`` 返回 ``AuthorizedLocalShellBackend`` 实例（继承 ``SafeLocalShellBackend`` → ``LocalShellBackend`` → ``FilesystemBackend``）。"""
    from deepagents.backends import FilesystemBackend, LocalShellBackend

    from app.deepagent.authorized_backend import AuthorizedLocalShellBackend
    from app.deepagent.safe_shell_backend import SafeLocalShellBackend

    backend = resolve_backend(str(tmp_path))
    assert isinstance(backend, AuthorizedLocalShellBackend)
    assert isinstance(backend, SafeLocalShellBackend)
    assert isinstance(backend, LocalShellBackend)
    assert isinstance(backend, FilesystemBackend)


def test_resolve_backend_none_when_no_workspace() -> None:
    """``workspace_path`` 为 None 时不启用 backend。"""
    assert resolve_backend(None) is None


# ============================================================
# create_agent 整体配置组装
# ============================================================


@pytest.mark.asyncio
async def test_create_agent_passes_correct_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``create_agent`` 正确组装所有 deepagents 参数，含 SkillsMiddleware。"""
    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    agentx_dir = tmp_path / ".agentx"
    agentx_dir.mkdir()
    (agentx_dir / "AGENTS.md").write_text("agents", encoding="utf-8")

    with patch("app.deepagent.factory.create_deep_agent") as mock_create:
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
        assert kwargs["backend"] is not None

        # 验证 SkillsMiddleware 已注入 middleware 列表
        from deepagents.middleware.skills import SkillsMiddleware
        middleware_types = [type(mw) for mw in kwargs["middleware"]]
        assert SkillsMiddleware in middleware_types

        # 验证 memory 非空
        assert len(kwargs["memory"]) == 1
        assert kwargs["memory"][0].endswith("AGENTS.md")


@pytest.mark.asyncio
async def test_create_agent_uses_default_name_and_none_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未传 name 时默认 ``deep_agent``；未传 workspace 时 backend 为 None。"""
    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)

    with patch("app.deepagent.factory.create_deep_agent") as mock_create:
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
        # 无 skills 目录时 middleware 为空（不含 SkillsMiddleware）
        assert kwargs["middleware"] == []


# ============================================================
# permissions= 已移除（deepagents 0.6+ 与 SafeLocalShellBackend 不兼容）
# 项目通过 SafeLocalShellBackend + SessionSandbox 替代框架级 permissions
# 以下测试已删除
# ============================================================


# ============================================================
# RubricMiddleware 注入
# ============================================================


@pytest.mark.asyncio
async def test_create_agent_rubric_injects_rubric_middleware(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``rubric=`` 非 None 时注入 ``RubricMiddleware`` 到 ``middleware`` 列表。"""
    from deepagents import RubricMiddleware

    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)
    fake_grader = MagicMock(name="auto_grader")
    with patch("app.deepagent.factory.create_deep_agent") as mock_create, \
         patch("app.deepagent.factory.get_chat_model", return_value=fake_grader) as mock_get_model:
        mock_create.return_value = MagicMock()
        create_agent(
            MagicMock(),
            [],
            system_prompt="prompt",
            workspace_path=str(tmp_path),
            rubric="answer must be concise and accurate",
        )
        _, kwargs = mock_create.call_args
        # middleware 含一个 RubricMiddleware 实例
        assert len(kwargs["middleware"]) == 1
        mw = kwargs["middleware"][0]
        assert isinstance(mw, RubricMiddleware)
        # max_iterations 默认 3
        assert mw.max_iterations == 3
        # 未传 grader_model 时调用 get_chat_model(temperature=0)
        mock_get_model.assert_called_once_with(temperature=0)


@pytest.mark.asyncio
async def test_create_agent_no_rubric_yields_skills_middleware(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``rubric=`` 为 None 时 ``middleware`` 仅含 SkillsMiddleware（skills 目录存在）。"""
    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    with patch("app.deepagent.factory.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()
        create_agent(
            MagicMock(),
            [],
            system_prompt="prompt",
            workspace_path=str(tmp_path),
            # rubric=None
        )
        _, kwargs = mock_create.call_args
        from deepagents.middleware.skills import SkillsMiddleware
        assert len(kwargs["middleware"]) == 1
        assert isinstance(kwargs["middleware"][0], SkillsMiddleware)


@pytest.mark.asyncio
async def test_create_agent_rubric_uses_grader_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``rubric=`` + ``grader_model=`` 时使用传入的 grader 而非 ``get_chat_model``。"""
    from deepagents import RubricMiddleware

    monkeypatch.setattr("app.deepagent.factory.DATA_DIR", tmp_path)
    fake_grader = MagicMock(name="custom_grader")

    with patch("app.deepagent.factory.create_deep_agent") as mock_create, \
         patch("app.deepagent.factory.get_chat_model") as mock_get_model:
        mock_create.return_value = MagicMock()
        create_agent(
            MagicMock(),
            [],
            system_prompt="prompt",
            workspace_path=str(tmp_path),
            rubric="any rubric",
            grader_model=fake_grader,
        )
        _, kwargs = mock_create.call_args
        mw = kwargs["middleware"][0]
        assert isinstance(mw, RubricMiddleware)
        # grader_model 应被直接使用，get_chat_model 不应被调用
        # （RubricMiddleware 将 model 存储为私有属性 _model，不依赖私有 API；
        # 通过 get_chat_model 未被调用来间接验证）
        mock_get_model.assert_not_called()


# ============================================================
# SafeLocalShellBackend 提供 execute 工具
# ============================================================


def test_safe_local_shell_backend_inherits_from_local_shell_backend() -> None:
    """``SafeLocalShellBackend`` 继承 ``LocalShellBackend`` → ``FilesystemBackend``。"""
    from deepagents.backends import FilesystemBackend, LocalShellBackend

    from app.deepagent.safe_shell_backend import SafeLocalShellBackend

    assert issubclass(SafeLocalShellBackend, LocalShellBackend)
    assert issubclass(SafeLocalShellBackend, FilesystemBackend)


def test_safe_local_shell_backend_execute_blocks_blocklisted_command(tmp_path: Path) -> None:
    """``SafeLocalShellBackend.execute`` 拦截黑名单命令（如 ``rm``）。"""
    from app.deepagent.safe_shell_backend import SafeLocalShellBackend
    from deepagents.backends.protocol import ExecuteResponse

    backend = SafeLocalShellBackend(root_dir=str(tmp_path), virtual_mode=True)
    result = backend.execute("rm -rf /")
    assert isinstance(result, ExecuteResponse)
    assert result.exit_code == 126
    assert "黑名单" in result.output


def test_safe_local_shell_backend_execute_blocks_metachar(tmp_path: Path) -> None:
    """``SafeLocalShellBackend.execute`` 拦截 shell 元字符（命令链/管道/重定向）。"""
    from app.deepagent.safe_shell_backend import SafeLocalShellBackend
    from deepagents.backends.protocol import ExecuteResponse

    backend = SafeLocalShellBackend(root_dir=str(tmp_path), virtual_mode=True)
    # ; 是命令分隔符
    result = backend.execute("echo hello; rm -rf /")
    assert isinstance(result, ExecuteResponse)
    assert result.exit_code == 126
    assert "元字符" in result.output or "非法" in result.output


def test_safe_local_shell_backend_execute_blocks_empty_command(tmp_path: Path) -> None:
    """``SafeLocalShellBackend.execute`` 拒绝空命令。"""
    from app.deepagent.safe_shell_backend import SafeLocalShellBackend
    from deepagents.backends.protocol import ExecuteResponse

    backend = SafeLocalShellBackend(root_dir=str(tmp_path), virtual_mode=True)
    result = backend.execute("")
    assert isinstance(result, ExecuteResponse)
    assert result.exit_code == 1
    assert result.output == "command 不能为空"
    result2 = backend.execute("   ")
    assert isinstance(result2, ExecuteResponse)
    assert result2.exit_code == 1
    assert result2.output == "command 不能为空"


# ============================================================
# 内置 fs 工具启用验证
# ============================================================


def test_builtin_fs_tools_enabled_by_default() -> None:
    """默认 profile 不排除任何内置 fs 工具，确保 LLM 可用 ls/read_file/write_file/edit_file/glob/grep。

    Phase A 后这些工具由 AuthorizedLocalShellBackend 提供（带 SessionSandbox 动态授权），
    不再需要手动排除以避免与自研工具冲突——自研 fs 工具已删除。
    """
    with patch("app.deepagent.factory.register_harness_profile") as mock_register:
        ensure_harness_profile(None)
        profile = mock_register.call_args[0][1]
        builtin_fs = {"ls", "read_file", "write_file", "edit_file", "glob", "grep"}
        # 默认 profile 不排除任何内置 fs 工具
        assert not (builtin_fs & profile.excluded_tools)


def test_dangerous_tools_does_not_contain_execute() -> None:
    """``execute`` 已从 ``DANGEROUS_TOOLS`` 中移除，审批改为 directory_extension 机制。"""
    assert "execute" not in DANGEROUS_TOOLS
    # cli_execute 也不在 DANGEROUS_TOOLS 中
    assert "cli_execute" not in DANGEROUS_TOOLS
