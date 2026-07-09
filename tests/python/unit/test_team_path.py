"""AgentTeam 路径单元测试： Orchestrator 拆任务、并行调度、黑板汇总、安全改写。

覆盖：
1. _postprocess_plan：结构化输出截断 / 危险任务强制改写 deep
2. _validate_task：内置子代理可用性校验
3. _bounded_gather：并发上限
4. Blackboard：汇总序列化
5. run_team_path：mock Orchestrator + mock 子代理，验证 SSE 事件序列
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import get_settings
from app.team.orchestrator import (
    Blackboard,
    TeamPlanTask,
    _build_summary,
    _serialize_blackboard,
    _validate_task,
    run_team_path,
)
from app.team.planner import TeamPlan, TeamPlanItem, _postprocess_plan
from app.utils.sse_events import make_team_event


# ============================================================
# 辅助
# ============================================================


async def _collect_events(gen: AsyncIterator[dict]) -> list[dict]:
    """收集异步生成器的所有事件。"""
    events: list[dict] = []
    async for event in gen:
        events.append(event)
    return events


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch):
    """每个用例前后清理 settings lru_cache，避免环境变量污染。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================
# 1. _postprocess_plan
# ============================================================


def test_postprocess_plan_valid() -> None:
    """结构化 TeamPlan 输出被正确转换为 TeamPlanTask 列表。"""
    plan = TeamPlan(
        reasoning="需要同时查看代码和文档",
        plan=[
            TeamPlanItem(agent="code", input="读取 main.py", purpose="入口结构"),
            TeamPlanItem(agent="rag", input="Router 设计", purpose="检索文档"),
        ],
    )
    tasks, reasoning = _postprocess_plan(plan, max_tasks=5)
    assert len(tasks) == 2
    assert tasks[0].agent == "code"
    assert tasks[0].input == "读取 main.py"
    assert reasoning == "需要同时查看代码和文档"


def test_postprocess_plan_empty_plan() -> None:
    """空 plan 列表返回空任务。"""
    plan = TeamPlan(reasoning="", plan=[])
    tasks, reasoning = _postprocess_plan(plan, max_tasks=5)
    assert tasks == []
    assert reasoning == ""


def test_postprocess_plan_truncates_over_max_tasks() -> None:
    """子任务数超过 max_tasks 时被截断。"""
    plan = TeamPlan(
        plan=[
            TeamPlanItem(agent="code", input=f"任务{i}", purpose=f"目的{i}")
            for i in range(8)
        ],
    )
    tasks, _ = _postprocess_plan(plan, max_tasks=5)
    assert len(tasks) == 5


def test_postprocess_plan_missing_required_fields_skipped() -> None:
    """缺少 agent 或 input 的条目被跳过。"""
    plan = TeamPlan(
        plan=[
            TeamPlanItem(agent="code", input="有效"),
            TeamPlanItem(agent="", input="无效 agent"),
            TeamPlanItem(agent="rag", input="", purpose="缺少 input"),
        ],
    )
    tasks, _ = _postprocess_plan(plan, max_tasks=5)
    assert len(tasks) == 1
    assert tasks[0].agent == "code"


def test_postprocess_plan_rewrites_dangerous_task_to_deep() -> None:
    """涉及写/编辑/shell 的任务被强制改写为 deep agent。"""
    plan = TeamPlan(
        plan=[
            TeamPlanItem(agent="code", input="写入 config.py", purpose="改配置"),
            TeamPlanItem(agent="code", input="编辑 README.md", purpose="改文档"),
            TeamPlanItem(agent="code", input="执行命令 ls", purpose="shell"),
            TeamPlanItem(agent="code", input="读取 main.py", purpose="只读"),
        ],
    )
    tasks, _ = _postprocess_plan(plan, max_tasks=5)
    assert len(tasks) == 4
    assert tasks[0].agent == "deep"
    assert tasks[1].agent == "deep"
    assert tasks[2].agent == "deep"
    assert tasks[3].agent == "code"


# ============================================================
# 2. _validate_task
# ============================================================


def _make_settings(
    code_enabled: bool = True,
    rag_enabled: bool = True,
    web_enabled: bool = True,
    web_tools: list[str] | None = None,
) -> Any:
    """构造一个可替换 subagents/tools_enabled 的 settings 对象。

    注: ``code_enabled`` 仅为参数兼容保留，场景化架构下 code 子代理已由
    coding Expert 取代，_validate_task 对 ``agent="code"`` 直接返回 True，
    不再读取 settings.subagents["code"]。
    """
    settings = get_settings()
    settings.subagents_config = {
        "rag": {"enabled": rag_enabled},
        "web": {"enabled": web_enabled, "tools": web_tools or ["web_search"]},
    }
    settings.tools_config = {t: True for t in ["read_file", "list_dir", "glob", "grep", "rag_retrieve", "web_search"]}
    return settings


def test_validate_task_builtin_enabled() -> None:
    """内置子代理启用且工具有效时校验通过。"""
    settings = _make_settings()
    ok, err = _validate_task(TeamPlanTask("code", "读文件", ""), settings)
    assert ok is True
    assert err == ""


def test_validate_task_builtin_disabled() -> None:
    """内置子代理（rag）被禁用时校验失败。

    注: code 已映射到 coding Expert，_validate_task 直接返回 True，
    故用 rag 验证禁用校验逻辑。
    """
    settings = _make_settings(rag_enabled=False)
    ok, err = _validate_task(TeamPlanTask("rag", "检索", ""), settings)
    assert ok is False
    assert "已禁用" in err


def test_validate_task_tools_all_disabled() -> None:
    """子代理绑定工具全部被禁用时校验失败。"""
    settings = _make_settings()
    settings.tools_config["web_search"] = False
    ok, err = _validate_task(TeamPlanTask("web", "搜索", ""), settings)
    assert ok is False
    assert "全部被禁用" in err


def test_validate_task_deep_always_ok() -> None:
    """deep 子任务无需校验工具，始终通过。"""
    settings = _make_settings()
    ok, err = _validate_task(TeamPlanTask("deep", "写入文件", ""), settings)
    assert ok is True
    assert err == ""


def test_validate_task_unknown_agent() -> None:
    """未知 agent 类型校验失败。"""
    settings = _make_settings()
    ok, err = _validate_task(TeamPlanTask("unknown", "xxx", ""), settings)
    assert ok is False
    assert "未知 agent 类型" in err


def test_validate_task_custom_agent() -> None:
    """自定义子代理按 custom_subagents 校验。"""
    settings = _make_settings()
    settings.custom_subagents_config = {
        "my_agent": {
            "key": "my_agent",
            "name": "我的代理",
            "enabled": True,
            "tools": ["read_file"],
        }
    }
    ok, err = _validate_task(TeamPlanTask("custom-my_agent", "读文件", ""), settings)
    assert ok is True
    assert err == ""


# ============================================================
# 3. Blackboard / summary
# ============================================================


def test_serialize_blackboard() -> None:
    """黑板序列化包含 findings 和 errors。"""
    bb = Blackboard()
    bb.findings["code"] = "main.py 启动 8123"
    bb.errors["rag"] = "检索失败"
    text = _serialize_blackboard(bb)
    assert "main.py 启动 8123" in text
    assert "检索失败" in text
    assert "code" in text
    assert "rag" in text


def test_build_summary_truncates_long_text() -> None:
    """结果摘要超过上限时被截断。"""
    settings = get_settings()
    settings.agent_team_result_max_chars = 10
    long_text = "a" * 100
    summary = _build_summary([long_text], [], "code")
    assert len(summary) < 100
    assert "[结果已截断]" in summary


def test_build_summary_empty_returns_placeholder() -> None:
    """无输出时返回占位提示。"""
    summary = _build_summary([], [], "rag")
    assert "未返回有效内容" in summary


# ============================================================
# 4. run_team_path 集成
# ============================================================


def _make_fake_llm(plan: TeamPlan) -> MagicMock:
    """构造 mock LLM：with_structured_output().ainvoke 返回 TeamPlan；astream 返回汇总 chunk。"""
    mock = MagicMock()
    structured_mock = MagicMock()
    structured_mock.ainvoke = AsyncMock(return_value=plan)
    mock.with_structured_output = MagicMock(return_value=structured_mock)

    async def _fake_astream(messages: Any) -> AsyncIterator:
        yield SimpleNamespace(content="最终汇总")

    mock.astream = _fake_astream
    return mock


async def test_run_team_path_emits_team_plan_progress_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完整链路：team_plan → team_progress(running) → team_progress(done) → team_result → token/done。"""
    plan = TeamPlan(
        reasoning="需要代码和检索",
        plan=[
            TeamPlanItem(agent="code", input="读 main.py", purpose="入口"),
            TeamPlanItem(agent="rag", input="Router 设计", purpose="文档"),
        ],
    )
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: _make_fake_llm(plan))

    async def _fake_run_coding_expert(message: str, thread_id: str, profile_prompt: str = "", history: list | None = None, permission_mode: str = "standard", workspace_path: str | None = None, parent_thread_id: str | None = None, chat_model=None) -> AsyncIterator[dict]:
        yield {"event": "token", "data": "代码结果"}

    async def _fake_run_rag_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None) -> AsyncIterator[dict]:
        yield {"type": "token", "content": "检索结果"}

    events = await _collect_events(
        run_team_path(
            "分析项目入口文件和文档结构",
            "t-team",
            {"thread_id": "t-team", "messages": []},
            subtask_runners={"code": _fake_run_coding_expert, "rag": _fake_run_rag_agent},
        )
    )

    event_types = [e["event"] for e in events]

    # team_plan 在最前
    assert event_types[0] == "team_plan"
    plan_data = json.loads(events[0]["data"])
    assert len(plan_data["plan"]) == 2

    # running 事件
    running = [e for e in events if e["event"] == "team_progress" and json.loads(e["data"])["status"] == "running"]
    assert len(running) == 2

    # done 事件
    done_progress = [e for e in events if e["event"] == "team_progress" and json.loads(e["data"])["status"] == "done"]
    assert len(done_progress) == 2

    # team_result 事件
    results = [e for e in events if e["event"] == "team_result"]
    assert len(results) == 2

    # Aggregator 输出 token（token 事件 data 为纯字符串，与 graph.py _sse 约定一致）
    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) >= 1
    assert "".join(e["data"] for e in tokens) == "最终汇总"


async def test_run_team_path_all_subtasks_fail_yields_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """所有子任务失败时，发送 error 事件。"""
    plan = TeamPlan(
        plan=[
            TeamPlanItem(agent="code", input="读文件", purpose="读"),
        ],
    )
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: _make_fake_llm(plan))

    async def _fake_run_coding_expert(message: str, thread_id: str, profile_prompt: str = "", history: list | None = None, permission_mode: str = "standard", workspace_path: str | None = None, parent_thread_id: str | None = None, chat_model=None) -> AsyncIterator[dict]:
        # 只返回空，导致 summary 为未返回有效内容 → 标记失败
        if False:
            yield {}

    events = await _collect_events(
        run_team_path(
            "分析项目的整体架构设计",
            "t-fail",
            {"thread_id": "t-fail", "messages": []},
            subtask_runners={"code": _fake_run_coding_expert},
        )
    )

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert "所有专家任务均失败" in error_events[0]["data"]


async def test_run_team_path_partial_failure_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """部分子任务失败时，成功结果仍进入黑板并触发 Aggregator。"""
    plan = TeamPlan(
        plan=[
            TeamPlanItem(agent="code", input="读文件", purpose="读"),
            TeamPlanItem(agent="rag", input="检索", purpose="检索"),
        ],
    )
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: _make_fake_llm(plan))

    async def _fake_run_coding_expert(message: str, thread_id: str, profile_prompt: str = "", history: list | None = None, permission_mode: str = "standard", workspace_path: str | None = None, parent_thread_id: str | None = None, chat_model=None) -> AsyncIterator[dict]:
        yield {"event": "token", "data": "代码成功"}

    async def _fake_run_rag_agent(thread_id: str, message: str, history: list | None = None, workspace_path: str | None = None) -> AsyncIterator[dict]:
        # 空输出 → 失败
        if False:
            yield {}

    events = await _collect_events(
        run_team_path(
            "分析项目的整体架构设计",
            "t-partial",
            {"thread_id": "t-partial", "messages": []},
            subtask_runners={"code": _fake_run_coding_expert, "rag": _fake_run_rag_agent},
        )
    )

    # code 成功，rag 失败
    results = {json.loads(e["data"])["agent"]: e for e in events if e["event"] == "team_result"}
    assert "code" in results
    assert "rag" not in results

    # Aggregator 仍执行
    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) >= 1


async def test_run_team_path_invalid_plan_yields_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Orchestrator 输出空计划时发送 error。"""
    # with_structured_output 永远返回结构化对象，无法返回非法 JSON；
    # 用空 plan 模拟"未生成有效计划"场景
    plan = TeamPlan(reasoning="", plan=[])
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: _make_fake_llm(plan))

    events = await _collect_events(
        run_team_path("分析项目的整体架构设计", "t-invalid", {"thread_id": "t-invalid", "messages": []})
    )

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert "Orchestrator 未生成有效计划" in error_events[0]["data"]


async def test_run_team_path_deep_subtask_propagates_approval_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deep 子任务的 approval_request 事件必须透传到前端，否则审批流死锁。"""
    plan = TeamPlan(
        plan=[
            TeamPlanItem(agent="deep", input="写入文件", purpose="改配置"),
        ],
    )
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: _make_fake_llm(plan))

    async def _fake_run_deep_path(state, message, **kwargs):
        yield {"event": "approval_request", "data": json.dumps({"tool_name": "write_file", "preview": "test"})}
        yield {"event": "token", "data": "deep 结果"}
        yield {"event": "tool_result", "data": json.dumps({"name": "write_file", "result": "ok"})}

    events = await _collect_events(
        run_team_path(
            "请修改配置文件中的数据库连接",
            "t-deep",
            {"thread_id": "t-deep", "messages": []},
            subtask_runners={"deep": _fake_run_deep_path},
        )
    )

    # approval_request 必须出现在事件流中（Bug 2 回归测试）
    approval_events = [e for e in events if e["event"] == "approval_request"]
    assert len(approval_events) == 1
    assert "write_file" in approval_events[0]["data"]

    # deep 子任务成功完成
    done_progress = [
        e for e in events
        if e["event"] == "team_progress" and json.loads(e["data"])["status"] == "done"
    ]
    assert len(done_progress) == 1


async def test_run_team_path_token_data_is_plain_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """token 事件 data 必须是纯字符串，不能是 JSON 序列化（Bug 1 回归测试）。"""
    plan = TeamPlan(
        plan=[
            TeamPlanItem(agent="code", input="读文件", purpose="读"),
        ],
    )
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: _make_fake_llm(plan))

    async def _fake_run_coding_expert(message: str, thread_id: str, profile_prompt: str = "", history: list | None = None, permission_mode: str = "standard", workspace_path: str | None = None, parent_thread_id: str | None = None, chat_model=None) -> AsyncIterator[dict]:
        yield {"event": "token", "data": "代码结果"}

    events = await _collect_events(
        run_team_path(
            "分析项目的整体架构设计",
            "t-token",
            {"thread_id": "t-token", "messages": []},
            subtask_runners={"code": _fake_run_coding_expert},
        )
    )

    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) >= 1
    # token data 是纯字符串，不是 JSON——直接拼接即可，不应 json.loads
    for t in tokens:
        assert not t["data"].startswith('"'), f"token data 不应是 JSON 字符串: {t['data']!r}"


# ============================================================
# 4b. 降级测试
# ============================================================


async def test_run_team_path_downgrades_simple_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """简单短消息降级：场景化架构下不再回退到 chat path，
    改为直接 yield token（建议切换 work 模式）+ team_done 事件，不触发 Orchestrator。
    """
    # mock team Orchestrator LLM — 若被调用则测试失败
    def _orchestrator_should_not_be_called(**_: Any) -> Any:
        raise AssertionError("Orchestrator should not be called for simple message")
    monkeypatch.setattr("app.team.orchestrator.get_chat_model", _orchestrator_should_not_be_called)

    events = await _collect_events(
        run_team_path("你好", "t-simple", {"thread_id": "t-simple", "messages": []})
    )

    # 不应有 team_plan 事件
    event_types = [e["event"] for e in events]
    assert "team_plan" not in event_types
    # 应有 token 事件（降级提示）+ team_done 事件
    assert "token" in event_types
    assert "team_done" in event_types
    # token 内容应包含切换模式提示
    token_evt = next(e for e in events if e["event"] == "token")
    assert "work" in token_evt["data"] or "切换" in token_evt["data"]


# ============================================================
# 5. 工具函数
# ============================================================


def test_make_team_event_serializes_data() -> None:
    """_make_team_event 将 dict 序列化为 JSON 字符串。"""
    event = make_team_event("team_plan", {"plan": [], "reasoning": "r"})
    assert event["event"] == "team_plan"
    data = json.loads(event["data"])
    assert data["reasoning"] == "r"


def test_make_team_event_token_uses_plain_string() -> None:
    """token 事件 data 必须是纯字符串（与 graph.py _sse 约定一致）。"""
    event = make_team_event("token", "你好")
    assert event["event"] == "token"
    assert event["data"] == "你好"  # 不是 '"你好"'
