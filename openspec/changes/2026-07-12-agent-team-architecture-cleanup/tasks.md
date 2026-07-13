# 任务追踪 — 2026-07-12-agent-team-architecture-cleanup (Phase 2)

## 预期修改文件

- [x] `backend/app/team/orchestrator.py` — 删除 6 节点函数 + `_in_progress_tasks` 全局；新增 `_run_subtask_node` + `_NODE_DISPATCH` + `SubtaskConfig`；改 `_dispatch_node`/`_aggregate_node`/`run_team_path`/`_make_subtask_state_update`/`initial_state`
- [x] `backend/app/team/blackboard.py` — `_merge_todos` 粘性 in_progress；删 `TeamState.subtask_results` + `Blackboard` dataclass
- [x] `backend/app/team/scheduler.py` — `_route_event_for_node` 窄异常；`_inherit_workspace` 窄异常+失败结果；`_run_subtask_stream` abort 竞速
- [x] `backend/app/team/aggregator.py` — `_run_aggregator` 内联 Blackboard；`_quality_gate` 恢复 identical 检查；`_KEYWORD_PATTERNS` 共享 helper
- [x] `backend/app/team/planner.py` — `_looks_like_dangerous_task` 词边界；消除 `clean_todos`/`tasks` 双列表；新增 `tasks_to_display_todos`
- [x] `backend/app/api/chat.py` — /reset 端点新增 `_enumerate_child_thread_ids` + 清理孤儿 child checkpoint
- [x] `backend/app/router/state.py` — 删 `RouterState.team_blackboard`
- [x] `backend/app/deepagent/approval_runner.py` — 9 处 `except Exception: pass` 窄化
- [x] `backend/app/utils/text.py` — NEW: `compile_keyword_patterns` + `matches_any`

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | GitNexus 影响分析与索引校验 | (分析 only) | blast radius 已记录 | ✅ |
| T2 | `_run_subtask_node` + 派发表（A1/R7） | orchestrator.py | 6 旧节点删除 + 单一节点 + 5 类 runner_args 工厂 | ✅ |
| T3 | 删除 `_in_progress_tasks` 全局 + reducer 粘性（A2） | blackboard.py, orchestrator.py | 全局删除 + 粘性 reducer + overlay 删除 | ✅ |
| T4 | 删除死字段与 `Blackboard` 内联（A4） | blackboard.py, orchestrator.py, aggregator.py, router/state.py, planner.py | 死字段全删 + dict 入参 + 派生函数 | ✅ |
| T5 | /reset 清理孤儿 checkpoint + UUID 化（A5） | orchestrator.py, api/chat.py | UUID child_id + /reset 清理 + 不撞 stale | ✅ |
| T6 | `approval_runner.py` 异常收窄（R1） | approval_runner.py | 9 处窄化 + 顶层 logger.warning | ✅ |
| T7 | scheduler 静默异常收窄（R2/R3） | scheduler.py, orchestrator.py | JSON/KeyError 窄化 + PathNotAuthorized/ValueError 窄化 + 失败结果 | ✅ |
| T8 | abort 可中断 LLM 长调用（R4） | scheduler.py | asyncio.wait 竞速 + 5s 内响应 | ✅ |
| T9 | 恢复 identical-findings 质量门（R5） | aggregator.py | 全等检查恢复 + reject reason | ✅ |
| T10 | 降级路径不发 `team_done`（R6） | orchestrator.py | 降级仅发 token + chat.py 收尾 | ✅ |
| T11 | `_looks_like_dangerous_task` 词边界 + 共享模式（D3） | utils/text.py, planner.py, aggregator.py | helper 新建 + 两处共用 + 词边界 | ✅ |
| T12 | `astream_events` v2/v3 评估（D2） | scheduler.py | 版本确认 + 文档化（v3 可用但 schema 未验证，保持 v2） | ✅ |
| T13 | 全量验证 | (验证 only) | ruff + pytest + 冒烟 全通过 | ✅ |

## 规模判定

- 涉及文件数: 9 → 规模: **L（大改）**
- 涉及模块数: 5（team / api / router / deepagent / utils）
- 流程: 全流程（Worktree + TDD + 双轨 Review + 部署 + 归档）
