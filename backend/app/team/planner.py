"""AgentTeam Orchestrator：拆解任务为子任务计划。

包含：
- ``_BASE_EXPERTS`` / ``_TEAM_EXPERTS``：可用专家清单（基础 + coding 场景团队角色）。
- ``_ORCHESTRATOR_PROMPT``：Orchestrator 系统 prompt 模板。
- ``_build_project_context``：构建项目上下文摘要，避免 subagent 盲探索。
- ``_build_orchestrator_prompt``：根据场景组装 Orchestrator prompt。
- ``_parse_plan``：从 LLM 输出中提取 JSON 计划并校验（含危险任务强制改写 deep）。
- ``_try_parse_json`` / ``_extract_codeblock`` / ``_extract_first_json_object``：
  JSON 提取辅助（支持纯 JSON / markdown 代码块 / 前后带额外文本三种形态）。
- ``_looks_like_dangerous_task``：启发式判断子任务是否涉及危险操作。
- ``_validate_task``：校验子任务 agent 是否可用（含 custom / 团队角色）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.observability.logger import logger
from app.team.blackboard import TeamPlanTask

__all__ = [
    "_BASE_EXPERTS",
    "_TEAM_EXPERTS",
    "_ORCHESTRATOR_PROMPT",
    "_build_project_context",
    "_build_orchestrator_prompt",
    "_parse_plan",
    "_try_parse_json",
    "_extract_codeblock",
    "_extract_first_json_object",
    "_looks_like_dangerous_task",
    "_validate_task",
]


# 基础专家（始终可用）
_BASE_EXPERTS = (
    "- code: 读取/搜索代码与文件，只读工具（read_file/list_dir/glob/grep）。\n"
    "- rag: 从向量知识库检索文档。\n"
    "- web: 联网搜索实时信息。\n"
    "- deep: 执行需要写文件、编辑文件或系统命令的危险任务（会走审批）。\n"
)

# 软件开发专家团角色（coding 场景下可用）
_TEAM_EXPERTS = (
    "- frontend_dev: 前端开发专家，擅长 React/Vue/HTML/CSS/JS/TS、组件开发、前端性能优化。\n"
    "- backend_dev: 后端开发专家，擅长 Python/Java/Go/Node.js、API 设计、数据库、业务逻辑。\n"
    "- tester: 测试专家，擅长单元测试/集成测试/E2E、测试框架、覆盖率分析。\n"
    "- architect: 架构专家，擅长系统设计、技术选型、性能优化、微服务架构。\n"
    "- devops: 运维专家，擅长 CI/CD、Docker/K8s、部署流水线、监控告警。\n"
    "- ui_designer: UI 设计师，擅长界面设计、交互设计、视觉规范、用户体验。\n"
    "- product_manager: 产品专家，擅长需求分析、PRD 撰写、用户故事、功能规划。\n"
)

_ORCHESTRATOR_PROMPT = (
    "你是一个任务拆解专家（Orchestrator）。请把用户请求拆分成若干子任务，"
    "每个子任务指定一个执行专家和输入。"
    "\n\n可用专家：\n"
    "{experts}"
    "\n项目上下文：\n{context}\n"
    "\n输出必须是严格 JSON，不要 markdown 代码块，不要额外解释：\n"
    "{{\n"
    '  "reasoning": "为什么这样拆任务",\n'
    '  "plan": [\n'
    '    {{"agent": "code", "input": "具体子任务输入", "purpose": "目的说明"}}\n'
    "  ]\n"
    "}}\n"
    "\n约束：\n"
    "1. 如果任务涉及写文件、编辑文件、执行系统命令，agent 必须设为 deep。\n"
    "2. 不要编造文件路径；若用户没给路径，子任务输入里说明需要搜索或推断。\n"
    "3. 子任务数量不要超过 {max_tasks} 个。\n"
    "4. 若任务简单，可只返回一个子任务。\n"
    "5. 子任务输入中应引用项目上下文里的具体路径，避免 subagent 盲探索。\n"
    "6. 若用户请求涉及多个软件开发环节（如前端+后端+测试），优先使用团队角色（frontend_dev/backend_dev/tester 等）而非通用 code。\n"
)


def _build_project_context() -> str:
    """构建项目上下文摘要，供 Orchestrator 拆任务时参考。

    包含 AGENTS.md §11 文件地图的关键路径，避免 subagent 盲探索。
    """
    lines = [
        "项目结构（agentx）：",
        "- 后端 Python: backend/app/（FastAPI + LangGraph）",
        "  - router/ (classifier.py, graph.py, state.py) — 消息分类 + StateGraph",
        "  - chat/ (run.py), subagents/ (dispatch.py), deep/ (agent.py), team/ (orchestrator.py)",
        "  - subagents/ (code/rag/web/custom) — 子代理",
        "  - tools/ (filesystem + rag_retrieve) — 工具",
        "- 前端 Tauri+React: frontend/",
        "  - renderer/components/chat/ — 聊天组件",
        "  - renderer/hooks/useChatStream.ts — SSE 事件处理",
        "  - renderer/stores/ (chat.ts, agentMode.ts) — zustand 状态",
        "- 配置: AGENTS.md（工程规范 + 文件地图 + Router 路径说明）",
    ]
    return "\n".join(lines)


def _build_orchestrator_prompt(user_message: str, max_tasks: int, context: str = "", scene: str = "work") -> str:
    """构建 Orchestrator prompt，根据场景选择可用专家。"""
    experts = _BASE_EXPERTS
    if scene == "coding":
        experts = _BASE_EXPERTS + _TEAM_EXPERTS
    return (
        _ORCHESTRATOR_PROMPT.format(max_tasks=max_tasks, context=context, experts=experts)
        + f"\n\n用户请求：{user_message}"
    )


def _parse_plan(text: str, max_tasks: int) -> tuple[list[TeamPlanTask], str]:
    """从 Orchestrator 输出中提取 JSON 计划并校验。

    支持三种 LLM 输出形态：
    1. 纯 JSON 对象（理想情况）
    2. markdown 代码块包裹的 JSON（```json ... ```）
    3. JSON 对象前后有额外文本（"好的，这是计划：{...}"）

    Returns:
        (tasks, reasoning)
    """
    text = text.strip()

    # 策略 1：直接解析（最快路径）
    data = _try_parse_json(text)
    if data is None:
        # 策略 2：提取 markdown 代码块内容
        codeblock = _extract_codeblock(text)
        if codeblock:
            data = _try_parse_json(codeblock)
    if data is None:
        # 策略 3：从文本中提取第一个 {...} 对象
        extracted = _extract_first_json_object(text)
        if extracted:
            data = _try_parse_json(extracted)

    if data is None:
        logger.warning("team orchestrator output is not valid JSON", raw_len=len(text))
        return [], ""

    if not isinstance(data, dict):
        return [], ""

    plan = data.get("plan", [])
    if not isinstance(plan, list):
        return [], ""

    tasks: list[TeamPlanTask] = []
    for item in plan[:max_tasks]:
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent", "")).strip().lower()
        input_text = str(item.get("input", "")).strip()
        purpose = str(item.get("purpose", "")).strip()
        if not agent or not input_text:
            continue
        # 安全改写：涉及危险工具关键词但非 deep 的任务强制改为 deep
        if agent != "deep" and _looks_like_dangerous_task(input_text):
            agent = "deep"
        tasks.append(TeamPlanTask(agent=agent, input=input_text, purpose=purpose))

    if len(plan) > max_tasks:
        logger.warning(
            "team orchestrator plan truncated",
            original=len(plan),
            max_tasks=max_tasks,
        )

    reasoning = str(data.get("reasoning", "")).strip()
    return tasks, reasoning


def _try_parse_json(text: str) -> Any | None:
    """尝试解析 JSON，失败返回 None。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_codeblock(text: str) -> str | None:
    """从 markdown 代码块中提取内容（```json ... ``` 或 ``` ... ```）。"""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _extract_first_json_object(text: str) -> str | None:
    """从文本中提取第一个 {...} 对象（平衡括号匹配）。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


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
    if task.agent in ("code", "rag", "web"):
        cfg = settings.subagents.get(task.agent)
        if not cfg or not cfg.enabled:
            return False, f"子代理 {task.agent} 已禁用"
        if not any(settings.tools_enabled.get(t, True) for t in cfg.tools):
            return False, f"子代理 {task.agent} 绑定的工具全部被禁用"
        return True, ""
    # 软件开发团队角色（仅 coding 场景下可用）
    team_keys = {"frontend_dev", "backend_dev", "tester", "architect", "devops", "ui_designer", "product_manager"}
    if task.agent in team_keys:
        cfg = settings.team_subagents.get(task.agent)
        if not cfg or not cfg.enabled:
            return False, f"团队角色 {task.agent} 已禁用"
        if not any(settings.tools_enabled.get(t, True) for t in cfg.tools):
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
