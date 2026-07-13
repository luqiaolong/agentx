# Tasks: AgentTeam DAG 依赖编排

> **DEPENDS_ON**: `2026-07-12-agent-team-architecture-cleanup`（Phase 2 架构清理）
>
> 实现顺序按依赖关系排列。每个任务标记 [P0]/[P1]/[P2] 表示优先级。
> 验证命令：`uv run pytest tests/python/unit -m "not integration" -k "team"` + `uv run ruff check backend/app/team/`
>
> **关键约束**：T9 / T13 / T15 依赖 Phase 2 A1（`_run_subtask_node` + `_NODE_DISPATCH` 派发表）落地。
> 若 Phase 2 未先落地，这三个任务无法实现（会与 Phase 2 删除旧节点函数的改造硬冲突）。

## T1 [P0] 扩展 `TeamPlanTask` 与 `TeamState` 数据结构

**Files**: `backend/app/team/blackboard.py`
**Depends on**: 无
**Verified by**: `tests/python/unit/test_team_dag.py::test_team_plan_task_deps_field`

- [ ] `TeamPlanTask` dataclass 新增 `deps: list[int] = field(default_factory=list)` 字段
- [ ] `TeamState` TypedDict 新增 `pending_levels: list[list[int]]` 字段
- [ ] `TeamState` 新增 `completed_tasks: list[int]` 字段（用 list 不用 set，避免 TypedDict 序列化问题）
- [ ] `TeamState` 新增 `replan_count: int` 字段
- [ ] 新增 `_merge_pending_levels(left, right)` reducer：非空 right 覆盖 left，空 right 忽略
- [ ] `pending_levels` 字段标注 `Annotated[list[list[int]], _merge_pending_levels]`
- [ ] 更新 `__all__` 导出

## T2 [P0] 扩展 `_AGENT_PREFIX_RE` 正则与 `_parse_todos_from_text`

**Files**: `backend/app/team/planner.py`
**Depends on**: T1
**Verified by**: `tests/python/unit/test_team_dag.py::test_parse_after_annotation`

- [ ] `_AGENT_PREFIX_RE` 扩展为 3 group：`[agent:xxx][after:N1,N2] input`
  - group(1) = agent 名
  - group(2) = after 内容（可选，如 `"0, 1"` 或 `None`）
  - group(3) = input 文本
- [ ] `_parse_todos_from_text` 返回的每个 todo dict 新增 `deps: list[int]` 字段
- [ ] 容错：`[after:abc]` 非数字 → `deps=[]` + logger.warning
- [ ] 容错：`[after:#0,#1]` 去掉 `#` 前缀
- [ ] 容错：`[after:0;1]` 分号也作分隔符
- [ ] 兼容：无 `[after:]` → `deps=[]`

## T3 [P0] 新增 `_validate_dag` 拓扑排序 + 循环检测

**Files**: `backend/app/team/planner.py`
**Depends on**: T2
**Verified by**: `tests/python/unit/test_team_dag.py::test_validate_dag_*`

- [ ] 实现 `_validate_dag(tasks: list[TeamPlanTask]) -> tuple[list[list[int]], list[int]]`
- [ ] Kahn 算法分层：返回 `levels`（每层一组任务索引）
- [ ] 循环检测：入度为 0 的节点为空时，强加入度最小的节点打破循环
- [ ] 越界索引（`dep >= len(tasks)` 或 `dep < 0`）丢弃 + logger.warning
- [ ] 自环（`dep == i`）丢弃 + logger.warning
- [ ] 循环打破时 logger.error 记录被强制执行的任务索引
- [ ] 更新 `__all__` 导出

## T4 [P0] 扩展 `_todos_to_team_tasks` 传递 deps

**Files**: `backend/app/team/planner.py`
**Depends on**: T3
**Verified by**: `tests/python/unit/test_team_dag.py::test_todos_to_team_tasks_deps`

- [ ] `_todos_to_team_tasks` 解析每个 todo 的 `deps` 字段，传入 `TeamPlanTask.deps`
- [ ] 保留现有安全改写（`_looks_like_dangerous_task`）与 `max_tasks` 截断逻辑
- [ ] 截断时同步截断 `deps`（若任务被截断，引用它的 deps 也需清理）
- [ ] 调用 `_validate_dag(tasks)` 返回 `levels`，连同 tasks 一起返回（或单独函数）

## T5 [P0] 修改 `_plan_node` 初始化 `pending_levels`

**Files**: `backend/app/team/orchestrator.py`
**Depends on**: T4
**Verified by**: `tests/python/unit/test_team_dag.py::test_plan_node_pending_levels`

- [ ] `_plan_node` 返回值新增 `pending_levels`（来自 `_validate_dag`）
- [ ] `_plan_node` 返回值新增 `completed_tasks: []`（初始为空）
- [ ] `_plan_node` 返回值新增 `replan_count: 0`
- [ ] `team_init` 事件 payload 的 plan items 新增 `deps` 字段（前端展示依赖关系）
- [ ] 空 plan 时 `pending_levels = []`

## T6 [P0] 新增 `_inject_dependency_context` helper

**Files**: `backend/app/team/orchestrator.py`（或 `planner.py`，视模块职责）
**Depends on**: T1
**Verified by**: `tests/python/unit/test_team_dag.py::test_inject_dependency_context_*`

- [ ] 实现 `_inject_dependency_context(task: TeamPlanTask, findings: dict[str, str]) -> str`
- [ ] 无 deps 时原样返回 task.input
- [ ] 有 deps 时构造 `[依赖任务结果]` + 每个依赖的 `--- #N [agent-N] ---` + 内容 + `[当前任务]` + input
- [ ] 依赖未完成时显示 `--- #N (未完成或失败) ---`
- [ ] 每个依赖内容截断到 `agent_team_result_max_chars`
- [ ] 更新 `__all__` 导出

## T7 [P0] 新增 `_dispatch_batch_node` 替换 `_dispatch_node`

**Files**: `backend/app/team/orchestrator.py`
**Depends on**: T5, T6
**Verified by**: `tests/python/unit/test_team_dag.py::test_dispatch_batch_node_*`

- [ ] 新增 `_dispatch_batch_node(state: TeamState) -> list[Send]`
- [ ] 从 `state.pending_levels[0]` 取当前层任务索引
- [ ] 对每个索引：调用 `_inject_dependency_context` 构造 input，`_route_node_for_task` 路由节点
- [ ] 返回 `list[Send]`，每个 Send 携带 `{task, task_index, injected_input, ...}`
- [ ] 空 `pending_levels` 时返回 `[Send("aggregate", {})]`
- [ ] 保留 `_dispatch_node` 旧函数标记删除（或直接替换，开发阶段推倒重来）

## T8 [P0] 新增 `_check_next_level_node` 与条件边路由

**Files**: `backend/app/team/orchestrator.py`
**Depends on**: T7
**Verified by**: `tests/python/unit/test_team_dag.py::test_check_next_level_*`

- [ ] 新增 `_check_next_level_node(state: TeamState) -> dict`（返回空 dict，路由由条件边决定）
- [ ] 新增 `_route_after_level(state: TeamState) -> str`：`pending_levels` 非空 → `"dispatch_batch"`，空 → `"replan_check"`
- [ ] 子任务节点返回时通过 `_merge_pending_levels` reducer 弹出已派发的层
  - 第一个子任务返回 `{"pending_levels": remaining_levels}`
  - 后续子任务返回 `{"pending_levels": []}`（reducer 忽略空）

## T9 [P0] 扩展 Phase 2 `_DEFAULT_CONFIG` 为 fallback 到 code

**Files**: `backend/app/team/orchestrator.py`（或 Phase 2 引入的 `_NODE_DISPATCH` 派发表）
**Depends on**: T1, **Phase 2 A1（_run_subtask_node + _NODE_DISPATCH 落地）**
**Verified by**: `tests/python/unit/test_team_fallback.py::test_default_config_fallback`

- [ ] 在 Phase 2 的 `_NODE_DISPATCH` 派发表中，为 default key 注册 `_DEFAULT_FALLBACK_CONFIG`
  （或修改现有 `_DEFAULT_CONFIG`）
- [ ] `_DEFAULT_FALLBACK_CONFIG.runner = _get_runner("code")`
- [ ] `_DEFAULT_FALLBACK_CONFIG.workspace_inherit = True`（code runner 需要 workspace）
- [ ] 新增 `_log_unknown_agent_fallback(task, ctx)` pre_run_hook：`logger.warning("team unknown agent fallback to code", agent=task.agent)`
- [ ] `_run_subtask_node` 中 `agent_name=task.agent` 保留原始名（不改为 "code"）
- [ ] 不新增节点函数，完全复用 Phase 2 的 `_run_subtask_node`

## T10 [P1] 改造 `_run_team_role_subtask` 显式失败

**Files**: `backend/app/team/scheduler.py`
**Depends on**: 无
**Verified by**: `tests/python/unit/test_team_fallback.py::test_team_role_explicit_fail`

- [ ] `cfg` 为 None 或 `cfg.system_prompt` 为空时：
  - logger.error 记录
  - 返回 `TeamSubtaskResult(success=False, payload="团队角色 {agent} 配置缺失 system_prompt")`
  - 不再降级到 coding Expert
- [ ] 保留正常路径（`build_custom_agent` + `astream_events`）不变

## T11 [P1] 新增 `validate_team_subagents` 启动校验

**Files**: `backend/app/config/subagents.py`, `backend/app/main.py`（或 lifespan）
**Depends on**: T10
**Verified by**: `tests/python/unit/test_team_fallback.py::test_validate_team_subagents`

- [ ] 新增 `validate_team_subagents(settings: Any) -> None`
- [ ] 遍历 `settings.team_subagents`，所有 `enabled=True` 的必须有非空 `system_prompt`
- [ ] 否则抛 `ValueError` 阻止启动
- [ ] 在 `app.main` lifespan 启动阶段调用

## T12 [P1] 新增 `_replan_check_node` 与条件边

**Files**: `backend/app/team/orchestrator.py`
**Depends on**: T8
**Verified by**: `tests/python/unit/test_team_dag.py::test_replan_check_*`

- [ ] 新增 `_REPLAN_SYSTEM_PROMPT` 模板（含已完成任务摘要 + 用户原始请求）
- [ ] 新增 `_replan_check_node(state: TeamState) -> dict`
  - `replan_count >= max_replans` 时不调 LLM，返回空 dict
  - 调 LLM 判定是否追加任务
  - `NO_NEW_TASKS` → 返回空 dict
  - 解析新任务（`_parse_todos_from_text` + `_validate_task`）
  - 追加到 `plan`，重算 `pending_levels`，递增 `replan_count`
  - 发射 `team_replan` SSE 事件
- [ ] 新增 `_route_after_replan(state) -> str`：`pending_levels` 非空 → `"dispatch_batch"`，空 → `"aggregate"`

## T13 [P0] 重构 `_build_team_graph` StateGraph 拓扑（复用 Phase 2 单一 `subtask` 节点）

**Files**: `backend/app/team/orchestrator.py`
**Depends on**: T7, T8, T12, **Phase 2 A1（单一 `subtask` 节点落地）**
**Verified by**: `tests/python/unit/test_team_dag.py::test_build_team_graph_topology`

- [ ] 复用 Phase 2 的 `plan` / `subtask` / `aggregate` 三个节点（不重命名、不拆分）
- [ ] 新增 `dispatch_batch` / `check_next_level` / `replan_check` 三个 DAG 编排节点
- [ ] 边：`START → plan → dispatch_batch`
- [ ] `dispatch_batch` 用 `add_conditional_edges`（返回 `list[Send("subtask", {...})]`）
- [ ] `subtask → check_next_level`
- [ ] `check_next_level` 条件边 → `dispatch_batch | replan_check`
- [ ] `replan_check` 条件边 → `dispatch_batch | aggregate`
- [ ] `aggregate → END`
- [ ] `_dispatch_batch_node` 通过 `_NODE_DISPATCH.get(task.agent, _DEFAULT_FALLBACK_CONFIG)` 解析 config（复用 Phase 2）
- [ ] **不**引入 `subtask_deep` / `subtask_code` / `subtask_builtin` / `subtask_team_role` / `subtask_custom` / `subtask_default` 节点

## T14 [P0] 新增 `agent_team_max_replans` 配置

**Files**: `backend/app/config/settings.py`
**Depends on**: 无
**Verified by**: `tests/python/unit/test_team_dag.py::test_max_replans_config`

- [ ] `Settings` 类新增 `agent_team_max_replans: int = Field(default=2, ge=0, le=5)`
- [ ] 更新 `__all__` 或字段导出

## T15 [P0] Phase 2 `_run_subtask_node` 适配新 SubtaskState 字段

**Files**: `backend/app/team/orchestrator.py`（Phase 2 引入的 `_run_subtask_node`）
**Depends on**: T7, **Phase 2 A1（_run_subtask_node 落地）**
**Verified by**: 既有 team 测试不回归

- [ ] Phase 2 的 `_run_subtask_node` 从 `state["task"]` 读取时兼容 `deps` 字段（`TeamPlanTask(**state["task"])` 自动支持）
- [ ] `_run_subtask_node` 返回时通过 `_make_subtask_state_update` 附带：
  - `pending_levels: []`（让 reducer 不覆盖首任务的 remaining_levels）
  - `completed_tasks: [task_index]`（累积已完成索引）
- [ ] `_make_subtask_state_update` 扩展：新增 `completed_tasks` 更新
- [ ] **不**修改 Phase 2 已删除的 5 个旧节点函数（`_deep_node` / `_code_node` / `_builtin_node` / `_team_role_node` / `_custom_node`）

## T16 [P1] `_make_subtask_state_update` 扩展 completed_tasks

**Files**: `backend/app/team/orchestrator.py`
**Depends on**: T15
**Verified by**: `tests/python/unit/test_team_dag.py::test_completed_tasks_accumulation`

- [ ] `_make_subtask_state_update` 新增 `completed_tasks: [task_index]` 到返回 dict
- [ ] `TeamState.completed_tasks` 用 `Annotated[list[int], _merge_list]` reducer（新增 `_merge_list` 去重合并）

## T17 [P0] 单元测试 - DAG 解析与校验

**Files**: `tests/python/unit/test_team_dag.py`（新建）
**Depends on**: T2, T3
**Verified by**: 测试通过

- [ ] `test_parse_after_annotation`：单依赖/多依赖/无依赖/容错
- [ ] `test_validate_dag_linear`：线性链
- [ ] `test_validate_dag_diamond`：菱形
- [ ] `test_validate_dag_all_parallel`：全并行
- [ ] `test_validate_dag_cycle`：循环打破
- [ ] `test_validate_dag_self_loop`：自环
- [ ] `test_validate_dag_out_of_bounds`：越界

## T18 [P0] 单元测试 - 结果注入

**Files**: `tests/python/unit/test_team_dag.py`
**Depends on**: T6
**Verified by**: 测试通过

- [ ] `test_inject_dependency_context_single`：单依赖
- [ ] `test_inject_dependency_context_multi`：多依赖
- [ ] `test_inject_dependency_context_missing`：依赖未完成
- [ ] `test_inject_dependency_context_truncate`：截断
- [ ] `test_inject_dependency_context_no_deps`：无依赖

## T19 [P0] 单元测试 - 分批 fan-out

**Files**: `tests/python/unit/test_team_dag.py`
**Depends on**: T7, T8
**Verified by**: 测试通过

- [ ] `test_dispatch_batch_node_multi_level`：多层派发
- [ ] `test_dispatch_batch_node_empty`：空计划
- [ ] `test_check_next_level_has_more`：还有层 → dispatch_batch
- [ ] `test_check_next_level_done`：无层 → replan_check

## T20 [P0] 单元测试 - fallback 与显式失败

**Files**: `tests/python/unit/test_team_fallback.py`（新建）
**Depends on**: T9, T10, T11
**Verified by**: 测试通过

- [ ] `test_default_config_fallback`：未知 agent 经 `_DEFAULT_FALLBACK_CONFIG` 走 code runner
- [ ] `test_default_config_abort`：fallback 中止
- [ ] `test_team_role_explicit_fail`：system_prompt 为空显式失败
- [ ] `test_validate_team_subagents_startup`：启动校验
- [ ] `test_validate_team_subagents_normal`：正常配置不报错

## T21 [P1] 单元测试 - replan

**Files**: `tests/python/unit/test_team_dag.py`
**Depends on**: T12
**Verified by**: 测试通过

- [ ] `test_replan_check_add_tasks`：LLM 判定追加
- [ ] `test_replan_check_no_new`：LLM 判定无需追加
- [ ] `test_replan_check_limit`：replan 次数上限
- [ ] `test_replan_check_dep_injection`：replan 新任务依赖注入

## T22 [P0] 单元测试 - StateGraph 拓扑

**Files**: `tests/python/unit/test_team_dag.py`
**Depends on**: T13
**Verified by**: 测试通过

- [ ] `test_build_team_graph_nodes`：节点完整（`plan` / `dispatch_batch` / `subtask` / `check_next_level` / `replan_check` / `aggregate`）
- [ ] `test_build_team_graph_no_legacy_nodes`：不包含 `subtask_deep` / `subtask_code` / 等旧节点
- [ ] `test_route_after_level`：条件边路由
- [ ] `test_route_after_replan`：条件边路由

## T23 [P1] 集成测试 - 端到端 DAG 场景

**Files**: `tests/python/integration/test_team_dag_e2e.py`（新建，可选）
**Depends on**: T13, T15
**Verified by**: 测试通过

- [ ] `test_e2e_sequential_tasks`：`[code] 读取 → [deep][after:0] 修改` 验证 deep 拿到 code findings
- [ ] `test_e2e_parallel_tasks`：无 `[after:]` 的任务仍并行
- [ ] `test_e2e_mixed_tasks`：混合并行 + 依赖
- [ ] `test_e2e_replan`：第一轮后 replan 追加任务
- [ ] `test_e2e_fallback`：未知 agent 走 code
- [ ] `test_e2e_explicit_fail`：team_role 缺 system_prompt 显式失败

## T24 [P1] 前端适配 - team_init plan 渲染 deps

**Files**: `frontend/renderer/components/chat/TeamNodeCard.tsx`（或相关组件）
**Depends on**: T5
**Verified by**: 前端 typecheck 通过

- [ ] `team_init.plan[].deps` 可选字段渲染（依赖箭头/缩进展示）
- [ ] 无 deps 字段时兼容（旧格式）
- [ ] 不破坏现有 TeamNodeCard 渲染逻辑

## T25 [P2] 文档更新

**Files**: `AGENTS.md`
**Depends on**: T13
**Verified by**: 文档 review

- [ ] §9 Team 路径说明补 DAG 依赖编排语义
- [ ] 说明 `[after:]` 标注格式
- [ ] 说明 `agent_team_max_replans` 配置

## T26 [P0] ruff check + 全量测试回归

**Files**: 无
**Depends on**: 所有前置任务
**Verified by**: `uv run ruff check backend/app/team/ && uv run pytest tests/python/unit -m "not integration"`

- [ ] `uv run ruff check backend/app/team/ backend/app/config/` 通过
- [ ] `uv run pytest tests/python/unit -m "not integration" -k "team"` 全部通过
- [ ] `uv run pytest tests/python/unit -m "not integration"` 全量不回归（预存失败除外）

## T27 [P0] OpenSpec validate

**Files**: 无
**Depends on**: 所有前置任务
**Verified by**: `openspec validate agent-team-dag-orchestration`

- [ ] `openspec validate agent-team-dag-orchestration` 通过
- [ ] `openspec status agent-team-dag-orchestration` 显示所有 artifact 就绪
