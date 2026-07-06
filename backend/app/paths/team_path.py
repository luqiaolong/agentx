"""AgentTeam 路径（路径 D）：多代理协作。

核心流程：
1. Orchestrator 把用户任务拆成子任务计划（JSON）。
2. Scheduler 并行调度子任务到对应专家（code / rag / web / custom / deep）。
3. 每个子代理结果写入共享黑板（blackboard）。
4. Aggregator 综合黑板内容生成最终回复。

安全：
- 普通子代理只调用只读/安全工具；写/编辑/shell 等危险任务必须指定为 deep 子任务，
  由 run_deep_path 执行并走 interrupt_before 审批。
- 若 Orchestrator 把危险任务误分配给普通子代理，后端会强制改写为 deep 子任务。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

from app.config import get_settings
from app.llm import get_chat_model
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.paths.deep_path import run_deep_path
from app.subagents import (
    run_code_agent,
    run_custom_agent,
    run_rag_agent,
    run_web_agent,
)
from app.utils.text import ThinkFilter, extract_chunk_text

if TYPE_CHECKING:
    from app.router.state import RouterState


# Orchestrator prompt：要求输出 JSON 计划
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

# Aggregator prompt
_AGGREGATOR_PROMPT = (
    "你是团队汇总专家。以下是一群专家针对用户问题的协作结果。\n\n"
    "用户问题：{user_message}\n\n"
    "专家发现：\n{blackboard_summary}\n\n"
    "失败说明：\n{error_summary}\n\n"
    "请综合以上信息，给出完整、准确的最终回答。"
    "如果专家结果有冲突，请说明并给出判断依据。"
    "保持回答简洁，使用标准 Markdown。"
)


@dataclass
class TeamPlanTask:
    """Orchestrator 产出的单个子任务。"""

    agent: str
    input: str
    purpose: str


@dataclass
class TeamSubtaskResult:
    """单个子任务的执行结果。"""

    agent: str
    success: bool
    payload: str


@dataclass
class Blackboard:
    """AgentTeam 共享黑板：保存每个专家的结果摘要与错误信息。"""

    findings: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


# 子任务完成哨兵事件类型（内部使用，绝不输出到前端）
_SUBTASK_DONE_EVENT = "_subtask_done"

# deep 子任务中需要实时透传到前端的事件类型
# approval_request 必须直达前端，否则 DeepAgent 审批流会死锁
# tool_result 必须透传，否则前端 tool_call 配对断裂
# reasoning 透传供前端展示 deep 子任务的思考过程
_PASSTHROUGH_EVENTS: frozenset[str] = frozenset(
    {"approval_request", "todo_update", "delegation", "tool_call", "tool_result", "reasoning"}
)


def _build_project_context() -> str:
    """构建项目上下文摘要，供 Orchestrator 拆任务时参考。

    包含 AGENTS.md §11 文件地图的关键路径，避免 subagent 盲探索。
    """
    lines = [
        "项目结构（agentx）：",
        "- 后端 Python: backend/app/（FastAPI + LangGraph）",
        "  - router/ (classifier.py, graph.py, state.py) — 消息分类 + StateGraph",
        "  - paths/ (chat_path.py, tool_path.py, deep_path.py, team_path.py) — 四路径",
        "  - subagents/ (code/rag/web/custom) — 子代理",
        "  - tools/ (filesystem + rag_retrieve) — 工具",
        "- 前端 Electron+React: frontend/",
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
        return True, ""
    return False, f"未知 agent 类型: {task.agent}"


async def _run_subtask(
    task: TeamPlanTask,
    thread_id: str,
    history: list | None,
    permission_mode: str,
    scene_prompt: str | None,
    state: RouterState,
    profile_prompt: str,
    task_index: int = 0,
) -> AsyncIterator[dict[str, str]]:
    """执行单个子任务，流式产出透传事件，最后产出 _subtask_done 哨兵。

    deep 子任务复用 DeepAgent 路径，其 approval_request / todo_update /
    delegation / tool_call 事件必须实时透传到前端——否则审批流会死锁。
    其余子代理（code/rag/web/custom）只产出文本与工具痕迹，无需透传。

    Args:
        task_index: 子任务序号，用于为 deep 子任务生成独立 thread_id，
            避免并行 deep 子任务共享 checkpoint 与审批流冲突。
    """
    agent_name = task.agent
    input_text = task.input

    collected_text: list[str] = []
    tool_traces: list[str] = []

    def _done(success: bool, payload: str) -> dict[str, str]:
        return _make_team_event(
            _SUBTASK_DONE_EVENT,
            {"agent": agent_name, "success": success, "payload": payload},
        )

    if agent_name == "deep":
        # deep 子任务使用独立 thread_id，避免并行 deep 子任务共享 checkpoint
        # 与 _pending_approvals 审批流冲突
        deep_thread_id = f"{thread_id}-team-deep-{task_index}"
        deep_state: RouterState = {
            "thread_id": deep_thread_id,
            "messages": [{"role": "user", "content": input_text}],
        }
        try:
            async for event in run_deep_path(
                deep_state,
                input_text,
                profile_prompt=profile_prompt,
                history=history,
                permission_mode=permission_mode,
                scene_prompt=scene_prompt,
            ):
                etype = event.get("event", "")
                data = event.get("data", "")
                if etype == "token":
                    collected_text.append(str(data))
                elif etype == "tool_result":
                    # 收集工具痕迹用于 summary，同时透传到前端（配对 tool_call）
                    try:
                        obj = json.loads(data) if isinstance(data, str) else data
                        if isinstance(obj, dict):
                            tool_traces.append(f"{obj.get('name', '?')}: {str(obj.get('result', ''))[:200]}")
                    except Exception:  # noqa: BLE001
                        pass
                    yield event
                elif etype == "error":
                    yield _done(False, f"deep 子任务失败: {data}")
                    return
                elif etype in _PASSTHROUGH_EVENTS:
                    # 透传审批/委派/工具/推理事件到前端
                    yield event
        except Exception as exc:  # noqa: BLE001
            yield _done(False, f"deep 子任务异常: {exc}")
            return
    elif agent_name == "code":
        async for event in run_code_agent(thread_id, input_text, history=history):
            _collect_event(event, collected_text, tool_traces)
    elif agent_name == "rag":
        async for event in run_rag_agent(thread_id, input_text, history=history):
            _collect_event(event, collected_text, tool_traces)
    elif agent_name == "web":
        async for event in run_web_agent(thread_id, input_text, history=history):
            _collect_event(event, collected_text, tool_traces)
    # 软件开发专家团角色：用 custom_agent 工厂构建专属 agent，复用 astream_events 事件流
    elif agent_name in ("frontend_dev", "backend_dev", "tester", "architect", "devops", "ui_designer", "product_manager"):
        cfg = get_settings().team_subagents.get(agent_name)
        if cfg and cfg.system_prompt:
            # MUST 传 thread_id：_make_custom_tools 用 thread_id 绑定沙箱授权
            from app.subagents.custom_agent import build_custom_agent
            agent = build_custom_agent(
                key=agent_name,
                thread_id=thread_id,
                system_prompt=cfg.system_prompt,
                tools=cfg.tools,
                temperature=cfg.temperature,
            )
            history_msgs = list(history) if history else []
            inputs = {"messages": [*history_msgs, {"role": "user", "content": input_text}]}
            async for event in agent.astream_events(inputs, version="v2"):
                kind = event["event"]
                ename = event.get("name", "")
                edata = event.get("data", {}) or {}
                if kind == "on_chat_model_stream":
                    content = extract_chunk_text(edata.get("chunk"), strip=False)
                    if content:
                        collected_text.append(content)
                elif kind == "on_tool_start":
                    tool_traces.append(f"{ename}: {str(edata.get('input', ''))[:200]}")
                elif kind == "on_tool_end":
                    tool_traces.append(f"{ename}: {str(edata.get('output', ''))[:200]}")
        else:
            # 无专属配置时降级到 code_agent
            async for event in run_code_agent(thread_id, input_text, history=history):
                _collect_event(event, collected_text, tool_traces)
    elif agent_name.startswith("custom-"):
        key = agent_name[len("custom-"):]
        async for event in run_custom_agent(key, thread_id, input_text, history=history):
            _collect_event(event, collected_text, tool_traces)
    else:
        yield _done(False, f"未知 agent: {agent_name}")
        return

    # 直接检查输出是否为空，不依赖字符串匹配
    if not collected_text and not tool_traces:
        yield _done(False, _build_summary(collected_text, tool_traces, agent_name))
        return
    yield _done(True, _build_summary(collected_text, tool_traces, agent_name))


def _collect_event(event: dict, text_parts: list[str], tool_traces: list[str]) -> None:
    etype = event.get("type", "")
    if etype == "token":
        text_parts.append(str(event.get("content", "")))
    elif etype == "tool_result":
        name = str(event.get("name", "?"))
        result = str(event.get("result", ""))[:200]
        tool_traces.append(f"{name}: {result}")


def _build_summary(text_parts: list[str], tool_traces: list[str], agent_name: str) -> str:
    settings = get_settings()
    max_chars = settings.agent_team_result_max_chars
    full_text = "".join(text_parts).strip()
    if not full_text and not tool_traces:
        return f"[{agent_name}] 未返回有效内容"
    # 若文本较长，取前 max_chars
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n[结果已截断]"
    summary = full_text
    if tool_traces:
        traces = "\n".join(tool_traces[:5])
        summary += f"\n\n工具痕迹：\n{traces}"
    return summary.strip()


def _make_team_event(event: str, data: Any) -> dict[str, str]:
    """构造标准 SSE 事件 dict（与 router/graph.py 的 _sse 约定一致）。

    - token: data 为纯字符串（前端直接拼接，不做 JSON.parse）
    - team_plan / team_progress / team_result / reasoning / error / _subtask_done:
      data 为 JSON 字符串（dict 会被 json 序列化）
    - done: data 为 "{}"
    """
    if event in (
        "team_plan",
        "team_progress",
        "team_result",
        "team_done",
        "reasoning",
        "error",
        _SUBTASK_DONE_EVENT,
    ):
        if isinstance(data, str):
            return {"event": event, "data": data}
        return {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}
    if event == "done":
        return {"event": "done", "data": "{}"}
    return {"event": event, "data": str(data)}


def _serialize_blackboard(blackboard: Blackboard) -> str:
    lines: list[str] = []
    for agent_name, finding in blackboard.findings.items():
        lines.append(f"--- {agent_name} ---")
        lines.append(finding)
    for agent_name, error in blackboard.errors.items():
        lines.append(f"--- {agent_name} [失败] ---")
        lines.append(error)
    return "\n\n".join(lines)


def _quality_gate(blackboard: Blackboard) -> tuple[bool, str]:
    """Aggregator 质量门：检查黑板结果质量。

    Returns:
        (ok, reason) — ok=False 时 reason 说明拒绝原因
    """
    if not blackboard.findings:
        return False, "无任何成功的子任务结果"
    unique_findings = set(blackboard.findings.values())
    if len(unique_findings) == 1 and len(blackboard.findings) > 1:
        return False, "所有子任务返回相同内容，疑似未实际执行"
    truncated_only = all(
        "[结果已截断]" in v and len(v.strip()) < 50
        for v in blackboard.findings.values()
    )
    if truncated_only:
        return False, "所有结果均为截断片段，无有效内容"
    return True, ""


async def _run_aggregator(
    user_message: str,
    blackboard: Blackboard,
) -> AsyncIterator[dict[str, str]]:
    """调用 Aggregator LLM，流式输出最终回复。"""
    settings = get_settings()

    # 质量门检查
    ok, reason = _quality_gate(blackboard)
    if not ok:
        logger.warning("team aggregator quality gate rejected", reason=reason)
        yield _make_team_event(
            "error",
            {"message": f"专家结果质量不足: {reason}"},
        )
        return

    try:
        llm = get_chat_model(temperature=0.5, streaming=True)
    except ValueError as exc:
        yield _make_team_event("error", {"message": f"LLM 不可用: {exc}"})
        return

    prompt = _AGGREGATOR_PROMPT.format(
        user_message=user_message,
        blackboard_summary=_serialize_blackboard(blackboard),
        error_summary="\n".join(f"{k}: {v}" for k, v in blackboard.errors.items()) or "无",
    )

    think_filter = ThinkFilter(max_hold=settings.think_filter_max_hold, retain_think=True)
    try:
        async for chunk in llm.astream([{"role": "user", "content": prompt}]):
            raw = extract_chunk_text(chunk, strip=False)
            cleaned = think_filter.feed(raw)
            if getattr(think_filter, "_retain_think", False):
                reasoning = think_filter.take_think()
                if reasoning:
                    yield _make_team_event("reasoning", {"content": reasoning, "source": "team"})
            if cleaned:
                yield _make_team_event("token", cleaned)
        tail = think_filter.flush()
        if tail:
            yield _make_team_event("token", tail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("team aggregator stream failed", error=str(exc))
        yield _make_team_event("error", {"message": f"Aggregator 流式失败: {exc}"})


_SIMPLE_TASK_KEYWORDS = frozenset({
    "你好", "hello", "hi", "谢谢", "翻译", "解释", "什么是",
    "总结", "摘要",
})


def _should_downgrade_to_single(message: str) -> tuple[bool, str]:
    """评估是否应降级到单 agent 路径。

    Returns:
        (downgrade, reason) — downgrade=True 时应走单 agent
    """
    lower = message.lower().strip()
    if len(lower) < 10:
        return True, "消息过短，无需 team 协作"
    if any(kw in lower for kw in _SIMPLE_TASK_KEYWORDS):
        return True, "命中简单任务关键词"
    return False, ""


async def run_team_path(
    message: str,
    thread_id: str,
    state: RouterState,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "workspace",
    scene_prompt: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    """AgentTeam 路径入口。

    1. Orchestrator 拆任务 → team_plan 事件
    2. 并行执行子任务 → team_progress / team_result 事件
    3. Aggregator 汇总 → token / reasoning 事件
    """
    settings = get_settings()

    # 简单任务降级：短消息 / 问候 / 翻译等无需 team 协作，直接走 chat 路径
    # 避免浪费 Orchestrator + Aggregator 两次 LLM 调用
    downgrade, reason = _should_downgrade_to_single(message)
    if downgrade:
        logger.info("team downgrade to chat", reason=reason, message_len=len(message))
        # 延迟 import 避免循环依赖（graph.py 顶层 import team_path）
        from app.router.graph import _run_chat_path
        async for sse in _run_chat_path(
            message,
            thread_id,
            system_prompt_extra=profile_prompt or None,
            history=history,
            scene_prompt=scene_prompt,
        ):
            yield sse
        return

    max_tasks = settings.agent_team_max_tasks
    max_parallel = settings.agent_team_max_parallel

    # 构建项目上下文：AGENTS.md 文件地图 + 关键目录结构
    context = _build_project_context()

    with trace_span("team.run", thread_id=thread_id, message_len=len(message)):
        # ---- 1. Orchestrator 拆任务 ----
        try:
            llm = get_chat_model(temperature=0.3, streaming=False)
        except ValueError as exc:
            yield _make_team_event("error", {"message": f"LLM 不可用: {exc}"})
            return

        orchestrator_prompt = _build_orchestrator_prompt(message, max_tasks, context=context)
        try:
            response = await llm.ainvoke(orchestrator_prompt)
            raw_text = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:  # noqa: BLE001
            logger.warning("team orchestrator invoke failed", error=str(exc))
            yield _make_team_event("error", {"message": f"Orchestrator 调用失败: {exc}"})
            return

        plan, reasoning = _parse_plan(raw_text, max_tasks)
        if not plan:
            yield _make_team_event("error", {"message": "Orchestrator 未生成有效计划"})
            return

        yield _make_team_event(
            "team_plan",
            {
                "plan": [
                    {"agent": t.agent, "input": t.input, "purpose": t.purpose}
                    for t in plan
                ],
                "reasoning": reasoning,
            },
        )

        # ---- 2. 并行执行子任务 ----
        blackboard = Blackboard()
        # 校验并标记不可用任务
        valid_tasks: list[TeamPlanTask] = []
        for task in plan:
            ok, err = _validate_task(task, settings)
            if ok:
                valid_tasks.append(task)
            else:
                blackboard.errors[task.agent] = err
                yield _make_team_event(
                    "team_progress",
                    {"agent": task.agent, "status": "error", "message": err},
                )

        # 队列驱动的并行执行：deep 子任务的 approval_request 等事件实时透传
        # 若用 asyncio.gather 直接收集结果，deep 子任务的审批事件会被吞掉导致死锁
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        semaphore = asyncio.Semaphore(max_parallel)

        async def _runner(t: TeamPlanTask, idx: int) -> None:
            async with semaphore:
                # 实际开始执行时才发 running
                await queue.put(
                    _make_team_event(
                        "team_progress",
                        {"agent": t.agent, "status": "running", "message": t.purpose},
                    )
                )
                try:
                    async for ev in _run_subtask(
                        t, thread_id, history, permission_mode, scene_prompt, state, profile_prompt,
                        task_index=idx,
                    ):
                        await queue.put(ev)
                except Exception as exc:  # noqa: BLE001
                    await queue.put(
                        _make_team_event(
                            _SUBTASK_DONE_EVENT,
                            {
                                "agent": t.agent,
                                "success": False,
                                "payload": f"{t.agent} 子任务异常: {exc}",
                            },
                        )
                    )

        runner_tasks = [
            asyncio.create_task(_runner(t, idx)) for idx, t in enumerate(valid_tasks)
        ]

        results: dict[str, TeamSubtaskResult] = {}
        done_count = 0
        total = len(valid_tasks)
        subtask_timeout = settings.agent_team_subtask_timeout
        while done_count < total:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=subtask_timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "team subtask timeout",
                    done_count=done_count,
                    total=total,
                    timeout=subtask_timeout,
                )
                for rt in runner_tasks:
                    if not rt.done():
                        rt.cancel()
                for task in valid_tasks:
                    if task.agent not in results:
                        err_msg = f"{task.agent} 子任务超时（{subtask_timeout}s）"
                        results[task.agent] = TeamSubtaskResult(
                            agent=task.agent, success=False, payload=err_msg,
                        )
                        blackboard.errors[task.agent] = err_msg
                        yield _make_team_event(
                            "team_progress",
                            {"agent": task.agent, "status": "error", "message": err_msg},
                        )
                break

            if event["event"] == _SUBTASK_DONE_EVENT:
                done_count += 1
                obj = json.loads(event["data"])
                results[obj["agent"]] = TeamSubtaskResult(
                    agent=obj["agent"],
                    success=obj["success"],
                    payload=obj["payload"],
                )
            else:
                # 透传事件（approval_request / todo_update / delegation / tool_call）
                yield event

        await asyncio.gather(*runner_tasks, return_exceptions=True)

        for task in valid_tasks:
            agent_name = task.agent
            res = results.get(agent_name)
            if res is None:
                err_msg = f"{agent_name} 子任务未返回结果"
                blackboard.errors[agent_name] = err_msg
                yield _make_team_event(
                    "team_progress",
                    {"agent": agent_name, "status": "error", "message": err_msg},
                )
            elif res.success:
                blackboard.findings[agent_name] = res.payload
                yield _make_team_event(
                    "team_progress",
                    {"agent": agent_name, "status": "done", "message": task.purpose},
                )
                yield _make_team_event(
                    "team_result",
                    {"agent": agent_name, "summary": res.payload},
                )
            else:
                blackboard.errors[agent_name] = res.payload
                yield _make_team_event(
                    "team_progress",
                    {"agent": agent_name, "status": "error", "message": res.payload},
                )

        if not blackboard.findings:
            yield _make_team_event("error", {"message": "所有专家任务均失败"})
            yield _make_team_event("team_done", {"status": "error"})
            return

        # ---- 3. Aggregator 汇总 ----
        async for sse in _run_aggregator(message, blackboard):
            yield sse

        # team 整体结束
        has_error = bool(blackboard.errors)
        yield _make_team_event(
            "team_done",
            {"status": "error" if has_error and not blackboard.findings else "done"},
        )


__all__ = ["run_team_path", "Blackboard", "TeamPlanTask", "TeamSubtaskResult"]
