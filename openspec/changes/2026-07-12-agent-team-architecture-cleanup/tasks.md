# Tasks: AgentTeam 架构清理（Phase 2 — 清理技术债）

## 1. 准备与影响分析

### T1: GitNexus 影响分析与索引校验

- [ ] T1.1 确认 GitNexus 索引新鲜（`gitnexus://repo/agentx/context`），过期则 `npx gitnexus analyze`
- [ ] T1.2 对 `_merge_todos` 跑 `gitnexus_impact({target: "_merge_todos", direction: "upstream"})`，记录 blast radius
- [ ] T1.3 对 `_run_subtask_node`（新）/ `_deep_node` 等 6 节点跑 impact analysis，确认 HIGH/CRITICAL 风险并报告
- [ ] T1.4 对 `_run_aggregator` / `_quality_gate` / `_looks_like_dangerous_task` 跑 impact analysis
- [ ] T1.5 确认 LangChain 版本（`pyproject.toml`）以决定 `astream_events` v3 可用性 + `InterruptException` 类型名

## 2. 节点统一与全局删除（A1/A2/R7）

### T2: `_run_subtask_node` + 派发表（A1/R7）

- [ ] T2.1 [orchestrator.py:353-564](../../../backend/app/team/orchestrator.py#L353-L564) 删除 `_deep_node`/`_code_node`/`_builtin_node`/`_team_role_node`/`_custom_node`/`_default_node` 共 6 函数
- [ ] T2.2 新增 `SubtaskConfig` dataclass（agent_kind / inherit_workspace / runner_factory）
- [ ] T2.3 新增 `_NODE_DISPATCH` 派发表 + `_DEFAULT_CONFIG`（emit delegation 后返回失败）
- [ ] T2.4 新增 `_run_subtask_node(state)` 实现：abort → delegation → todo in_progress → inherit_ws（按 config）→ runner → update → CancelledError
- [ ] T2.4a 验证 Phase 1 的 subtask_timeout 和 semaphore 在统一节点中仍生效
    - [ ] T2.4a.1 单元测试：超时子任务在 `_run_subtask_node` 中仍触发 `TeamSubtaskResult(success=False, payload="子任务超时")`
    - [ ] T2.4a.2 单元测试：max_parallel=2 时第 3 个子任务阻塞至前 2 个完成
- [ ] T2.5 新增 `_build_runner_args(config, state, task, child_id, parent_thread_id)` 工厂，集中 5 类 runner_args 差异
- [ ] T2.6 `_dispatch_node` 改为 `Send("subtask", {"task":..., "config": _NODE_DISPATCH.get(t.agent, _DEFAULT_CONFIG), "task_index": idx})` 单一目标
- [ ] T2.7 StateGraph `add_node("subtask", _run_subtask_node)` 注册单一节点，移除 6 个旧节点注册
- [ ] T2.8 写单测：每个 agent 类型验证 `_build_runner_args` 等价于原函数构造的 args

### T3: 删除 `_in_progress_tasks` 全局 + reducer 粘性（A2）

- [ ] T3.1 [blackboard.py:80-107](../../../backend/app/team/blackboard.py#L80-L107) `_merge_todos` 实现粘性 in_progress：任一侧 in_progress 且新状态非 completed/error → 保持 in_progress
- [ ] T3.2 [orchestrator.py:91](../../../backend/app/team/orchestrator.py#L91) 删除 `_in_progress_tasks` 全局定义
- [ ] T3.3 删除 `_emit_todo_in_progress` 中写 `_in_progress_tasks` 的副作用，仅保留 emit SSE
- [ ] T3.4 [orchestrator.py:759-770](../../../backend/app/team/orchestrator.py#L759-L770) 删除 values-mode in_progress overlay 逻辑，直接 `yield make_todo_update_event(current_todos, ...)`
- [ ] T3.5 删除 `_aggregate_node` L572 与 `run_team_path` L723 的 `_in_progress_tasks.pop`
- [ ] T3.6 写单测：`_merge_todos` 覆盖「in_progress→completed」「in_progress→pending(保持)」「completed→pending(覆盖)」三场景

## 3. 死字段与孤儿 checkpoint 清理（A4/A5）

### T4: 删除死字段与 `Blackboard` 内联（A4）

- [ ] T4.1 [blackboard.py:135](../../../backend/app/team/blackboard.py#L135) 删除 `TeamState.subtask_results` 字段
- [ ] T4.2 [orchestrator.py:265,276](../../../backend/app/team/orchestrator.py#L265) `_make_subtask_state_update` 删除 `subtask_results` 写入
- [ ] T4.3 [orchestrator.py:739](../../../backend/app/team/orchestrator.py#L739) `initial_state` 删除 `subtask_results: {}`
- [ ] T4.4 [router/state.py:27](../../../backend/app/router/state.py#L27) 删除 `RouterState.team_blackboard` 字段
- [ ] T4.5 [blackboard.py:55-66](../../../backend/app/team/blackboard.py#L55) 删除 `Blackboard` dataclass（含 `meta`）
- [ ] T4.6 [aggregator.py](../../../backend/app/team/aggregator.py) `_run_aggregator` 改为直接接收 `findings`/`errors` dict；`_aggregate_node` 构造 dict 入参
- [ ] T4.7 grep `_serialize_blackboard` 调用点，改为接收 dict 或删除；若保留则改为薄 helper
- [ ] T4.8 [planner.py](../../../backend/app/team/planner.py) 消除 `clean_todos`/`tasks` 双列表，新增 `tasks_to_display_todos(tasks)` 纯函数
- [ ] T4.9 写单测：`tasks_to_display_todos` 输出格式 + `_run_aggregator` dict 入参等价

### T5: /reset 清理孤儿 checkpoint + UUID 化（A5）

- [ ] T5.1 child_thread_id 模板从 `{parent}-team-{agent}-{idx}` 改为 `{parent}-team-{uuid4()}`
- [ ] T5.2 [chat.py reset](../../../backend/app/api/chat.py) 端点新增 `_enumerate_child_thread_ids(parent_thread_id, checkpointer)`，优先 `alist` 过滤前缀，fallback SQL `LIKE`
- [ ] T5.3 /reset 流程在删除父 thread 后循环删除所有 `{thread_id}-team-*` child checkpoint
- [ ] T5.4 写单测：`_enumerate_child_thread_ids` 前缀过滤 + /reset 后无 child 残留
- [ ] T5.5 验证 retry 场景 child_id 不碰撞（UUID 唯一性）

## 4. 异常处理收窄（R1/R2/R3/R4）

### T6: `approval_runner.py` 异常收窄（R1）

- [ ] T6.1 确认 LangGraph 中断专用异常类型名（grep site-packages，1.x 多为 `langgraph.errors` 下）
- [ ] T6.2 [approval_runner.py:283,321,327,437,474,550,587,603,640](../../../backend/app/deepagent/approval_runner.py#L283) 9 处 `except Exception: pass` → `except <中断异常>: pass`
- [ ] T6.3 每处补顶层 `except Exception as exc: logger.warning(f"unexpected resume exception: {exc}")`
- [ ] T6.4 写单测：mock 非中断异常验证不再静默（logger.warning 被调用）

### T7: scheduler 静默异常收窄（R2/R3）

- [ ] T7.1 [scheduler.py:145-146,179-180](../../../backend/app/team/scheduler.py#L145) `_route_event_for_node` 2 处 `except Exception: pass` → `except (json.JSONDecodeError, KeyError) as exc: logger.warning(f"dropped malformed event: {event_type}: {exc}")`
- [ ] T7.2 [scheduler.py:104-112](../../../backend/app/team/scheduler.py#L104) `_inherit_workspace` `except Exception` → `except (SandboxError, ValueError)`
- [ ] T7.3 `_inherit_workspace` 授权失败返回 `TeamSubtaskResult(success=False, payload=f"workspace authorization failed: {exc}")`
- [ ] T7.4 [orchestrator.py](../../../backend/app/team/orchestrator.py) `_run_subtask_node` 处理 `_inherit_workspace` 返回的失败结果（跳过 runner 直接返回）
- [ ] T7.5 写单测：畸形 JSON 事件被丢弃且 logger.warning 被调用；授权失败返回失败结果

### T8: abort 可中断 LLM 长调用（R4）

- [ ] T8.1 [scheduler.py _run_subtask_stream](../../../backend/app/team/scheduler.py) 将 `async for event in agent.astream_events(...)` 改为 `asyncio.wait({runner.__anext__(), abort_event.wait()}, FIRST_COMPLETED, timeout=5)` 竞速
- [ ] T8.2 abort 触发时 cancel runner task 并返回 `_done(False, "用户中止")`
- [ ] T8.3 wait 超时且 abort 未触发则继续下一轮（runner 仍在跑）
- [ ] T8.4 写单测：mock runner 不产出事件 + abort set，验证 5s 内返回中止

## 5. 质量门、降级路径与关键词模式（R5/R6/D3）

### T9: 恢复 identical-findings 质量门（R5）

- [ ] T9.1 [aggregator.py:78](../../../backend/app/team/aggregator.py#L78) `_quality_gate` 恢复全等检查：所有 `findings.values()` 相等（strip 后）→ reject
- [ ] T9.2 reject reason 设为 `all_findings_identical`，`logger.info` 记录
- [ ] T9.3 写单测：4 个相同非截断 findings → reject；1 个不同 → 通过

### T10: 降级路径不发 `team_done`（R6）

- [ ] T10.1 [orchestrator.py:700-711](../../../backend/app/team/orchestrator.py#L700) 降级路径删除 `yield make_sse_event("team_done", {"status": "done"})`
- [ ] T10.2 降级路径仅发 `token`，由 chat.py 收尾发 `done`（无 team_init 则无 team_done）
- [ ] T10.3 grep 前端 `useChatStream.ts` 确认 `team_done` 处理 `createIfMissing:false`，降级路径无残留 TeamNodeCard
- [ ] T10.4 写单测：降级路径不产出 `team_done` 事件

### T11: `_looks_like_dangerous_task` 词边界 + 共享模式（D3）

- [ ] T11.1 新建/编辑 [utils/text.py](../../../backend/app/utils/text.py) `compile_keyword_patterns(keywords)` + `matches_any(text, patterns)`
- [ ] T11.2 [planner.py:272-276](../../../backend/app/team/planner.py#L272) `_looks_like_dangerous_task` 改用 `compile_keyword_patterns(_DANGEROUS_KEYWORDS)`
- [ ] T11.3 [aggregator.py:185-189](../../../backend/app/team/aggregator.py#L185) `_KEYWORD_PATTERNS` 改用同一 helper
- [ ] T11.4 写单测：`"read the writeup"` 不命中 `"write"`；`"解释修改符"` 命中 `"修改"`（子串匹配，预期 True）；`"解释 const 修饰符"` 不命中（无 `"修改"` 子串）；`"write file"` 命中 `"write"`

## 6. `astream_events` 评估与全量验证（D2 + 回归）

### T12: `astream_events` v2/v3 评估（D2）

- [ ] T12.1 查 `pyproject.toml` LangChain 版本，确认 v3 是否可用
- [ ] T12.2 v3 可用则 [scheduler.py:311](../../../backend/app/team/scheduler.py#L311) `version="v2"` → `version="v3"`，跑回归
- [ ] T12.3 v3 不可用则保留 v2，在本文件 design.md D2 决策处文档化迁移路径

### T13: 全量验证

- [ ] T13.1 `uv run ruff check backend/` 通过
- [ ] T13.2 `uv run pytest tests/python/unit -m "not integration" -q` 全部通过
- [ ] T13.3 GitNexus `gitnexus_detect_changes()` 确认变更范围仅预期符号
- [ ] T13.4 端到端冒烟：`pnpm tauri dev` → /reset → team 任务（多 agent）→ 中止 → retry → /reset 验证清理
- [ ] T13.5 验证 `data/agentx.db` 中孤儿 checkpoint 在 /reset 后被清理（SQL count）
- [ ] T13.6 验证降级路径（短消息/问候）不发 `team_done`，前端无残留 TeamNodeCard
