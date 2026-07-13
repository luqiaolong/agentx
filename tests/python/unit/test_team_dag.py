"""AgentTeam DAG 依赖编排单元测试（T17 / T18 / T19 / T21 / T22）。

覆盖：
- T17: ``_parse_todos_from_text`` ``[after:]`` 标注解析 + ``_validate_dag`` 拓扑分层
- T18: ``_inject_dependency_context`` 依赖结果注入
- T19: ``_dispatch_batch_router`` 分批 fan-out + ``_route_after_level`` 条件边路由
- T21: ``_replan_check_node`` 迭代式 replan
- T22: ``_build_team_graph`` StateGraph 拓扑
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.types import Send

from app.config import get_settings
from app.team.blackboard import TeamPlanTask
from app.team.orchestrator import (
    _build_team_graph,
    _check_next_level_node,
    _dispatch_batch_node,
    _dispatch_batch_router,
    _inject_dependency_context,
    _replan_check_node,
    _route_after_level,
    _route_after_replan,
)
from app.team.planner import _parse_after_deps, _parse_todos_from_text, _validate_dag


# ============================================================
# 公共 fixtures
# ============================================================


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例前后清理 settings lru_cache，避免环境变量污染。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _task(agent: str, input_text: str, deps: list[int] | None = None) -> TeamPlanTask:
    """构造 TeamPlanTask（便于测试）。"""
    return TeamPlanTask(
        agent=agent,
        input=input_text,
        purpose="",
        deps=list(deps) if deps is not None else [],
    )


# ============================================================
# T17: DAG 解析与校验
# ============================================================


class TestParseAfterAnnotation:
    """``_parse_todos_from_text`` ``[after:]`` 标注解析（含容错）。"""

    def test_parse_after_annotation(self) -> None:
        """单依赖/多依赖/无依赖/容错（# 前缀、分号、非数字）。"""
        text = (
            "[agent:code] 读取 main.py\n"
            "[agent:deep][after:0] 修改 main.py\n"
            "[agent:rag][after:0,1] 检索相关文档\n"
            "[agent:web][after:abc] 联网搜索\n"
            "[agent:code][after:#0,#1] 复核代码\n"
            "[agent:rag][after:0;1] 二次检索\n"
        )
        todos = _parse_todos_from_text(text)
        assert len(todos) == 6

        # 0: 无依赖
        assert todos[0]["deps"] == []

        # 1: 单依赖 [after:0]
        assert todos[1]["deps"] == [0]
        assert "[after:0]" in todos[1]["content"]

        # 2: 多依赖 [after:0,1]
        assert todos[2]["deps"] == [0, 1]
        assert "[after:0,1]" in todos[2]["content"]

        # 3: 容错 - 非数字 [after:abc] → []
        assert todos[3]["deps"] == []

        # 4: 容错 - # 前缀 [after:#0,#1] → [0, 1]
        assert todos[4]["deps"] == [0, 1]

        # 5: 容错 - 分号 [after:0;1] → [0, 1]
        assert todos[5]["deps"] == [0, 1]

    def test_parse_after_deps_unit(self) -> None:
        """``_parse_after_deps`` 单元测试：覆盖各种边界输入。"""
        # 正常多依赖
        assert _parse_after_deps("0,1") == [0, 1]
        # 带空格
        assert _parse_after_deps("0, 1") == [0, 1]
        # # 前缀
        assert _parse_after_deps("#0,#1") == [0, 1]
        # 分号分隔
        assert _parse_after_deps("0;1") == [0, 1]
        # 非数字丢弃
        assert _parse_after_deps("abc") == []
        # 混合：有效 + 无效
        assert _parse_after_deps("0,abc,1") == [0, 1]
        # 空串 / None
        assert _parse_after_deps("") == []
        assert _parse_after_deps(None) == []  # type: ignore[arg-type]


class TestValidateDag:
    """``_validate_dag`` Kahn 算法分层 + 循环检测。"""

    def test_validate_dag_linear(self) -> None:
        """A→B→C 线性链：levels=[[0],[1],[2]]，无 dropped_edges。"""
        tasks = [
            _task("code", "A"),
            _task("code", "B", deps=[0]),
            _task("code", "C", deps=[1]),
        ]
        levels, dropped = _validate_dag(tasks)
        assert levels == [[0], [1], [2]]
        assert dropped == []

    def test_validate_dag_diamond(self) -> None:
        """菱形：A→B, A→C, B→D, C→D → levels=[[0],[1,2],[3]]。"""
        tasks = [
            _task("code", "A"),                 # 0
            _task("code", "B", deps=[0]),       # 1
            _task("code", "C", deps=[0]),       # 2
            _task("code", "D", deps=[1, 2]),    # 3
        ]
        levels, dropped = _validate_dag(tasks)
        assert levels == [[0], [1, 2], [3]]
        assert dropped == []

    def test_validate_dag_all_parallel(self) -> None:
        """全并行（无 deps）→ 单层 [[0,1,2,...]]。"""
        tasks = [
            _task("code", "A"),
            _task("code", "B"),
            _task("code", "C"),
        ]
        levels, dropped = _validate_dag(tasks)
        assert levels == [[0, 1, 2]]
        assert dropped == []

    def test_validate_dag_cycle(self) -> None:
        """循环：A→B, B→A → 强制打破，两任务都出现在某层 + dropped_edges 非空。"""
        tasks = [
            _task("code", "A", deps=[1]),  # 0 依赖 1
            _task("code", "B", deps=[0]),  # 1 依赖 0
        ]
        levels, dropped = _validate_dag(tasks)
        # 所有任务都应被分层（强加进当前层打破循环）
        all_idx = {i for level in levels for i in level}
        assert all_idx == {0, 1}
        # dropped_edges 非空（循环打破被记录）
        assert len(dropped) >= 1

    def test_validate_dag_self_loop(self) -> None:
        """自环：``[after:0]`` 引用自身 → dep 丢弃 + dropped_edges 含该任务索引。"""
        tasks = [
            _task("code", "A", deps=[0]),  # 自环
            _task("code", "B"),            # 无依赖
        ]
        levels, dropped = _validate_dag(tasks)
        # 自环 dep 被丢弃，A 与 B 同层
        assert levels == [[0, 1]]
        # dropped_edges 记录了自环任务索引
        assert 0 in dropped

    def test_validate_dag_out_of_bounds(self) -> None:
        """越界：``[after:99]`` → dep 丢弃 + dropped_edges 含该任务索引。"""
        tasks = [
            _task("code", "A"),
            _task("code", "B", deps=[99]),  # 越界
        ]
        levels, dropped = _validate_dag(tasks)
        # 越界 dep 丢弃后，两任务都无依赖 → 同层
        assert levels == [[0, 1]]
        # dropped_edges 记录了越界任务索引
        assert 1 in dropped

    def test_validate_dag_empty(self) -> None:
        """空 tasks 列表 → ([], [])。"""
        levels, dropped = _validate_dag([])
        assert levels == []
        assert dropped == []


# ============================================================
# T18: 结果注入
# ============================================================


class TestInjectDependencyContext:
    """``_inject_dependency_context`` 依赖结果注入。"""

    def test_inject_dependency_context_single(self) -> None:
        """单依赖：input 头部加 ``[依赖任务结果]`` + 依赖内容 + ``[当前任务]``。"""
        task = _task("code", "分析代码", deps=[0])
        findings = {"code-0": "main.py 入口是 uvicorn.run"}
        result = _inject_dependency_context(task, findings)
        assert result.startswith("[依赖任务结果]")
        assert "main.py 入口是 uvicorn.run" in result
        assert "[当前任务]" in result
        assert "分析代码" in result

    def test_inject_dependency_context_multi(self) -> None:
        """多依赖：所有依赖内容都注入。"""
        task = _task("deep", "综合实现", deps=[0, 1])
        findings = {
            "code-0": "代码结构分析结果",
            "rag-1": "知识库检索结果",
        }
        result = _inject_dependency_context(task, findings)
        assert "[依赖任务结果]" in result
        assert "代码结构分析结果" in result
        assert "知识库检索结果" in result
        assert "[当前任务]" in result
        assert "综合实现" in result
        # 两个依赖都应出现 #0 与 #1 标记
        assert "#0" in result
        assert "#1" in result

    def test_inject_dependency_context_missing(self) -> None:
        """依赖未完成（findings 中无对应 key）→ 显示 ``(未完成或失败)``。"""
        task = _task("code", "后续任务", deps=[0])
        findings: dict[str, str] = {}  # 空 findings
        result = _inject_dependency_context(task, findings)
        assert "[依赖任务结果]" in result
        assert "#0 (未完成或失败)" in result
        assert "[当前任务]" in result
        assert "后续任务" in result

    def test_inject_dependency_context_truncate(self) -> None:
        """依赖内容 > max_chars → 截断 + ``[结果已截断]`` 标记。"""
        # 设置较小的 max_chars 触发截断
        settings = get_settings()
        original = settings.agent_team_result_max_chars
        settings.agent_team_result_max_chars = 10
        try:
            task = _task("code", "后续", deps=[0])
            long_content = "a" * 200
            findings = {"code-0": long_content}
            result = _inject_dependency_context(task, findings)
            assert "[结果已截断]" in result
            # 截断后不应包含完整 200 字符
            assert long_content not in result
        finally:
            settings.agent_team_result_max_chars = original

    def test_inject_dependency_context_no_deps(self) -> None:
        """无依赖 → 原样返回 task.input（不注入任何头部）。"""
        task = _task("code", "原始任务输入")
        findings = {"code-0": "不应被注入的内容"}
        result = _inject_dependency_context(task, findings)
        assert result == "原始任务输入"
        assert "依赖任务结果" not in result
        assert "不应被注入的内容" not in result


# ============================================================
# T19: 分批 fan-out
# ============================================================


class TestDispatchBatch:
    """``_dispatch_batch_router`` 分批派发 + ``_route_after_level`` 条件边路由。"""

    def test_dispatch_batch_node_returns_empty_dict(self) -> None:
        """``_dispatch_batch_node`` 是 join 节点，返回空 dict（无状态更新）。"""
        state: dict[str, Any] = {"pending_levels": [[0]]}
        result = _dispatch_batch_node(state)
        assert result == {}

    async def test_check_next_level_node_returns_empty_dict(self) -> None:
        """``_check_next_level_node`` 是 join 节点，返回空 dict（不修改 state）。"""
        state: dict[str, Any] = {"pending_levels": [[0]]}
        result = await _check_next_level_node(state)
        assert result == {}

    def test_dispatch_batch_router_multi_level(self) -> None:
        """多层派发：``_dispatch_batch_router`` 只 fan-out 当前层（pending_levels[0]）。"""
        plan = [
            _task("code", "A"),                # 0
            _task("code", "B", deps=[0]),      # 1
            _task("code", "C", deps=[1]),      # 2
        ]
        state: dict[str, Any] = {
            "pending_levels": [[0], [1], [2]],  # 3 层
            "plan": plan,
            "findings": {},
            "todos": [],
            "thread_id": "t-multi",
        }
        sends = _dispatch_batch_router(state)
        # 只派发第一层 [0]，1 个 Send
        assert len(sends) == 1
        assert all(isinstance(s, Send) for s in sends)
        assert sends[0].node == "subtask"
        # payload 含 task_index=0 与 remaining_levels=[[1],[2]]
        payload = sends[0].arg
        assert payload["task_index"] == 0
        assert payload["remaining_levels"] == [[1], [2]]
        assert payload["task"]["agent"] == "code"
        assert payload["task"]["input"] == "A"

    def test_dispatch_batch_router_parallel_level(self) -> None:
        """同层多任务并行：当前层 [0,1] 都被 fan-out。"""
        plan = [
            _task("code", "A"),  # 0
            _task("rag", "B"),   # 1
            _task("code", "C", deps=[0, 1]),  # 2
        ]
        state: dict[str, Any] = {
            "pending_levels": [[0, 1], [2]],
            "plan": plan,
            "findings": {},
            "todos": [],
            "thread_id": "t-parallel-level",
        }
        sends = _dispatch_batch_router(state)
        assert len(sends) == 2
        # 两个 Send 都指向 subtask 节点
        assert all(s.node == "subtask" for s in sends)
        # task_index 分别为 0 和 1
        indices = sorted(s.arg["task_index"] for s in sends)
        assert indices == [0, 1]
        # remaining_levels 都是 [[2]]
        for s in sends:
            assert s.arg["remaining_levels"] == [[2]]

    def test_dispatch_batch_router_empty(self) -> None:
        """空 pending_levels → 返回 ``[Send("aggregate", {})]``。"""
        state: dict[str, Any] = {
            "pending_levels": [],
            "plan": [],
            "findings": {},
        }
        sends = _dispatch_batch_router(state)
        assert len(sends) == 1
        assert isinstance(sends[0], Send)
        assert sends[0].node == "aggregate"

    def test_dispatch_batch_router_injects_dependency_context(self) -> None:
        """fan-out 时对依赖任务调用 ``_inject_dependency_context`` 注入 findings。"""
        plan = [
            _task("code", "A"),                  # 0
            _task("deep", "B", deps=[0]),        # 1
        ]
        findings = {"code-0": "前置任务结果"}
        state: dict[str, Any] = {
            "pending_levels": [[1]],  # 只派发第二层
            "plan": plan,
            "findings": findings,
            "todos": [],
            "thread_id": "t-inject",
        }
        sends = _dispatch_batch_router(state)
        assert len(sends) == 1
        payload = sends[0].arg
        # input 应被注入依赖上下文（含前置任务结果）
        assert "前置任务结果" in payload["task"]["input"]
        assert "[依赖任务结果]" in payload["task"]["input"]


class TestRouteAfterLevel:
    """``_route_after_level`` 条件边路由。"""

    def test_check_next_level_has_more(self) -> None:
        """pending_levels 非空 → 路由到 ``dispatch_batch``。"""
        state: dict[str, Any] = {"pending_levels": [[1], [2]]}
        assert _route_after_level(state) == "dispatch_batch"

    def test_check_next_level_done(self) -> None:
        """pending_levels 为空 → 路由到 ``replan_check``。"""
        state: dict[str, Any] = {"pending_levels": []}
        assert _route_after_level(state) == "replan_check"

    def test_check_next_level_missing_field(self) -> None:
        """state 缺少 pending_levels 字段 → 视为空，路由到 ``replan_check``。"""
        state: dict[str, Any] = {}
        assert _route_after_level(state) == "replan_check"


# ============================================================
# T21: Replan
# ============================================================


def _make_replan_settings() -> Any:
    """构造 _replan_check_node 使用的 settings（max_replans=2）。"""
    settings = get_settings()
    # 确保有 team_subagents 默认配置（_validate_task 需要）
    return settings


def _make_replan_state(
    *,
    plan: list[TeamPlanTask],
    findings: dict[str, str],
    completed_tasks: list[int],
    replan_count: int,
    message: str = "原始请求",
) -> dict[str, Any]:
    """构造 _replan_check_node 的 state。"""
    return {
        "message": message,
        "plan": plan,
        "findings": findings,
        "completed_tasks": completed_tasks,
        "replan_count": replan_count,
    }


class TestReplanCheck:
    """``_replan_check_node`` 迭代式 replan。"""

    async def test_replan_check_add_tasks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 返回新任务 → plan 扩展，pending_levels 更新，replan_count 递增。"""
        # 初始 plan: 1 个已完成 code 任务
        plan = [_task("code", "读取 main.py")]
        findings = {"code-0": "main.py 入口分析完成"}
        state = _make_replan_state(
            plan=plan,
            findings=findings,
            completed_tasks=[0],
            replan_count=0,
        )

        # mock LLM 返回新任务
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(
            return_value=SimpleNamespace(
                content="[agent:code][after:0] 复核关键函数实现"
            )
        )
        monkeypatch.setattr(
            "app.team.orchestrator.get_chat_model", lambda **_: fake_llm
        )
        # mock stream writer（_replan_check_node 发射 team_replan 事件）
        written: list[dict] = []

        def _fake_writer(event: dict) -> None:
            written.append(event)

        monkeypatch.setattr(
            "app.team.orchestrator.get_stream_writer", lambda: _fake_writer
        )

        result = await _replan_check_node(state)

        # 返回 dict 含 plan / pending_levels / replan_count
        assert "plan" in result
        assert "pending_levels" in result
        assert "replan_count" in result
        # plan 扩展为 2 个任务
        assert len(result["plan"]) == 2
        new_task = result["plan"][1]
        assert new_task.agent == "code"
        assert new_task.input == "复核关键函数实现"
        assert new_task.deps == [0]
        # replan_count 递增
        assert result["replan_count"] == 1
        # pending_levels：新任务索引 1 未完成 → 出现在某层
        all_pending = {i for level in result["pending_levels"] for i in level}
        assert 1 in all_pending
        # 已完成任务 0 不应出现在 pending_levels
        assert 0 not in all_pending
        # 发射了 team_replan 事件
        assert any(e.get("event") == "team_replan" for e in written)

    async def test_replan_check_no_new(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 返回 ``NO_NEW_TASKS`` → 返回空 dict（走 aggregate）。"""
        plan = [_task("code", "读取 main.py")]
        findings = {"code-0": "结果"}
        state = _make_replan_state(
            plan=plan,
            findings=findings,
            completed_tasks=[0],
            replan_count=0,
        )

        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(
            return_value=SimpleNamespace(content="NO_NEW_TASKS")
        )
        monkeypatch.setattr(
            "app.team.orchestrator.get_chat_model", lambda **_: fake_llm
        )
        monkeypatch.setattr(
            "app.team.orchestrator.get_stream_writer", lambda: lambda _e: None
        )

        result = await _replan_check_node(state)
        # 返回空 dict → 走 aggregate
        assert result == {}

    async def test_replan_check_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``replan_count >= max_replans`` → 返回空 dict，不调 LLM。"""
        plan = [_task("code", "读取 main.py")]
        findings = {"code-0": "结果"}
        # replan_count 已达上限（默认 max_replans=2）
        state = _make_replan_state(
            plan=plan,
            findings=findings,
            completed_tasks=[0],
            replan_count=2,
        )

        # LLM 不应被调用
        def _llm_should_not_be_called(**_: Any) -> Any:
            raise AssertionError("LLM should not be called when replan_count >= max_replans")

        monkeypatch.setattr(
            "app.team.orchestrator.get_chat_model", _llm_should_not_be_called
        )
        monkeypatch.setattr(
            "app.team.orchestrator.get_stream_writer", lambda: lambda _e: None
        )

        result = await _replan_check_node(state)
        assert result == {}

    async def test_replan_check_dep_injection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """replan 新任务带 ``[after:N1,N2]`` → deps 正确解析并附在 TeamPlanTask 上。"""
        plan = [
            _task("code", "A"),   # 0
            _task("rag", "B"),    # 1
        ]
        findings = {
            "code-0": "A 结果",
            "rag-1": "B 结果",
        }
        state = _make_replan_state(
            plan=plan,
            findings=findings,
            completed_tasks=[0, 1],
            replan_count=0,
        )

        # LLM 返回多依赖新任务
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(
            return_value=SimpleNamespace(
                content="[agent:deep][after:0,1] 综合 A 和 B 实现完整方案"
            )
        )
        monkeypatch.setattr(
            "app.team.orchestrator.get_chat_model", lambda **_: fake_llm
        )
        monkeypatch.setattr(
            "app.team.orchestrator.get_stream_writer", lambda: lambda _e: None
        )

        result = await _replan_check_node(state)
        # 新任务索引 2，deps=[0, 1]
        new_task = result["plan"][2]
        assert new_task.agent == "deep"
        assert new_task.deps == [0, 1]
        assert "综合" in new_task.input


# ============================================================
# T22: StateGraph 拓扑
# ============================================================


class TestBuildTeamGraph:
    """``_build_team_graph`` StateGraph 拓扑校验。"""

    def test_build_team_graph_nodes(self) -> None:
        """图含 6 个节点：plan / dispatch_batch / subtask / check_next_level / replan_check / aggregate。"""
        graph = _build_team_graph()
        node_names = set(graph.nodes.keys())
        # 必需节点
        required = {
            "plan",
            "dispatch_batch",
            "subtask",
            "check_next_level",
            "replan_check",
            "aggregate",
        }
        assert required.issubset(node_names), (
            f"缺少节点: {required - node_names}; 实际节点: {node_names}"
        )

    def test_build_team_graph_no_legacy_nodes(self) -> None:
        """图不含旧分类节点（subtask_deep / subtask_code / subtask_builtin / 等）。"""
        graph = _build_team_graph()
        node_names = set(graph.nodes.keys())
        legacy = {
            "subtask_deep",
            "subtask_code",
            "subtask_builtin",
            "subtask_team_role",
            "subtask_custom",
            "subtask_default",
            # 旧 plan-once 架构的节点
            "dispatch",
            "execute",
        }
        present_legacy = legacy & node_names
        assert not present_legacy, (
            f"不应存在旧节点: {present_legacy}"
        )

    def test_build_team_graph_edges(self) -> None:
        """关键边存在：START→plan, plan→dispatch_batch, subtask→check_next_level, aggregate→END。"""
        graph = _build_team_graph()
        edges = set(graph.builder.edges)
        # START → plan
        assert ("__start__", "plan") in edges
        # plan → dispatch_batch
        assert ("plan", "dispatch_batch") in edges
        # subtask → check_next_level
        assert ("subtask", "check_next_level") in edges
        # aggregate → END
        assert ("aggregate", "__end__") in edges

    def test_build_team_graph_conditional_edges(self) -> None:
        """条件边存在：dispatch_batch / check_next_level / replan_check 都有 branches。"""
        graph = _build_team_graph()
        branches = graph.builder.branches
        # dispatch_batch 用 add_conditional_edges（fan-out 到 subtask 或 aggregate）
        assert "dispatch_batch" in branches
        # check_next_level 条件边 → dispatch_batch | replan_check
        assert "check_next_level" in branches
        # replan_check 条件边 → dispatch_batch | aggregate
        assert "replan_check" in branches


class TestRouteAfterReplan:
    """``_route_after_replan`` 条件边路由。"""

    def test_route_after_replan_has_new_tasks(self) -> None:
        """pending_levels 非空（replan 追加了新任务）→ ``dispatch_batch``。"""
        state: dict[str, Any] = {"pending_levels": [[2]]}
        assert _route_after_replan(state) == "dispatch_batch"

    def test_route_after_replan_no_new_tasks(self) -> None:
        """pending_levels 为空（无新任务 / 达到上限）→ ``aggregate``。"""
        state: dict[str, Any] = {"pending_levels": []}
        assert _route_after_replan(state) == "aggregate"

    def test_route_after_replan_missing_field(self) -> None:
        """state 缺少 pending_levels 字段 → 视为空，路由到 ``aggregate``。"""
        state: dict[str, Any] = {}
        assert _route_after_replan(state) == "aggregate"


class TestRouteAfterLevelIntegration:
    """``_route_after_level`` 条件边路由（T22 综合测试）。"""

    def test_route_after_level_has_more(self) -> None:
        """pending_levels 非空 → ``dispatch_batch``。"""
        state: dict[str, Any] = {"pending_levels": [[1]]}
        assert _route_after_level(state) == "dispatch_batch"

    def test_route_after_level_done(self) -> None:
        """pending_levels 为空 → ``replan_check``。"""
        state: dict[str, Any] = {"pending_levels": []}
        assert _route_after_level(state) == "replan_check"
