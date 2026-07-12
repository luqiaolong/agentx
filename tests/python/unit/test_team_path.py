"""AgentTeam 路径单元测试：Orchestrator 拆任务、并行调度、黑板汇总、安全改写。

覆盖：
1. _todos_to_team_tasks：从 deepagents 原生 todos 解析子任务 / 危险任务强制改写 deep
2. _validate_task：内置子代理可用性校验
3. Blackboard：汇总序列化
4. run_team_path：mock Orchestrator + mock 子代理，验证 SSE 事件序列
   （todo_update / token / team_done / error，不再有 team_plan/team_progress/team_result）
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
from app.team.planner import _parse_todos_from_text, _todos_to_team_tasks
from app.sse.events import make_sse_event


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


@pytest.fixture(autouse=True)
def _clear_abort_state():
    """每个用例前后清理全局 abort 状态。"""
    from app.security.approval import state as approval_state

    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()
    yield
    approval_state._abort_flags.clear()
    approval_state._abort_events.clear()


def _todo(content: str, status: str = "pending") -> dict:
    """构造 deepagents 原生 Todo dict。"""
    return {"content": content, "status": status}


# ============================================================
# 1. _todos_to_team_tasks
# ============================================================


def _make_validate_settings() -> Any:
    """构造 _todos_to_team_tasks 内部 _validate_task 使用的 settings。"""
    settings = get_settings()
    settings.subagents_config = {
        "rag": {"enabled": True},
        "web": {"enabled": True, "tools": ["web_search"]},
    }
    settings.tools_config = {t: True for t in ["read_file", "list_dir", "glob", "grep", "rag_retrieve", "web_search"]}
    return settings


def test_todos_to_team_tasks_valid() -> None:
    """todos 列表被正确转换为 TeamPlanTask 列表（解析 [agent:xxx] 前缀）。"""
    settings = _make_validate_settings()
    todos = [
        _todo("[agent:code] 读取 main.py"),
        _todo("[agent:rag] 检索 Router 设计"),
    ]
    tasks, reasoning = _todos_to_team_tasks(todos, settings)
    assert len(tasks) == 2
    assert tasks[0].agent == "code"
    assert tasks[0].input == "读取 main.py"
    assert tasks[1].agent == "rag"
    assert tasks[1].input == "检索 Router 设计"
    # deepagents write_todos 不产 reasoning
    assert reasoning == ""


def test_todos_to_team_tasks_empty_list() -> None:
    """空 todos 列表返回空任务。"""
    settings = _make_validate_settings()
    tasks, reasoning = _todos_to_team_tasks([], settings)
    assert tasks == []
    assert reasoning == ""


def test_todos_to_team_tasks_missing_prefix_skipped() -> None:
    """缺少 [agent:xxx] 前缀的 todo 被跳过。"""
    settings = _make_validate_settings()
    todos = [
        _todo("[agent:code] 有效任务"),
        _todo("没有前缀的任务"),
        _todo("[agent:rag]"),
    ]
    tasks, _ = _todos_to_team_tasks(todos, settings)
    assert len(tasks) == 1
    assert tasks[0].agent == "code"


def test_todos_to_team_tasks_rewrites_dangerous_task_to_deep() -> None:
    """涉及写/编辑/shell 的任务被强制改写为 deep agent。"""
    settings = _make_validate_settings()
    todos = [
        _todo("[agent:code] 写入 config.py"),
        _todo("[agent:code] 编辑 README.md"),
        _todo("[agent:code] 执行命令 ls"),
        _todo("[agent:code] 读取 main.py"),
    ]
    tasks, _ = _todos_to_team_tasks(todos, settings)
    assert len(tasks) == 4
    assert tasks[0].agent == "deep"
    assert tasks[1].agent == "deep"
    assert tasks[2].agent == "deep"
    assert tasks[3].agent == "code"


def test_todos_to_team_tasks_invalid_agent_filtered() -> None:
    """未知 agent 类型的 todo 被过滤（_validate_task 返回 False）。"""
    settings = _make_validate_settings()
    todos = [
        _todo("[agent:code] 有效"),
        _todo("[agent:unknown_agent] 无效"),
    ]
    tasks, _ = _todos_to_team_tasks(todos, settings)
    assert len(tasks) == 1
    assert tasks[0].agent == "code"


# ============================================================
# 1b. _parse_todos_from_text（从 LLM 回复正文解析 [agent:xxx] 任务行）
# ============================================================


def test_parse_todos_from_text_basic() -> None:
    """基本解析：多行 [agent:xxx] 任务行被正确解析为 Todo 列表。"""
    text = "[agent:code] 读取 main.py\n[agent:rag] 检索文档\n[agent:deep] 修改 config.py"
    todos = _parse_todos_from_text(text)
    assert len(todos) == 3
    assert todos[0] == {"content": "[agent:code] 读取 main.py", "status": "pending"}
    assert todos[1] == {"content": "[agent:rag] 检索文档", "status": "pending"}
    assert todos[2] == {"content": "[agent:deep] 修改 config.py", "status": "pending"}


def test_parse_todos_from_text_empty() -> None:
    """空文本返回空列表。"""
    assert _parse_todos_from_text("") == []
    assert _parse_todos_from_text(None) == []  # type: ignore[arg-type]


def test_parse_todos_from_text_skips_non_matching_lines() -> None:
    """非 [agent:xxx] 格式的行（解释性文字、空行）被跳过。"""
    text = """这是任务清单：
[agent:code] 读文件

下面是说明文字，不应被解析。
[agent:rag] 检索"""
    todos = _parse_todos_from_text(text)
    assert len(todos) == 2
    assert todos[0]["content"] == "[agent:code] 读文件"
    assert todos[1]["content"] == "[agent:rag] 检索"


def test_parse_todos_from_text_skips_markdown_codeblock_markers() -> None:
    """markdown 代码块标记 ``` 被跳过。"""
    text = """```
[agent:code] 读文件
```
[agent:rag] 检索"""
    todos = _parse_todos_from_text(text)
    assert len(todos) == 2
    assert todos[0]["content"] == "[agent:code] 读文件"
    assert todos[1]["content"] == "[agent:rag] 检索"


def test_parse_todos_from_text_skips_empty_task() -> None:
    """[agent:xxx] 后无任务描述的行被跳过。"""
    text = "[agent:code]\n[agent:rag] 有效任务"
    todos = _parse_todos_from_text(text)
    assert len(todos) == 1
    assert todos[0]["content"] == "[agent:rag] 有效任务"


def test_parse_todos_from_text_normalizes_agent_case() -> None:
    """agent 类型被转为小写（CODE → code）。"""
    text = "[agent:CODE] 读文件"
    todos = _parse_todos_from_text(text)
    assert len(todos) == 1
    # content 中的 agent 已规范化为小写
    assert todos[0]["content"] == "[agent:code] 读文件"


def test_parse_todos_from_text_feeds_into_todos_to_team_tasks() -> None:
    """_parse_todos_from_text 产出的 todos 可直接传入 _todos_to_team_tasks。"""
    settings = _make_validate_settings()
    text = "[agent:code] 读取 main.py\n[agent:rag] 检索文档"
    todos = _parse_todos_from_text(text)
    tasks, _ = _todos_to_team_tasks(todos, settings)
    assert len(tasks) == 2
    assert tasks[0].agent == "code"
    assert tasks[0].input == "读取 main.py"
    assert tasks[1].agent == "rag"
    assert tasks[1].input == "检索文档"


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
# 4. run_team_path 集成（todo_update + token + team_done）
# ============================================================


def _make_fake_llm_for_aggregator() -> MagicMock:
    """构造 mock LLM：astream 返回汇总 chunk（供 Aggregator 使用）。"""
    mock = MagicMock()

    async def _fake_astream(messages: Any) -> AsyncIterator:
        yield SimpleNamespace(content="最终汇总")

    mock.astream = _fake_astream
    return mock


def _patch_orchestrator_to_return_todos(
    monkeypatch: pytest.MonkeyPatch,
    todos: list[dict],
) -> MagicMock:
    """patch get_chat_model 返回 mock LLM（ainvoke 返回 [agent:xxx] 任务行文本）。

    旧方案 patch ``deepagents.create_deep_agent`` 返回 mock orchestrator（ainvoke
    返回 ``{"todos": todos}`` dict）。新方案 ``_plan_node`` 直接用 ``llm.ainvoke``
    调用 LLM，从回复正文解析 ``[agent:xxx]`` 任务行，故 mock LLM 的 ``ainvoke``
    需返回 ``SimpleNamespace(content=任务行文本)``，模拟 LLM 直接输出任务清单。

    同时 mock LLM 的 ``astream`` 供 Aggregator 使用（返回汇总 chunk）。
    """
    fake_llm = _make_fake_llm_for_aggregator()

    # 构造 ainvoke 响应：把 todos 的 content 拼成文本（模拟 LLM 输出 [agent:xxx] 任务行）
    todo_lines = "\n".join(t["content"] for t in todos)
    fake_response = SimpleNamespace(content=todo_lines)
    fake_llm.ainvoke = AsyncMock(return_value=fake_response)

    monkeypatch.setattr("app.team.orchestrator.get_chat_model", lambda **_: fake_llm)
    return fake_llm


async def test_run_team_path_emits_todo_update_and_aggregates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完整链路：todo_update（in_progress + completed）→ token（Aggregator 汇总）→ team_done。"""
    todos = [
        _todo("[agent:code] 读 main.py"),
        _todo("[agent:rag] 检索 Router 设计"),
    ]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

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

    # todo_update 事件存在（替代旧的 team_plan / team_progress）
    assert "todo_update" in event_types

    # 第一个 todo_update 应包含初始 pending 列表（来自 plan 节点后的 state）
    todo_updates = [e for e in events if e["event"] == "todo_update"]
    assert len(todo_updates) >= 1

    # 解析最后一个 todo_update：所有 todo 应为 completed
    final_todo_data = json.loads(todo_updates[-1]["data"])
    final_todos = final_todo_data.get("todos", [])
    assert len(final_todos) == 2
    for todo in final_todos:
        assert todo["status"] == "completed", f"Expected completed, got {todo['status']}"

    # Aggregator 输出 token
    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) >= 1
    assert "".join(e["data"] for e in tokens) == "最终汇总"

    # team_done 收尾
    assert "team_done" in event_types


async def test_run_team_path_all_subtasks_fail_yields_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """所有子任务失败时，发送 error 事件。"""
    todos = [
        _todo("[agent:code] 读文件"),
    ]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

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
    assert len(error_events) >= 1
    assert any("所有专家任务均失败" in e["data"] for e in error_events)


async def test_run_team_path_partial_failure_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """部分子任务失败时，成功结果仍进入黑板并触发 Aggregator。"""
    todos = [
        _todo("[agent:code] 读文件"),
        _todo("[agent:rag] 检索"),
    ]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

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

    # code 成功 → Aggregator 仍执行并输出 token
    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) >= 1
    # team_done 收尾
    assert any(e["event"] == "team_done" for e in events)


async def test_run_team_path_invalid_plan_yields_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Orchestrator 输出空 todos 时发送 error。"""
    _patch_orchestrator_to_return_todos(monkeypatch, [])

    events = await _collect_events(
        run_team_path("分析项目的整体架构设计", "t-invalid", {"thread_id": "t-invalid", "messages": []})
    )

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) >= 1
    assert any("Orchestrator 未生成有效计划" in e["data"] for e in error_events)


async def test_run_team_path_deep_subtask_propagates_approval_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deep 子任务的 approval_request 事件必须透传到前端，否则审批流死锁。"""
    todos = [
        _todo("[agent:deep] 写入文件"),
    ]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

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

    # deep 子任务完成后 todo_update 最终为 completed
    todo_updates = [e for e in events if e["event"] == "todo_update"]
    assert len(todo_updates) >= 1
    final_todos = json.loads(todo_updates[-1]["data"]).get("todos", [])
    assert any(t["status"] == "completed" for t in final_todos)


async def test_run_team_path_token_data_is_plain_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """token 事件 data 必须是纯字符串，不能是 JSON 序列化（Bug 1 回归测试）。"""
    todos = [
        _todo("[agent:code] 读文件"),
    ]
    _patch_orchestrator_to_return_todos(monkeypatch, todos)

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

    # 不应有 todo_update 事件（Orchestrator 未执行）
    event_types = [e["event"] for e in events]
    assert "todo_update" not in event_types
    # 应有 token 事件（降级提示）+ team_done 事件
    assert "token" in event_types
    assert "team_done" in event_types
    # token 内容应包含切换模式提示
    token_evt = next(e for e in events if e["event"] == "token")
    assert "work" in token_evt["data"] or "切换" in token_evt["data"]


# ============================================================
# 5. SSE 事件构造工具
# ============================================================


def test_make_sse_event_serializes_dict_data() -> None:
    """make_sse_event 把 dict data 序列化为 JSON 字符串。"""
    event = make_sse_event("reasoning", {"content": "思考中", "source": "team"})
    assert event["event"] == "reasoning"
    data = json.loads(event["data"])
    assert data["content"] == "思考中"
    assert data["source"] == "team"


def test_make_sse_event_token_uses_plain_string() -> None:
    """token 事件 data 必须是纯字符串（与 graph.py _sse 约定一致）。"""
    event = make_sse_event("token", "你好")
    assert event["event"] == "token"
    assert event["data"] == "你好"  # 不是 '"你好"'
