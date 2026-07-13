# Spec: agent-team-architecture

## 概述

AgentTeam 路径（plan → dispatch fan-out → subtask 节点并行 → aggregate）从「6 个复制粘贴节点 +
模块级可变全局 `_in_progress_tasks` + 死字段（`subtask_results`/`team_blackboard`/`Blackboard.meta`）+
孤儿 child_thread_id checkpoint + 静默异常吞」清理为「单一 `_run_subtask_node` + 派发表 +
`_merge_todos` 粘性 in_progress reducer + 无死字段 + /reset 清理孤儿 + UUID 化 child_id +
窄异常 + 可观测日志 + 恢复的质量门 + 修正的降级路径」。

不新增 SSE 事件类型、不动前端渲染层、不引入灰度开关。Phase 2 仅清理架构技术债。

## MODIFIED Requirements

### Requirement: 统一子任务节点执行

The system MUST consolidate Team 路径子任务节点从 6 个复制粘贴函数为单一 `_run_subtask_node` + `_NODE_DISPATCH` 派发表；
所有 agent 类型 MUST 共享相同的 abort/delegation/todo/runner/CancelledError 流程，差异由 `SubtaskConfig`
描述。未知 agent 类型 MUST 经 `_DEFAULT_CONFIG` 统一 emit `delegation` 后返回失败。

#### Scenario: 已知 agent 类型经派发表执行

- **WHEN** `_dispatch_node` 为 `TeamPlanTask(agent="deep")` 生成 `Send("subtask", {...})`
- **THEN** `_run_subtask_node` 从 `_NODE_DISPATCH["deep"]` 取 `SubtaskConfig(agent_kind="deep", inherit_workspace=True)`
- **AND** 依次执行：abort 检查 → `_emit_delegation` → `_emit_todo_in_progress` → `_inherit_workspace` → `_run_subtask_stream(deep_runner, ...)`
- **AND** runner 返回后经 `_make_subtask_state_update` 产出 findings/errors + todos 更新
- **AND** 不再存在独立的 `_deep_node` / `_code_node` / `_builtin_node` / `_team_role_node` / `_custom_node` 函数

#### Scenario: 未知 agent 类型经默认配置统一失败

- **WHEN** `TeamPlanTask(agent="unknown_type")` 经 `_dispatch_node` 派发
- **THEN** `_NODE_DISPATCH.get("unknown_type", _DEFAULT_CONFIG)` 返回 `_DEFAULT_CONFIG`
- **AND** `_run_subtask_node` 先 `_emit_delegation(writer, "unknown_type", purpose)` 创建前端 SubAgentGroup 容器
- **AND** 返回 `TeamSubtaskResult(success=False, payload="未知 agent: unknown_type")`
- **AND** 不再调用 runner（`runner_factory=None`）

#### Scenario: child_thread_id 唯一化

- **WHEN** `_run_subtask_node` 为子任务生成 child_thread_id
- **THEN** child_id 格式为 `{parent_thread_id}-team-{uuid4()}`（不再含 agent 名与 idx）
- **AND** 同一 parent 的 retry 因 UUID 不同不会撞 stale checkpoint

#### Scenario: 子任务取消返回部分状态

- **WHEN** 子任务执行期间 `asyncio.CancelledError` 被抛出（断连/中止）
- **THEN** `_run_subtask_node` 捕获并 `logger.info("team subtask cancelled", agent, task_index)`
- **AND** 返回 `_make_subtask_state_update(TeamSubtaskResult(success=False, payload="子任务已取消"), task_index)`
- **AND** aggregator 仍可汇总该失败结果

#### Scenario: team_role agent 经派发表执行

- **WHEN** plan 包含 `TeamPlanTask(agent="frontend_dev")` 任务
- **THEN** `_dispatch_node` 返回 `Send("subtask", {"task": t, "config": _NODE_DISPATCH["frontend_dev"]})`
- **AND** `_NODE_DISPATCH["frontend_dev"]` 为 `SubtaskConfig(agent_kind="team_role", inherit_workspace=False)`
- **AND** `_run_subtask_node` 通过 `agent_kind="team_role"` 分支调用 `_run_team_role_subtask`（经 `TeamRoleRunnerAdapter` 适配统一签名）
- **AND** `backend_dev`/`tester`/`architect`/`devops`/`ui_designer`/`product_manager` 同理走 `team_role` 分支

### Requirement: 统一节点保留 Phase 1 稳定性保护

`_run_subtask_node` 在统一 6 个节点函数后，MUST 保留 Phase 1 引入的 `subtask_timeout` 超时保护
与 `max_parallel` 并发限流。超时保护 MUST 由 `_run_subtask_stream` 内部的 `asyncio.wait_for(timeout=subtask_timeout)`
自动生效；并发限流 MUST 由 `_run_subtask_node` 在调用 runner 前获取 `state["team_semaphore"]` 实现。

#### Scenario: subtask_timeout 仍生效于 `_run_subtask_node`

- **WHEN** 子任务 runner 执行时间超过 `subtask_timeout`（Phase 1 注入的 state 字段）
- **THEN** `_run_subtask_stream` 内部的 `asyncio.wait_for(timeout=subtask_timeout)` 触发超时
- **AND** `_run_subtask_node` 收到超时结果 `TeamSubtaskResult(success=False, payload="子任务超时")`
- **AND** 该结果经 `_make_subtask_state_update` 产出 errors 更新

#### Scenario: max_parallel 限流仍生效于 `_run_subtask_node`

- **WHEN** `state["team_semaphore"]` 为 `asyncio.Semaphore(2)`（max_parallel=2）且已有 2 个子任务在执行
- **THEN** 第 3 个子任务在 `async with semaphore:` 处阻塞
- **AND** 直到前 2 个子任务中至少 1 个释放 semaphore 后第 3 个才开始执行 runner
- **AND** `_emit_delegation` 与 `_emit_todo_in_progress` 在阻塞前已 emit（前端立即收到信号）

### Requirement: todo in_progress 状态由 reducer 粘性持有

`in_progress` 状态 MUST 由 `_merge_todos` reducer 持久化，删除模块级全局 `_in_progress_tasks`。
reducer 不变量：`in_progress` 一旦设置，MUST 只能由显式 `completed`/`error` 覆盖，不因 values-mode
归并回退为 `pending`。

#### Scenario: in_progress 不被 pending 降级

- **WHEN** 子任务节点 A 返回 `{"todos": [{"_index": 0, "status": "in_progress"}]}`
- **AND** 并行子任务节点 B 返回 `{"todos": [{"_index": 0, "status": "pending"}]}`（B 未感知 A 已标记）
- **THEN** `_merge_todos` 归并后 `state.todos[0].status == "in_progress"`（粘性保持）
- **AND** 不存在 `_in_progress_tasks` 模块级全局变量
- **AND** `_emit_todo_in_progress` 仅 emit `todo_update` SSE 事件，不写任何全局副作用

#### Scenario: in_progress 被显式 completed 覆盖

- **WHEN** todo[0] 已为 `in_progress`
- **AND** 子任务节点返回 `{"todos": [{"_index": 0, "status": "completed"}]}`
- **THEN** `_merge_todos` 归并后 `state.todos[0].status == "completed"`（显式终态覆盖粘性）
- **AND** values-mode 推送的 `todo_update` SSE 事件携带 `completed` 状态

#### Scenario: 全局在 graph 崩溃时不泄漏

- **WHEN** Team 任务执行中途 graph 崩溃（`_aggregate_node` 未执行）
- **THEN** 不存在需要清理的 `_in_progress_tasks` 残留（全局已删除）
- **AND** 下次同 thread_id 的任务启动时 `state.todos` 由 checkpointer 恢复，in_progress 粘性语义自洽
- **AND** `run_team_path` values-mode 不再执行 in_progress overlay 逻辑

### Requirement: 死字段与双重表示消除

The system MUST 删除从未被读取的 `TeamState.subtask_results`、从未被填充的 `RouterState.team_blackboard` 与
`Blackboard.meta`，`Blackboard` dataclass MUST 内联进 `_run_aggregator` 入参。`clean_todos`/`tasks`
双列表 MUST 合并为单一 `tasks` 数据源 + 派生函数 `tasks_to_display_todos`。

#### Scenario: subtask_results 不再写入或读取

- **WHEN** 子任务节点经 `_make_subtask_state_update` 产出状态更新
- **THEN** 返回的 dict 仅含 `findings` 或 `errors` + `todos`，不含 `subtask_results` 键
- **AND** `TeamState` TypedDict 不再声明 `subtask_results` 字段
- **AND** `initial_state` 初始化不再包含 `subtask_results: {}`

#### Scenario: Blackboard 内联进 aggregator

- **WHEN** `_aggregate_node` 调用 `_run_aggregator`
- **THEN** 入参为 `findings: dict[str,str]` 与 `errors: dict[str,str]`（不再构造 `Blackboard` 实例）
- **AND** 不存在 `Blackboard` dataclass 定义
- **AND** `RouterState` 不再声明 `team_blackboard` 字段

#### Scenario: tasks 单一数据源派生展示 todos

- **WHEN** `_plan_node` 产出任务列表
- **THEN** 返回 `{"plan": tasks, "todos": initial_todos}`（tasks 为 `TeamPlanTask` 列表单一来源）
- **AND** 展示用 todos 由 `tasks_to_display_todos(tasks)` 纯函数派生
- **AND** 不再同时维护 `clean_todos` 与 `tasks` 双列表

### Requirement: /reset 清理孤儿 child checkpoint

`/reset` 端点在删除父 thread checkpoint 后，MUST 枚举并删除所有 `{thread_id}-team-*` 前缀的子 thread
checkpoint，避免 `data/agentx.db` 无限膨胀。child_thread_id MUST UUID 化防止 retry 撞 stale 状态。

#### Scenario: /reset 清理所有子任务 checkpoint

- **WHEN** 用户发送 `/reset` 且父 `thread_id` 存在子任务 checkpoint（`{thread_id}-team-*` 前缀）
- **THEN** /reset 端点调用 `_enumerate_child_thread_ids(thread_id, checkpointer)` 列出所有匹配前缀的 child_id
- **AND** 对每个 child_id 调用 `checkpointer.adelete_thread(child_id)` 删除
- **AND** 删除完成后 `data/agentx.db` 中该 parent 的子任务 checkpoint 计数为 0

#### Scenario: 枚举 fallback 支持 无 alist 的 checkpointer

- **WHEN** checkpointer 不提供 `alist` 方法
- **THEN** `_enumerate_child_thread_ids` fallback 为 SQL `SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ?`
- **AND** 返回的 child_id 列表与 `alist` 路径一致

#### Scenario: retry 不撞 stale 状态

- **WHEN** 同一 parent_thread_id 的 team 任务 retry（重新执行）
- **THEN** 新子任务生成 `{parent}-team-{uuid4()}` child_id（与上次不同）
- **AND** 即使旧 child checkpoint 未被 /reset 清理，新子任务也不读取旧 state

### Requirement: 异常处理窄化与可观测

静默的 `except Exception: pass` MUST 收窄为窄异常类型，非中断异常 MUST 经 `logger.warning` 可观测。
`_inherit_workspace` 授权失败 MUST 返回失败结果而非继续未授权执行。

#### Scenario: approval_runner resume 非中断异常可观测

- **WHEN** `agent.astream` 在 resume 期间抛出非中断异常（如网络错误、序列化错误）
- **THEN** 顶层 `except Exception as exc: logger.warning(f"unexpected resume exception: {exc}")` 捕获并记录
- **AND** 不再静默 `pass` 导致循环空转
- **AND** LangGraph 中断专用异常仍被窄捕获 `pass`（合法的「无中断待恢复」退出）

#### Scenario: 畸形事件被丢弃且有日志

- **WHEN** `_route_event_for_node` 解析 `tool_result` JSON 或 `_subtask_done` payload 失败
- **THEN** `except (json.JSONDecodeError, KeyError) as exc: logger.warning(f"dropped malformed event: {event_type}: {exc}")` 捕获
- **AND** 事件被丢弃（子任务继续，不向上抛）
- **AND** 日志含 event_type 与异常信息便于排查

#### Scenario: workspace 授权失败返回失败结果打破死锁

- **WHEN** `_inherit_workspace` 调用 `sandbox.authorize(...)` 抛出 `SandboxError` 或 `ValueError`
- **THEN** `_inherit_workspace` 返回 `TeamSubtaskResult(success=False, payload=f"workspace authorization failed: {exc}")`
- **AND** `_run_subtask_node` 收到失败结果后跳过 runner，直接返回失败状态更新
- **AND** 子任务不再以未授权 workspace 继续执行（避免 `directory_extension` 审批死锁）
- **AND** 非授权类异常（如 `RuntimeError`）不再被宽 `except Exception` 吞掉

### Requirement: abort 可中断 LLM 长调用

子任务执行期间，abort 信号 MUST 在 5 秒内被响应，即使 LLM 处于长流式调用中。

#### Scenario: LLM 长调用期间 abort 生效

- **WHEN** 子任务 runner 的 `astream_events` 长时间不产出新事件（LLM 单次调用 >5s）
- **AND** 用户触发 abort（`abort_event.set()`）
- **THEN** `_run_subtask_stream` 的 `asyncio.wait({runner.__anext__(), abort_event.wait()}, FIRST_COMPLETED, timeout=5)` 在 5s 内检测到 abort
- **AND** cancel runner task 并返回 `_done(False, "用户中止")`
- **AND** 不再仅在事件迭代间检查 abort（M25 注释的缺陷已修复）

#### Scenario: 正常流式不被竞速打断

- **WHEN** LLM 正常流式产出 token（每 token ~50ms）
- **AND** abort 未触发
- **THEN** `runner.__anext__()` 先完成，abort_task 被 cancel
- **AND** 事件正常处理，下一轮重新创建竞速任务
- **AND** 无性能损耗（正常流式不触发 timeout）

### Requirement: 质量门恢复 identical-findings 检查

`_quality_gate` MUST 恢复对所有 findings 全等的拒绝逻辑，避免 4 个相同非截断结果被无意义汇总。

#### Scenario: 全部 findings 相同被拒绝

- **WHEN** `blackboard.findings` 所有 value 经 strip 后完全相等（且非空、非纯截断标记）
- **THEN** `_quality_gate` 返回 `(False, "all_findings_identical")`
- **AND** `logger.info` 记录拒绝原因
- **AND** aggregator 触发重试或降级路径

#### Scenario: 至少一个 finding 不同通过

- **WHEN** `blackboard.findings` 存在至少一个与其他不同的 value
- **THEN** `_quality_gate` 返回 `(True, "")`
- **AND** aggregator 正常汇总

#### Scenario: 截断专用检查保留

- **WHEN** 所有 findings value 均为 `"[结果已截断]"`
- **THEN** `_quality_gate` 返回 `(False, "所有结果均为截断片段，无有效内容")`（M2 既有逻辑保留）
- **AND** identical-findings 检查不与之冲突

### Requirement: 降级路径不产出 team 生命周期事件

简单任务降级（短消息/问候/翻译）MUST 不 emit `team_done`，因无对应 `team_init`。降级 MUST 等价于单 agent
执行，仅产出 `token`，由 chat.py 收尾发 `done`。

#### Scenario: 降级路径不发 team_done

- **WHEN** `_should_downgrade_to_single(message)` 返回 `(True, reason)`
- **THEN** `run_team_path` 仅 `yield make_sse_event("token", ...)` 提示
- **AND** 不 `yield make_sse_event("team_done", ...)`
- **AND** 不 `yield` 任何 `team_init` / `team_plan` 等 team 生命周期事件
- **AND** chat.py 负责收尾发 `done` 终态事件

#### Scenario: 前端降级路径无残留 TeamNodeCard

- **WHEN** 前端 `useChatStream.ts` 收到降级路径的 `token` 事件
- **THEN** `team_done` 处理分支的 `createIfMissing:false` 不创建 TeamNodeCard
- **AND** 不存在因缺失 `team_init` 而悬空的 team 容器

### Requirement: 危险任务关键词词边界匹配

`_looks_like_dangerous_task` MUST 用编译正则匹配，ASCII 关键词带 `\b` 词边界，CJK 关键词 MUST 用子串。
关键词模式 MUST 与 aggregator 共享 helper，消除两处分叉。

#### Scenario: ASCII 关键词不误命中子串

- **WHEN** 子任务 input 为 `"read the writeup"`
- **THEN** `_looks_like_dangerous_task` 返回 `False`（`"write"` 词边界不命中 `"writeup"`）
- **AND** 子任务不被强制改写为 `deep` agent

#### Scenario: ASCII 关键词正确命中整词

- **WHEN** 子任务 input 为 `"write file to src/main.py"`
- **THEN** `_looks_like_dangerous_task` 返回 `True`（`\bwrite\b` 命中）
- **AND** 子任务被强制改写为 `deep` agent 走审批流

#### Scenario: CJK 关键词子串匹配

- **WHEN** 子任务 input 为 `"解释修改符"`
- **THEN** `_looks_like_dangerous_task` 返回 `True`（`"修改"` 作为 CJK 子串命中 `"解释修改符"`，CJK 无词边界概念，子串匹配为文档化行为）

#### Scenario: CJK 关键词不跨字符边界误命中

- **WHEN** 子任务 input 为 `"解释 const 修饰符"`
- **THEN** `_looks_like_dangerous_task` 返回 `False`（`"修饰"` 不含 `"修改"` 子串，子串匹配不跨字符边界）

#### Scenario: 关键词模式与 aggregator 共享

- **WHEN** planner 与 aggregator 都调用 `compile_keyword_patterns(keywords)`
- **THEN** 两处对同一关键词集的判定结果一致
- **AND** 新增关键词仅修改一处（`utils/text.py` helper 调用方传入新列表）

## REMOVED Requirements

### Requirement: 模块级 `_in_progress_tasks` 全局

- **WHEN** Team 路径执行子任务
- **THEN** 不存在 `_in_progress_tasks: dict[str, set[int]]` 模块级可变全局
- **AND** in_progress 状态完全由 `_merge_todos` reducer 持久化
- **AND** `_emit_todo_in_progress` 不写任何全局副作用
- **AND** `run_team_path` values-mode 不执行 in_progress overlay 逻辑

### Requirement: `subtask_results` / `team_blackboard` / `Blackboard.meta` 死字段

- **WHEN** Team 路径维护状态
- **THEN** `TeamState` 不含 `subtask_results` 字段
- **AND** `RouterState` 不含 `team_blackboard` 字段
- **AND** 不存在 `Blackboard` dataclass（含 `meta` 字段）
- **AND** `_make_subtask_state_update` 不写入 `subtask_results`

### Requirement: 6 个复制粘贴子任务节点函数

- **WHEN** Team 路径 dispatch 子任务
- **THEN** 不存在 `_deep_node` / `_code_node` / `_builtin_node` / `_team_role_node` / `_custom_node` / `_default_node` 6 个独立函数
- **AND** 所有子任务经单一 `_run_subtask_node` + `_NODE_DISPATCH` 派发表执行

## ADDED Requirements

### Requirement: 共享关键词模式 helper

The system MUST 新增 `backend/app/utils/text.py` 提供 `compile_keyword_patterns(keywords)` 与 `matches_any(text, patterns)`，
planner 与 aggregator MUST 共用以消除关键词匹配逻辑分叉。

#### Scenario: ASCII 与 CJK 混合关键词编译

- **WHEN** 调用 `compile_keyword_patterns(["write", "写入", "edit"])`
- **THEN** 返回 tuple，`"write"` 编译为 `\bwrite\b`，`"写入"` 编译为子串（CJK 无词边界）
- **AND** `matches_any("read the writeup", patterns)` 返回 `False`
- **AND** `matches_any("write file", patterns)` 返回 `True`

#### Scenario: 两处调用方行为一致

- **WHEN** planner 的 `_looks_like_dangerous_task` 与 aggregator 的 `_KEYWORD_PATTERNS` 共用同一 helper
- **THEN** 对同一 input 文本两处判定结果一致
- **AND** 新增关键词仅修改一处调用方的关键词列表
