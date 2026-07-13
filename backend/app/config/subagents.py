"""子代理配置模型与解析逻辑。

从 ``config.py`` 迁出：SubagentSettings / CustomSubagentEntry 模型 +
BUILTIN_* 常量 + _default_subagents / _default_team_subagents /
_sanitize_custom_tools / _parse_custom_subagents 函数。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.config.prompts.builtin import (
    _DEFAULT_RAG_SYSTEM_PROMPT,
    _DEFAULT_RAG_TOOLS,
    _DEFAULT_RAG_TRIGGER_DESCRIPTION,
    _DEFAULT_WEB_SYSTEM_PROMPT,
    _DEFAULT_WEB_TOOLS,
    _DEFAULT_WEB_TRIGGER_DESCRIPTION,
)
from app.config.prompts.team import (
    _DEFAULT_ARCHITECT_SYSTEM_PROMPT,
    _DEFAULT_ARCHITECT_TRIGGER_DESCRIPTION,
    _DEFAULT_BACKEND_DEV_SYSTEM_PROMPT,
    _DEFAULT_BACKEND_DEV_TRIGGER_DESCRIPTION,
    _DEFAULT_DEVOPS_SYSTEM_PROMPT,
    _DEFAULT_DEVOPS_TRIGGER_DESCRIPTION,
    _DEFAULT_FRONTEND_DEV_SYSTEM_PROMPT,
    _DEFAULT_FRONTEND_DEV_TRIGGER_DESCRIPTION,
    _DEFAULT_PRODUCT_MANAGER_SYSTEM_PROMPT,
    _DEFAULT_PRODUCT_MANAGER_TRIGGER_DESCRIPTION,
    _DEFAULT_TEAM_TOOLS,
    _DEFAULT_TESTER_SYSTEM_PROMPT,
    _DEFAULT_TESTER_TRIGGER_DESCRIPTION,
    _DEFAULT_UI_DESIGNER_SYSTEM_PROMPT,
    _DEFAULT_UI_DESIGNER_TRIGGER_DESCRIPTION,
)
from app.security.dangerous_tools import FORBIDDEN_SUBAGENT_TOOLS

__all__ = [
    "SubagentSettings",
    "CustomSubagentEntry",
    "BUILTIN_SUBAGENT_KEYS",
    "BUILTIN_TEAM_KEYS",
    "FORBIDDEN_SUBAGENT_TOOLS",
    "_ALL_TOOLS",
    "_default_subagents",
    "_default_team_subagents",
    "_sanitize_custom_tools",
    "_parse_custom_subagents",
    "validate_team_subagents",
]


# 全部工具清单（tools_enabled 默认值）
# 内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由 AuthorizedLocalShellBackend
# 自动注入，此处保留 key 用于配置可见性；``execute`` 由 backend 提供，不在此列表中。
# ``delete_file`` 是项目自研工具，可通过 tools_enabled 禁用。
# Git 操作（status/diff/log/commit/push 等）由 deepagents 内置 ``execute`` 工具承担，
# 不再有独立 git_* 工具（Phase B.1 已删除 make_git_tools）。
_ALL_TOOLS = [
    "read_file", "ls", "glob", "grep",
    "write_file", "edit_file", "delete_file",
    "web_search", "rag_retrieve",
]


class SubagentSettings(BaseModel):
    """单个子代理的可配置项。

    字段说明：
    - system_prompt: 角色定义（合并原 description + system_prompt）。
      既作为 LLM 的系统提示词，也作为 UI 展示的描述。
    - trigger_description: 触发条件描述（短句）。用于 LLM 语义路由决策
      和降级关键词匹配（从短句中提取关键词）。
    - rubric: 可选自纠规则文本；非空时由 create_agent 挂载
      RubricMiddleware 启用运行时自纠（graders/feedback loop）。
    - grader_model: 可选判官模型名（如 "gpt-4o-mini"）；为 None 时
      create_agent 默认用 get_chat_model(temperature=0)。
    """

    enabled: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    trigger_description: str = ""
    rubric: str = ""
    grader_model: str | None = None


# 内置子代理键名集合（与 _default_subagents 一致，用于区分内置/自定义）
# 场景化架构下 code 子代理已被 coding Expert 取代，仅保留 rag/web
BUILTIN_SUBAGENT_KEYS: frozenset[str] = frozenset({"rag", "web"})

# 内置软件开发专家团角色键名集合（仅用于 AgentTeam 多代理协作）
BUILTIN_TEAM_KEYS: frozenset[str] = frozenset(
    {
        "frontend_dev",
        "backend_dev",
        "tester",
        "architect",
        "devops",
        "ui_designer",
        "product_manager",
    }
)


class CustomSubagentEntry(BaseModel):
    """自定义子代理条目（含展示元数据）。

    与 ``SubagentSettings`` 的差异：额外含 ``name`` / ``system_prompt`` 用于 UI 展示。

    ``key`` 字段严格校验：仅允许 ``[a-zA-Z0-9_-]{1,64}``（与前端
    ``frontend/main/store.ts::sanitizeCustomEntry::CUSTOM_KEY_RE`` 一致）。
    防止 env JSON 序列化、shell 注入、URL 路径解析等下游环节出错。

    ``rubric`` 字段为可选自纠规则文本，透传到 build_custom_agent → create_agent。
    """

    key: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str
    system_prompt: str = ""
    enabled: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    tools: list[str] = Field(default_factory=list)
    trigger_description: str = ""
    rubric: str = ""


def _default_team_subagents() -> dict[str, SubagentSettings]:
    """默认软件开发团队角色配置。"""
    return {
        "frontend_dev": SubagentSettings(
            enabled=True,
            temperature=0.2,
            system_prompt=_DEFAULT_FRONTEND_DEV_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS),
            trigger_description=_DEFAULT_FRONTEND_DEV_TRIGGER_DESCRIPTION,
        ),
        "backend_dev": SubagentSettings(
            enabled=True,
            temperature=0.2,
            system_prompt=_DEFAULT_BACKEND_DEV_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS),
            trigger_description=_DEFAULT_BACKEND_DEV_TRIGGER_DESCRIPTION,
        ),
        "tester": SubagentSettings(
            enabled=True,
            temperature=0.2,
            system_prompt=_DEFAULT_TESTER_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS),
            trigger_description=_DEFAULT_TESTER_TRIGGER_DESCRIPTION,
        ),
        "architect": SubagentSettings(
            enabled=True,
            temperature=0.2,
            system_prompt=_DEFAULT_ARCHITECT_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS),
            trigger_description=_DEFAULT_ARCHITECT_TRIGGER_DESCRIPTION,
            # 架构自纠 rubric：要求 trade-off 表 + 风险评估，启用 RubricMiddleware
            # 运行时校验 LLM 输出是否包含这些要素；缺失则触发 grader 重写。
            rubric=(
                "输出必须包含:\n"
                "1. 至少 2 个备选方案的对比表（维度: 性能/成本/复杂度/团队匹配度）\n"
                "2. 明确推荐方案及 3 条以内核心理由\n"
                "3. 风险评估：列出 1-3 个主要风险及对应缓解措施"
            ),
        ),
        "devops": SubagentSettings(
            enabled=True,
            temperature=0.2,
            system_prompt=_DEFAULT_DEVOPS_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS),
            trigger_description=_DEFAULT_DEVOPS_TRIGGER_DESCRIPTION,
        ),
        "ui_designer": SubagentSettings(
            enabled=True,
            temperature=0.2,
            system_prompt=_DEFAULT_UI_DESIGNER_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS),
            trigger_description=_DEFAULT_UI_DESIGNER_TRIGGER_DESCRIPTION,
        ),
        "product_manager": SubagentSettings(
            enabled=True,
            temperature=0.2,
            system_prompt=_DEFAULT_PRODUCT_MANAGER_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS),
            trigger_description=_DEFAULT_PRODUCT_MANAGER_TRIGGER_DESCRIPTION,
        ),
    }


def _default_subagents() -> dict[str, SubagentSettings]:
    """默认子代理配置（场景化架构下仅 rag/web，code 子代理已被 coding Expert 取代）。"""
    return {
        "rag": SubagentSettings(
            enabled=True,
            temperature=0.2,
            system_prompt=_DEFAULT_RAG_SYSTEM_PROMPT,
            tools=list(_DEFAULT_RAG_TOOLS),
            trigger_description=_DEFAULT_RAG_TRIGGER_DESCRIPTION,
        ),
        "web": SubagentSettings(
            enabled=True,
            temperature=0.2,
            system_prompt=_DEFAULT_WEB_SYSTEM_PROMPT,
            tools=list(_DEFAULT_WEB_TOOLS),
            trigger_description=_DEFAULT_WEB_TRIGGER_DESCRIPTION,
        ),
    }


def _sanitize_custom_tools(tools: list[str]) -> list[str]:
    """过滤自定义子代理工具：移除危险工具与未知工具名，去重保序。"""
    allowed = set(_ALL_TOOLS) - FORBIDDEN_SUBAGENT_TOOLS
    seen: set[str] = set()
    result: list[str] = []
    for t in tools:
        if t in allowed and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _parse_custom_subagents(raw: Any) -> dict[str, CustomSubagentEntry]:
    """从 env JSON 解析自定义子代理 dict，过滤非法字段与危险工具。

    - raw 必须是 dict，每个 value 也是 dict
    - key 必须是非空字符串、不与内置 key 冲突、且符合 ``[a-zA-Z0-9_-]{1,64}``
    - 工具列表经 _sanitize_custom_tools 过滤
    - 非法 key / 非法 value 字段跳过并记 warning（不抛异常，保持向后兼容）
    """
    from app.observability.logger import logger

    # key 格式必须与前端 sanitizeCustomEntry 的 CUSTOM_KEY_RE 完全一致
    _CUSTOM_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

    if not isinstance(raw, dict):
        return {}
    result: dict[str, CustomSubagentEntry] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not key:
            logger.warning("custom subagent key 空或非字符串，已跳过", key=repr(key))
            continue
        if key in BUILTIN_SUBAGENT_KEYS:
            # 不允许自定义 key 与内置冲突
            logger.warning(
                "custom subagent key 与内置冲突，已跳过",
                key=key,
                builtin=list(BUILTIN_SUBAGENT_KEYS),
            )
            continue
        if not _CUSTOM_KEY_RE.match(key):
            # 防御性预校验：与 CustomSubagentEntry.key pattern 保持一致
            # 避免后续 pydantic ValidationError 静默吞掉，难以排查
            logger.warning(
                "custom subagent key 格式非法，已跳过",
                key=key,
                pattern=_CUSTOM_KEY_RE.pattern,
            )
            continue
        if not isinstance(val, dict):
            logger.warning("custom subagent value 非 dict，已跳过", key=key)
            continue
        try:
            entry = CustomSubagentEntry(
                key=key,
                name=str(val.get("name", key)),
                system_prompt=str(val.get("system_prompt") or val.get("systemPrompt") or ""),
                enabled=bool(val.get("enabled", True)),
                temperature=float(val.get("temperature", 0.2)),
                tools=_sanitize_custom_tools(list(val.get("tools", []))),
                trigger_description=str(
                    val.get("trigger_description") or val.get("triggerDescription") or ""
                ),
                rubric=str(val.get("rubric") or ""),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            logger.warning(
                "custom subagent entry 构造失败，已跳过",
                key=key,
                error=str(exc),
            )
            continue
        # 温度 clamp（pydantic 已校验，但防御性再 clamp）
        entry.temperature = max(0.0, min(2.0, entry.temperature))
        result[key] = entry
    return result


def validate_team_subagents(settings: Any) -> None:
    """启动时校验团队角色子代理配置（T11）。

    遍历 ``settings.team_subagents``，所有 ``enabled=True`` 的角色必须含非空
    ``system_prompt``，否则 ``raise ValueError``。防止配置错误导致运行时
    ``_run_team_role_subtask`` 静默降级或失败。

    Args:
        settings: ``Settings`` 实例（含 ``team_subagents`` property）。

    Raises:
        ValueError: 任一启用的团队角色缺少 ``system_prompt``。
    """
    from app.observability.logger import logger

    team_subagents = settings.team_subagents
    missing: list[str] = []
    for name, cfg in team_subagents.items():
        if cfg.enabled and not cfg.system_prompt.strip():
            missing.append(name)
    if missing:
        detail = ", ".join(missing)
        logger.error(
            "team subagents validation failed: missing system_prompt",
            missing=missing,
        )
        raise ValueError(
            f"团队角色配置校验失败，以下启用的角色缺少 system_prompt: {detail}"
        )
    logger.info(
        "team subagents validation passed",
        total=len(team_subagents),
        enabled=sum(1 for c in team_subagents.values() if c.enabled),
    )
