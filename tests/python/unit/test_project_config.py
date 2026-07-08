"""``.agentx/`` 项目级配置目录单元测试。

覆盖 generator / loader / merger 全链路。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.project_config.generator import generate_agentx_dir
from app.project_config.loader import ProjectConfig, load_project_config
from app.project_config.merger import MergedConfig, merge_configs
from app.project_config.templates import TEMPLATES


# ============================================================
# Generator 测试
# ============================================================


class TestGenerator:
    """``generate_agentx_dir`` 测试。"""

    def test_generate_creates_all_files(self, tmp_path: Path) -> None:
        """首次生成应创建所有模板文件。"""
        result = generate_agentx_dir(tmp_path)

        assert result.path == str(tmp_path / ".agentx")
        # 应创建所有模板文件
        assert len(result.created) == len(TEMPLATES)
        assert len(result.skipped) == 0
        # 验证文件实际存在
        for rel_path in TEMPLATES:
            assert (tmp_path / ".agentx" / rel_path).exists()

    def test_generate_idempotent(self, tmp_path: Path) -> None:
        """第二次调用应跳过所有文件（created=[]）。"""
        generate_agentx_dir(tmp_path)
        result = generate_agentx_dir(tmp_path)

        assert len(result.created) == 0
        assert len(result.skipped) == len(TEMPLATES)

    def test_generate_skips_existing_user_edited(self, tmp_path: Path) -> None:
        """已编辑的文件不被覆盖。"""
        # 预创建一个用户编辑过的 AGENTS.md
        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()
        user_content = "# My Custom Rules\n\n- Use snake_case"
        (agentx_dir / "AGENTS.md").write_text(user_content, encoding="utf-8")

        result = generate_agentx_dir(tmp_path)

        # AGENTS.md 应被跳过
        assert "AGENTS.md" in result.skipped
        assert "AGENTS.md" not in result.created
        # 内容未被覆盖
        assert (agentx_dir / "AGENTS.md").read_text(encoding="utf-8") == user_content
        # 其他文件仍被创建
        assert "mcp.json" in result.created

    def test_generate_creates_rules_dir(self, tmp_path: Path) -> None:
        """应创建 rules/ 目录及 README.md。"""
        generate_agentx_dir(tmp_path)

        rules_dir = tmp_path / ".agentx" / "rules"
        assert rules_dir.is_dir()
        assert (rules_dir / "README.md").exists()

    def test_generate_nonexistent_workspace(self, tmp_path: Path) -> None:
        """workspace 不存在应抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            generate_agentx_dir(tmp_path / "nonexistent")

    def test_generate_file_not_directory(self, tmp_path: Path) -> None:
        """workspace 是文件不是目录应抛 NotADirectoryError。"""
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello")
        with pytest.raises(NotADirectoryError):
            generate_agentx_dir(file_path)


# ============================================================
# Loader 测试
# ============================================================


class TestLoader:
    """``load_project_config`` 测试。"""

    def test_load_missing_dir(self, tmp_path: Path) -> None:
        """`.agentx/` 不存在时返回 exists=False。"""
        config = load_project_config(tmp_path)

        assert config.exists is False
        assert config.agents_md is None
        assert config.rules == []
        assert config.mcp_servers == []
        assert config.subagents_config == {}
        assert config.tools_config == {}
        assert config.system_prompt is None

    def test_load_after_generate(self, tmp_path: Path) -> None:
        """生成后加载应返回有效配置。"""
        generate_agentx_dir(tmp_path)
        config = load_project_config(tmp_path)

        assert config.exists is True
        assert config.agents_md is not None
        assert "# Project AGENTS.md" in config.agents_md
        assert config.mcp_servers == []  # 模板是空数组
        assert config.subagents_config == {}  # 模板是空对象
        assert config.tools_config == {}
        assert config.system_prompt is not None
        # rules/README.md 不被加载为 rule（它是说明文档而非规则）
        assert len(config.rules) == 0

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        """JSON 语法错误应降级为空配置，不抛异常。"""
        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()

        # 写入格式错误的 JSON
        (agentx_dir / "mcp.json").write_text("{invalid json}", encoding="utf-8")
        (agentx_dir / "subagents.json").write_text("not a json", encoding="utf-8")
        (agentx_dir / "tools.json").write_text("}{", encoding="utf-8")

        config = load_project_config(tmp_path)

        assert config.exists is True
        assert config.mcp_servers == []  # 降级为空
        assert config.subagents_config == {}  # 降级为空
        assert config.tools_config == {}  # 降级为空

    def test_load_valid_mcp_servers(self, tmp_path: Path) -> None:
        """有效 MCP servers 配置正确加载。"""
        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()

        mcp_config = [
            {"name": "github", "transport": "stdio", "command": "npx", "args": ["-y", "@mcp/github"]},
        ]
        (agentx_dir / "mcp.json").write_text(json.dumps(mcp_config), encoding="utf-8")

        config = load_project_config(tmp_path)

        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0]["name"] == "github"

    def test_load_valid_subagents(self, tmp_path: Path) -> None:
        """有效子代理配置正确加载。"""
        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()

        subagents = {"rag": {"enabled": False, "temperature": 0.5}}
        (agentx_dir / "subagents.json").write_text(json.dumps(subagents), encoding="utf-8")

        config = load_project_config(tmp_path)

        assert config.subagents_config == subagents

    def test_load_valid_tools(self, tmp_path: Path) -> None:
        """有效工具开关正确加载。"""
        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()

        tools = {"web_search": False, "rag_retrieve": True}
        (agentx_dir / "tools.json").write_text(json.dumps(tools), encoding="utf-8")

        config = load_project_config(tmp_path)

        assert config.tools_config == tools

    def test_load_rules(self, tmp_path: Path) -> None:
        """rules/*.md 文件正确加载。"""
        agentx_dir = tmp_path / ".agentx"
        rules_dir = agentx_dir / "rules"
        rules_dir.mkdir(parents=True)

        (rules_dir / "coding-style.md").write_text("# Coding Style\n\n- Use 4 spaces", encoding="utf-8")
        (rules_dir / "testing.md").write_text("# Testing\n\n- Write tests first", encoding="utf-8")

        config = load_project_config(tmp_path)

        assert len(config.rules) == 2
        # 按文件名排序
        assert config.rules[0].name == "coding-style"
        assert config.rules[1].name == "testing"

    def test_load_rules_limit(self, tmp_path: Path) -> None:
        """超过 10 个规则文件只加载前 10 个。"""
        agentx_dir = tmp_path / ".agentx"
        rules_dir = agentx_dir / "rules"
        rules_dir.mkdir(parents=True)

        for i in range(15):
            (rules_dir / f"rule-{i:02d}.md").write_text(f"# Rule {i}", encoding="utf-8")

        config = load_project_config(tmp_path)

        assert len(config.rules) == 10

    def test_context_prompt(self, tmp_path: Path) -> None:
        """context_prompt 正确拼接 AGENTS.md + rules。"""
        agentx_dir = tmp_path / ".agentx"
        rules_dir = agentx_dir / "rules"
        rules_dir.mkdir(parents=True)

        (agentx_dir / "AGENTS.md").write_text("# My Project Rules", encoding="utf-8")
        (rules_dir / "style.md").write_text("Use TypeScript", encoding="utf-8")

        config = load_project_config(tmp_path)

        prompt = config.context_prompt
        assert "# My Project Rules" in prompt
        assert "## style" in prompt
        assert "Use TypeScript" in prompt

    def test_context_prompt_empty(self, tmp_path: Path) -> None:
        """无 AGENTS.md 且无 rules 时 context_prompt 为空。"""
        config = load_project_config(tmp_path)
        assert config.context_prompt == ""

    def test_load_rules_excludes_readme(self, tmp_path: Path) -> None:
        """rules/README.md 不被加载为 rule（它是说明文档）。"""
        agentx_dir = tmp_path / ".agentx"
        rules_dir = agentx_dir / "rules"
        rules_dir.mkdir(parents=True)

        (rules_dir / "README.md").write_text("# Rules 目录说明", encoding="utf-8")
        (rules_dir / "coding-style.md").write_text("- Use 4 spaces", encoding="utf-8")

        config = load_project_config(tmp_path)

        # 只有 coding-style 被加载，README.md 被排除
        assert len(config.rules) == 1
        assert config.rules[0].name == "coding-style"

    def test_load_rules_rejects_symlink(self, tmp_path: Path) -> None:
        """rules/ 下的符号链接被拒绝（防止泄漏敏感文件）。"""
        agentx_dir = tmp_path / ".agentx"
        rules_dir = agentx_dir / "rules"
        rules_dir.mkdir(parents=True)

        # 创建一个指向外部敏感文件的符号链接
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("SECRET_CONTENT", encoding="utf-8")
        try:
            (rules_dir / "leak.md").symlink_to(secret_file)
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不支持创建符号链接")

        config = load_project_config(tmp_path)

        # symlink 被拒绝，rules 为空
        assert len(config.rules) == 0

    def test_load_agents_md_symlink_rejected(self, tmp_path: Path) -> None:
        """AGENTS.md 符号链接被拒绝（防止泄漏敏感文件）。"""
        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()

        secret_file = tmp_path / "secret.md"
        secret_file.write_text("SECRET_AGENTS", encoding="utf-8")
        try:
            (agentx_dir / "AGENTS.md").symlink_to(secret_file)
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不支持创建符号链接")

        config = load_project_config(tmp_path)

        # symlink 被拒绝，agents_md 为 None
        assert config.agents_md is None

    def test_load_agents_md_truncated(self, tmp_path: Path) -> None:
        """AGENTS.md 超过 32KB 应被截断。"""
        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()

        # 写入 40KB 内容
        large_content = "# Large AGENTS.md\n\n" + ("x" * 40 * 1024)
        (agentx_dir / "AGENTS.md").write_text(large_content, encoding="utf-8")

        config = load_project_config(tmp_path)

        assert config.agents_md is not None
        assert "[truncated]" in config.agents_md
        # 截断后应小于原始大小
        assert len(config.agents_md) < len(large_content)

    def test_load_system_prompt_truncated(self, tmp_path: Path) -> None:
        """system_prompt.md 超过 32KB 应被截断。"""
        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()

        large_content = "# Large Prompt\n\n" + ("y" * 40 * 1024)
        (agentx_dir / "system_prompt.md").write_text(large_content, encoding="utf-8")

        config = load_project_config(tmp_path)

        assert config.system_prompt is not None
        assert "[truncated]" in config.system_prompt
        assert len(config.system_prompt) < len(large_content)


# ============================================================
# Merger 测试
# ============================================================


class TestMerger:
    """``merge_configs`` 测试。"""

    def test_merge_no_project(self) -> None:
        """project=None 时等价于原 Settings。"""
        from app.config import get_settings

        settings = get_settings()
        merged = merge_configs(settings, None)

        assert merged.has_project_config is False
        assert merged.context_prompt == ""
        assert merged.default_system_prompt == settings.default_system_prompt
        assert merged.mcp_servers_config == settings.mcp_servers_config
        assert merged.tools_enabled == settings.tools_enabled

    def test_merge_nonexistent_project(self, tmp_path: Path) -> None:
        """project.exists=False 时等价于原 Settings。"""
        from app.config import get_settings

        config = load_project_config(tmp_path)  # .agentx/ 不存在
        settings = get_settings()
        merged = merge_configs(settings, config)

        assert merged.has_project_config is False
        assert merged.context_prompt == ""

    def test_merge_mcp_servers_append(self, tmp_path: Path) -> None:
        """MCP servers 追加模式（全局 + 项目，按 name 去重）。"""
        from app.config import get_settings

        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()
        mcp_config = [
            {"name": "project-server", "transport": "stdio", "command": "node"},
        ]
        (agentx_dir / "mcp.json").write_text(json.dumps(mcp_config), encoding="utf-8")

        config = load_project_config(tmp_path)
        settings = get_settings()
        merged = merge_configs(settings, config)

        # 项目 server 应在结果中
        names = [s.get("name") for s in merged.mcp_servers_config if isinstance(s, dict)]
        assert "project-server" in names

    def test_merge_mcp_servers_dedup(self, tmp_path: Path) -> None:
        """MCP servers 按 name 去重（项目优先）。"""
        from app.config import get_settings

        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()
        # 项目配置中有同 name 的 server
        mcp_config = [
            {"name": "existing", "transport": "stdio", "command": "project-cmd"},
        ]
        (agentx_dir / "mcp.json").write_text(json.dumps(mcp_config), encoding="utf-8")

        config = load_project_config(tmp_path)
        settings = get_settings()

        # 模拟全局已有同 name 的 server
        original = settings.mcp_servers_config
        try:
            # monkey-patch settings 的 mcp_servers_config
            settings.__dict__["mcp_servers_config"] = [
                {"name": "existing", "transport": "stdio", "command": "global-cmd"},
                {"name": "global-only", "transport": "stdio", "command": "global-cmd2"},
            ]
            merged = merge_configs(settings, config)

            # 项目优先：existing 的 command 应为 project-cmd
            existing_servers = [s for s in merged.mcp_servers_config if s.get("name") == "existing"]
            assert len(existing_servers) == 1
            assert existing_servers[0]["command"] == "project-cmd"
            # 全局独有的保留
            global_only = [s for s in merged.mcp_servers_config if s.get("name") == "global-only"]
            assert len(global_only) == 1
        finally:
            settings.__dict__["mcp_servers_config"] = original

    def test_merge_tools_override(self, tmp_path: Path) -> None:
        """工具开关覆盖模式。"""
        from app.config import get_settings

        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()
        tools = {"web_search": False}
        (agentx_dir / "tools.json").write_text(json.dumps(tools), encoding="utf-8")

        config = load_project_config(tmp_path)
        settings = get_settings()
        merged = merge_configs(settings, config)

        # 项目的 web_search=False 应覆盖全局
        assert merged.tools_enabled["web_search"] is False

    def test_merge_system_prompt_prepend(self, tmp_path: Path) -> None:
        """系统提示词前置模式。"""
        from app.config import get_settings

        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()
        project_prompt = "# Project Instructions\n\nAlways use TypeScript."
        (agentx_dir / "system_prompt.md").write_text(project_prompt, encoding="utf-8")

        config = load_project_config(tmp_path)
        settings = get_settings()
        merged = merge_configs(settings, config)

        # 项目提示词应前置
        assert merged.default_system_prompt.startswith(project_prompt)
        assert settings.default_system_prompt in merged.default_system_prompt

    def test_merge_context_prompt(self, tmp_path: Path) -> None:
        """context_prompt 从项目配置获取。"""
        from app.config import get_settings

        agentx_dir = tmp_path / ".agentx"
        rules_dir = agentx_dir / "rules"
        rules_dir.mkdir(parents=True)

        (agentx_dir / "AGENTS.md").write_text("# Project Rules", encoding="utf-8")
        (rules_dir / "style.md").write_text("Use 4 spaces", encoding="utf-8")

        config = load_project_config(tmp_path)
        settings = get_settings()
        merged = merge_configs(settings, config)

        assert merged.has_project_config is True
        assert "# Project Rules" in merged.context_prompt
        assert "## style" in merged.context_prompt

    def test_merge_credentials_not_affected(self, tmp_path: Path) -> None:
        """凭证不受项目配置影响（安全红线）。"""
        from app.config import get_settings

        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()
        # 即使项目配置有内容，凭证应来自全局
        (agentx_dir / "mcp.json").write_text("[]", encoding="utf-8")
        (agentx_dir / "subagents.json").write_text("{}", encoding="utf-8")

        config = load_project_config(tmp_path)
        settings = get_settings()
        merged = merge_configs(settings, config)

        # 凭证属性应透传 base
        assert merged.openai_api_key == settings.openai_api_key
        assert merged.default_model == settings.default_model

    def test_merge_subagents_deep_merge(self, tmp_path: Path) -> None:
        """子代理配置深合并：嵌套 dict 递归合并而非整体替换。"""
        from app.config import get_settings

        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()
        # 项目配置覆盖 rag 子代理的 enabled 字段
        subagents = {"rag": {"enabled": False}}
        (agentx_dir / "subagents.json").write_text(
            json.dumps(subagents), encoding="utf-8"
        )

        config = load_project_config(tmp_path)
        settings = get_settings()
        merged = merge_configs(settings, config)

        # rag 子代理应存在且 enabled=False（项目覆盖）
        assert "rag" in merged.subagents
        assert merged.subagents["rag"].enabled is False
        # 其他字段应保留全局默认值（深合并，非整体替换）
        base_rag = settings.subagents.get("rag")
        if base_rag is not None:
            # 非被覆盖字段应与全局一致
            assert merged.subagents["rag"].model_dump().keys() >= base_rag.model_dump().keys()

    def test_merge_subagents_invalid_field_fallback(self, tmp_path: Path) -> None:
        """子代理配置含非法字段时降级为保留全局配置 + warning。"""
        from app.config import get_settings

        agentx_dir = tmp_path / ".agentx"
        agentx_dir.mkdir()
        # 写入非法字段（temperature 应为 float，传入字符串）
        subagents = {"rag": {"temperature": "not-a-number"}}
        (agentx_dir / "subagents.json").write_text(
            json.dumps(subagents), encoding="utf-8"
        )

        config = load_project_config(tmp_path)
        settings = get_settings()
        # 不应抛异常
        merged = merge_configs(settings, config)

        # rag 子代理应保留全局配置（降级处理）
        assert "rag" in merged.subagents
        base_rag = settings.subagents.get("rag")
        if base_rag is not None:
            # 应退化为全局值
            assert merged.subagents["rag"].temperature == base_rag.temperature
