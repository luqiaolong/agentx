# Proposal: AgentTeam 架构清理（Phase 2 — 清理技术债）

## Why

Phase 1（Stability Hardening）已落地 `subtask_timeout` / `max_parallel` / `team_done`
终态约束 / `tryRecoverResult` 合成 / `INACTIVITY_TIMEOUT` 180s / 错误路径 `team_done` 去重。
稳定性层面已收敛，但 **Team 路径的代码结构本身仍背负 5 类历史技术债**，每次行为修复
都要改 5 处、删字段要查 3 个文件、异常被静默吞掉导致排查链路断裂。这些债不清理，
Phase 3（UX Enhancement）的新事件 / 新 UI 会再次堆在脆弱地基上。

本次（Phase 2）只做 **架构清理**，不新增 SSE 事件、不动前端渲染、不引入灰度开关。

### 现状痛点（深度分析结论）

| 编号 | 痛点 | 位置 | 后果 |
|---|---|---|---|
| A1 | 5 个高度相似的子任务节点函数 + `_default_node`（行为不一致） | [orchestrator.py:353-564](../../../backend/app/team/orchestrator.py#L353-L564) `_deep_node`/`_code_node`/`_builtin_node`/`_team_role_node`/`_custom_node` ~90% 相同 | 行为修复要改 5 处，漂移风险高；`_default_node`（L556-564）还不发 `delegation` |
| A2 | `_in_progress_tasks` 模块级可变 dict | [orchestrator.py:91](../../../backend/app/team/orchestrator.py#L91) 写于 L342/572/723，读于 L763 | LangGraph reducer 无法表达 transient 状态的 workaround；崩溃时泄漏残留 |
| A4 | 死字段 + 双重表示 | [blackboard.py:128](../../../backend/app/team/blackboard.py#L128) `subtask_results` 写而永不读；[router/state.py:27](../../../backend/app/router/state.py#L27) `team_blackboard` 永不填充；[blackboard.py:66](../../../backend/app/team/blackboard.py#L66) `Blackboard.meta` 永不填充；`Blackboard` dataclass 仅作 aggregator 入参 | 死代码遮蔽真实数据流 |
| A5 | 孤儿 child_thread_id checkpoint 累积 | [orchestrator.py:365](../../../backend/app/team/orchestrator.py#L365) `{parent}-team-deep-{idx}`；[/reset chat.py:48](../../../backend/app/api/chat.py#L48) 只清父 thread | `data/agentx.db` 无限膨胀；retry 撞 stale 状态 |
| R1 | `approval_runner.py` 9+ 处 `except Exception: pass` | [approval_runner.py:283,321,327,437,474,550,587,603,640](../../../backend/app/deepagent/approval_runner.py#L283) | resume 期间非中断异常被静默吞掉，循环空转 |
| R2 | `_route_event_for_node` JSON 解析静默失败 | [scheduler.py:145-146,179-180](../../../backend/app/team/scheduler.py#L145-L146) | 畸形事件被丢弃且无日志 |
| R3 | `_inherit_workspace` 宽异常吞 | [scheduler.py:104-112](../../../backend/app/team/scheduler.py#L104-L112) | 授权失败仍以未授权 workspace 继续 → `directory_extension` 审批死锁 |
| R4 | abort 检查粒度粗 | [scheduler.py:311](../../../backend/app/team/scheduler.py#L311) 仅在事件迭代间检查 | LLM 长调用期间无法响应中止（M25 注释自承认） |
| R5 | `_quality_gate` 削弱 | [aggregator.py:78](../../../backend/app/team/aggregator.py#L78) 已移除 identical-findings 检查 | 4 个相同非截断结果仍被汇总 |
| R6 | 降级路径跳发 `team_init` | [orchestrator.py:700-711](../../../backend/app/team/orchestrator.py#L700-L711) 发 `token`+`team_done` 不发 `team_init` | 前端 TeamNodeCard 生命周期不一致 |
| R7 | `_default_node` 不一致 | [orchestrator.py:556-564](../../../backend/app/team/orchestrator.py#L556-L564) 不发 `delegation` 直接返回失败 | 由 A1 统一节点修复 |
| D2 | `astream_events(version="v2")` 弃用 | [scheduler.py:311](../../../backend/app/team/scheduler.py#L311) | LangChain 推 v3 |
| D3 | `_looks_like_dangerous_task` 子串误命中 | [planner.py:272-276](../../../backend/app/team/planner.py#L272-L276) `"write"` 命中 `"read the writeup"`、`"修改"` 命中 `"解释修改符"` | 危险任务误判，安全降级失真 |

## What Changes

### A1. 统一 6 个子任务节点为单一 `_run_subtask_node`（see design.md D1）

- 删除 [orchestrator.py:353-564](../../../backend/app/team/orchestrator.py#L353-L564) 的 6 个函数（5 个相似 + `_default_node`）：`_deep_node` / `_code_node` / `_builtin_node` / `_team_role_node` / `_custom_node` / `_default_node`
- 新增 `_run_subtask_node(state, config)`：abort 检查 → emit `delegation` → emit todo in_progress → 跑 runner → 产出 state update → 处理 `CancelledError`
- 新增 `SubtaskConfig`（描述 agent 类型、runner、runner_args、workspace 继承标志）
- 新增 `_NODE_DISPATCH: dict[str, SubtaskConfig]` 派发表，`_default_node` 走 `_DEFAULT_CONFIG`（emit `delegation` 后返回失败）
- `_dispatch_node` 统一返回 `Send("subtask", {...})` 单一目标节点

### A2. 删除 `_in_progress_tasks` 全局，reducer 持久化 in_progress

- 扩展 [blackboard.py:80-107](../../../backend/app/team/blackboard.py#L80-L107) `_merge_todos`：`in_progress` 为粘性状态，任一侧为 `in_progress` 即保持，不降级为 `pending`
- 删除 [orchestrator.py:91](../../../backend/app/team/orchestrator.py#L91) `_in_progress_tasks` 全局
- `_emit_todo_in_progress` 仅 emit `todo_update` SSE，不再写全局
- 删除 [orchestrator.py:759-770](../../../backend/app/team/orchestrator.py#L759-L770) 的 in_progress 覆盖逻辑

### A4. 删除死字段与双重表示

- 删除 `TeamState.subtask_results`（blackboard.py:135）与 `_make_subtask_state_update` 中的写入（orchestrator.py:265,276）
- 删除 `RouterState.team_blackboard`（router/state.py:27）
- 删除 `Blackboard.meta`（blackboard.py:66）
- `Blackboard` 内联进 `_run_aggregator` 入参（传 `findings`/`errors` dict），删除 `Blackboard` dataclass
- 消除 `clean_todos`/`tasks` 双列表，新增纯函数 `tasks_to_display_todos(tasks)`

### A5. /reset 清理孤儿 child_thread_id + UUID 化 child_id

- [chat.py reset](../../../backend/app/api/chat.py) 端点枚举并删除 `{thread_id}-team-*` 子 thread checkpoint
- 新增 `_enumerate_child_thread_ids(parent_thread_id)` 辅助
- child_thread_id 改为 `{parent}-team-{uuid4()}` 避免 retry 撞 stale

### R1. 收窄 `approval_runner.py` 异常

- 9 处 `except Exception: pass` → `except <LangGraph 中断专用异常>: pass`
- 顶层补 `except Exception as exc: logger.warning(...)` 保证非中断异常可观测

### R2. `_route_event_for_node` 日志化丢弃

- [scheduler.py:145-146,179-180](../../../backend/app/team/scheduler.py#L145-L146) `except Exception: pass` → `except (json.JSONDecodeError, KeyError) as exc: logger.warning(...)`，保留 drop 行为

### R3. `_inherit_workspace` 失败即返回失败结果

- [scheduler.py:104-112](../../../backend/app/team/scheduler.py#L104-L112) 收窄到 `SandboxError`/`ValueError`；授权失败返回 `TeamSubtaskResult(success=False, payload="workspace authorization failed: ...")`，打破审批死锁

### R4. abort 可中断 LLM 长调用

- runner `__anext__()` 包 `asyncio.wait_for(…, 5s)` 与 `abort_event.wait()` 竞速；abort 触发则 cancel runner

### R5. 恢复 identical-findings 质量门

- [aggregator.py:78](../../../backend/app/team/aggregator.py#L78) 恢复全等检查，reject reason=`all_findings_identical`

### R6. 降级路径不发 `team_done`

- [orchestrator.py:700-711](../../../backend/app/team/orchestrator.py#L700-L711) 降级路径只发 `token` + `done`（由 chat.py 收尾），不发 `team_done`（无对应 `team_init`）

### astream 迁移评估（see tasks.md T12）

- 校验 [pyproject.toml](../../../pyproject.toml) LangChain 版本；v3 可用则迁，否则文档化迁移路径

### 词边界修复（see design.md D7 / tasks.md T11）

- [planner.py:272-276](../../../backend/app/team/planner.py#L272-L276) 子串 → 编译正则（ASCII 用 `\b`，CJK 用子串），抽取到 [utils/text.py](../../../backend/app/utils/text.py) 复用 aggregator 的 `_KEYWORD_PATTERNS` 模式

## Capabilities

### Modified Capabilities

- `agent-team-architecture`：Team 路径从「6 个复制节点 + 全局 transient 状态 + 死字段」
  清理为「单一节点 + 派发表 + reducer 粘性 in_progress + 无死字段」；异常处理从「静默吞」
  收敛为「窄异常 + 可观测日志」；质量门与降级路径行为修正。

### New Capabilities

- 无（Phase 2 不引入新能力，仅清理）

## Impact

- **后端**：修改 8 个文件（orchestrator.py、blackboard.py、scheduler.py、aggregator.py、
  planner.py、approval_runner.py、chat.py、router/state.py），新增 1 个文件（utils/text.py
  共享关键词模式）
- **前端**：仅 useChatStream.ts 降级路径不再期待 `team_done`（若 `createIfMissing:false` 已正确则无变更）
- **数据**：删除 `subtask_results`/`team_blackboard`/`Blackboard.meta` 字段；旧 checkpoint 不迁移
  （开发阶段推倒重来，无灰度）
- **SSE 事件契约**：无新增、无 schema 变更；仅修正降级路径不再发 `team_done`
- **观测**：异常从静默改为 `logger.warning`，日志量略增但可观测性显著提升
- **风险**：`_merge_todos` 粘性语义变更影响所有 todo 状态归并；`_run_subtask_node` 统一节点
  替换 6 个函数，需全量回归 Team 路径
- **依赖**：GitNexus 已索引，编辑前将跑 impact analysis

## Rollback Plan

1. **代码层**：所有变更集中于单分支，回滚即 `git revert` 单个 merge commit；无数据迁移、无 schema 变更
2. **reducer 行为**：若粘性 `in_progress` 引发 todo 卡死，回退 `_merge_todos` 至原始实现并恢复
   `_in_progress_tasks` 全局；因 Phase 1 未修改该全局，回退后等价于 Phase 1 落地后的线上行为
   （即当前基线）
3. **child_thread_id UUID 化**：若 `alist(configs)` 枚举失败，UUID 化仍独立生效（不依赖清理逻辑），
   回滚仅需还原 child_id 模板字符串
4. **异常收窄**：若 `InterruptException` 类型匹配失败导致 resume 死循环，临时放宽回 `except Exception`
   并补 `logger.warning`（保留可观测性），不影响主流程
5. **质量门**：`_quality_gate` identical-findings 检查独立开关，回滚仅需删除新增 if 分支
6. **降级路径**：`team_done` 移除是行为修正，回滚仅需还原 `yield make_sse_event("team_done", ...)`
7. **验证门禁**：合并前必须通过 `pytest tests/python/unit -m "not integration"` +
   Team 路径端到端冒烟（/reset → team 任务 → 中止 → retry）

## Out of Scope

- Phase 1 工作（`subtask_timeout` / `max_parallel` / `team_done` 终态 / `tryRecoverResult`）已完成，不重复
- Phase 3 工作（`team_node_done`/`team_node_error` 新事件、子代理输出实时流、进度条、单子代理取消、
  前端渲染 O(n²)→O(n)、role→label 映射合并、planner token 流、re-plan diff 可视化）不做
- 不新增 SSE 事件类型、不动前端渲染层、不引入灰度开关 / 版本兼容层 / `@Deprecated`
- 不做 macOS/Windows 内核级沙箱、不做沙箱远程热更新、不做审计 OTel 扩展
