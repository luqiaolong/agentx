# Spec: AgentTeam v2

## ADDED Requirements

### Requirement: 概述

AgentTeam v2 是 AgentX Team 路径的全量重构能力契约。系统 SHALL 将
`backend/app/team/orchestrator.py` 单文件拆为多个单一职责模块，并落地
DAG 多波次派发、结构化任务通信、并发限流、危险任务 LLM 分类、迭代式 replan
等核心能力。

本 capability supersede `agent-team-dag`（来自 `agent-team-dag-orchestration` change）与
`agent-team-architecture`（来自 `2026-07-12-agent-team-architecture-cleanup` change），
二者均未落地，被本 capability 合并取代。

#### Scenario: v2 模块拆分验证

- **Given** `backend/app/team/` 目录
- **When** 检查模块文件
- **Then** `orchestrator.py` 不存在，`state.py` / `planner.py` / `dispatcher.py` /
  `nodes.py` / `graph_builder.py` / `runner.py` / `scheduler.py` / `aggregator.py` /
  `classifier.py` / `blackboard.py` 均存在且可导入

### Requirement: DAG 解析与 wave 派发

The system MUST resolve task dependencies into topological waves via Kahn's algorithm and dispatch one wave at a time.

#### Scenario: 线性链分层

- **Given** `tasks = [t1(depends_on=[]), t2(depends_on=["t1"]), t3(depends_on=["t2"])]`
- **When** 调用 `dispatcher.resolve_waves(tasks)`
- **Then** 返回 `[[t1], [t2], [t3]]`（三层串行）

#### Scenario: 菱形依赖分层

- **Given** `tasks = [t1(depends_on=[]), t2(depends_on=["t1"]), t3(depends_on=["t1"]), t4(depends_on=["t2","t3"])]`
- **When** 调用 `dispatcher.resolve_waves(tasks)`
- **Then** 返回 `[[t1], [t2, t3], [t4]]`（中间层并行）

#### Scenario: 全并行（无依赖）

- **Given** `tasks = [t1(depends_on=[]), t2(depends_on=[]), t3(depends_on=[])]`
- **When** 调用 `dispatcher.resolve_waves(tasks)`
- **Then** 返回 `[[t1, t2, t3]]`（单层，等价于旧并行 fan-out）

#### Scenario: 循环检测与打破

- **Given** `tasks = [t1(depends_on=["t2"]), t2(depends_on=["t1"])]`（循环）
- **When** 调用 `dispatcher.resolve_waves(tasks)`
- **Then** 不抛异常，返回包含所有任务的 waves（强制打破循环），logger.error 记录

#### Scenario: 自环丢弃

- **Given** `tasks = [t1(depends_on=["t1"])]`（自环）
- **When** 调用 `dispatcher.resolve_waves(tasks)`
- **Then** 自环边丢弃，返回 `[[t1]]`，logger.warning 记录

#### Scenario: 越界依赖丢弃

- **Given** `tasks = [t1(depends_on=["t99"])]`（t99 不存在）
- **When** 调用 `dispatcher.resolve_waves(tasks)`
- **Then** 越界边丢弃，返回 `[[t1]]`，logger.warning 记录

#### Scenario: 单 wave 派发

- **Given** `pending_waves = [[t1], [t2, t3], [t4]]`
- **When** 执行 `nodes.dispatch_node`
- **Then** 返回 `[Send("execute", {task: t1, ...})]`（只派发第一 wave）

#### Scenario: 空 wave 派发

- **Given** `pending_waves = []`
- **When** 执行 `nodes.dispatch_node`
- **Then** 返回 `[Send("aggregate", {})]`

### Requirement: wave 间串行与 barrier 路由

`barrier_node` MUST route to next `dispatch` when waves remain, otherwise to `aggregate`.

#### Scenario: 有 wave 剩余

- **Given** 当前 wave 执行完毕，`pending_waves = [[t2, t3], [t4]]`
- **When** `_route_after_barrier(state)` 被调用
- **Then** 返回 `"dispatch"`（回到 dispatch 派发下一 wave）

#### Scenario: 无 wave 剩余

- **Given** 当前 wave 执行完毕，`pending_waves = []`
- **When** `_route_after_barrier(state)` 被调用
- **Then** 返回 `"aggregate"`

### Requirement: 子任务结果传递

The system MUST inject upstream task findings into dependent task input via `_inject_upstream_findings` and `_compose_input_with_upstream`.

#### Scenario: 单依赖注入

- **Given** task = `TeamTask(id="t2", agent="deep", description="修改", depends_on=["t1"])`，
  `findings = {"code:t1:0": Finding(agent="code", task_id="t1", content="src/main.py 内容...")}`
- **When** 调用 `_inject_upstream_findings(task, findings)` + `_compose_input_with_upstream(task, upstream)`
- **Then** 返回字符串包含 `[依赖任务结果]` + `--- #t1 [code] ---` + `src/main.py 内容...` + `[当前任务]` + `修改`

#### Scenario: 多依赖注入

- **Given** task = `TeamTask(id="t3", agent="deep", description="修改", depends_on=["t1", "t2"])`，
  `findings = {"code:t1:0": ..., "rag:t2:1": ...}`
- **When** 调用 `_inject_upstream_findings` + `_compose_input_with_upstream`
- **Then** 返回字符串包含两个依赖段 + 当前任务段

#### Scenario: 依赖未完成

- **Given** task = `TeamTask(id="t2", agent="deep", description="修改", depends_on=["t1", "t99"])`，
  `findings = {"code:t1:0": ...}`（t99 不存在）
- **When** 调用 `_inject_upstream_findings`
- **Then** 只返回 t1 的 finding，logger.warning 记录 t99 缺失

#### Scenario: 截断

- **Given** task depends_on=["t1"]，`findings["code:t1:0"].content` 长度 3000，`team_result_max_chars=2000`
- **When** 调用 `_compose_input_with_upstream`
- **Then** 依赖段长度不超过 2000 + `[结果已截断]` 标记

#### Scenario: 无依赖

- **Given** task = `TeamTask(id="t1", agent="code", description="读取", depends_on=[])`
- **When** 调用 `_compose_input_with_upstream`
- **Then** 返回 `task.description` 原样

### Requirement: 并发限流

`scheduler.acquire_and_run` MUST enforce global concurrency limit via `asyncio.Semaphore`.

#### Scenario: 限流生效

- **Given** `team_max_concurrency=5`，10 个子任务同时派发
- **When** 执行 `acquire_and_run` 并发
- **Then** 任意时刻最多 5 个子任务在执行，其余等待 semaphore

#### Scenario: 释放后唤醒

- **Given** 5 个子任务执行中，semaphore 已满，第 6 个等待
- **When** 某个子任务完成释放 semaphore
- **Then** 第 6 个子任务被唤醒执行

#### Scenario: 异常释放

- **Given** 子任务执行中抛异常
- **When** 异常被捕获
- **Then** semaphore 释放（不泄漏）

### Requirement: 危险任务 LLM 分类

`DangerousTaskClassifier` MUST classify dangerous tasks via LLM with structured output, falling back to keyword matching with word boundaries.

#### Scenario: LLM 路径分类

- **Given** task = `TeamTask(id="t1", agent="code", description="删除 src/main.py")`，LLM 可用
- **When** 调用 `classifier.classify(task, available_tools)`
- **Then** 返回 `ClassificationResult(is_dangerous=True, reason="删除文件操作", suggested_agent="deep")`

#### Scenario: 关键词降级

- **Given** LLM 不可用，task description 含「删除文件」
- **When** 调用 `classifier.classify`
- **Then** 走 `_keyword_fallback`，返回 `is_dangerous=True`，`suggested_agent="deep"`

#### Scenario: ASCII 词边界防误命中

- **Given** task description = `"read the writeup file"`，`_keyword_fallback` 含 `r"\bwrite\b"` 模式
- **When** 调用 `_keyword_fallback`
- **Then** 返回 `is_dangerous=False`（「writeup」不匹配 `\bwrite\b` 词边界）

#### Scenario: CJK 词边界防误命中

- **Given** task description = `"查看修改历史"`，旧实现用「修改」子串会误命中
- **When** 调用 `_keyword_fallback`
- **Then** 返回 `is_dangerous=False`（CJK 关键词要求前后非汉字字符，「修改历史」中的「修改」前后是汉字）

#### Scenario: 缓存命中

- **Given** 相同 `task.description` 第二次调用
- **When** 调用 `classifier.classify`
- **Then** 不调用 LLM，直接返回缓存结果

#### Scenario: 危险任务改写

- **Given** task agent="code"，`classifier.classify` 返回 `is_dangerous=True, suggested_agent="deep"`
- **When** `execute_node` 处理该任务
- **Then** task agent 改写为 "deep"，发射 `warning` SSE 事件，写入 `state.warnings`

### Requirement: 未知 agent fallback

Unknown agents via `_FALLBACK_CONFIG` MUST fallback to code runner instead of failing directly.

#### Scenario: 未知 agent 兜底

- **Given** task = `TeamTask(id="t1", agent="unknown_agent", description="...")`，经 `_FALLBACK_CONFIG` 派发
- **When** `execute_node` 执行未知 agent 分支
- **Then** `logger.warning` 记录 `agent="unknown_agent"`，实际调用 `_get_code_runner` 执行
- **And** `TeamSubtaskResult.agent` 为 `"unknown_agent"`（保留原始名）
- **And** 发射 `warning` SSE 事件，写入 `state.warnings`
- **And** 不直接返回 `success=False` 硬错误

#### Scenario: fallback 执行成功

- **Given** 未知 agent fallback 到 code runner，code runner 执行成功
- **When** `execute_node` 完成
- **Then** 返回 `TeamSubtaskResult(success=True, payload=<code runner 输出>)`

### Requirement: abort 响应 LLM 长调用

`scheduler.acquire_and_run` MUST register running tasks as `asyncio.Task` and cancel them on abort.

#### Scenario: abort 中止子任务

- **Given** 子任务正在执行 LLM 长调用，`abort_event.is_set()` 被触发
- **When** `acquire_and_run` 检测到 abort
- **Then** 调用 `task_obj.cancel()` 中断 LLM 调用
- **And** 捕获 `CancelledError`，返回 `TeamSubtaskResult(success=False, payload="用户中止")`

#### Scenario: abort 释放 semaphore

- **Given** 子任务持有 semaphore，abort 触发 cancel
- **When** `CancelledError` 被捕获
- **Then** semaphore 释放（不泄漏）

#### Scenario: cancel 清理 registry

- **Given** `_running_tasks[thread_id] = [task1, task2]`
- **When** `cancel_running_tasks(thread_id)` 被调用
- **Then** 两个 task 都被 cancel（若未完成），`_running_tasks[thread_id] = []`

#### Scenario: 正常完成不触发 cancel

- **Given** 子任务正常完成，未 abort
- **When** `acquire_and_run` 返回
- **Then** 不调用 `task_obj.cancel()`，task 从 `_running_tasks` 移除

### Requirement: 失败重试

`scheduler.run_with_retry` MUST retry transient errors with exponential backoff, not retry non-transient errors.

#### Scenario: 瞬态错误重试

- **Given** runner 抛 `asyncio.TimeoutError`，`team_max_retries=2`
- **When** 调用 `run_with_retry`
- **Then** 重试 3 次（初始 + 2 重试），退避 1s/2s/4s，最终返回 `success=False, retries=2`

#### Scenario: 网络异常重试

- **Given** runner 抛 `httpx.ConnectError`
- **When** 调用 `run_with_retry`
- **Then** 重试 3 次

#### Scenario: 5xx 重试

- **Given** runner 抛 `httpx.HTTPStatusError` 5xx
- **When** 调用 `run_with_retry`
- **Then** 重试 3 次

#### Scenario: 4xx 不重试

- **Given** runner 抛 `httpx.HTTPStatusError` 4xx
- **When** 调用 `run_with_retry`
- **Then** 不重试，直接返回 `success=False, retries=0`

#### Scenario: 逻辑错误不重试

- **Given** runner 抛 `ValueError`
- **When** 调用 `run_with_retry`
- **Then** 不重试，直接返回 `success=False, retries=0`

#### Scenario: CancelledError 不重试

- **Given** runner 抛 `asyncio.CancelledError`（abort）
- **When** 调用 `run_with_retry`
- **Then** 不重试，直接 raise `CancelledError`

#### Scenario: 记录 retries 字段

- **Given** runner 重试 2 次后成功
- **When** 调用 `run_with_retry`
- **Then** 返回 `TeamSubtaskResult(success=True, retries=2)`

### Requirement: 迭代式 replan

`replan_node` MUST re-invoke planner when quality gate fails, with `max_replan_attempts` limit.

#### Scenario: 质量门失败触发 replan

- **Given** `aggregate_node` 质量门判定失败，`replan_count=0`，`team_max_replan_attempts=1`
- **When** `_route_after_aggregate(state)` 被调用
- **Then** 返回 `"replan"`

#### Scenario: replan 追加任务

- **Given** `replan_node` 调用 `Planner.replan`，LLM 返回新任务
- **When** `replan_node` 执行
- **Then** 新任务追加到 `plan`，`resolve_waves` 重算 `pending_waves`，`replan_count` 递增
- **And** 发射 `replan` SSE 事件（含 new_tasks + replan_count）
- **And** `_route_after_replan` 返回 `"plan"`（回到 plan_node）

#### Scenario: LLM 判定无需追加

- **Given** `replan_node` 调用 `Planner.replan`，LLM 返回空 tasks
- **When** `replan_node` 执行
- **Then** `pending_waves` 为空，`_route_after_replan` 返回 END（失败）

#### Scenario: replan 次数上限

- **Given** `replan_count=1`，`team_max_replan_attempts=1`
- **When** `replan_node` 执行
- **Then** 不调用 LLM，`_route_after_replan` 返回 END（失败）

### Requirement: token 透传一致性

All execute nodes and aggregator MUST emit token events with unified schema `{agent: str, content: str}`.

#### Scenario: execute 节点 token schema

- **Given** 任意 execute 节点（code/deep/rag/web/builtin）发射 token 事件
- **When** 前端收到事件
- **Then** payload schema 为 `{agent: <agent_name>, content: <token_text>}`

#### Scenario: aggregator token schema

- **Given** aggregator 节点发射 token 事件
- **When** 前端收到事件
- **Then** payload schema 为 `{agent: "aggregator", content: <token_text>}`

#### Scenario: 前端无需特殊处理

- **Given** 前端收到任意 token 事件
- **When** 渲染
- **Then** 统一按 `event.agent` 分组展示，无需按 agent 类型特殊适配

### Requirement: findings key 复合格式

Findings key MUST follow `{agent}:{task_id}:{wave_index}` format to avoid collision.

#### Scenario: 单任务 key 格式

- **Given** code agent 在 wave 0 执行 task t1
- **When** `_make_finding_key("code", task_t1, 0)` 被调用
- **Then** 返回 `"code:t1:0"`

#### Scenario: 同类型多任务不覆盖

- **Given** wave 0 有两个 code 任务 t1 和 t2
- **When** 两个任务都写入 findings
- **Then** `findings["code:t1:0"]` 和 `findings["code:t2:0"]` 共存，不互相覆盖

#### Scenario: 跨 wave 同 agent 不覆盖

- **Given** code agent 在 wave 0 执行 t1，在 wave 1 执行 t3
- **When** 两个任务都写入 findings
- **Then** `findings["code:t1:0"]` 和 `findings["code:t3:1"]` 共存

#### Scenario: 旧格式删除

- **Given** 旧格式 `{agent}-{task_index}`（如 `code-0`）
- **When** 检查代码
- **Then** 不存在旧格式引用，全部改为 `{agent}:{task_id}:{wave_index}`

### Requirement: team_role 显式失败

`_run_team_role_subtask` MUST fail explicitly when `cfg.system_prompt` is empty, not silently degrade.

#### Scenario: 配置缺失 system_prompt

- **Given** `settings.team_subagents["frontend_dev"].system_prompt = ""`，`enabled=True`
- **When** 执行 `_run_team_role_subtask(task=TeamTask(agent="frontend_dev", ...))`
- **Then** logger.error 记录
- **And** 发射 `warning` SSE 事件
- **And** 写入 `state.warnings`
- **And** 返回 `TeamSubtaskResult(success=False, payload="团队角色 frontend_dev 配置缺失 system_prompt")`
- **And** 不调用 code runner 兜底

#### Scenario: 启动时校验

- **Given** settings 中某 `team_subagents[key].enabled=True` 且 `system_prompt=""`
- **When** 应用启动调用 `validate_team_subagents(settings)`
- **Then** 抛 `ValueError` 阻止启动

#### Scenario: 正常配置

- **Given** `settings.team_subagents["frontend_dev"].system_prompt` 非空
- **When** 执行 `_run_team_role_subtask`
- **Then** 走原 `build_custom_agent` 逻辑（不触发显式失败）

### Requirement: StateGraph 拓扑完整性

The new StateGraph MUST contain unified `execute` node replacing 6 legacy classification nodes.

#### Scenario: 拓扑节点完整

- **Given** 调用 `_build_team_graph()`
- **When** 检查 graph 节点
- **Then** 包含 `plan` / `dispatch` / `execute` / `barrier` / `aggregate` / `replan`
- **And** 不包含 `_deep_node` / `_code_node` / `_builtin_node` / `_team_role_node` / `_custom_node` / `_default_node`（旧节点已删除）

#### Scenario: 条件边路由 - barrier

- **Given** `pending_waves` 非空
- **When** `_route_after_barrier(state)` 被调用
- **Then** 返回 `"dispatch"`
- **Given** `pending_waves` 为空
- **When** `_route_after_barrier(state)` 被调用
- **Then** 返回 `"aggregate"`

#### Scenario: 条件边路由 - aggregate

- **Given** 质量门通过
- **When** `_route_after_aggregate(state)` 被调用
- **Then** 返回 END
- **Given** 质量门失败
- **When** `_route_after_aggregate(state)` 被调用
- **Then** 返回 `"replan"`

#### Scenario: 条件边路由 - replan

- **Given** `replan_node` 返回后 `pending_waves` 非空
- **When** `_route_after_replan(state)` 被调用
- **Then** 返回 `"plan"`
- **Given** `pending_waves` 为空
- **When** `_route_after_replan(state)` 被调用
- **Then** 返回 END

### Requirement: 配置项

New config fields MUST be configurable with sensible defaults.

#### Scenario: team_max_concurrency 默认值

- **Given** 未显式配置 `team_max_concurrency`
- **When** 读取 `settings.team_max_concurrency`
- **Then** 返回 `5`

#### Scenario: team_max_concurrency 范围校验

- **Given** 配置 `team_max_concurrency=20`
- **When** 加载 settings
- **Then** 校验通过（范围 1-20）
- **Given** 配置 `team_max_concurrency=0`
- **When** 加载 settings
- **Then** 校验失败

#### Scenario: team_max_replan_attempts 默认值

- **Given** 未显式配置 `team_max_replan_attempts`
- **When** 读取 `settings.team_max_replan_attempts`
- **Then** 返回 `1`

#### Scenario: team_max_replan_attempts 范围校验

- **Given** 配置 `team_max_replan_attempts=5`
- **When** 加载 settings
- **Then** 校验通过（范围 0-5）
- **Given** 配置 `team_max_replan_attempts=-1`
- **When** 加载 settings
- **Then** 校验失败

#### Scenario: team_max_retries 默认值

- **Given** 未显式配置 `team_max_retries`
- **When** 读取 `settings.team_max_retries`
- **Then** 返回 `2`

#### Scenario: team_max_retries 范围校验

- **Given** 配置 `team_max_retries=5`
- **When** 加载 settings
- **Then** 校验通过（范围 0-5）
- **Given** 配置 `team_max_retries=-1`
- **When** 加载 settings
- **Then** 校验失败

## Constraints

### 性能约束

- DAG 解析（`resolve_waves`）在 100 个子任务以内完成时间 < 10ms（Kahn 算法 O(V+E)）
- `DangerousTaskClassifier` LLM 路径超时阈值 `team_classifier_timeout=2.0s`，超时降级到关键词
- Semaphore 限流不引入额外开销（`asyncio.Semaphore` acquire/release 是微秒级）
- retry 退避总时间不超过 `team_max_retries * 4s = 8s`（max_retries=2 时）

### 安全约束

- 危险任务（删除文件、执行命令）必须经 `DangerousTaskClassifier` 判定后改写到 `deep` agent
- 危险任务改写必须发射 `warning` SSE 事件 + 写入 `state.warnings`，可观测
- `validate_team_subagents` 启动校验必须阻止缺配置的 team_role 静默运行
- abort cancel 必须释放 semaphore，避免资源泄漏导致后续子任务死锁

### 向后兼容

**项目约定无需向后兼容**，直接推倒重来：

- 旧 `_AGENT_PREFIX_RE` 正则解析保留作为 `with_structured_output` 不支持时的 fallback，不是兼容层
- 旧 6 个分类节点（`_deep_node` 等）直接删除，不保留 shim
- 旧 `TeamPlanTask` schema 直接替换为 `TeamTask`，不保留旧字段
- 旧 findings key 格式 `{agent}-{task_index}` 直接替换为 `{agent}:{task_id}:{wave_index}`，不保留兼容
- 旧 `_default_node` 硬错误行为直接替换为 fallback 到 code，不保留旧行为开关
- 旧 `_looks_like_dangerous_task` 子串匹配直接替换为 `DangerousTaskClassifier`，不保留旧实现

## Invariants

### I1: DAG 无环保证

`resolve_waves` 输出的 waves 中，任意 task 的 `depends_on` 引用必须出现在前置 wave 中
（或被丢弃的越界/自环边）。循环被强制打破后，被打破的 task 仍出现在某个 wave 中，
但其依赖 finding 可能缺失（由 `_inject_upstream_findings` 占位 warning 处理）。

### I2: findings key 唯一性

对任意 `(agent, task_id, wave_index)` 三元组，`findings` dict 中至多有一个对应 key。
同类型多任务（如两个 code 任务）通过不同 `task_id` 区分，不互相覆盖。

### I3: semaphore 不泄漏

`acquire_and_run` 在任意退出路径（成功 / 异常 / `CancelledError`）都必须释放 semaphore。
`async with semaphore` 语法保证这一点。

### I4: abort 不重试

`run_with_retry` 遇到 `CancelledError` 时直接 raise，不重试。
abort 是用户主动中止，重试无意义且会延长中止响应时间。

### I5: replan 单调有界

`replan_count` 单调递增，上限为 `team_max_replan_attempts`。
达到上限后 `replan_node` 不再调用 LLM，直接结束（失败）。

### I6: token 事件 schema 一致

所有 `execute` 节点 + `aggregate` 节点发射的 token 事件 payload 必须包含
`agent: str` 和 `content: str` 两个字段，无其他必填字段。

## 错误处理

### E1: LLM 不支持结构化输出

- **触发**：`chat_model.with_structured_output(TeamPlan)` 抛异常
- **处理**：启动时检测，记录 warning，走 `_parse_plan_from_text_fallback` 正则解析
- **可观测**：logger.warning 记录 LLM provider 名 + 异常信息

### E2: DAG 循环

- **触发**：`resolve_waves` 检测到入度为 0 的节点为空
- **处理**：强制打破循环（取入度最小者强加进当前 wave），logger.error 记录被打破的 task
- **可观测**：logger.error + `state.warnings` 记录

### E3: 依赖 finding 缺失

- **触发**：`_inject_upstream_findings` 找不到 `depends_on` 对应的 finding
- **处理**：跳过该依赖，logger.warning 记录，继续执行（input 不含该依赖段）
- **可观测**：logger.warning

### E4: 危险分类器 LLM 超时

- **触发**：`classifier.classify` 的 LLM 调用超过 `team_classifier_timeout=2.0s`
- **处理**：降级到 `_keyword_fallback` 关键词匹配
- **可观测**：logger.warning 记录超时

### E5: 未知 agent

- **触发**：`_NODE_DISPATCH.get(task.agent)` 返回 None
- **处理**：走 `_FALLBACK_CONFIG`（fallback 到 code runner），发射 `warning` SSE + 写入 `state.warnings`
- **可观测**：logger.warning + `warning` SSE + `state.warnings`

### E6: team_role 配置缺失

- **触发**：`_run_team_role_subtask` 中 `cfg.system_prompt` 为空
- **处理**：显式失败，返回 `TeamSubtaskResult(success=False, payload="团队角色 {agent} 配置缺失 system_prompt")`
- **可观测**：logger.error + `warning` SSE + `state.warnings`

### E7: 子任务重试耗尽

- **触发**：`run_with_retry` 重试 `team_max_retries` 次后仍失败
- **处理**：返回 `TeamSubtaskResult(success=False, retries=<max>, payload="重试 N 次后仍失败: <error>")`
- **可观测**：logger.warning + `result.retries` 字段

### E8: abort 中止

- **触发**：`abort_event.is_set()` 或 `task_obj.cancel()`
- **处理**：捕获 `CancelledError`，返回 `TeamSubtaskResult(success=False, payload="用户中止")`
- **可观测**：logger.info（非错误，用户主动行为）

### E9: replan 上限

- **触发**：`replan_count >= team_max_replan_attempts`
- **处理**：不调用 LLM，直接结束（失败），发射 `team_done` SSE 标记失败
- **可观测**：logger.info + `team_done` 事件含 `success=False`

## Observability

### O1: SSE 事件

v2 复用现有 SSE 事件类型，不新增：

- `team_init`：团队初始化（含 plan tasks 列表 + deps）
- `delegation`：子任务派发（含 agent / task_id / wave_index）
- `todo_update`：todo 状态更新
- `token`：流式 token（统一 schema `{agent, content}`）
- `warning`：警告事件（危险任务改写 / 未知 agent fallback / team_role 缺配置）
- `replan`：重规划事件（含 new_tasks + replan_count + reason）
- `team_done`：团队完成（含 success 标志）

### O2: 日志

所有错误路径必须有 logger 记录（不允许静默吞异常）：

- `logger.warning`：fallback / 降级 / 容错路径
- `logger.error`：DAG 循环 / team_role 配置缺失 / 严重错误
- `logger.info`：abort / replan / retry 等可观测的正常路径

### O3: state.warnings channel

`TeamState.warnings: list[str]` 字段收集所有 warning 消息，`aggregate_node` 把 warnings
一并发射 SSE，便于前端展示与排查。

### O4: result.retries 字段

`TeamSubtaskResult.retries: int` 字段记录实际重试次数，写入 `Finding.retries`，
前端可展示「该子任务重试了 N 次才成功」。
