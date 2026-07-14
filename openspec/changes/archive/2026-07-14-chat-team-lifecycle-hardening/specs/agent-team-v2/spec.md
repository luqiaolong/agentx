# Spec Delta: agent-team-v2

## Purpose

补齐 `agent-team-v2` 在结果语义、事件关联、prompt/model 透传、危险任务分类和 task id 唯一性上的实现缺口，
让 Team 路径与既有 capability 契约一致。

## Delta Requirements

### REQ-TEAM-OUTCOME-1: Team 最终结果必须使用 typed outcome

Team 最终结果必须显式输出 `outcome`，取值为：

- `success`
- `partial`
- `error`
- `aborted`

不得再用“存在 findings”或“transport 收到 done”推断成功。

#### Scenario: 全失败不能标记 success

- **Given** 一个 Team run 中所有子任务都失败
- **When** 聚合器完成最终判定
- **Then** `team_done.outcome` 必须为 `error` 或 `aborted`
- **And** 不得因为存在失败 finding 文本而标记为成功

#### Scenario: 部分成功需要 partial

- **Given** Team run 中存在成功子任务，也存在失败子任务
- **And** 系统仍能给出带风险标记的总结
- **When** 生成最终 Team 结果
- **Then** `team_done.outcome` 必须为 `partial`

### REQ-TEAM-OUTCOME-2: 聚合异常与持久化必须使用同一 outcome

聚合器异常、黑板构建异常、上游 abort、部分完成持久化都必须显式写入同一个 outcome，不得出现“历史里是成功、SSE 里是失败”或相反。

#### Scenario: 聚合异常不会被落成 done

- **Given** Team 子任务执行结束，但 aggregator 抛出异常
- **When** 系统写入 Team 历史并发出 `team_done`
- **Then** 历史记录与 `team_done.outcome` 都必须是 `error`

### REQ-TEAM-CORRELATION-1: 重复角色与 replan 必须以 task_id 关联

Team 事件与前端渲染必须以稳定 `task_id` 为主键，而不是角色名。

#### Scenario: 两个相同角色任务不会互相覆盖

- **Given** 同一轮 Team 计划里有两个 `frontend_dev` 子任务，`task_id` 分别为 `f1`、`f2`
- **When** 两个任务分别发出 delegation、summary、final update
- **Then** 前端能分别将它们关联到 `f1` 与 `f2`
- **And** 不会因为角色名相同而覆盖同一行

#### Scenario: replan 新任务不会占用旧任务行

- **Given** replan 之后新增一个 `backend_dev` 任务 `b3`
- **When** `b3` 开始执行并发出事件
- **Then** 前端创建或更新 `task_id=b3` 的行
- **And** 不会误写到历史 `backend_dev` 任务上

### REQ-TEAM-PLANNER-1: 初始计划与 replan 都必须保证 task_id 唯一

planner 在首次拆分任务和后续 replan 时都必须保证 `task_id` 在该 Team run 内唯一。

#### Scenario: 初始计划不产生重复 task_id

- **Given** planner 为一个复杂需求生成多个子任务
- **When** 系统校验初始 plan
- **Then** 所有 `task_id` 必须唯一

### REQ-TEAM-SAFETY-1: DangerousTaskClassifier 必须进入执行主链

危险任务分类不能只是定义存在；它必须在执行前真正参与 Team 路由决策。

#### Scenario: 危险写操作在 Team 中被分类和改写

- **Given** planner 产出一个会删除文件或执行危险 shell 的 Team task
- **When** 系统准备执行该 task
- **Then** `DangerousTaskClassifier` 必须在执行前运行
- **And** 分类结果必须决定该 task 是升级、改写、阻断还是允许继续

### REQ-TEAM-PROMPT-1: Team role 必须继承 chat model 与 prompt 上下文

Team role 执行必须继承：

- chat model
- project prompt
- profile prompt
- request/system prompt

#### Scenario: role agent 继承用户请求级 system prompt

- **Given** 用户请求携带自定义 `system_prompt`
- **And** Team planner 将任务分配给 `frontend_dev`
- **When** `frontend_dev` 执行子任务
- **Then** 它收到的 prompt 上下文中必须包含该 `system_prompt`

#### Scenario: role agent 使用注入的 chat model

- **Given** 当前线程配置了特定 chat model
- **When** Team role 被调度执行
- **Then** 该 role 使用的模型必须与线程注入一致
