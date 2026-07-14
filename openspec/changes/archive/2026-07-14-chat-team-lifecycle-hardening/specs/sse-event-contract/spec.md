# Spec: SSE Event Contract

## Purpose

重新对齐聊天与 Team 路径的 SSE 事件契约，解决事件类型漂移、字段缺失、终态覆写和解析失真的问题。

## Requirements

### REQ-SSE-1: 共享类型必须覆盖实际运行事件

共享类型与文档必须显式覆盖以下事件及字段：

- `approval_request`
- `sandbox_escalation`
- `warning`
- `replan`
- `team_done`
- `done`

#### Scenario: warning/replan 不再是 undocumented runtime event

- **Given** Team 执行期间发出 `warning` 或 `replan`
- **When** 前端解析该事件
- **Then** 共享类型系统中存在对应定义
- **And** 文档中有字段说明

### REQ-SSE-2: sandbox_escalation 必须端到端保真

`sandbox_escalation` 事件在前端解析时必须保持原始类型，不得被强制映射成 `dangerous_tool`。

#### Scenario: sandbox_escalation 类型保持不变

- **Given** 后端发出 `sandbox_escalation`
- **When** 前端 parser 处理该事件
- **Then** 解析结果的事件类型仍然是 `sandbox_escalation`

### REQ-SSE-3: Team 相关事件必须携带稳定关联字段

涉及 Team 子任务状态的事件必须携带稳定关联字段，至少包含：

- `task_id`
- `run_id`
- `role`

`task_id` 是前端渲染和 reducer 的主关联键。

#### Scenario: final summary 也能回链到 task_id

- **Given** 一个 Team task `task_id="f1"` 已完成
- **When** 后端发出该任务的最终 summary / delegation / final card payload
- **Then** payload 中包含 `task_id="f1"`

### REQ-SSE-4: team_done 必须与 transport done 解耦

`team_done` 表示 Team 业务终态，`done` 表示 transport 终态。前端不得用后到的 `done` 覆盖先到的 `team_done.outcome`。

兼容策略：

- `team_done` 新增 `outcome`
- `done` 新增可选 `reason`

#### Scenario: team_done:error 不会被 done 覆写成完成

- **Given** 前端先收到 `team_done{outcome:"error"}`
- **And** 随后收到 transport `done{reason:"error"}`
- **When** reducer 更新 Team 状态
- **Then** Team 最终状态仍然是 error

### REQ-SSE-5: team_done payload 的 blackboard 必须有共享类型

`team_done.blackboard` 必须在共享类型中有明确结构，而不是仅在某个前端组件中隐式假设字段存在。

#### Scenario: blackboard 字段有共享定义

- **Given** `team_done` payload 包含 `blackboard.findings` 与 `blackboard.errors`
- **When** 前后端编译/校验共享类型
- **Then** `blackboard` 结构有统一定义

### REQ-SSE-6: done 事件必须允许表达非成功收尾

`done` 不得默认等价于成功完成；它必须允许表达 `completed`、`aborted`、`recovered`、`error` 等 transport/terminal reason。

#### Scenario: abort 使用 done.reason 表达终态

- **Given** 用户中止了一个 run
- **When** 后端发出最终 `done` 事件
- **Then** `done.reason` 为 `aborted`
- **And** 前端 reducer 使用该 reason 完成清理，而不是标记成功
