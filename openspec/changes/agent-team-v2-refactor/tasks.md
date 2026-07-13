# Tasks: AgentTeam v2 全量重构

> **Supersedes**: `agent-team-dag-orchestration` 与 `2026-07-12-agent-team-architecture-cleanup`（均未落地）
>
> 实现顺序按依赖关系排列，分 10 个阶段。每个任务标记 [P0]/[P1]/[P2] 表示优先级。
> 验证命令：`uv run pytest tests/python/unit -m "not integration" -k "team"` + `uv run ruff check backend/app/team/`
>
> **关键约束**：项目约定无需灰度、无需兼容老版本，直接推倒重来。所有任务完成后 `orchestrator.py` 整文件删除。

## 阶段1 数据结构（T1-T3）

## T1 [P0] 创建 `state.py`：TypedDict + Pydantic schema 拆分

**Files**: `backend/app/team/state.py`（新建）
**Depends on**: 无
**Verified by**: `tests/python/unit/test_team_v2.py::test_team_task_schema`

- [ ] 定义 `TeamTask` Pydantic BaseModel（`id` / `agent` / `description` / `depends_on` / `expected_output` / `is_dangerous_hint`）
- [ ] 定义 `TeamPlan` Pydantic BaseModel（`tasks` / `summary` / `needs_iterative`）
- [ ] 定义 `Finding` Pydantic BaseModel（`agent` / `task_id` / `wave_index` / `content` / `success` / `error` / `retries`）
- [ ] 定义 `ClassificationResult` Pydantic BaseModel（`is_dangerous` / `reason` / `suggested_agent`）
- [ ] 定义 `TeamState` TypedDict（含新字段 `pending_waves` / `warnings` / `completed_task_ids` / `replan_count`）
- [ ] 定义 `SubtaskState` TypedDict（含新字段 `upstream_findings` / `wave_index` / `replan_count`）
- [ ] 定义 `SubtaskConfig` dataclass（`agent_type` / `runner` / `workspace_inherit` / `emit_delegation` / `pre_run_hook`）
- [ ] 更新 `__all__` 导出

## T2 [P0] 创建 `blackboard.py`：Reducers 集中

**Files**: `backend/app/team/blackboard.py`（重写）
**Depends on**: T1
**Verified by**: `tests/python/unit/test_team_v2.py::test_reducers_merge`

- [ ] 实现 `_merge_findings(left, right) -> dict[str, Finding]`：dict 合并（right 覆盖 left 同 key）
- [ ] 实现 `_merge_errors(left, right) -> list[str]`：list 拼接去重
- [ ] 实现 `_merge_warnings(left, right) -> list[str]`：list 拼接去重（新 channel）
- [ ] 实现 `_merge_pending_waves(left, right) -> list[list[TeamTask]]`：非空 right 覆盖 left，空 right 忽略
- [ ] 实现 `_merge_completed_task_ids(left, right) -> set[str]`：set 合并
- [ ] 删除旧 `TeamPlanTask` / `TeamState`（迁移到 state.py）
- [ ] 删除死字段 `subtask_results`（Phase 2 A4）
- [ ] 更新 `__all__` 导出

## T3 [P0] 创建 `planner.py`：`with_structured_output(TeamPlan)`

**Files**: `backend/app/team/planner.py`（重写）
**Depends on**: T1, T2
**Verified by**: `tests/python/unit/test_team_v2.py::test_plan_with_structured_output`

- [ ] 实现 `Planner` 类，封装 Orchestrator LLM 调用
- [ ] `plan_with_llm(message, context) -> TeamPlan`：调用 `chat_model.with_structured_output(TeamPlan).ainvoke()`
- [ ] `replan(original_message, previous_plan, findings, errors, hint) -> TeamPlan`：replan prompt + 结构化输出
- [ ] 保留 `_AGENT_PREFIX_RE` 正则作为 fallback（`_parse_plan_from_text_fallback`）
- [ ] 启动时检测 LLM 是否支持 `with_structured_output`，不支持则走 fallback
- [ ] 删除旧 `_looks_like_dangerous_task`（迁移到 classifier.py，T19）
- [ ] 删除旧 `_parse_todos_from_text` / `_todos_to_team_tasks`（被 `with_structured_output` 替代）
- [ ] 更新 `__all__` 导出

## 阶段2 模块拆分（T4-T8）

## T4 [P0] 创建 `dispatcher.py`：Kahn wave 解析 + Send 生成

**Files**: `backend/app/team/dispatcher.py`（新建）
**Depends on**: T1
**Verified by**: `tests/python/unit/test_team_v2_dag.py::test_resolve_waves_*`

- [ ] 实现 `resolve_waves(tasks: list[TeamTask]) -> list[list[TeamTask]]`：Kahn 算法按依赖分层
- [ ] 循环检测：入度为 0 的节点为空时，强加入度最小的节点打破循环 + logger.error
- [ ] 越界 `dep_id`（不在 tasks 中）丢弃 + logger.warning
- [ ] 自环（`dep_id == task.id`）丢弃 + logger.warning
- [ ] 实现 `_inject_upstream_findings(task, findings) -> dict[str, Finding]`：根据 `depends_on` 提取上游结果
- [ ] 实现 `_compose_input_with_upstream(task, upstream) -> str`：拼入 `[依赖任务结果]` 段 + 当前任务
- [ ] 截断到 `team_result_max_chars`（默认 2000）
- [ ] 实现 `build_dispatch_sends(state) -> list[Send]`：从 `pending_waves[0]` 构造 Send 列表
- [ ] 更新 `__all__` 导出

## T5 [P0] 创建 `nodes.py`：统一节点函数

**Files**: `backend/app/team/nodes.py`（新建）
**Depends on**: T1, T2, T3, T4
**Verified by**: `tests/python/unit/test_team_v2.py::test_nodes_signatures`

- [ ] 实现 `plan_node(state) -> dict`：调用 `Planner.plan_with_llm` + `resolve_waves` 初始化 `pending_waves`
- [ ] 实现 `dispatch_node(state) -> list[Send]`：调用 `build_dispatch_sends`
- [ ] 实现 `execute_node(state) -> dict`：统一执行节点（替代 6 个分类节点）
  - 危险任务分类（调用 `DangerousTaskClassifier`）
  - upstream_findings 拼入 input
  - 调用 `acquire_and_run`（限流 + abort cancel）
  - 调用 `run_with_retry`（失败重试）
  - 用 `_make_subtask_state_update` 写入 findings（复合 key）
- [ ] 实现 `barrier_node(state) -> dict`：空 dict，路由由条件边决定
- [ ] 实现 `aggregate_node(state) -> dict`：调用 `Aggregator.run_aggregator` + 质量门
- [ ] 实现 `replan_node(state) -> dict`：质量门失败时调用 `Planner.replan`
- [ ] 实现 `_make_finding_key(agent, task, wave_index) -> str`：返回 `"{agent}:{task_id}:{wave_index}"`
- [ ] 实现 `_make_subtask_state_update(result, task, wave_index) -> dict`
- [ ] 实现 `_NODE_DISPATCH` 派发表（含 `_FALLBACK_CONFIG`）
- [ ] 更新 `__all__` 导出

## T6 [P0] 创建 `graph_builder.py`：StateGraph 构建

**Files**: `backend/app/team/graph_builder.py`（新建）
**Depends on**: T5
**Verified by**: `tests/python/unit/test_team_v2_dag.py::test_build_team_graph_topology`

- [ ] 实现 `_build_team_graph()`：StateGraph 构建并 `compile()`
- [ ] 注册节点：`plan` / `dispatch` / `execute` / `barrier` / `aggregate` / `replan`
- [ ] 边：`START -> plan -> dispatch`
- [ ] `dispatch` 用 `add_conditional_edges`（返回 `list[Send("execute", {...})]`）
- [ ] `execute -> barrier`
- [ ] `barrier` 条件边：`_route_after_barrier` -> `dispatch | aggregate`
- [ ] `aggregate` 条件边：`_route_after_aggregate` -> `replan | END`
- [ ] `replan` 条件边：`_route_after_replan` -> `plan | END`
- [ ] 实现 `_route_after_barrier(state) -> str`
- [ ] 实现 `_route_after_aggregate(state) -> str`（质量门通过 -> END；失败 -> replan）
- [ ] 实现 `_route_after_replan(state) -> str`（有新任务 -> plan；无 -> END）
- [ ] 更新 `__all__` 导出

## T7 [P0] 创建 `runner.py`：入口 + astream 事件消费

**Files**: `backend/app/team/runner.py`（新建）
**Depends on**: T6
**Verified by**: `tests/python/unit/test_team_v2.py::test_run_team_path_entry`

- [ ] 实现 `run_team_path(state, writer, ...) -> AsyncGenerator`：入口函数
- [ ] 调用 `_should_downgrade_to_single` 判断降级
- [ ] 调用 `_build_team_graph().astream(state)` 消费事件
- [ ] 把 LangGraph 内部事件转换为 SSE 事件发射（`team_init` / `delegation` / `todo_update` / `team_done` / `replan` / `warning`）
- [ ] abort handler 注册：`abort_event.add_done_callback(lambda: cancel_running_tasks(thread_id))`
- [ ] 迁移 [orchestrator.py:700-711](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L700-L711) 降级路径（不发 `team_done`，Phase 2 R6）
- [ ] 更新 `__all__` 导出

## T8 [P0] 创建 `scheduler.py`：Semaphore + retry + abort cancel

**Files**: `backend/app/team/scheduler.py`（重写）
**Depends on**: T1, T2
**Verified by**: `tests/python/unit/test_team_v2_concurrency.py::test_acquire_and_run`

- [ ] 实现 `_get_team_semaphore() -> asyncio.Semaphore`：全局单例（`team_max_concurrency`，默认 5）
- [ ] 实现 `acquire_and_run(task, runner_coro, thread_id, abort_event) -> TeamSubtaskResult`：限流 + abort cancel
- [ ] 实现 `run_with_retry(task, runner_factory, max_retries) -> TeamSubtaskResult`：失败重试 + 指数退避
- [ ] 定义 `TRANSIENT_ERRORS`（`TimeoutError` / `httpx.ConnectError` / 5xx）
- [ ] 实现 `_running_tasks` 全局 dict + `register_running_task` / `cancel_running_tasks`
- [ ] 迁移 `_run_team_role_subtask`：显式失败（不静默降级，D11）
- [ ] 删除旧 `_route_event_for_node` 静默吞异常（Phase 2 R2，改 logger.warning）
- [ ] 删除旧 `_inherit_workspace` 宽异常（Phase 2 R3，失败即返回失败结果）
- [ ] 更新 `__all__` 导出

## 阶段3 DAG 编排（T9-T14）

## T9 [P0] `resolve_waves` 单元测试

**Files**: `tests/python/unit/test_team_v2_dag.py`（新建）
**Depends on**: T4
**Verified by**: 测试通过

- [ ] `test_resolve_waves_linear`：线性链 t1->t2->t3
- [ ] `test_resolve_waves_diamond`：菱形 t1->t2,t1->t3,t2->t4,t3->t4
- [ ] `test_resolve_waves_all_parallel`：全无依赖
- [ ] `test_resolve_waves_cycle_break`：循环 t1->t2->t1 强制打破
- [ ] `test_resolve_waves_self_loop`：t1.depends_on=[t1] 自环丢弃
- [ ] `test_resolve_waves_out_of_bounds`：t1.depends_on=[t99] 越界丢弃
- [ ] `test_resolve_waves_single_task`：单任务

## T10 [P0] `dispatch_node` 分 wave 派发测试

**Files**: `tests/python/unit/test_team_v2_dag.py`
**Depends on**: T5, T9
**Verified by**: 测试通过

- [ ] `test_dispatch_node_multi_wave`：多 wave 派发，只派发第一 wave
- [ ] `test_dispatch_node_empty`：空 `pending_waves` 返回 `[Send("aggregate", {})]`
- [ ] `test_dispatch_node_inject_upstream`：依赖任务 input 含上游 findings

## T11 [P0] `barrier_node` 条件边路由测试

**Files**: `tests/python/unit/test_team_v2_dag.py`
**Depends on**: T6
**Verified by**: 测试通过

- [ ] `test_route_after_barrier_has_more`：有 wave 剩余 -> `dispatch`
- [ ] `test_route_after_barrier_done`：无 wave 剩余 -> `aggregate`

## T12 [P0] `_inject_upstream_findings` 注入测试

**Files**: `tests/python/unit/test_team_v2_dag.py`
**Depends on**: T4
**Verified by**: 测试通过

- [ ] `test_inject_upstream_single`：单依赖注入
- [ ] `test_inject_upstream_multi`：多依赖注入
- [ ] `test_inject_upstream_missing`：依赖未完成（占位 warning）
- [ ] `test_inject_upstream_truncate`：截断到 `team_result_max_chars`
- [ ] `test_inject_upstream_no_deps`：无依赖原样返回

## T13 [P0] StateGraph 拓扑完整性测试

**Files**: `tests/python/unit/test_team_v2_dag.py`
**Depends on**: T6
**Verified by**: 测试通过

- [ ] `test_build_team_graph_nodes`：包含 `plan` / `dispatch` / `execute` / `barrier` / `aggregate` / `replan`
- [ ] `test_build_team_graph_no_legacy_nodes`：不包含 `subtask_deep` / `subtask_code` / `subtask_builtin` / `subtask_team_role` / `subtask_custom` / `subtask_default`
- [ ] `test_route_after_aggregate_pass`：质量门通过 -> END
- [ ] `test_route_after_aggregate_fail`：质量门失败 -> replan
- [ ] `test_route_after_replan_has_tasks`：replan 有新任务 -> plan
- [ ] `test_route_after_replan_no_tasks`：replan 无新任务 -> END

## T14 [P0] `execute_node` upstream_findings 拼入 context 测试

**Files**: `tests/python/unit/test_team_v2_dag.py`
**Depends on**: T5
**Verified by**: 测试通过

- [ ] `test_execute_node_upstream_findings_compose`：upstream_findings 拼入 `[依赖任务结果]` 段
- [ ] `test_execute_node_no_upstream`：无依赖时 input 原样传给 runner
- [ ] `test_execute_node_finding_key_format`：findings key 为 `{agent}:{task_id}:{wave_index}`

## 阶段4 统一执行节点（T15-T18）

## T15 [P0] `execute_node` 替代 6 个分类节点

**Files**: `backend/app/team/nodes.py`
**Depends on**: T5
**Verified by**: `tests/python/unit/test_team_v2.py::test_execute_node_dispatch`

- [ ] `_NODE_DISPATCH` 派发表注册：`deep` / `code` / `rag` / `web` / `builtin`
- [ ] team_role 与 custom 在运行时根据 settings 动态注册
- [ ] `_FALLBACK_CONFIG.runner = _get_code_runner`（fallback 到 code，D7）
- [ ] `execute_node` 通过 `_NODE_DISPATCH.get(task.agent, _FALLBACK_CONFIG)` 解析 config
- [ ] 删除 [orchestrator.py:353-564](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L353-L564) 的 6 个旧节点函数

## T16 [P1] Semaphore 限流测试

**Files**: `tests/python/unit/test_team_v2_concurrency.py`（新建）
**Depends on**: T8
**Verified by**: 测试通过

- [ ] `test_semaphore_max_concurrency`：10 子任务 + `team_max_concurrency=5` 验证最多 5 并发
- [ ] `test_semaphore_release_on_complete`：子任务完成后释放 semaphore
- [ ] `test_semaphore_release_on_exception`：子任务异常时也释放 semaphore

## T17 [P1] asyncio.Task abort cancel 测试

**Files**: `tests/python/unit/test_team_v2_concurrency.py`
**Depends on**: T8
**Verified by**: 测试通过

- [ ] `test_abort_cancel_returns_aborted`：abort_event 触发后返回 `TeamSubtaskResult(success=False, payload="用户中止")`
- [ ] `test_abort_during_llm_call`：LLM 长调用期间 abort 验证 cancel 中断
- [ ] `test_abort_releases_semaphore`：abort 后 semaphore 释放
- [ ] `test_cancel_running_tasks_clears_registry`：`cancel_running_tasks` 清空 `_running_tasks[thread_id]`

## T18 [P2] retry 策略测试

**Files**: `tests/python/unit/test_team_v2_concurrency.py`
**Depends on**: T8
**Verified by**: 测试通过

- [ ] `test_retry_transient_timeout`：`TimeoutError` 重试 3 次
- [ ] `test_retry_network_error`：`httpx.ConnectError` 重试 3 次
- [ ] `test_retry_5xx`：5xx 重试 3 次
- [ ] `test_retry_no_retry_on_4xx`：4xx 不重试
- [ ] `test_retry_no_retry_on_value_error`：`ValueError` 不重试
- [ ] `test_retry_no_retry_on_cancelled`：`CancelledError` 不重试（直接 raise）
- [ ] `test_retry_backoff`：退避间隔 1s/2s/4s（mock sleep 验证调用次数）
- [ ] `test_retry_records_retries_field`：`result.retries` 字段记录实际重试次数

## 阶段5 危险分类 + fallback（T19-T21）

## T19 [P0] 创建 `classifier.py`：DangerousTaskClassifier

**Files**: `backend/app/team/classifier.py`（新建）
**Depends on**: T1
**Verified by**: `tests/python/unit/test_team_v2_classifier.py::test_classify_*`

- [ ] 实现 `DangerousTaskClassifier` 类
- [ ] `__init__`：尝试 `chat_model.with_structured_output(ClassificationResult)`，失败则降级
- [ ] `classify(task, available_tools) -> ClassificationResult`：LLM 路径
- [ ] `_keyword_fallback(task) -> ClassificationResult`：改进的关键词匹配
  - ASCII 关键词用 `\b` 词边界（`r"\bdelete\b"` / `r"\brm\s+-rf\b"`）
  - CJK 关键词要求前后非汉字字符（避免「修改」误命中「查看修改历史」）
- [ ] 缓存按 `task.description` hash（`hashlib.sha256`）
- [ ] 删除 [planner.py:272-276](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L272-L276) 旧 `_looks_like_dangerous_task`

## T20 [P0] 未知 agent fallback 测试

**Files**: `tests/python/unit/test_team_v2_fallback.py`（新建）
**Depends on**: T5, T15
**Verified by**: 测试通过

- [ ] `test_fallback_to_code_runner`：未知 agent 经 `_FALLBACK_CONFIG` 走 code runner
- [ ] `test_fallback_preserves_agent_name`：`TeamSubtaskResult.agent` 保留原始 agent 名
- [ ] `test_fallback_emits_warning_sse`：发射 `warning` SSE 事件 + 写入 `state.warnings`
- [ ] `test_fallback_no_hard_error`：不返回 `success=False` 硬错误

## T21 [P1] team_role 显式失败 + 启动校验

**Files**: `backend/app/team/scheduler.py`, `backend/app/config/subagents.py`, `backend/app/main.py`
**Depends on**: T8
**Verified by**: `tests/python/unit/test_team_v2_fallback.py::test_team_role_explicit_fail`

- [ ] `_run_team_role_subtask` 配置缺失时发射 `warning` 事件 + 写入 `state.warnings`（D11）
- [ ] 返回 `TeamSubtaskResult(success=False, payload="团队角色 {agent} 配置缺失 system_prompt")`
- [ ] 不再静默降级到 coding Expert
- [ ] 新增 `validate_team_subagents(settings) -> None`：遍历 `team_subagents`，`enabled=True` 必须有非空 `system_prompt`
- [ ] 在 `app.main` lifespan 启动阶段调用 `validate_team_subagents(get_settings())`
- [ ] `test_team_role_explicit_fail`：system_prompt 为空时显式失败
- [ ] `test_validate_team_subagents_startup_fail`：启动校验抛 `ValueError`
- [ ] `test_validate_team_subagents_normal`：正常配置不报错

## 阶段6 重试 + replan（T22-T24）

## T22 [P2] replan_node 实现

**Files**: `backend/app/team/nodes.py`, `backend/app/team/planner.py`
**Depends on**: T3, T5
**Verified by**: `tests/python/unit/test_team_v2_replan.py::test_replan_node_*`

- [ ] `Planner.replan(original_message, previous_plan, findings, errors, hint) -> TeamPlan`：replan prompt + 结构化输出
- [ ] `replan_node` 检查 `replan_count < max_replan_attempts`
- [ ] `replan_node` 调用 `Planner.replan` + `resolve_waves`
- [ ] `replan_node` 发射 `replan` SSE 事件（含 new_tasks + replan_count）
- [ ] `replan_count >= max` 时不调 LLM，直接结束（失败）
- [ ] `_route_after_replan`：有新任务 -> `plan`；无 -> END

## T23 [P2] replan 单元测试

**Files**: `tests/python/unit/test_team_v2_replan.py`（新建）
**Depends on**: T22
**Verified by**: 测试通过

- [ ] `test_replan_node_add_tasks`：质量门失败时 LLM 判定追加
- [ ] `test_replan_node_no_new_tasks`：LLM 判定无需追加
- [ ] `test_replan_node_limit_reached`：`replan_count >= max` 不调 LLM
- [ ] `test_replan_node_emits_sse`：发射 `replan` SSE 事件
- [ ] `test_route_after_replan_has_tasks`：有新任务 -> `plan`
- [ ] `test_route_after_replan_no_tasks`：无新任务 -> END

## T24 [P2] `max_replan_attempts` 配置

**Files**: `backend/app/config/settings.py`
**Depends on**: 无
**Verified by**: `tests/python/unit/test_team_v2_replan.py::test_max_replan_attempts_config`

- [ ] `Settings` 类新增 `team_max_replan_attempts: int = Field(default=1, ge=0, le=5)`
- [ ] `test_max_replan_attempts_default`：默认 1
- [ ] `test_max_replan_attempts_range`：范围校验 0-5

## 阶段7 一致性（T25-T27）

## T25 [P2] Token 事件 schema 统一

**Files**: `backend/app/team/nodes.py`, `backend/app/team/aggregator.py`
**Depends on**: T5
**Verified by**: `tests/python/unit/test_team_v2.py::test_token_event_schema`

- [ ] 所有 `execute` 节点 token 事件 schema 统一为 `{agent: str, content: str}`
- [ ] aggregator token 事件 schema 为 `{agent: "aggregator", content: str}`
- [ ] 删除各分类节点独立的 token 字段（如 `agent_type` / `node_name`）
- [ ] `test_token_event_schema`：验证所有节点发射的 token 事件字段一致

## T26 [P2] Findings key 复合格式

**Files**: `backend/app/team/nodes.py`, `backend/app/team/blackboard.py`
**Depends on**: T5
**Verified by**: `tests/python/unit/test_team_v2_replan.py::test_findings_key_format`

- [ ] `_make_finding_key(agent, task, wave_index)` 返回 `"{agent}:{task_id}:{wave_index}"`
- [ ] `_make_subtask_state_update` 使用复合 key 写入 `findings`
- [ ] `_merge_findings` reducer 用复合 key 合并（不覆盖）
- [ ] `test_findings_key_format`：验证 key 为 `code:t1:0` 格式
- [ ] `test_findings_no_collision`：同类型多任务不互相覆盖

## T27 [P1] Warnings channel

**Files**: `backend/app/team/state.py`, `backend/app/team/blackboard.py`, `backend/app/team/nodes.py`
**Depends on**: T2, T5
**Verified by**: `tests/python/unit/test_team_v2.py::test_warnings_channel`

- [ ] `TeamState.warnings: list[str]` 字段（T1 已定义，此处验证使用）
- [ ] `_merge_warnings` reducer（T2 已实现）
- [ ] `_fallback_node` 写入 `state.warnings`（T20 已实现）
- [ ] team_role 缺配置写入 `state.warnings`（T21 已实现）
- [ ] 危险任务改写写入 `state.warnings`（T19 已实现）
- [ ] `aggregate_node` 把 `state.warnings` 一并发射 SSE
- [ ] `test_warnings_channel`：验证 warning 写入 + 合并 + SSE 发射

## 阶段8 配置 + 启动校验（T28-T29）

## T28 [P1] `team_max_concurrency` 配置

**Files**: `backend/app/config/settings.py`
**Depends on**: 无
**Verified by**: `tests/python/unit/test_team_v2_concurrency.py::test_max_concurrency_config`

- [ ] `Settings` 类新增 `team_max_concurrency: int = Field(default=5, ge=1, le=20)`
- [ ] `test_max_concurrency_default`：默认 5
- [ ] `test_max_concurrency_range`：范围校验 1-20

## T29 [P1] `validate_team_subagents` 启动校验

**Files**: `backend/app/config/subagents.py`, `backend/app/main.py`
**Depends on**: T21
**Verified by**: `tests/python/unit/test_team_v2_fallback.py::test_validate_team_subagents`

- [ ] 实现 `validate_team_subagents(settings) -> None`
- [ ] 遍历 `settings.team_subagents`，`enabled=True` 必须有非空 `system_prompt`
- [ ] 否则抛 `ValueError` 阻止启动
- [ ] 在 `app.main` lifespan 启动阶段调用
- [ ] `test_validate_team_subagents_startup_fail`：缺失时抛 `ValueError`
- [ ] `test_validate_team_subagents_normal`：正常配置不报错

## 阶段9 测试（T30-T35）

## T30 [P0] classifier 单元测试

**Files**: `tests/python/unit/test_team_v2_classifier.py`（新建）
**Depends on**: T19
**Verified by**: 测试通过

- [ ] `test_classify_llm_path`：LLM 路径返回 `ClassificationResult`
- [ ] `test_classify_keyword_fallback`：LLM 失败时降级到关键词
- [ ] `test_classify_word_boundary_ascii`：`r"\bdelete\b"` 不误命中「read the delete log」
- [ ] `test_classify_word_boundary_cjk`：「修改」不误命中「查看修改历史」
- [ ] `test_classify_cache_hit`：相同 description 第二次命中缓存
- [ ] `test_classify_suggests_agent`：危险任务 `suggested_agent="deep"`
- [ ] `test_classify_non_dangerous`：非危险任务 `is_dangerous=False`

## T31 [P0] fallback 单元测试

**Files**: `tests/python/unit/test_team_v2_fallback.py`
**Depends on**: T20, T21
**Verified by**: 测试通过

- [ ] `test_fallback_to_code_runner`：未知 agent fallback 到 code
- [ ] `test_fallback_preserves_agent_name`：保留原始 agent 名
- [ ] `test_fallback_emits_warning_sse`：发射 warning SSE
- [ ] `test_fallback_writes_state_warnings`：写入 `state.warnings`
- [ ] `test_team_role_explicit_fail`：system_prompt 为空显式失败
- [ ] `test_team_role_emits_warning`：team_role 缺配置发射 warning
- [ ] `test_validate_team_subagents_startup_fail`：启动校验抛 `ValueError`
- [ ] `test_validate_team_subagents_normal`：正常配置不报错

## T32 [P0] 集成测试 - 端到端 DAG 场景

**Files**: `tests/python/integration/test_team_v2_e2e.py`（新建，可选）
**Depends on**: T13, T15
**Verified by**: 测试通过

- [ ] `test_e2e_sequential_tasks`：`[code] t1 读取 → [deep][after:t1] 修改` 验证 deep 拿到 code findings
- [ ] `test_e2e_parallel_tasks`：无 `depends_on` 的任务仍并行
- [ ] `test_e2e_mixed_tasks`：混合并行 + 依赖
- [ ] `test_e2e_replan`：第一轮后 replan 追加任务
- [ ] `test_e2e_fallback`：未知 agent 走 code
- [ ] `test_e2e_explicit_fail`：team_role 缺 system_prompt 显式失败
- [ ] `test_e2e_abort`：子任务执行中 abort 验证返回 "用户中止"
- [ ] `test_e2e_retry`：mock runner 抛 `TimeoutError` 验证重试

## T33 [P0] ruff check 静态检查

**Files**: 无
**Depends on**: 所有前置任务
**Verified by**: `uv run ruff check backend/app/team/`

- [ ] `uv run ruff check backend/app/team/` 通过
- [ ] `uv run ruff check backend/app/config/` 通过
- [ ] 无未使用 import / 未定义变量 / 行过长

## T34 [P0] 全量单元测试回归

**Files**: 无
**Depends on**: 所有前置任务
**Verified by**: `uv run pytest tests/python/unit -m "not integration" -k "team"`

- [ ] `uv run pytest tests/python/unit -m "not integration" -k "team"` 全部通过
- [ ] `uv run pytest tests/python/unit -m "not integration"` 全量不回归（预存失败除外）

## T35 [P0] 删除旧 `orchestrator.py`

**Files**: `backend/app/team/orchestrator.py`（删除）
**Depends on**: T7（runner.py 已接管入口）
**Verified by**: `uv run ruff check backend/app/team/` + 全量测试通过

- [ ] 确认 `runner.run_team_path` 已替代 `orchestrator.run_team_path`
- [ ] 确认所有 import 已迁移到新模块
- [ ] 删除 [orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py)（约 780 行）
- [ ] 更新 `backend/app/team/__init__.py` 导出 `run_team_path`（从 runner.py）
- [ ] 全局搜索 `from app.team.orchestrator` 确认无残留 import
- [ ] `uv run ruff check backend/app/team/` 通过
- [ ] `uv run pytest tests/python/unit -m "not integration" -k "team"` 通过

## 阶段10 OpenSpec validate（T36）

## T36 [P0] OpenSpec validate

**Files**: 无
**Depends on**: 所有前置任务
**Verified by**: `openspec validate agent-team-v2-refactor`

- [ ] `openspec validate agent-team-v2-refactor` 通过
- [ ] `openspec status agent-team-v2-refactor` 显示所有 artifact 就绪
- [ ] 确认 `.openspec.yaml` 的 `supersedes` 字段正确引用两个 prior change
