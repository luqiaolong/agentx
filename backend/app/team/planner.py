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
    "_parse_after_deps",
    "_parse_todos_from_text",
    "_todos_to_team_tasks",
    "_validate_dag",
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
    return [
        {"content": task.input, "status": "pending"}
        for task in tasks
    ]


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
        todos.append({
            "content": content,
            "status": "pending",
            "deps": deps,
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
    """从 ``state.todos`` 解析子任务，转换为 ``TeamPlanTask`` 列表（含 deps 字段）。

    解析每个 todo 的 content 前缀 ``[agent:xxx][after:N1,N2]`` 确定 agent 类型与
    依赖任务索引，剥离前缀后的内容作为 input。调用 ``_validate_task`` 校验 +
    ``_looks_like_dangerous_task`` 安全改写。

    截断到 ``settings.agent_team_max_tasks`` 时同步清理 deps：引用被截断任务索引
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

    max_tasks = settings.agent_team_max_tasks
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
