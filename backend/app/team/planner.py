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

from app.config import get_settings
from app.config.subagents import BUILTIN_TEAM_KEYS
from app.llm import get_chat_model
from app.observability.logger import logger
from app.team.blackboard import TeamPlanTask
from app.team.classifier import _DANGEROUS_PATTERNS
from app.team.state import Finding, TeamPlan, TeamTask
from app.utils.text import matches_any

__all__ = [
    "_BASE_EXPERTS",
    "_ORCHESTRATOR_SYSTEM_PROMPT",
    "_build_project_context",
    "_build_team_experts_description",
    "_looks_like_dangerous_task",
    "_parse_after_deps",
    "_parse_plan_from_text_fallback",
    "_parse_todos_from_text",
    "_todos_to_team_tasks",
    "_validate_dag",
    "_validate_task",
    "Planner",
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
# DAG 依赖编排：扩展为 3 group，支持可选 [after:N1,N2] 标注
#   group(1) = agent 名
#   group(2) = after 内容（可选，如 "0, 1" 或 None）
#   group(3) = input 文本
_AGENT_PREFIX_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*\[agent:\s*([a-zA-Z0-9_\-]+)\]"
    r"(?:\[after:\s*([\d,\s;#]*)\])?"  # 可选 [after:0,1]，容忍 # 前缀与分号分隔
    r"\s*(.*)"
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
    return [{"content": task.input, "status": "pending"} for task in tasks]


def _parse_after_deps(after_content: str) -> list[int]:
    """解析 ``[after:N1,N2]`` 标注内容为 int 列表（容错）。

    容错策略（见 design.md D2）：
    - ``"0,1"`` → ``[0, 1]`` 正常
    - ``"0, 1"`` → ``[0, 1]`` strip 空格
    - ``"#0,#1"`` → ``[0, 1]`` 去掉 ``#`` 前缀
    - ``"0;1"`` → ``[0, 1]`` 分号也作分隔符
    - ``"abc"`` → ``[]`` 非数字丢弃 + logger.warning
    - 空串 / None → ``[]``

    Args:
        after_content: 正则 group(2) 内容（可能为 None）。

    Returns:
        依赖任务索引列表（int）。
    """
    if not after_content:
        return []
    # 把分号也作为分隔符，统一替换为逗号
    normalized = after_content.replace(";", ",").replace("#", "")
    parts = [p.strip() for p in normalized.split(",")]
    deps: list[int] = []
    has_invalid = False
    for part in parts:
        if not part:
            continue
        try:
            deps.append(int(part))
        except ValueError:
            has_invalid = True
    if has_invalid:
        logger.warning(
            "team planner [after:] contains non-numeric content, dropped invalid parts",
            raw=after_content,
        )
    return deps


def _parse_todos_from_text(text: str) -> list[dict]:
    """从 LLM 回复正文中解析 ``[agent:xxx][after:N1,N2]`` 前缀的任务行，构造 deepagents 原生 Todo schema。

    替代旧方案中依赖 LLM 调用 ``write_todos`` 工具的方式：新方案让 LLM 直接在
    回复正文输出任务行，本函数逐行扫描 ``[agent:类型][after:依赖] 任务描述`` 格式
    的行，构造 ``{content: "[agent:类型][after:依赖] 任务描述", status: "pending", deps: list[int]}`` 列表。
    不匹配的行（解释性文字、空行、markdown 标记等）自动跳过。

    ``deps`` 字段为依赖任务索引列表（DAG 依赖编排），空列表表示无依赖（根任务）。
    产出的 todos 格式与 deepagents ``write_todos`` 工具产出一致，可直接传入
    ``_todos_to_team_tasks`` 解析为 ``TeamPlanTask`` 列表。

    Args:
        text: LLM 回复正文（可能含多行、markdown 标记等）。

    Returns:
        deepagents 原生 Todo 列表 ``[{content, status, deps}, ...]``。
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
        agent = match.group(1)
        after_content = match.group(2)  # 可能为 None
        task_text = match.group(3).strip()
        if not task_text:
            continue
        deps = _parse_after_deps(after_content)
        # 重建规范化 content：[agent:xxx][after:...] task_text
        # 剥离 markdown 前缀、规范冒号空格、保留 [after:] 标注供 _todos_to_team_tasks 再次解析
        content = f"[agent:{agent}]"
        if after_content is not None:
            content += f"[after:{after_content.strip()}]"
        content += f" {task_text}"
        todos.append(
            {
                "content": content,
                "status": "pending",
                "deps": deps,
            }
        )
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
    """从 ``state.todos`` 解析子任务，转换为 ``TeamPlanTask`` 列表（含 deps 字段）。

    解析每个 todo 的 content 前缀 ``[agent:xxx][after:N1,N2]`` 确定 agent 类型与
    依赖任务索引，剥离前缀后的内容作为 input。调用 ``_validate_task`` 校验 +
    ``_looks_like_dangerous_task`` 安全改写。

    截断到 ``settings.team_max_tasks`` 时同步清理 deps：引用被截断任务索引
    的 deps 项会被丢弃（避免悬空依赖）。

    Args:
        todos: deepagents 原生 Todo 列表 ``[{content: str, status: str, deps: list[int]}, ...]``
        settings: 全局配置（用于 ``_validate_task`` 校验 agent 可用性）

    Returns:
        ``(tasks, reasoning)``: tasks 是 ``TeamPlanTask`` 列表（含 deps），reasoning 是空串
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
        after_content = match.group(2)
        input_text = match.group(3).strip()
        if not input_text:
            continue
        deps = _parse_after_deps(after_content)
        # 安全改写：涉及危险工具关键词但非 deep 的任务强制改为 deep
        if agent != "deep" and _looks_like_dangerous_task(input_text):
            agent = "deep"

        ok, err = _validate_task(
            TeamPlanTask(agent=agent, input=input_text, purpose="", deps=deps), settings
        )
        if ok:
            tasks.append(TeamPlanTask(agent=agent, input=input_text, purpose="", deps=deps))
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

    max_tasks = settings.team_max_tasks
    if len(tasks) > max_tasks:
        logger.warning(
            "team orchestrator tasks truncated",
            original_count=len(tasks),
            max_tasks=max_tasks,
        )
        tasks = tasks[:max_tasks]
        # 截断后清理悬空 deps：引用被截断任务索引的 deps 项丢弃
        valid_indices = set(range(len(tasks)))
        for i, task in enumerate(tasks):
            cleaned_deps = [d for d in task.deps if d in valid_indices]
            if len(cleaned_deps) != len(task.deps):
                logger.warning(
                    "team orchestrator task deps cleaned after truncation",
                    task_index=i,
                    original_deps=task.deps,
                    cleaned_deps=cleaned_deps,
                )
                task.deps = cleaned_deps

    return tasks, ""


def _validate_dag(
    tasks: list[TeamPlanTask],
) -> tuple[list[list[int]], list[int]]:
    """Kahn 算法分层 + 循环检测（DAG 依赖编排）。

    Args:
        tasks: ``TeamPlanTask`` 列表（含 ``deps`` 字段）。

    Returns:
        ``(levels, dropped_edges)``：
        - ``levels``: 拓扑分层结果，每层一组任务索引（list[list[int]]）。
          层内任务可并行，层间串行。所有任务无 deps 时返回单层（等价并行 fan-out）。
        - ``dropped_edges``: 因循环被强制打破 / 越界 / 自环被丢弃的任务索引列表。

    边界处理：
    - 越界索引（``dep >= len(tasks)`` 或 ``dep < 0``）丢弃 + logger.warning
    - 自环（``dep == i``）丢弃 + logger.warning
    - 循环检测：入度为 0 的节点为空时，强加入度最小的节点打破循环 + logger.error
    """
    n = len(tasks)
    if n == 0:
        return [], []

    # 构建邻接表 + 入度表
    in_degree = [0] * n
    adj: list[list[int]] = [[] for _ in range(n)]
    dropped_edges: list[int] = []
    for i, task in enumerate(tasks):
        for dep in task.deps:
            if dep < 0 or dep >= n:
                # 越界，丢弃
                dropped_edges.append(i)
                logger.warning(
                    "team DAG dep out of bounds, dropped",
                    task_index=i,
                    dep=dep,
                    task_count=n,
                )
                continue
            if dep == i:
                # 自环，丢弃
                dropped_edges.append(i)
                logger.warning(
                    "team DAG self-loop dep, dropped",
                    task_index=i,
                    dep=dep,
                )
                continue
            adj[dep].append(i)
            in_degree[i] += 1

    # Kahn 分层
    levels: list[list[int]] = []
    remaining = set(range(n))
    while remaining:
        # 当前层：入度为 0 的节点
        current_level = [i for i in sorted(remaining) if in_degree[i] == 0]
        if not current_level:
            # 循环：把剩余节点中入度最小的强加进当前层（打破循环）
            min_indeg = min(in_degree[i] for i in remaining)
            current_level = [i for i in sorted(remaining) if in_degree[i] == min_indeg]
            for i in current_level:
                dropped_edges.append(i)
                logger.error(
                    "team DAG cycle detected, breaking by force",
                    task_index=i,
                    deps=tasks[i].deps,
                )
        levels.append(current_level)
        for i in current_level:
            remaining.discard(i)
            for j in adj[i]:
                in_degree[j] -= 1
    return levels, dropped_edges


def _looks_like_dangerous_task(input_text: str) -> bool:
    """启发式判断子任务是否涉及危险操作（关键词匹配，快速预筛）。

    精确分类由 ``DangerousTaskClassifier``（LLM 路径）在 execute_node 中执行。
    """
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
        key = task.agent[len("custom-") :]
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
    # DAG 依赖编排 D6：未知 agent 不在白名单时也不直接过滤，留给运行时
    # _resolve_subtask_config 统一 fallback 到 code runner，保持任务可执行。
    return True, ""


# ============================================================
# v2 Planner：with_structured_output(TeamPlan) + fallback（T3）
# ============================================================

# v2 Orchestrator system prompt（结构化输出路径）。
# 当 LLM 支持 ``with_structured_output(TeamPlan)`` 时使用本 prompt；框架自动注入
# JSON schema，LLM 填充结构化字段。不支持时回退到 ``_ORCHESTRATOR_SYSTEM_PROMPT``
# （文本格式 ``[agent:xxx][after:N]``）+ ``_parse_plan_from_text_fallback`` 正则解析。
_PLANNER_V2_SYSTEM_PROMPT = """你是任务拆解专家（Orchestrator）。请把用户请求拆分为若干子任务。

可用 agent 类型：
{experts}

约束：
1. 每个子任务必须有唯一的 id（如 t1/t2/t3），用于 depends_on 引用
2. 涉及写文件、编辑文件、执行系统命令的任务，agent 必须设为 deep
3. 不要编造文件路径；若用户没给路径，在 description 中说明需要搜索或推断
4. 子任务数量不要超过 {max_tasks} 个
5. 若用户请求涉及多个软件开发环节（如前端+后端+测试），优先使用团队角色而非通用 code
6. 若任务简单，可只返回一个子任务
7. depends_on 引用其他任务的 id（如 ["t1"]），空列表表示无依赖（根任务）
8. is_dangerous_hint 标注涉及危险操作的任务

项目上下文：
{context}
"""


_REPLAN_V2_SYSTEM_PROMPT = """你是任务拆解专家。以下是已完成的子任务结果与错误：

已完成的子任务：
{completed_findings}

失败的错误：
{errors}

用户原始请求：
{user_message}

请判断是否需要追加新任务来完善最终回答。
- 若需要追加，输出新的 TeamPlan（仅包含需要追加的新任务，新任务必须依赖至少一个已完成任务）
- 若无需追加，返回空 tasks 列表
"""


def _parse_plan_from_text_fallback(text: str) -> TeamPlan:
    """LLM 不支持结构化输出时的 fallback 解析（D2）。

    复用现有 ``_AGENT_PREFIX_RE`` 正则解析 ``[agent:xxx][after:N1,N2]`` 文本行，
    转换为 v2 ``TeamPlan``（``TeamTask`` 含 ``id`` / ``depends_on`` str 列表）。

    旧 ``[after:N]`` 标注为 0-indexed 整数（引用任务在文本中的行序），本函数
    映射为 1-indexed task id（``t{N+1}``），使 ``depends_on`` 引用有效的 task id。

    Args:
        text: LLM 回复正文（含 ``[agent:xxx][after:N]`` 任务行）。

    Returns:
        ``TeamPlan``（含 ``tasks`` 列表）。无有效任务行时返回空 plan。
    """
    raw_tasks: list[tuple[str, str, list[int]]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        match = _AGENT_PREFIX_RE.match(line)
        if not match:
            continue
        agent = match.group(1)
        after_content = match.group(2)
        desc = match.group(3).strip()
        if not desc:
            continue
        after_indices = _parse_after_deps(after_content)
        raw_tasks.append((agent, desc, after_indices))

    n = len(raw_tasks)
    tasks: list[TeamTask] = []
    for idx, (agent, desc, after_indices) in enumerate(raw_tasks):
        task_id = f"t{idx + 1}"
        # 映射 0-indexed after:N → task id "t{N+1}"，过滤越界索引
        depends_on = [f"t{i + 1}" for i in after_indices if 0 <= i < n]
        tasks.append(
            TeamTask(
                id=task_id,
                agent=agent.lower(),
                description=desc,
                depends_on=depends_on,
            )
        )
    return TeamPlan(tasks=tasks)


class Planner:
    """v2 Orchestrator LLM 封装（D2）。

    优先使用 ``chat_model.with_structured_output(TeamPlan)`` 获取结构化计划，
    LLM 不支持时回退到 ``_ORCHESTRATOR_SYSTEM_PROMPT`` + ``_parse_plan_from_text_fallback``
    正则解析（保留旧路径，确保降级可用）。

    旧 ``orchestrator._plan_node`` 仍直接调用 ``llm.ainvoke`` + ``_parse_todos_from_text``，
    本类仅供 v2 ``nodes.plan_node`` / ``nodes.replan_node`` 使用，互不干扰。
    """

    def __init__(self, chat_model: Any | None = None) -> None:
        self._chat_model = chat_model
        self._structured: Any = None
        if chat_model is not None:
            try:
                self._structured = chat_model.with_structured_output(TeamPlan)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "team planner LLM does not support structured output, will use fallback",
                    error=str(exc),
                )
                self._structured = None

    async def plan_with_llm(self, message: str, context: str = "") -> TeamPlan:
        """调用 LLM 生成 ``TeamPlan``（结构化输出优先，fallback 正则解析）。

        Args:
            message: 用户原始请求。
            context: 项目上下文摘要（可选，默认走 ``_build_project_context``）。

        Returns:
            ``TeamPlan``（含 ``tasks`` 列表）。LLM 调用失败或未生成有效任务时
            返回 ``TeamPlan(tasks=[])``。
        """
        settings = get_settings()
        experts = _BASE_EXPERTS
        team_desc = _build_team_experts_description(settings)
        if team_desc:
            experts = experts + "\n" + team_desc
        project_context = context or _build_project_context()

        llm = self._resolve_llm(settings)
        if llm is None:
            logger.warning("team planner no LLM available")
            return TeamPlan(tasks=[])

        # 结构化输出路径
        if self._structured is not None:
            system_prompt = _PLANNER_V2_SYSTEM_PROMPT.format(
                experts=experts,
                max_tasks=settings.team_max_tasks,
                context=project_context,
            )
            try:
                plan = await self._structured.ainvoke(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message},
                    ]
                )
                return self._post_filter(plan, settings)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "team planner structured output failed, falling back to text parse",
                    error=str(exc),
                )

        # Fallback：文本输出 + 正则解析
        system_prompt = _ORCHESTRATOR_SYSTEM_PROMPT.format(
            experts=experts,
            max_tasks=settings.team_max_tasks,
            context=project_context,
        )
        try:
            response = await llm.ainvoke(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("team planner llm.ainvoke failed", error=str(exc))
            return TeamPlan(tasks=[])

        raw_content = (
            response.content if hasattr(response, "content") else str(response)
        )
        if isinstance(raw_content, list):
            text = "\n".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in raw_content
            )
        else:
            text = str(raw_content)

        plan = _parse_plan_from_text_fallback(text)
        return self._post_filter(plan, settings)

    async def replan(
        self,
        original_message: str,
        previous_plan: list[TeamTask],
        findings: dict[str, Finding],
        errors: list[str],
        hint: bool = False,
    ) -> TeamPlan:
        """质量门失败后的迭代式重规划（D10）。

        把已完成子任务的 findings 与失败的 errors 喂回 LLM，让其追加新任务
        补全回答。新任务必须依赖至少一个已完成任务（不能是无依赖的根任务）。

        Args:
            original_message: 用户原始请求。
            previous_plan: 上一轮的计划（``list[TeamTask]``）。
            findings: 已完成子任务的结果（``dict[str, Finding]``）。
            errors: 失败错误列表。
            hint: 上一轮是否含危险任务提示。

        Returns:
            新的 ``TeamPlan``（仅含追加任务）。无需追加时返回空 tasks。
        """
        settings = get_settings()
        llm = self._resolve_llm(settings)
        if llm is None:
            logger.warning("team replanner no LLM available")
            return TeamPlan(tasks=[])

        # 构建已完成 findings 摘要
        completed_lines: list[str] = []
        for finding in findings.values():
            status = "成功" if finding.success else f"失败({finding.error or '未知'})"
            content_preview = finding.content[:500] if finding.content else ""
            completed_lines.append(
                f"- [{finding.task_id}] agent={finding.agent} {status}: {content_preview}"
            )
        completed_findings = "\n".join(completed_lines) or "（无已完成任务）"
        errors_text = "\n".join(f"- {e}" for e in errors) or "（无错误）"

        system_prompt = _REPLAN_V2_SYSTEM_PROMPT.format(
            completed_findings=completed_findings,
            errors=errors_text,
            user_message=original_message,
        )

        # 结构化输出路径
        if self._structured is not None:
            try:
                plan = await self._structured.ainvoke(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": original_message},
                    ]
                )
                return self._post_filter_replan(plan, settings, previous_plan)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "team replanner structured output failed, falling back to text parse",
                    error=str(exc),
                )

        # Fallback：文本输出 + 正则解析
        replan_text_prompt = system_prompt + (
            "\n若需要追加，输出新的任务行，格式：[agent:类型][after:0,1] 任务描述\n"
            "after 引用已完成任务的 0-indexed 位置。若无需追加，输出：NO_NEW_TASKS"
        )
        try:
            response = await llm.ainvoke(
                [
                    {"role": "system", "content": replan_text_prompt},
                    {"role": "user", "content": original_message},
                ]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("team replanner llm.ainvoke failed", error=str(exc))
            return TeamPlan(tasks=[])

        raw_content = (
            response.content if hasattr(response, "content") else str(response)
        )
        if isinstance(raw_content, list):
            text = "\n".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in raw_content
            )
        else:
            text = str(raw_content)

        if "NO_NEW_TASKS" in text:
            return TeamPlan(tasks=[])

        plan = _parse_plan_from_text_fallback(text)
        return self._post_filter_replan(plan, settings, previous_plan)

    def _resolve_llm(self, settings: Any) -> Any:
        """解析可用的 LLM 实例。

        优先使用构造时传入的 ``chat_model``；为 None 时回退到 ``get_chat_model``。
        ``get_chat_model`` 不可用（未配置 API key）时返回 None。
        """
        if self._chat_model is not None:
            return self._chat_model
        try:
            return get_chat_model(
                temperature=settings.llm_temperature_orchestrator, streaming=False
            )
        except ValueError:
            return None

    def _post_filter(self, plan: TeamPlan, settings: Any) -> TeamPlan:
        """对 LLM 产出的 plan 做安全改写与截断。

        - 危险任务但非 deep 的强制改写为 deep
        - 超过 ``team_max_tasks`` 的截断 + 清理悬空 depends_on
        """
        if not plan.tasks:
            return plan

        tasks = list(plan.tasks)
        # 安全改写：涉及危险工具关键词但非 deep 的任务强制改为 deep
        for i, task in enumerate(tasks):
            if task.agent != "deep" and _looks_like_dangerous_task(task.description):
                tasks[i] = task.model_copy(update={"agent": "deep"})

        # 截断到 max_tasks + 清理悬空 depends_on
        max_tasks = settings.team_max_tasks
        if len(tasks) > max_tasks:
            tasks = tasks[:max_tasks]
            valid_ids = {t.id for t in tasks}
            for i, task in enumerate(tasks):
                cleaned = [d for d in task.depends_on if d in valid_ids]
                if len(cleaned) != len(task.depends_on):
                    tasks[i] = task.model_copy(update={"depends_on": cleaned})

        return TeamPlan(
            tasks=tasks,
            summary=plan.summary,
            needs_iterative=plan.needs_iterative,
        )

    def _post_filter_replan(
        self, plan: TeamPlan, settings: Any, previous_plan: list[TeamTask]
    ) -> TeamPlan:
        """replan 产出的 plan 后处理：过滤掉已在 previous_plan 中的任务 id。

        replan 只返回追加的新任务，避免重复执行已完成任务。
        """
        if not plan.tasks:
            return plan

        prev_ids = {t.id for t in previous_plan}
        new_tasks = [t for t in plan.tasks if t.id not in prev_ids]
        return self._post_filter(TeamPlan(tasks=new_tasks), settings)
