"""AgentTeam Orchestrator：拆解任务为子任务计划。

包含：
- ``_BASE_EXPERTS``：基础专家清单（硬编码：code / rag / web / deep）。
- ``_build_team_experts_description``：声明式团队角色描述生成器，从
  ``settings.team_subagents`` 动态生成团队专家清单。
- ``_ORCHESTRATOR_PROMPT``：Orchestrator 系统 prompt 模板。
- ``_build_project_context``：构建项目上下文摘要，避免 subagent 盲探索。
- ``_build_orchestrator_prompt``：根据场景组装 Orchestrator prompt。
- ``TeamPlan`` / ``TeamPlanItem``：结构化输出 schema（配合
  ``llm.with_structured_output(TeamPlan)``，避免手写 JSON 解析）。
- ``_postprocess_plan``：对结构化输出做截断 + 危险任务强制改写 deep。
- ``_looks_like_dangerous_task``：启发式判断子任务是否涉及危险操作。
- ``_validate_task``：校验子任务 agent 是否可用（含 custom / 团队角色）。
  团队角色集合由 ``BUILTIN_TEAM_KEYS`` 决定（不硬编码字面量）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.config.subagents import BUILTIN_TEAM_KEYS
from app.observability.logger import logger
from app.team.blackboard import TeamPlanTask

__all__ = [
    "TeamPlan",
    "TeamPlanItem",
    "_BASE_EXPERTS",
    "_ORCHESTRATOR_PROMPT",
    "_build_project_context",
    "_build_orchestrator_prompt",
    "_build_team_experts_description",
    "_looks_like_dangerous_task",
    "_postprocess_plan",
    "_validate_task",
]


class TeamPlanItem(BaseModel):
    """单个子任务的结构化输出项。"""

    agent: str = Field(description="执行专家：code / rag / web / deep / 团队角色 / custom-*")
    input: str = Field(description="子任务输入，具体到文件路径或搜索词")
    purpose: str = Field(default="", description="该子任务的目的说明")


class TeamPlan(BaseModel):
    """Orchestrator 结构化输出 schema：拆解后的子任务计划。"""

    reasoning: str = Field(default="", description="为什么这样拆任务的推理")
    plan: list[TeamPlanItem] = Field(default_factory=list, description="子任务列表")


# 基础专家（始终可用）
_BASE_EXPERTS = (
    "- code: 读取/搜索代码与文件，只读工具（read_file/list_dir/glob/grep）。\n"
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

_ORCHESTRATOR_PROMPT = ChatPromptTemplate.from_messages([
    (
        "human",
        "你是一个任务拆解专家（Orchestrator）。请把用户请求拆分成若干子任务，"
        "每个子任务指定一个执行专家和输入。"
        "\n\n可用专家：\n"
        "{experts}"
        "\n项目上下文：\n{context}\n"
        "\n按结构化输出返回计划（reasoning + plan 列表，每项含 agent/input/purpose）。\n"
        "\n约束：\n"
        "1. 如果任务涉及写文件、编辑文件、执行系统命令，agent 必须设为 deep。\n"
        "2. 不要编造文件路径；若用户没给路径，子任务输入里说明需要搜索或推断。\n"
        "3. 子任务数量不要超过 {max_tasks} 个。\n"
        "4. 若任务简单，可只返回一个子任务。\n"
        "5. 子任务输入中应引用项目上下文里的具体路径，避免 subagent 盲探索。\n"
        "6. 若用户请求涉及多个软件开发环节（如前端+后端+测试），优先使用团队角色（frontend_dev/backend_dev/tester 等）而非通用 code。\n"
        "\n\n用户请求：{user_message}",
    ),
])


def _build_project_context() -> str:
    """构建项目上下文摘要，供 Orchestrator 拆任务时参考。

    包含 AGENTS.md §11 文件地图的关键路径，避免 subagent 盲探索。
    """
    lines = [
        "项目结构（agentx）：",
        "- 后端 Python: backend/app/（FastAPI + LangGraph）",
        "  - router/ (graph.py, state.py) — 场景化分发 StateGraph",
        "  - agents/ (supervisor/, expert/, team/) — 场景化智能体",
        "  - subagents/ (rag/web/custom) — 子代理（code 已由 coding Expert 取代）",
        "  - deep/ (agent.py) — DeepAgent 框架（供 coding Expert 复用）",
        "  - team/ (orchestrator.py) — AgentTeam 多代理协作",
        "  - tools/ (filesystem + rag_retrieve) — 工具",
        "- 前端 Tauri+React: frontend/",
        "  - renderer/components/chat/ — 聊天组件",
        "  - renderer/hooks/useChatStream.ts — SSE 事件处理",
        "  - renderer/stores/ (chat.ts, agentMode.ts) — zustand 状态",
        "- 配置: AGENTS.md（工程规范 + 文件地图 + Router 场景分发说明）",
    ]
    return "\n".join(lines)


def _build_orchestrator_prompt(
    user_message: str,
    max_tasks: int,
    context: str = "",
    scene: str = "work",
    settings: Any | None = None,
) -> Any:
    """构建 Orchestrator prompt，根据场景选择可用专家。

    coding 场景下，团队角色清单从 ``settings.team_subagents`` 动态生成。
    返回 ``ChatPromptValue``（LangChain 标准 messages 列表），供
    ``llm.with_structured_output(TeamPlan).ainvoke(prompt)`` 直接消费。
    """
    experts = _BASE_EXPERTS
    if scene == "coding" and settings is not None:
        team_desc = _build_team_experts_description(settings)
        if team_desc:
            experts = experts + "\n" + team_desc
    return _ORCHESTRATOR_PROMPT.invoke({
        "max_tasks": max_tasks,
        "context": context,
        "experts": experts,
        "user_message": user_message,
    })


def _postprocess_plan(plan: TeamPlan, max_tasks: int) -> tuple[list[TeamPlanTask], str]:
    """对结构化输出做截断 + 危险任务强制改写 deep。

    接收 ``llm.with_structured_output(TeamPlan)`` 的结果，做以下处理：
    - 截断到 ``max_tasks`` 条
    - 规范化 agent/input/purpose（strip + lower agent）
    - 跳过缺少 agent 或 input 的条目
    - 涉及危险操作但非 deep 的任务强制改写为 deep（安全约束）

    Returns:
        (tasks, reasoning)
    """
    raw_items = plan.plan or []
    tasks: list[TeamPlanTask] = []
    for item in raw_items[:max_tasks]:
        agent = (item.agent or "").strip().lower()
        input_text = (item.input or "").strip()
        purpose = (item.purpose or "").strip()
        if not agent or not input_text:
            continue
        # 安全改写：涉及危险工具关键词但非 deep 的任务强制改为 deep
        if agent != "deep" and _looks_like_dangerous_task(input_text):
            agent = "deep"
        tasks.append(TeamPlanTask(agent=agent, input=input_text, purpose=purpose))

    if len(raw_items) > max_tasks:
        logger.warning(
            "team orchestrator plan truncated",
            original=len(raw_items),
            max_tasks=max_tasks,
        )

    reasoning = (plan.reasoning or "").strip()
    return tasks, reasoning


def _looks_like_dangerous_task(input_text: str) -> bool:
    """启发式判断子任务是否涉及危险操作。"""
    dangerous_keywords = ["写入", "写文件", "write", "编辑", "修改", "edit", "执行命令", "shell", "运行脚本"]
    lower = input_text.lower()
    return any(kw in lower for kw in dangerous_keywords)


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
