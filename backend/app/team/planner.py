"""AgentTeam Orchestrator：拆解任务为子任务计划（deepagents 原生 Todo schema）。

包含：
- ``_BASE_EXPERTS``：基础专家清单（硬编码：code / rag / web / deep）。
- ``_build_team_experts_description``：声明式团队角色描述生成器，从
  ``settings.team_subagents`` 动态生成团队专家清单。
- ``_ORCHESTRATOR_SYSTEM_PROMPT``：Orchestrator 系统 prompt 模板（配合
  ``create_deep_agent`` 使用，由 ``TodoListMiddleware`` 自动注入
  ``write_todos`` 工具，LLM 调用 ``write_todos`` 写入 ``state.todos``）。
- ``_build_project_context``：构建项目上下文摘要，避免 subagent 盲探索。
- ``_todos_to_team_tasks``：从 ``state.todos`` 解析子任务，转换为
  ``TeamPlanTask`` 列表（解析 ``[agent:xxx]`` 前缀确定 agent 类型）。
- ``_looks_like_dangerous_task``：启发式判断子任务是否涉及危险操作。
- ``_validate_task``：校验子任务 agent 是否可用（含 custom / 团队角色）。
  团队角色集合由 ``BUILTIN_TEAM_KEYS`` 决定（不硬编码字面量）。
"""

from __future__ import annotations

import re
from typing import Any

from app.config.subagents import BUILTIN_TEAM_KEYS
from app.observability.logger import logger
from app.team.blackboard import TeamPlanTask
from app.utils.text import compile_keyword_patterns, matches_any

__all__ = [
    "_BASE_EXPERTS",
    "_ORCHESTRATOR_SYSTEM_PROMPT",
    "_build_project_context",
    "_build_team_experts_description",
    "_looks_like_dangerous_task",
    "_parse_todos_from_text",
    "_todos_to_team_tasks",
    "_validate_task",
    "tasks_to_display_todos",
]


# 基础专家（始终可用）
_BASE_EXPERTS = (
    "- code: 读取/搜索代码与文件，只读工具（read_file/ls/glob/grep）。\n"
    "- rag: 从向量知识库检索文档。\n"
    "- web: 联网搜索实时信息。\n"
    "- deep: 执行需要写文件、编辑文件或系统命令的危险任务（会走审批）。\n"
)


def _build_team_experts_description(settings: Any) -> str:
    """声明式生成团队角色描述（从 settings.team_subagents 动态生成）。

    替代旧的硬编码 ``_TEAM_EXPERTS`` 元组：现在新增/删除/重命名团队角色
    只需修改 config，无需改 planner 代码。

    Args:
        settings: 全局配置（含 team_subagents 字典）。

    Returns:
        多行字符串，每行一个 ``- key: trigger_description``；空配置返回空串。
    """
    team = getattr(settings, "team_subagents", None) or {}
    lines: list[str] = []
    for key, cfg in team.items():
        if not getattr(cfg, "enabled", True):
            continue
        desc = (
            getattr(cfg, "trigger_description", "")
            or getattr(cfg, "system_prompt", "")
            or "(无描述)"
        )
        # 描述截断到第一句/第一个换行，避免 prompt 过长
        desc = desc.strip().split("\n", 1)[0].strip()
        lines.append(f"- {key}: {desc}")
    return "\n".join(lines)


# Orchestrator 系统 prompt（直接 llm.ainvoke，不依赖 write_todos 工具）
# 旧方案用 create_deep_agent + TodoListMiddleware 注入 write_todos，但
# TodoListMiddleware 的 WRITE_TODOS_SYSTEM_PROMPT 主动建议 LLM "简单任务不要用
# write_todos"，导致 LLM 大多数时候不调用 write_todos → todos 为空 → 无输出。
# 新方案让 LLM 直接在回复正文输出 [agent:xxx] 任务行，_parse_todos_from_text 解析。
_ORCHESTRATOR_SYSTEM_PROMPT = """你是一个任务拆解专家（Orchestrator）。
请把用户请求拆分成若干子任务，在回复正文里直接输出任务清单（每行一个子任务，不要输出任何其他内容）。

每个子任务必须独占一行，以 [agent:类型] 开头，格式：
[agent:code] 读取 src/main.py 并分析入口逻辑
[agent:deep] 修改 src/main.py 添加日志输出
[agent:rag] 检索知识库中关于 FastAPI 最佳实践

可用 agent 类型：
{experts}

约束：
1. 涉及写文件、编辑文件、执行系统命令的任务，agent 必须设为 deep
2. 不要编造文件路径；若用户没给路径，子任务输入里说明需要搜索或推断
3. 子任务数量不要超过 {max_tasks} 个
4. 若任务简单，可只返回一个子任务
5. 若用户请求涉及多个软件开发环节（如前端+后端+测试），优先使用团队角色（frontend_dev/backend_dev/tester 等）而非通用 code

项目上下文：
{context}
"""

# [agent:xxx] 前缀正则：匹配 [agent:code] / [agent:deep] / [agent:custom-mycoder] 等
# 兼容 markdown 列表前缀（- / * / + / 1. / 1)），LLM 常输出此类格式
# 兼容 [agent: code] 冒号后有空格的情况
# M6: 移除 re.DOTALL — `(.*)` 不应跨行匹配，避免多行任务行被误合并
_AGENT_PREFIX_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*\[agent:\s*([a-zA-Z0-9_\-]+)\]\s*(.*)"
)


def tasks_to_display_todos(tasks: list[TeamPlanTask]) -> list[dict]:
    """从 ``TeamPlanTask`` 列表派生展示用 todos（单一数据源，消除双列表）。

    Phase 2 清理（T4.8）：``tasks`` 为单一数据源，展示用 todos 由本纯函数派生，
    不再同时维护 ``clean_todos``（剥离 ``[agent:xxx]`` 前缀的 todos）与 ``tasks`` 双列表。
    旧方案 ``_strip_agent_prefix_from_todos`` 已删除。

    Args:
        tasks: ``TeamPlanTask`` 列表（``input`` 已剥离前缀、已截断到 ``max_tasks``）。

    Returns:
        deepagents 原生 Todo schema ``[{content, status}, ...]``，
        ``content`` 为 ``task.input``（纯文本），``status`` 为 ``"pending"``。
    """
    return [
        {"content": task.input, "status": "pending"}
        for task in tasks
    ]


def _parse_todos_from_text(text: str) -> list[dict]:
    """从 LLM 回复正文中解析 ``[agent:xxx]`` 前缀的任务行，构造 deepagents 原生 Todo schema。

    替代旧方案中依赖 LLM 调用 ``write_todos`` 工具的方式：新方案让 LLM 直接在
    回复正文输出任务行，本函数逐行扫描 ``[agent:类型] 任务描述`` 格式的行，
    构造 ``{content: "[agent:类型] 任务描述", status: "pending"}`` 列表。
    不匹配的行（解释性文字、空行、markdown 标记等）自动跳过。

    产出的 todos 格式与 deepagents ``write_todos`` 工具产出一致，可直接传入
    ``_todos_to_team_tasks`` 解析为 ``TeamPlanTask`` 列表。

    Args:
        text: LLM 回复正文（可能含多行、markdown 标记等）。

    Returns:
        deepagents 原生 Todo 列表 ``[{content, status}, ...]``。
    """
    todos: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # 跳过 markdown 代码块标记
        if line.startswith("```"):
            continue
        match = _AGENT_PREFIX_RE.match(line)
        if not match:
            continue
        agent = match.group(1).strip().lower()
        task_text = match.group(2).strip()
        if not task_text:
            continue
        todos.append({
            "content": f"[agent:{agent}] {task_text}",
            "status": "pending",
        })
    return todos


def _build_project_context() -> str:
    """构建项目上下文摘要，供 Orchestrator 拆任务时参考。

    包含 AGENTS.md §11 文件地图的关键路径，避免 subagent 盲探索。
    """
    lines = [
        "项目结构（agentx）：",
        "- 后端 Python: backend/app/（FastAPI + LangGraph）",
        "  - router/ (graph.py, state.py) — 场景化分发 StateGraph",
        "  - scenarios/ (work/, coding/, coding_team/) — 场景化智能体",
        "  - subagents/ (base, rag_agent, web_agent, custom_agent) — 子代理",
        "  - deepagent/ (agent.py, approval_runner.py, streaming.py) — DeepAgent 框架",
        "  - team/ (orchestrator.py, planner.py, scheduler.py, blackboard.py, aggregator.py) — AgentTeam 多代理协作",
        "  - tools/ (filesystem + rag_retrieve) — 工具",
        "- 前端 Tauri+React: frontend/",
        "  - renderer/components/chat/ — 聊天组件",
        "  - renderer/hooks/useChatStream.ts — SSE 事件处理",
        "  - renderer/stores/ (chat.ts, agentMode.ts) — zustand 状态",
        "- 配置: AGENTS.md（工程规范 + 文件地图 + Router 场景分发说明）",
    ]
    return "\n".join(lines)


def _todos_to_team_tasks(
    todos: list[dict],
    settings: Any,
) -> tuple[list[TeamPlanTask], str]:
    """从 ``state.todos`` 解析子任务，转换为 ``TeamPlanTask`` 列表。

    解析每个 todo 的 content 前缀 ``[agent:xxx]`` 确定 agent 类型，
    剥离前缀后的内容作为 input。调用 ``_validate_task`` 校验 +
    ``_looks_like_dangerous_task`` 安全改写。

    Args:
        todos: deepagents 原生 Todo 列表 ``[{content: str, status: str}, ...]``
        settings: 全局配置（用于 ``_validate_task`` 校验 agent 可用性）

    Returns:
        ``(tasks, reasoning)``: tasks 是 ``TeamPlanTask`` 列表，reasoning 是空串
        （deepagents ``write_todos`` 不产 reasoning，原 ``TeamPlan.reasoning``
        字段已废弃）。
    """
    tasks: list[TeamPlanTask] = []
    errors: dict[str, str] = {}

    for todo in todos or []:
        content = todo.get("content", "") if isinstance(todo, dict) else ""
        if not content:
            continue
        match = _AGENT_PREFIX_RE.match(content)
        if not match:
            logger.warning(
                "team orchestrator todo missing [agent:xxx] prefix",
                content_preview=content[:100],
            )
            continue
        agent = match.group(1).strip().lower()
        input_text = match.group(2).strip()
        if not input_text:
            continue
        # 安全改写：涉及危险工具关键词但非 deep 的任务强制改为 deep
        if agent != "deep" and _looks_like_dangerous_task(input_text):
            agent = "deep"

        ok, err = _validate_task(TeamPlanTask(agent=agent, input=input_text, purpose=""), settings)
        if ok:
            tasks.append(TeamPlanTask(agent=agent, input=input_text, purpose=""))
        else:
            errors[agent] = err
            logger.warning(
                "team orchestrator task validation failed",
                agent=agent,
                error=err,
            )

    if errors:
        logger.info(
            "team orchestrator some tasks filtered",
            total_todos=len(todos),
            valid_tasks=len(tasks),
            invalid_agents=list(errors.keys()),
        )

    max_tasks = settings.agent_team_max_tasks
    if len(tasks) > max_tasks:
        logger.warning(
            "team orchestrator tasks truncated",
            original_count=len(tasks),
            max_tasks=max_tasks,
        )
        tasks = tasks[:max_tasks]

    return tasks, ""


_DANGEROUS_KEYWORDS = ["写入", "写文件", "write", "编辑", "修改", "edit", "执行命令", "shell", "运行脚本"]
_DANGEROUS_PATTERNS = compile_keyword_patterns(_DANGEROUS_KEYWORDS)


def _looks_like_dangerous_task(input_text: str) -> bool:
    """启发式判断子任务是否涉及危险操作。"""
    return matches_any(input_text, _DANGEROUS_PATTERNS)


def _validate_task(task: TeamPlanTask, settings: Any) -> tuple[bool, str]:
    """校验子任务 agent 是否可用。

    Returns:
        (ok, error_message)
    """
    if task.agent == "deep":
        return True, ""
    # code 任务映射到 coding Expert（场景化架构），不依赖 subagents 配置
    if task.agent == "code":
        return True, ""
    if task.agent in ("rag", "web"):
        cfg = settings.subagents.get(task.agent)
        if not cfg or not cfg.enabled:
            return False, f"子代理 {task.agent} 已禁用"
        if not any(settings.tools_enabled.get(t, True) for t in cfg.tools):
            return False, f"子代理 {task.agent} 绑定的工具全部被禁用"
        return True, ""
    # 软件开发团队角色（含 BUILTIN_TEAM_KEYS 与 settings.team_subagents 中声明的扩展角色）
    # 用 BUILTIN_TEAM_KEYS 作起点，扩展由 settings 提供（声明式）。
    if task.agent in BUILTIN_TEAM_KEYS or task.agent in (settings.team_subagents or {}):
        cfg = (settings.team_subagents or {}).get(task.agent)
        if not cfg or not cfg.enabled:
            return False, f"团队角色 {task.agent} 已禁用"
        if not any(settings.tools_enabled.get(t, True) for t in (cfg.tools or [])):
            return False, f"团队角色 {task.agent} 绑定的工具全部被禁用"
        return True, ""
    if task.agent.startswith("custom-"):
        key = task.agent[len("custom-"):]
        custom = settings.custom_subagents
        if key not in custom:
            return False, f"自定义子代理 {key} 不存在"
        cfg = custom[key]
        if not cfg.enabled:
            return False, f"自定义子代理 {key} 已禁用"
        if not cfg.tools:
            return False, f"自定义子代理 {key} 未绑定工具"
        if not any(settings.tools_enabled.get(t, True) for t in cfg.tools):
            return False, f"自定义子代理 {key} 绑定的工具全部被禁用"
        return True, ""
    return False, f"未知 agent 类型: {task.agent}"
