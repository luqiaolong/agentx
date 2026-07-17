"""_build_subagents + _build_team_role_subagents 单元测试（REQ-CP-5）。

覆盖：
- include_team_roles=False：仅返回 rag/web（历史行为）
- include_team_roles=True：追加 7 个团队角色子代理
- coding_complexity_team_roles_enabled=False：_build_team_role_subagents 返回空列表
- build_coding_expert 签名含 force_todo / include_team_roles
- _build_subagents 签名含 include_team_roles
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# _build_subagents 团队角色注入
# ============================================================


class TestBuildSubagentsTeamRoles:
    """_build_subagents 团队角色注入。"""

    def test_include_team_roles_false_returns_only_rag_web(self) -> None:
        """include_team_roles=False 时仅返回 rag/web（历史行为）。"""
        from app.scenarios.coding.agent import _build_subagents

        with patch("app.scenarios.coding.agent.make_rag_tools", return_value=[]), \
             patch("app.scenarios.coding.agent.make_web_tools", return_value=[]):
            subagents = _build_subagents("test-thread", include_team_roles=False)

        names = {s["name"] for s in subagents}
        assert "rag" in names
        assert "web" in names
        # 不含任何团队角色
        team_roles = {
            "frontend_dev", "backend_dev", "tester",
            "architect", "devops", "ui_designer", "product_manager",
        }
        assert not (names & team_roles)

    def test_include_team_roles_true_adds_team_roles(self) -> None:
        """include_team_roles=True 时追加 7 个团队角色子代理（默认开关开启）。"""
        from app.scenarios.coding.agent import _build_subagents

        with patch("app.scenarios.coding.agent.make_rag_tools", return_value=[]), \
             patch("app.scenarios.coding.agent.make_web_tools", return_value=[]), \
             patch("app.subagents.custom_agent._make_custom_tools", return_value=[]):
            subagents = _build_subagents("test-thread", include_team_roles=True)

        names = {s["name"] for s in subagents}
        assert "rag" in names
        assert "web" in names
        expected_team_roles = {
            "frontend_dev", "backend_dev", "tester",
            "architect", "devops", "ui_designer", "product_manager",
        }
        assert expected_team_roles.issubset(names)

    def test_team_roles_disabled_returns_empty(self) -> None:
        """coding_complexity_team_roles_enabled=False 时 _build_team_role_subagents 返回空列表。"""
        from app.scenarios.coding.agent import _build_team_role_subagents

        mock_settings = MagicMock()
        mock_settings.coding_complexity_team_roles_enabled = False

        with patch("app.scenarios.coding.agent.get_settings", return_value=mock_settings):
            result = _build_team_role_subagents("test-thread")

        assert result == []


# ============================================================
# 签名验证
# ============================================================


def test_build_coding_expert_signature_has_force_todo_and_include_team_roles() -> None:
    """build_coding_expert 接受 force_todo 和 include_team_roles 参数。"""
    from app.scenarios.coding.agent import build_coding_expert

    sig = inspect.signature(build_coding_expert)
    params = sig.parameters
    assert "force_todo" in params
    assert "include_team_roles" in params


def test_build_subagents_signature_has_include_team_roles() -> None:
    """_build_subagents 接受 include_team_roles 参数。"""
    from app.scenarios.coding.agent import _build_subagents

    sig = inspect.signature(_build_subagents)
    params = sig.parameters
    assert "include_team_roles" in params
