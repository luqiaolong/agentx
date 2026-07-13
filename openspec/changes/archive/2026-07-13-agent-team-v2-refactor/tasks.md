# 任务追踪 — agent-team-v2-refactor

> 本文件是 ralph Phase 0 输出物，后续所有 Phase 的唯一权威输入。
> 详细任务定义见 [tasks.md](tasks.md)。

## 预期修改文件

### 新建文件（9 个）
- [ ] `backend/app/team/state.py` — TypedDict + Pydantic schema（T1）
- [ ] `backend/app/team/dispatcher.py` — Kahn wave 解析 + Send 生成（T4）
- [ ] `backend/app/team/nodes.py` — 统一节点函数（T5）
- [ ] `backend/app/team/graph_builder.py` — StateGraph 构建（T6）
- [ ] `backend/app/team/runner.py` — 入口 + astream 事件消费（T7）
- [ ] `backend/app/team/classifier.py` — DangerousTaskClassifier（T19）
- [ ] `tests/python/unit/test_team_v2.py` — schema/reducer/nodes 测试（T1-T5）
- [ ] `tests/python/unit/test_team_v2_dag.py` — DAG 拓扑测试（T9-T14）
- [ ] `tests/python/unit/test_team_v2_concurrency.py` — 限流/abort/retry 测试（T16-T18）
- [ ] `tests/python/unit/test_team_v2_classifier.py` — 分类器测试（T30）
- [ ] `tests/python/unit/test_team_v2_replan.py` — replan 测试（T23）
- [ ] `tests/python/integration/test_team_v2_e2e.py` — 端到端集成测试（T32，可选）

### 重写文件（3 个）
- [ ] `backend/app/team/blackboard.py` — Reducers 集中 + warnings channel（T2/T37）
- [ ] `backend/app/team/planner.py` — with_structured_output(TeamPlan)（T3）
- [ ] `backend/app/team/scheduler.py` — Semaphore + retry + abort cancel（T8）

### 修改文件（5 个）
- [ ] `backend/app/config/settings.py` — 配置项重命名（T38）
- [ ] `backend/app/team/aggregator.py` — token schema 统一（T25）
- [ ] `backend/app/sse/events.py` — token 事件 payload 结构化（T25）
- [ ] `backend/app/config/subagents.py` — 命名对齐（T38）
- [ ] `backend/app/main.py` — 命名对齐验证（T38）

### 删除文件（1 个）
- [ ] `backend/app/team/orchestrator.py` — 约 1250 行，功能迁移到新模块后删除（T35）

## OpenSpec Tasks

| ID | 优先级 | 状态 | 任务描述 | 涉及文件 | 验收标准 |
|----|--------|------|---------|---------|---------|
| T1 | P0 | ⬜ | 创建 state.py：TypedDict + Pydantic schema | `state.py` | `test_team_task_schema` 通过 |
| T2 | P0 | ⬜ | 创建 blackboard.py：Reducers 集中 | `blackboard.py` | `test_reducers_merge` 通过 |
| T3 | P0 | ⬜ | 创建 planner.py：with_structured_output | `planner.py` | `test_plan_with_structured_output` 通过 |
| T4 | P0 | ⬜ | 创建 dispatcher.py：Kahn wave + Send | `dispatcher.py` | `test_resolve_waves_*` 通过 |
| T5 | P0 | ⬜ | 创建 nodes.py：统一节点函数 | `nodes.py` | `test_nodes_signatures` 通过 |
| T6 | P0 | ⬜ | 创建 graph_builder.py：StateGraph | `graph_builder.py` | `test_build_team_graph_topology` 通过 |
| T7 | P0 | ⬜ | 创建 runner.py：入口 + astream | `runner.py` | `test_run_team_path_entry` 通过 |
| T8 | PARTIAL | ⬜ | 重写 scheduler.py：Semaphore+retry+abort | `scheduler.py` | `test_acquire_and_run` 通过 |
| T9 | DONE | ✅ | resolve_waves 单元测试（命名对齐） | `test_team_dag.py` | 测试通过 |
| T10 | DONE | ✅ | dispatch_node 派发测试（命名对齐） | `test_team_dag.py` | 测试通过 |
| T11 | DONE | ✅ | barrier_node 路由测试（D16 对齐） | `test_team_dag.py` | 测试通过 |
| T12 | PARTIAL | ⬜ | _inject_upstream_findings 注入测试 | `test_team_v2_dag.py` | 5 场景通过 |
| T13 | PARTIAL | ⬜ | StateGraph 拓扑完整性测试 | `test_team_v2_dag.py` | 6 场景通过 |
| T14 | PARTIAL | ⬜ | execute_node upstream 测试 | `test_team_v2_dag.py` | 3 场景通过 |
| T15 | PARTIAL | ⬜ | execute_node 替代 6 分类节点 | `nodes.py` | `test_execute_node_dispatch` 通过 |
| T16 | PARTIAL | ⬜ | Semaphore 限流测试 | `test_team_v2_concurrency.py` | 4 场景通过 |
| T17 | PARTIAL | ⬜ | asyncio.Task abort cancel 测试 | `test_team_v2_concurrency.py` | 4 场景通过 |
| T18 | P2 | ⬜ | retry 策略测试 | `test_team_v2_concurrency.py` | 8 场景通过 |
| T19 | PARTIAL | ⬜ | 创建 classifier.py：DangerousTaskClassifier | `classifier.py` | `test_classify_*` 通过 |
| T20 | DONE | ✅ | 未知 agent fallback 测试（补 SSE） | `test_team_fallback.py` | 4 场景通过 |
| T21 | DONE | ✅ | team_role 显式失败+启动校验（补 SSE） | `scheduler.py` | 7 场景通过 |
| T22 | PARTIAL | ⬜ | replan_node 实现（依赖 T39） | `nodes.py`, `planner.py` | `test_replan_node_*` 通过 |
| T23 | P2 | ⬜ | replan 单元测试 | `test_team_v2_replan.py` | 8 场景通过 |
| T24 | PARTIAL | ⬜ | team_max_replan_attempts 配置（依赖 T38） | `settings.py` | 2 场景通过 |
| T25 | P2 | ⬜ | Token 事件 schema 统一 | `nodes.py`, `aggregator.py`, `events.py` | `test_token_event_schema` 通过 |
| T26 | P2 | ⬜ | Findings key 复合格式 | `nodes.py`, `blackboard.py` | `test_findings_key_format` 通过 |
| T27 | PARTIAL | ⬜ | Warnings channel 集成层（依赖 T37） | `state.py`, `nodes.py` | `test_warnings_channel` 通过 |
| T28 | PARTIAL | ⬜ | team_max_concurrency 配置（依赖 T38） | `settings.py` | 2 场景通过 |
| T29 | DONE | ✅ | validate_team_subagents 启动校验 | `subagents.py`, `main.py` | 命名对齐验证 |
| T30 | PARTIAL | ⬜ | classifier 单元测试 | `test_team_v2_classifier.py` | 7 场景通过 |
| T31 | DONE | ✅ | fallback 单元测试（补 SSE 断言） | `test_team_fallback.py` | 8 场景通过 |
| T32 | P0 | ⬜ | 集成测试 - 端到端 DAG 场景 | `test_team_v2_e2e.py` | 8 场景通过 |
| T33 | P0 | ⬜ | ruff check 静态检查 | 无 | ruff 通过 |
| T34 | P0 | ⬜ | 全量单元测试回归 | 无 | pytest 通过 |
| T35 | P0 | ⬜ | 删除旧 orchestrator.py | `orchestrator.py` | 删除+测试通过 |
| T36 | P0 | ⬜ | OpenSpec validate | 无 | openspec validate 通过 |
| T37 | P0 | ⬜ | state.warnings channel 基础设施（D14） | `state.py`, `blackboard.py` | `test_warnings_channel_infra` 通过 |
| T38 | P1 | ⬜ | 配置项重命名（D15） | `settings.py` 等 | `test_team_config_rename` 通过 |
| T39 | P0 | ⬜ | D10 Replanner 路由修复（D16） | `graph_builder.py`, `nodes.py` | `test_route_after_aggregate_*` 通过 |

## 规模判定

- 涉及文件数：18（9 新建 + 3 重写 + 5 修改 + 1 删除）→ **L 级**
- 涉及模块数：3（team / config / sse）→ **L 级**
- 任务总数：39（含 7 个 DONE 状态，32 个待实现）
- 用户指定：**L 级全流程**

## 执行优先级（关键路径）

1. **P0 基础链路**：T1 → T2 → T37 → T4 → T5 → T39 → T22 → T6 → T7 → T35
2. **P1 配置链路**：T38 → T28 / T24 / T29
3. **P1 分类器**：T19 → T30
4. **P2 一致性**：T25 / T26 / T27
5. **P2 测试**：T16 / T17 / T18 / T23 / T32
6. **收尾**：T33 → T34 → T36
