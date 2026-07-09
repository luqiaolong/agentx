"""AgentTeam 路径：LangGraph StateGraph + Send API 编排多代理协作。

本模块合并了原 ``orchestrator`` / ``scheduler`` / ``planner`` / ``aggregator``
/ ``blackboard`` 五个模块的职责，统一用 LangGraph 原生 ``Send`` API 实现并行
子任务分发：

- **节点**:
  - ``plan_node``：Orchestrator 拆任务 → ``TeamPlan``
  - ``dispatch`` 条件边：把每个子任务 fan-out 到对应类型的执行节点
  - ``deep_node`` / ``code_node`` / ``builtin_node`` / ``team_role_node`` /
    ``custom_node`` / ``default_node``：按 agent 类型分派执行
  - ``aggregate_node``：汇总 findings → 最终 token/reasoning

- **并行**: 用 ``add_conditional_edges("plan", _dispatch)`` + ``Send`` 实现
  LangGraph 原生 fan-out，每个子任务一个独立节点调用。

- **状态归并**: ``findings`` / ``errors`` / ``subtask_results`` 用
  ``Annotated[dict, _merge_dict]`` reducer，并行节点返回值自动归并。

- **事件流**: 各子任务节点内通过 ``get_stream_writer()`` 写入 custom stream，
  ``run_team_path`` 通过 ``graph.astream(..., stream_mode="custom")`` 消费后
  yield 给调用方。

混合方案说明：
- **不用** ``asyncio.gather`` 在单个节点内手动 fan-out，改用 LangGraph 原生
  ``Send``：每个子任务一个独立节点调用，状态归并走 reducer，更符合框架语义。
- **保留** child_thread_id 隔离（``{parent}-team-{agent}-{idx}``）：避免并行
  子任务共享 checkpoint 与审批流冲突。
- **保留** workspace 授权继承：deep/code 子任务启动时调用
  ``_inherit_workspace``，避免 directory_extension 审批死锁。
- **保留** 审批 passthrough：deep 子任务的 approval_request / token /
  tool_result 事件通过 ``get_stream_writer()`` 实时透传。

安全：
- 危险任务（含写入/编辑/shell 关键词）由 planner 强制改写为 deep，由
  ``deep_node`` 走 ``run_deep_path`` 的 interrupt_on 审批。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    AsyncIterator,
    Callable,
    Mapping,
    TypedDict,
)

from langgraph.config import get_stream_writer
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm import get_chat_model
from app.observability.langsmith import trace_span
from app.observability.logger import logger
from app.security.approval import get_abort_event
from app.utils.sse_events import make_error_event, make_sse_event, make_team_event
from app.utils.text import ThinkFilter, extract_chunk_text

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.router.state import RouterState


# ============================================================
# 公共数据结构
# ============================================================


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
    """AgentTeam 共享黑板（向后兼容，``aggregate_node`` 用其构造 Aggregator prompt）。"""

    findings: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


# ============================================================
# StateGraph 状态 + reducer
# ============================================================


def _merge_dict(left: dict, right: dict) -> dict:
    """reducer：合并两个 dict（right 覆盖 left）。"""
    result = dict(left or {})
    result.update(right or {})
    return result


class TeamState(TypedDict, total=False):
    """StateGraph 主状态：plan → dispatch（Send fan-out）→ aggregate 节点共享。

    - ``findings`` / ``errors`` / ``subtask_results`` 使用 ``_merge_dict`` reducer，
      并行子任务节点的返回值由 reducer 自动归并。
    - ``total=False`` 允许初始化时只传部分字段。
    """

    message: str
    thread_id: str
    history: list
    permission_mode: str
    scene_prompt: str | None
    profile_prompt: str
    workspace_path: str | None
    chat_model: Any
    subtask_runners: Any
    plan: list
    reasoning: str
    findings: Annotated[dict[str, str], _merge_dict]
    errors: Annotated[dict[str, str], _merge_dict]
    subtask_results: Annotated[dict[str, dict], _merge_dict]


class SubtaskState(TypedDict, total=False):
    """单个子任务节点的状态（通过 ``Send(node, arg)`` 注入）。

    - ``task``: ``TeamPlanTask.__dict__``（Send 序列化需求，避免 dataclass 直传）。
    - ``parent_thread_id``: 父 thread_id，用于 child_thread_id 命名 + abort 继承。
    - 其余字段透传自 ``TeamState``（初始化 dispatch 时一次性注入）。
    """

    task: dict
    task_index: int
    parent_thread_id: str
    history: list
    permission_mode: str
    scene_prompt: str | None
    profile_prompt: str
    workspace_path: str | None
    chat_model: Any
    subtask_runners: Any


# ============================================================
# planner：Orchestrator prompt + 计划生成
# ============================================================


class TeamPlanItem(BaseModel):
    """单个子任务的结构化输出项。"""

    agent: str = Field(description="执行专家：code / rag / web / deep / 团队角色 / custom-*")
    input: str = Field(description="子任务输入，具体到文件路径或搜索词")
    purpose: str = Field(default="", description="该子任务的目的说明")


class TeamPlan(BaseModel):
    """Orchestrator 结构化输出 schema。"""

    reasoning: str = Field(default="", description="为什么这样拆任务的推理")
    plan: list[TeamPlanItem] = Field(default_factory=list, description="子任务列表")


_BASE_EXPERTS = (
    "- code: 读取/搜索代码与文件，只读工具（read_file/list_dir/glob/grep）。\n"
    "- rag: 从向量知识库检索文档。\n"
    "- web: 联网搜索实时信息。\n"
    "- deep: 执行需要写文件、编辑文件或系统命令的危险任务（会走审批）。\n"
)


_ORCHESTRATOR_PROMPT = ChatPromptTemplate.from_messages([
    (
        "human",
        "你是一个任务拆解专家（Orchestrator）。请把用户请求拆分成若干子任务，"
        "每个子任务指定一个执行专家和输入。"
        "\n\n可用专家：\n{experts}"
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


def _build_team_experts_description(settings: Any) -> str:
    """声明式生成团队角色描述（从 settings.team_subagents 动态生成）。"""
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
        desc = desc.strip().split("\n", 1)[0].strip()
        lines.append(f"- {key}: {desc}")
    return "\n".join(lines)


def _build_project_context() -> str:
    """构建项目上下文摘要。"""
    return "\n".join([
        "项目结构（agentx）：",
        "- 后端 Python: backend/app/（FastAPI + LangGraph）",
        "  - router/ (graph.py, state.py) — 场景化分发 StateGraph",
        "  - agents/ (supervisor/, expert/, team/) — 场景化智能体",
        "  - subagents/ (rag/web/custom) — 子代理",
        "  - deep/ (agent.py) — DeepAgent 框架",
        "  - team/ (langgraph.py) — AgentTeam 多代理协作（本模块）",
        "  - tools/ (filesystem + rag_retrieve) — 工具",
        "- 前端 Tauri+React: frontend/",
        "  - renderer/components/chat/ — 聊天组件",
        "  - renderer/hooks/useChatStream.ts — SSE 事件处理",
        "  - renderer/stores/ (chat.ts, agentMode.ts) — zustand 状态",
        "- 配置: AGENTS.md（工程规范 + 文件地图 + Router 场景分发说明）",
    ])


def _build_orchestrator_prompt(
    user_message: str,
    max_tasks: int,
    context: str = "",
    scene: str = "work",
    settings: Any | None = None,
) -> Any:
    """构建 Orchestrator prompt。"""
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


def _looks_like_dangerous_task(input_text: str) -> bool:
    """启发式判断子任务是否涉及危险操作。"""
    dangerous_keywords = ["写入", "写文件", "write", "编辑", "修改", "edit", "执行命令", "shell", "运行脚本"]
    lower = input_text.lower()
    return any(kw in lower for kw in dangerous_keywords)


def _postprocess_plan(plan: TeamPlan, max_tasks: int) -> tuple[list[TeamPlanTask], str]:
    """对结构化输出做截断 + 危险任务强制改写 deep。"""
    raw_items = plan.plan or []
    tasks: list[TeamPlanTask] = []
    for item in raw_items[:max_tasks]:
        agent = (item.agent or "").strip().lower()
        input_text = (item.input or "").strip()
        purpose = (item.purpose or "").strip()
        if not agent or not input_text:
            continue
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


def _validate_task(task: TeamPlanTask, settings: Any) -> tuple[bool, str]:
    """校验子任务 agent 是否可用。"""
    from app.config.subagents import BUILTIN_TEAM_KEYS

    if task.agent == "deep":
        return True, ""
    if task.agent == "code":
        return True, ""
    if task.agent in ("rag", "web"):
        cfg = settings.subagents.get(task.agent)
        if not cfg or not cfg.enabled:
            return False, f"子代理 {task.agent} 已禁用"
        if not any(settings.tools_enabled.get(t, True) for t in cfg.tools):
            return False, f"子代理 {task.agent} 绑定的工具全部被禁用"
        return True, ""
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


# ============================================================
# scheduler：子任务执行 + 事件透传
# ============================================================


# 子任务完成哨兵事件类型（内部使用，绝不输出到前端）
_SUBTASK_DONE_EVENT = "_subtask_done"

# 需要透传到前端的事件类型（审批事件必须实时透传，否则审批流会死锁）
_PASSTHROUGH_EVENTS: frozenset[str] = frozenset(
    {"approval_request", "todo_update", "delegation", "tool_call", "tool_result", "reasoning", "token"}
)


def _resolve_subtask_runners(
    subtask_runners: dict[str, Any] | None,
) -> dict[str, Any]:
    """解析子任务 runner 字典。``None`` 时 lazy-import 真实 runner。"""
    if subtask_runners is not None:
        return subtask_runners
    from app.agents.expert.coding import run_coding_expert
    from app.deep.agent import run_deep_path
    from app.subagents import run_custom_agent, run_rag_agent, run_web_agent

    return {
        "code": run_coding_expert,
        "deep": run_deep_path,
        "rag": run_rag_agent,
        "web": run_web_agent,
        "custom": run_custom_agent,
    }


def _get_runner(name: str, subtask_runners: dict[str, Any] | None) -> Any:
    """延迟解析子任务 runner（subtask_runners dict 优先，否则懒加载真实模块）。"""
    if subtask_runners and name in subtask_runners:
        return subtask_runners[name]
    return _resolve_subtask_runners(None).get(name)


async def _inherit_workspace(child_thread_id: str, workspace_path: str | None) -> None:
    """将父 thread 的 workspace 授权继承到子任务 thread。"""
    if not workspace_path:
        return
    from app.sandbox import get_sandbox

    sandbox = get_sandbox()
    try:
        await sandbox.authorize(child_thread_id, workspace_path, writable=True, source="team_inherit")
    except ValueError as exc:  # noqa: BLE001
        logger.warning(
            "team subtask workspace inherit failed",
            child_thread_id=child_thread_id,
            workspace=workspace_path,
            error=str(exc),
        )


def _route_event_for_node(
    event: dict,
    collected_text: list[str],
    tool_traces: list[str],
    writer: Callable[[dict], None],
    abort_event: Any,
) -> dict | None:
    """单事件路由：passthrough 写入 writer，token/error/done 内部处理。

    Returns:
        ``TeamSubtaskResult`` 表示哨兵事件到达（subtask 完成），None 表示中间事件。
    """
    etype = event.get("event", "") or event.get("type", "")
    data = event.get("data", "")
    if data == "" and etype == "token":
        # 兼容旧版 rag/web runner 的 {type: token, content: str} 格式
        data = event.get("content", "")
    if etype == "token":
        collected_text.append(str(data))
        return None
    if etype == "tool_result":
        try:
            obj = json.loads(data) if isinstance(data, str) else data
            if isinstance(obj, dict):
                tool_traces.append(
                    f"{obj.get('name', '?')}: {str(obj.get('result', ''))[:200]}"
                )
        except Exception:  # noqa: BLE001
            pass
        writer(event)
        return None
    if etype == "error":
        agent = event.get("data") or ""
        if isinstance(agent, str) and agent:
            # error 直接透传（不是哨兵）
            writer(event)
            return None
        return None
    if etype == _SUBTASK_DONE_EVENT:
        try:
            obj = json.loads(data) if isinstance(data, str) else {}
            return TeamSubtaskResult(
                agent=obj.get("agent", ""),
                success=bool(obj.get("success")),
                payload=str(obj.get("payload", "")),
            )
        except Exception:  # noqa: BLE001
            return TeamSubtaskResult(agent="", success=False, payload="哨兵事件解析失败")
    if etype in _PASSTHROUGH_EVENTS:
        writer(event)
    return None


async def _run_subtask_stream(
    runner: Callable[..., AsyncIterator[dict]],
    runner_args: tuple,
    runner_kwargs: dict,
    agent_name: str,
    abort_event: Any,
    writer: Callable[[dict], None],
) -> TeamSubtaskResult:
    """通用子任务流式执行：调用 runner，路由事件，返回结果。

    处理 abort / error / 异常 / 哨兵事件，passthrough 实时写 writer。
    """
    collected_text: list[str] = []
    tool_traces: list[str] = []
    try:
        stream = runner(*runner_args, **runner_kwargs)
        async for event in stream:
            if abort_event.is_set():
                writer(make_team_event("team_progress", {"agent": agent_name, "status": "error", "message": "用户中止"}))
                return TeamSubtaskResult(agent=agent_name, success=False, payload="用户中止")
            result = _route_event_for_node(event, collected_text, tool_traces, writer, abort_event)
            if result is not None:
                return result
    except Exception as exc:  # noqa: BLE001
        writer(make_team_event("team_progress", {"agent": agent_name, "status": "error", "message": str(exc)}))
        return TeamSubtaskResult(agent=agent_name, success=False, payload=f"{agent_name} 子任务异常: {exc}")

    # runner 正常结束但未发 _subtask_done → 视为成功（rag/web/custom 无哨兵事件）
    if collected_text or tool_traces:
        summary = _build_summary(collected_text, tool_traces, agent_name)
        writer(make_team_event("team_progress", {"agent": agent_name, "status": "done", "message": "完成"}))
        writer(make_team_event("team_result", {"agent": agent_name, "success": True, "payload": summary}))
        return TeamSubtaskResult(agent=agent_name, success=True, payload=summary)

    summary = _build_summary(collected_text, tool_traces, agent_name)
    writer(make_team_event("team_progress", {"agent": agent_name, "status": "error", "message": summary}))
    writer(make_team_event("team_result", {"agent": agent_name, "success": False, "payload": summary}))
    return TeamSubtaskResult(agent=agent_name, success=False, payload=summary)


# ============================================================
# aggregator：汇总 + 质量门 + 降级
# ============================================================


_AGGREGATOR_PROMPT = ChatPromptTemplate.from_messages([
    (
        "human",
        "你是团队汇总专家。以下是一群专家针对用户问题的协作结果。\n\n"
        "用户问题：{user_message}\n\n"
        "专家发现：\n{blackboard_summary}\n\n"
        "失败说明：\n{error_summary}\n\n"
        "请综合以上信息，给出完整、准确的最终回答。"
        "如果专家结果有冲突，请说明并给出判断依据。"
        "保持回答简洁，使用标准 Markdown。",
    ),
])


def _build_summary(text_parts: list[str], tool_traces: list[str], agent_name: str) -> str:
    settings = get_settings()
    max_chars = settings.agent_team_result_max_chars
    full_text = "".join(text_parts).strip()
    if not full_text and not tool_traces:
        return f"[{agent_name}] 未返回有效内容"
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n[结果已截断]"
    summary = full_text
    if tool_traces:
        traces = "\n".join(tool_traces[:5])
        summary += f"\n\n工具痕迹：\n{traces}"
    return summary.strip()


def _quality_gate(blackboard: Blackboard) -> tuple[bool, str]:
    """Aggregator 质量门。"""
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


def _serialize_blackboard(blackboard: Blackboard | Mapping) -> str:
    """把黑板内容序列化为 Aggregator prompt 用的文本。"""
    findings = (
        blackboard.findings
        if isinstance(blackboard, Blackboard)
        else blackboard.get("findings", {})
    )
    errors = (
        blackboard.errors
        if isinstance(blackboard, Blackboard)
        else blackboard.get("errors", {})
    )
    lines: list[str] = []
    for agent_name, finding in findings.items():
        lines.append(f"--- {agent_name} ---")
        lines.append(finding)
    for agent_name, error in errors.items():
        lines.append(f"--- {agent_name} [失败] ---")
        lines.append(error)
    return "\n\n".join(lines)


async def _run_aggregator(
    user_message: str,
    blackboard: Blackboard,
    chat_model: BaseChatModel | None = None,
) -> AsyncIterator[dict[str, str]]:
    """调用 Aggregator LLM 流式输出最终回复。"""
    settings = get_settings()

    ok, reason = _quality_gate(blackboard)
    if not ok:
        logger.warning("team aggregator quality gate rejected", reason=reason)
        yield make_error_event(f"专家结果质量不足: {reason}")
        return

    try:
        llm = chat_model if chat_model is not None else get_chat_model(temperature=0.5, streaming=True)
    except ValueError as exc:
        yield make_error_event(f"LLM 不可用: {exc}")
        return

    prompt = _AGGREGATOR_PROMPT.invoke({
        "user_message": user_message,
        "blackboard_summary": _serialize_blackboard(blackboard),
        "error_summary": "\n".join(f"{k}: {v}" for k, v in blackboard.errors.items()) or "无",
    })

    think_filter = ThinkFilter(max_hold=settings.think_filter_max_hold, retain_think=True)
    try:
        async for chunk in llm.astream(prompt):
            raw = extract_chunk_text(chunk, strip=False)
            cleaned = think_filter.feed(raw)
            if getattr(think_filter, "_retain_think", False):
                reasoning = think_filter.take_think()
                if reasoning:
                    yield make_team_event("reasoning", {"content": reasoning, "source": "team"})
            if cleaned:
                yield make_team_event("token", cleaned)
        tail = think_filter.flush()
        if tail:
            yield make_team_event("token", tail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("team aggregator stream failed", error=str(exc))
        yield make_error_event(f"Aggregator 流式失败: {exc}")


_SIMPLE_TASK_KEYWORDS = frozenset({
    "你好", "hello", "hi", "谢谢", "翻译一下",
})

_KEYWORD_PATTERNS = tuple(
    re.compile(rf"\b{re.escape(kw)}\b") if all(ord(c) < 128 for c in kw)
    else re.compile(re.escape(kw))
    for kw in _SIMPLE_TASK_KEYWORDS
)


def _should_downgrade_to_single(message: str) -> tuple[bool, str]:
    """评估是否应降级到单 agent 路径。"""
    lower = message.lower().strip()
    has_ascii = any(ord(c) < 128 and c.isalnum() for c in lower)
    threshold = 12 if has_ascii else 6
    if len(lower) < threshold:
        return True, "消息过短，无需 team 协作"
    if any(pat.search(lower) for pat in _KEYWORD_PATTERNS):
        return True, "命中简单任务关键词"
    return False, ""


# ============================================================
# StateGraph 节点
# ============================================================


async def plan_node(state: TeamState) -> dict:
    """Orchestrator 拆任务节点：生成 TeamPlan，校验后写入 state。"""
    writer = get_stream_writer()
    settings = get_settings()

    chat_model = state.get("chat_model")
    try:
        llm = chat_model if chat_model is not None else get_chat_model(
            temperature=settings.llm_temperature_orchestrator, streaming=False
        )
    except ValueError as exc:
        writer(make_error_event(f"LLM 不可用: {exc}"))
        return {"plan": [], "errors": {}, "findings": {}, "subtask_results": {}}

    message = state["message"]
    max_tasks = settings.agent_team_max_tasks
    context = _build_project_context()
    orchestrator_prompt = _build_orchestrator_prompt(
        message, max_tasks, context=context, settings=settings
    )
    try:
        structured_llm = llm.with_structured_output(TeamPlan)
        plan_obj: TeamPlan = await structured_llm.ainvoke(orchestrator_prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("team orchestrator invoke failed", error=str(exc))
        writer(make_error_event(f"Orchestrator 调用失败: {exc}"))
        return {"plan": [], "errors": {}, "findings": {}, "subtask_results": {}}

    plan, reasoning = _postprocess_plan(plan_obj, max_tasks)
    if not plan:
        writer(make_error_event("Orchestrator 未生成有效计划"))
        return {"plan": [], "errors": {}, "findings": {}, "subtask_results": {}}

    errors: dict[str, str] = {}
    valid_tasks: list[TeamPlanTask] = []
    for task in plan:
        ok, err = _validate_task(task, settings)
        if ok:
            valid_tasks.append(task)
        else:
            errors[task.agent] = err
            writer(
                make_team_event(
                    "team_progress",
                    {"agent": task.agent, "status": "error", "message": err},
                )
            )

    writer(
        make_team_event(
            "team_plan",
            {
                "plan": [
                    {"agent": t.agent, "input": t.input, "purpose": t.purpose}
                    for t in plan
                ],
                "reasoning": reasoning,
            },
        )
    )

    return {"plan": valid_tasks, "reasoning": reasoning, "errors": errors}


def dispatch_node(state: TeamState) -> list[Send]:
    """dispatch 条件边：把每个子任务 fan-out 到对应类型的执行节点（LangGraph Send API）。

    Returns:
        每个子任务一个 ``Send(node_name, SubtaskState)``，LangGraph 并行执行。
    """
    plan = state.get("plan", [])
    thread_id = state.get("thread_id", "")
    sends: list[Send] = []
    for idx, task in enumerate(plan):
        node_name = _route_node_for_task(task.agent, state)
        sends.append(
            Send(
                node_name,
                {
                    "task": task.__dict__,
                    "task_index": idx,
                    "parent_thread_id": thread_id,
                    "history": state.get("history"),
                    "permission_mode": state.get("permission_mode", "standard"),
                    "scene_prompt": state.get("scene_prompt"),
                    "profile_prompt": state.get("profile_prompt", ""),
                    "workspace_path": state.get("workspace_path"),
                    "chat_model": state.get("chat_model"),
                    "subtask_runners": state.get("subtask_runners"),
                },
            )
        )
    return sends


def _route_node_for_task(agent: str, state: TeamState) -> str:
    """把 ``TeamPlanTask.agent`` 映射到对应的执行节点名。"""
    settings = state.get("chat_model") and get_settings()  # noqa: PD008 — settings 一致性
    if agent == "deep":
        return "deep_node"
    if agent == "code":
        return "code_node"
    if agent in ("rag", "web"):
        return "builtin_node"
    if agent.startswith("custom-"):
        return "custom_node"
    # 团队角色：检查 settings.team_subagents
    if settings is not None and agent in (getattr(settings, "team_subagents", None) or {}):
        return "team_role_node"
    return "default_node"


def _make_subtask_state_update(result: TeamSubtaskResult) -> dict:
    """把 ``TeamSubtaskResult`` 转换为 StateGraph reducer 兼容的 state update。"""
    if result.success:
        return {
            "findings": {result.agent: result.payload},
            "subtask_results": {
                result.agent: {
                    "success": True,
                    "payload": result.payload,
                },
            },
        }
    return {
        "errors": {result.agent: result.payload},
        "subtask_results": {
            result.agent: {
                "success": False,
                "payload": result.payload,
            },
        },
    }


# ---- per-agent 执行节点 ----


async def _emit_team_progress(writer: Callable, agent: str, status: str, message: str) -> None:
    writer(make_team_event("team_progress", {"agent": agent, "status": status, "message": message}))


async def deep_node(state: SubtaskState) -> dict:
    """deep 子任务节点：执行 DeepAgent 路径（审批 + 写文件 + 命令）。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    child_id = f"{parent_thread_id}-team-deep-{state['task_index']}"
    await _inherit_workspace(child_id, state.get("workspace_path"))
    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    abort_event = await get_abort_event(parent_thread_id)
    deep_state: dict = {
        "thread_id": child_id,
        "messages": [{"role": "user", "content": task.input}],
    }
    runner = _get_runner("deep", state.get("subtask_runners"))
    result = await _run_subtask_stream(
        runner,
        runner_args=(deep_state, task.input),
        runner_kwargs={
            "profile_prompt": state.get("profile_prompt", ""),
            "history": state.get("history"),
            "permission_mode": state.get("permission_mode", "standard"),
            "scene_prompt": state.get("scene_prompt"),
            "workspace_path": state.get("workspace_path"),
            "parent_thread_id": parent_thread_id,
            "chat_model": state.get("chat_model"),
        },
        agent_name=task.agent,
        abort_event=abort_event,
        writer=writer,
    )
    return _make_subtask_state_update(result)


async def code_node(state: SubtaskState) -> dict:
    """code 子任务节点：执行 coding Expert。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    child_id = f"{parent_thread_id}-team-code-{state['task_index']}"
    await _inherit_workspace(child_id, state.get("workspace_path"))
    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    abort_event = await get_abort_event(parent_thread_id)
    runner = _get_runner("code", state.get("subtask_runners"))
    result = await _run_subtask_stream(
        runner,
        runner_args=(task.input, child_id),
        runner_kwargs={
            "profile_prompt": state.get("profile_prompt", ""),
            "history": state.get("history"),
            "permission_mode": state.get("permission_mode", "standard"),
            "workspace_path": state.get("workspace_path"),
            "parent_thread_id": parent_thread_id,
            "chat_model": state.get("chat_model"),
        },
        agent_name=task.agent,
        abort_event=abort_event,
        writer=writer,
    )
    return _make_subtask_state_update(result)


async def builtin_node(state: SubtaskState) -> dict:
    """builtin（rag / web）子任务节点：执行 run_rag_agent / run_web_agent。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    abort_event = await get_abort_event(parent_thread_id)
    runner = _get_runner(task.agent, state.get("subtask_runners"))
    result = await _run_subtask_stream(
        runner,
        runner_args=(parent_thread_id, task.input),
        runner_kwargs={
            "history": state.get("history"),
            "workspace_path": state.get("workspace_path"),
        },
        agent_name=task.agent,
        abort_event=abort_event,
        writer=writer,
    )
    return _make_subtask_state_update(result)


async def team_role_node(state: SubtaskState) -> dict:
    """团队角色子任务节点（frontend_dev / backend_dev / tester 等）。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    settings = get_settings()
    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    abort_event = await get_abort_event(parent_thread_id)
    cfg = settings.team_subagents.get(task.agent)
    collected_text: list[str] = []
    tool_traces: list[str] = []

    def _done(success: bool, payload: str) -> TeamSubtaskResult:
        return TeamSubtaskResult(agent=task.agent, success=success, payload=payload)

    try:
        if not cfg or not cfg.system_prompt:
            # 降级到 coding Expert
            fallback_thread_id = f"{parent_thread_id}-team-fallback-{state['task_index']}"
            await _inherit_workspace(fallback_thread_id, state.get("workspace_path"))
            runner = _get_runner("code", state.get("subtask_runners"))
            stream = runner(
                task.input,
                fallback_thread_id,
                profile_prompt=state.get("profile_prompt", ""),
                history=state.get("history"),
                permission_mode=state.get("permission_mode", "standard"),
                workspace_path=state.get("workspace_path"),
                parent_thread_id=parent_thread_id,
                chat_model=state.get("chat_model"),
            )
            async for event in stream:
                if abort_event.is_set():
                    return _make_subtask_state_update(_done(False, "用户中止"))
                etype = event.get("event", "")
                data = event.get("data", "")
                if etype == "token":
                    collected_text.append(str(data))
                    writer(event)
                elif etype == "tool_result":
                    try:
                        obj = json.loads(data) if isinstance(data, str) else data
                        if isinstance(obj, dict):
                            tool_traces.append(
                                f"{obj.get('name', '?')}: {str(obj.get('result', ''))[:200]}"
                            )
                    except Exception:  # noqa: BLE001
                        pass
                    writer(event)
                elif etype in _PASSTHROUGH_EVENTS:
                    writer(event)
                elif etype == "error":
                    writer(event)
            return _make_subtask_state_update(
                _done(
                    bool(collected_text or tool_traces),
                    _build_summary(collected_text, tool_traces, task.agent),
                )
            )

        # 有专属配置：build_custom_agent + astream_events v2
        from app.subagents.custom_agent import build_custom_agent

        agent_obj = build_custom_agent(
            key=task.agent,
            thread_id=parent_thread_id,
            system_prompt=cfg.system_prompt,
            tools=cfg.tools,
            temperature=cfg.temperature,
            workspace_path=state.get("workspace_path"),
        )
        history_msgs = list(state.get("history") or [])
        inputs = {"messages": [*history_msgs, {"role": "user", "content": task.input}]}
        config = {"configurable": {"thread_id": parent_thread_id}}
        async for event in agent_obj.astream_events(inputs, version="v2", config=config):
            if abort_event.is_set():
                return _make_subtask_state_update(_done(False, "用户中止"))
            kind = event["event"]
            ename = event.get("name", "")
            edata = event.get("data", {}) or {}
            if kind == "on_chat_model_stream":
                content = extract_chunk_text(edata.get("chunk"), strip=False)
                if content:
                    collected_text.append(content)
            elif kind in ("on_tool_start", "on_tool_end"):
                trace_data = edata.get("input") if kind == "on_tool_start" else edata.get("output")
                tool_traces.append(f"{ename}: {str(trace_data)[:200]}")
    except Exception as exc:  # noqa: BLE001
        return _make_subtask_state_update(_done(False, f"团队角色 {task.agent} 子任务异常: {exc}"))

    return _make_subtask_state_update(
        _done(
            bool(collected_text or tool_traces),
            _build_summary(collected_text, tool_traces, task.agent),
        )
    )


async def custom_node(state: SubtaskState) -> dict:
    """custom-* 子任务节点。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    parent_thread_id = state["parent_thread_id"]
    key = task.agent[len("custom-"):]
    await _emit_team_progress(writer, task.agent, "running", task.purpose)

    abort_event = await get_abort_event(parent_thread_id)
    runner = _get_runner("custom", state.get("subtask_runners"))
    result = await _run_subtask_stream(
        runner,
        runner_args=(key, parent_thread_id, task.input),
        runner_kwargs={
            "history": state.get("history"),
            "workspace_path": state.get("workspace_path"),
        },
        agent_name=task.agent,
        abort_event=abort_event,
        writer=writer,
    )
    return _make_subtask_state_update(result)


async def default_node(state: SubtaskState) -> dict:
    """未知 agent 类型的 fallback 节点。"""
    writer = get_stream_writer()
    task = TeamPlanTask(**state["task"])
    err_msg = f"未知 agent: {task.agent}"
    writer(make_team_event("team_progress", {"agent": task.agent, "status": "error", "message": err_msg}))
    return _make_subtask_state_update(TeamSubtaskResult(agent=task.agent, success=False, payload=err_msg))


async def aggregate_node(state: TeamState) -> dict:
    """Aggregator 汇总节点：综合黑板内容生成最终回复。"""
    writer = get_stream_writer()

    findings: dict[str, str] = state.get("findings", {})
    errors: dict[str, str] = state.get("errors", {})
    plan: list = state.get("plan", [])

    if not findings:
        if not plan:
            writer(make_team_event("team_done", {"status": "error"}))
            return {}
        writer(make_error_event("所有专家任务均失败"))
        writer(make_team_event("team_done", {"status": "error"}))
        return {}

    blackboard = Blackboard(findings=dict(findings), errors=dict(errors))
    message = state["message"]
    chat_model = state.get("chat_model")

    async for sse in _run_aggregator(message, blackboard, chat_model=chat_model):
        writer(sse)

    has_error = bool(errors)
    writer(
        make_team_event(
            "team_done",
            {"status": "error" if has_error and not findings else "done"},
        )
    )
    return {}


# ============================================================
# StateGraph 构建
# ============================================================


def _build_team_graph() -> Any:
    """构建 Team StateGraph：plan → dispatch（Send fan-out）→ per-agent nodes → aggregate。"""
    graph: Any = StateGraph(TeamState)
    graph.add_node("plan", plan_node)
    graph.add_node("deep_node", deep_node)
    graph.add_node("code_node", code_node)
    graph.add_node("builtin_node", builtin_node)
    graph.add_node("team_role_node", team_role_node)
    graph.add_node("custom_node", custom_node)
    graph.add_node("default_node", default_node)
    graph.add_node("aggregate", aggregate_node)

    graph.add_edge(START, "plan")
    # dispatch 条件边：返回 list[Send] 触发 LangGraph 原生 fan-out
    graph.add_conditional_edges("plan", dispatch_node)
    # 所有子任务节点 → aggregate
    for node_name in ("deep_node", "code_node", "builtin_node", "team_role_node", "custom_node", "default_node"):
        graph.add_edge(node_name, "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()


_team_graph: Any = None


def _get_team_graph() -> Any:
    """获取已编译的 Team StateGraph（单例缓存）。"""
    global _team_graph
    if _team_graph is None:
        _team_graph = _build_team_graph()
    return _team_graph


# ============================================================
# 入口
# ============================================================


async def run_team_path(
    message: str,
    thread_id: str,
    state: RouterState,
    profile_prompt: str = "",
    history: list | None = None,
    permission_mode: str = "workspace",
    scene_prompt: str | None = None,
    workspace_path: str | None = None,
    chat_model: BaseChatModel | None = None,
    subtask_runners: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """AgentTeam 路径入口（LangGraph StateGraph + Send API 编排）。"""
    downgrade, reason = _should_downgrade_to_single(message)
    if downgrade:
        logger.info(
            "team downgrade suggest switch mode", reason=reason, message_len=len(message)
        )
        yield make_sse_event(
            "token",
            f"该任务似乎不需要团队协作（{reason}）。建议切换到 work 模式由 Supervisor 直接处理。",
        )
        yield make_team_event("team_done", {"status": "done"})
        return

    resolved_runners = _resolve_subtask_runners(subtask_runners)

    with trace_span("team.run", thread_id=thread_id, message_len=len(message)):
        graph = _get_team_graph()
        initial_state: TeamState = {
            "message": message,
            "thread_id": thread_id,
            "history": history or [],
            "permission_mode": permission_mode,
            "scene_prompt": scene_prompt,
            "profile_prompt": profile_prompt,
            "workspace_path": workspace_path,
            "chat_model": chat_model,
            "subtask_runners": resolved_runners,
            "plan": [],
            "reasoning": "",
            "findings": {},
            "errors": {},
            "subtask_results": {},
        }

        async for event in graph.astream(initial_state, stream_mode="custom"):
            yield event


__all__ = [
    "run_team_path",
    "Blackboard",
    "TeamPlanTask",
    "TeamPlan",
    "TeamPlanItem",
    "TeamSubtaskResult",
    "TeamState",
    "_run_subtask_stream",
    "_should_downgrade_to_single",
    "_validate_task",
    "_postprocess_plan",
    "_build_orchestrator_prompt",
    "_resolve_subtask_runners",
    "_SUBTASK_DONE_EVENT",
]