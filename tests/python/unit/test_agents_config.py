"""场景化智能体配置（AgentsConfig）单元测试。

覆盖 task 1.5：
1. SupervisorSettings / ExpertSettings / ScenarioTeamSettings 模型校验
2. _default_agents_config 默认值
3. _parse_agents_config 从 env JSON 解析（合并/覆盖/新增/异常容错）
4. Settings.agents property 透传
5. BUILTIN_EXPERT_KEYS / BUILTIN_TEAM_KEYS 常量
6. coding_team_enabled property
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.config.agents import (
    BUILTIN_EXPERT_KEYS,
    BUILTIN_TEAM_KEYS,
    AgentsConfig,
    ExpertSettings,
    ScenarioTeamSettings,
    SupervisorSettings,
    _DEFAULT_SUPERVISOR_TOOLS,
    _default_agents_config,
    _parse_agents_config,
)
from app.config.prompts.agent import (
    _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT,
    _DEFAULT_SUPERVISOR_SYSTEM_PROMPT,
)


# ============================================================
# 1. 模型校验
# ============================================================


class TestSupervisorSettings:
    """SupervisorSettings 字段校验。"""

    def test_default_values(self) -> None:
        """默认值：enabled=True, temperature=0.3, system_prompt='', tools 非空。"""
        s = SupervisorSettings()
        assert s.enabled is True
        assert s.temperature == 0.3
        assert s.system_prompt == ""
        assert "read_file" in s.tools
        assert "write_file" in s.tools
        assert "execute" in s.tools
        assert "git_commit" in s.tools
        assert "rag_retrieve" in s.tools
        assert "web_search" in s.tools

    def test_temperature_clamp_low(self) -> None:
        """temperature < 0 报错。"""
        with pytest.raises(ValidationError):
            SupervisorSettings(temperature=-0.1)

    def test_temperature_clamp_high(self) -> None:
        """temperature > 2.0 报错。"""
        with pytest.raises(ValidationError):
            SupervisorSettings(temperature=2.1)

    def test_custom_tools(self) -> None:
        """自定义 tools 列表覆盖默认。"""
        s = SupervisorSettings(tools=["read_file", "glob"])
        assert s.tools == ["read_file", "glob"]


class TestExpertSettings:
    """ExpertSettings 字段校验。"""

    def test_default_values(self) -> None:
        """默认值：enabled=True, temperature=0.2, scenario=''。"""
        e = ExpertSettings()
        assert e.enabled is True
        assert e.temperature == 0.2
        assert e.scenario == ""
        assert "read_file" in e.tools
        assert "write_file" in e.tools

    def test_temperature_clamp(self) -> None:
        """temperature 边界值校验。"""
        ExpertSettings(temperature=0.0)
        ExpertSettings(temperature=2.0)
        with pytest.raises(ValidationError):
            ExpertSettings(temperature=-0.01)
        with pytest.raises(ValidationError):
            ExpertSettings(temperature=2.01)

    def test_scenario_field(self) -> None:
        """scenario 字段可自定义。"""
        e = ExpertSettings(scenario="research")
        assert e.scenario == "research"


class TestScenarioTeamSettings:
    """ScenarioTeamSettings 字段校验。"""

    def test_default_values(self) -> None:
        """默认值与原 AgentTeam 配置一致。"""
        t = ScenarioTeamSettings()
        assert t.enabled is True
        assert t.max_tasks == 5
        assert t.max_parallel == 3
        assert t.result_max_chars == 2000
        assert t.subtask_timeout == 300

    def test_max_tasks_clamp(self) -> None:
        """max_tasks 边界 1-10。"""
        ScenarioTeamSettings(max_tasks=1)
        ScenarioTeamSettings(max_tasks=10)
        with pytest.raises(ValidationError):
            ScenarioTeamSettings(max_tasks=0)
        with pytest.raises(ValidationError):
            ScenarioTeamSettings(max_tasks=11)

    def test_max_parallel_clamp(self) -> None:
        """max_parallel 边界 1-5。"""
        ScenarioTeamSettings(max_parallel=1)
        ScenarioTeamSettings(max_parallel=5)
        with pytest.raises(ValidationError):
            ScenarioTeamSettings(max_parallel=0)
        with pytest.raises(ValidationError):
            ScenarioTeamSettings(max_parallel=6)

    def test_subtask_timeout_clamp(self) -> None:
        """subtask_timeout 边界 30-1800。"""
        ScenarioTeamSettings(subtask_timeout=30)
        ScenarioTeamSettings(subtask_timeout=1800)
        with pytest.raises(ValidationError):
            ScenarioTeamSettings(subtask_timeout=29)
        with pytest.raises(ValidationError):
            ScenarioTeamSettings(subtask_timeout=1801)


# ============================================================
# 2. _default_agents_config 默认值
# ============================================================


class TestDefaultAgentsConfig:
    """_default_agents_config 返回值校验。"""

    def test_supervisor_has_default_prompt(self) -> None:
        """Supervisor 默认 system_prompt 来自 prompts/agent.py。"""
        cfg = _default_agents_config()
        assert cfg.supervisor.system_prompt == _DEFAULT_SUPERVISOR_SYSTEM_PROMPT

    def test_coding_expert_exists(self) -> None:
        """默认配置含 coding Expert，prompt 来自 prompts/agent.py。"""
        cfg = _default_agents_config()
        assert "coding" in cfg.experts
        coding = cfg.experts["coding"]
        assert coding.system_prompt == _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT
        assert coding.scenario == "coding"

    def test_coding_team_exists(self) -> None:
        """默认配置含 coding team，enabled=True。"""
        cfg = _default_agents_config()
        assert "coding" in cfg.teams
        assert cfg.teams["coding"].enabled is True

    def test_coding_team_enabled_property(self) -> None:
        """coding_team_enabled property 默认 True。"""
        cfg = _default_agents_config()
        assert cfg.coding_team_enabled is True

    def test_coding_team_disabled(self) -> None:
        """coding team.enabled=False 时 coding_team_enabled=False。"""
        cfg = _default_agents_config()
        cfg.teams["coding"].enabled = False
        assert cfg.coding_team_enabled is False

    def test_coding_team_missing(self) -> None:
        """teams 中无 coding 时 coding_team_enabled=False。"""
        cfg = AgentsConfig()  # 空配置
        assert cfg.coding_team_enabled is False

    def test_supervisor_tools_complete(self) -> None:
        """Supervisor 默认工具集含 fs/cli/git/rag/web 全部。"""
        cfg = _default_agents_config()
        tools = set(cfg.supervisor.tools)
        # fs
        assert {"read_file", "list_dir", "glob", "grep", "write_file", "edit_file"} <= tools
        # cli
        assert "execute" in tools
        assert "cli_execute" not in tools
        # git
        assert {
            "git_status",
            "git_diff",
            "git_log",
            "git_branches",
            "git_clone",
            "git_pull",
            "git_checkout",
            "git_stage",
            "git_commit",
        } <= tools
        # rag + web
        assert {"rag_retrieve", "web_search"} <= tools

    def test_supervisor_tools_align_with_dangerous_tools(self) -> None:
        """Supervisor 默认工具集与 DANGEROUS_TOOLS 命名一致。"""
        from app.security.dangerous_tools import DANGEROUS_TOOLS

        tools = set(_DEFAULT_SUPERVISOR_TOOLS)
        assert DANGEROUS_TOOLS & tools == {
            "execute",
            "write_file",
            "edit_file",
            "git_clone",
            "git_pull",
            "git_checkout",
            "git_stage",
            "git_commit",
        }

    def test_coding_expert_tools_complete(self) -> None:
        """coding Expert 默认工具集与 Supervisor 一致。"""
        cfg = _default_agents_config()
        assert set(cfg.experts["coding"].tools) == set(cfg.supervisor.tools)


# ============================================================
# 3. _parse_agents_config env JSON 解析
# ============================================================


class TestParseAgentsConfig:
    """_parse_agents_config 解析逻辑。"""

    def test_empty_returns_default(self) -> None:
        """空 dict / None / 非 dict 返回默认配置。"""
        cfg = _parse_agents_config({})
        assert cfg.supervisor.system_prompt == _DEFAULT_SUPERVISOR_SYSTEM_PROMPT
        cfg = _parse_agents_config(None)
        assert cfg.supervisor.system_prompt == _DEFAULT_SUPERVISOR_SYSTEM_PROMPT
        cfg = _parse_agents_config("not a dict")
        assert cfg.supervisor.system_prompt == _DEFAULT_SUPERVISOR_SYSTEM_PROMPT

    def test_supervisor_override(self) -> None:
        """env 覆盖 supervisor 字段。"""
        cfg = _parse_agents_config({
            "supervisor": {"temperature": 0.5, "system_prompt": "custom"},
        })
        assert cfg.supervisor.temperature == 0.5
        assert cfg.supervisor.system_prompt == "custom"
        # 未覆盖字段保持默认
        assert "read_file" in cfg.supervisor.tools

    def test_expert_override(self) -> None:
        """env 覆盖 coding Expert 字段。"""
        cfg = _parse_agents_config({
            "experts": {"coding": {"temperature": 0.4, "system_prompt": "custom coding"}},
        })
        assert cfg.experts["coding"].temperature == 0.4
        assert cfg.experts["coding"].system_prompt == "custom coding"
        # scenario 字段保留
        assert cfg.experts["coding"].scenario == "coding"

    def test_team_override(self) -> None:
        """env 覆盖 coding team 字段。"""
        cfg = _parse_agents_config({
            "teams": {"coding": {"enabled": False, "max_tasks": 8}},
        })
        assert cfg.teams["coding"].enabled is False
        assert cfg.teams["coding"].max_tasks == 8
        assert cfg.coding_team_enabled is False

    def test_custom_expert_added(self) -> None:
        """env 新增自定义 Expert（未来扩展 research）。"""
        cfg = _parse_agents_config({
            "experts": {"research": {"system_prompt": "research expert", "scenario": "research"}},
        })
        # 默认 coding Expert 仍在
        assert "coding" in cfg.experts
        # 新增 research Expert
        assert "research" in cfg.experts
        assert cfg.experts["research"].scenario == "research"

    def test_custom_team_added(self) -> None:
        """env 新增自定义 team。"""
        cfg = _parse_agents_config({
            "teams": {"research": {"enabled": True, "max_tasks": 3}},
        })
        assert "coding" in cfg.teams
        assert "research" in cfg.teams
        assert cfg.teams["research"].max_tasks == 3

    def test_invalid_expert_value_skipped(self) -> None:
        """非法 expert value（非 dict）跳过不报错。"""
        cfg = _parse_agents_config({
            "experts": {"coding": "not a dict"},
        })
        # coding Expert 保持默认
        assert cfg.experts["coding"].system_prompt == _DEFAULT_CODING_EXPERT_SYSTEM_PROMPT

    def test_invalid_team_value_skipped(self) -> None:
        """非法 team value（非 dict）跳过不报错。"""
        cfg = _parse_agents_config({
            "teams": {"coding": "invalid"},
        })
        assert cfg.teams["coding"].enabled is True  # 默认

    def test_invalid_supervisor_value_ignored(self) -> None:
        """非法 supervisor value（非 dict 或空 dict）忽略。"""
        cfg = _parse_agents_config({
            "supervisor": "invalid",
        })
        # supervisor 保持默认
        assert cfg.supervisor.system_prompt == _DEFAULT_SUPERVISOR_SYSTEM_PROMPT

    def test_invalid_json_fallback(self) -> None:
        """整体解析异常时返回默认配置（容错）。"""
        # 构造一个会让 SupervisorSettings 报错的配置
        cfg = _parse_agents_config({
            "supervisor": {"temperature": "not a number"},
        })
        # 应该 fallback 到默认
        assert cfg.supervisor.system_prompt == _DEFAULT_SUPERVISOR_SYSTEM_PROMPT
        assert cfg.supervisor.temperature == 0.3


# ============================================================
# 4. Settings.agents property 透传
# ============================================================


class TestSettingsAgentsProperty:
    """Settings.agents property 透传 _parse_agents_config。"""

    def test_default_agents_property(self) -> None:
        """Settings 默认 agents property 返回默认 AgentsConfig。"""
        from app.config.settings import Settings

        s = Settings()
        assert isinstance(s.agents, AgentsConfig)
        assert s.agents.supervisor.system_prompt == _DEFAULT_SUPERVISOR_SYSTEM_PROMPT
        assert "coding" in s.agents.experts
        assert s.agents.coding_team_enabled is True

    def test_agents_config_from_env_json_string(self) -> None:
        """AGENTX_AGENTS_CONFIG JSON 字符串被正确解析。"""
        from app.config.settings import Settings

        env_json = json.dumps({
            "supervisor": {"temperature": 0.6},
            "experts": {"coding": {"temperature": 0.1}},
        })
        with patch.dict("os.environ", {"AGENTX_AGENTS_CONFIG": env_json}):
            s = Settings()
            assert s.agents.supervisor.temperature == 0.6
            assert s.agents.experts["coding"].temperature == 0.1

    def test_agents_config_invalid_json_raises(self) -> None:
        """AGENTX_AGENTS_CONFIG 非法 JSON 字符串 pydantic-settings 抛 SettingsError。

        这是 pydantic-settings 对 dict 字段的统一行为（与 subagents_config 等一致），
        由 _parse_agents_config 的容错逻辑处理非 dict 输入（见 test_invalid_json_fallback）。
        """
        from pydantic_settings.exceptions import SettingsError

        from app.config.settings import Settings

        with patch.dict("os.environ", {"AGENTX_AGENTS_CONFIG": "not json{"}):
            with pytest.raises(SettingsError):
                Settings()

    def test_agents_property_reparses_each_call(self) -> None:
        """agents property 每次调用重新解析（env 变化即时生效）。"""
        from app.config.settings import Settings

        s = Settings()
        original_temp = s.agents.supervisor.temperature
        assert original_temp == 0.3

        # 修改 agents_config 字段，property 应反映新值
        s.agents_config = {"supervisor": {"temperature": 0.9}}
        assert s.agents.supervisor.temperature == 0.9


# ============================================================
# 5. 常量校验
# ============================================================


class TestBuiltinKeys:
    """BUILTIN_EXPERT_KEYS / BUILTIN_TEAM_KEYS 常量。"""

    def test_builtin_expert_keys(self) -> None:
        """BUILTIN_EXPERT_KEYS 当前含 coding（未来扩展 research/trading）。"""
        assert "coding" in BUILTIN_EXPERT_KEYS
        assert isinstance(BUILTIN_EXPERT_KEYS, frozenset)

    def test_builtin_team_keys(self) -> None:
        """BUILTIN_TEAM_KEYS 当前含 coding。"""
        assert "coding" in BUILTIN_TEAM_KEYS
        assert isinstance(BUILTIN_TEAM_KEYS, frozenset)

    def test_keys_immutable(self) -> None:
        """frozenset 不可变。"""
        with pytest.raises(AttributeError):
            BUILTIN_EXPERT_KEYS.add("research")  # type: ignore[attr-defined]
