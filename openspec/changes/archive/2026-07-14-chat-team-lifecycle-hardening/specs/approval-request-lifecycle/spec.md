# Spec: Approval Request Lifecycle

## Purpose

为聊天与 Team 执行中的危险操作审批建立显式、可恢复、不可串用的请求生命周期，避免 stale approval、
预提交 approval、错误 `full_trust` 放开、刷新后 pending 状态漂移。

## Requirements

### REQ-APR-1: 审批请求必须拥有独立身份

每个 `approval_request` 事件必须携带独立 `approval_id`，并绑定：

- `thread_id`
- `run_id`
- `request_kind`
- `tool_call_id`（如适用）
- `created_at`
- `expires_at`

`approval_id` 是后续 `submit_approval` 的主键；`thread_id` 不得单独作为授权判断依据。

#### Scenario: 同线程前后两个审批请求不可复用同一决定

- **Given** 线程 `t1` 上先后产生两个审批请求 `a1` 与 `a2`
- **When** 用户提交对 `a1` 的决定后，第二个请求 `a2` 开始等待
- **Then** `a1` 的决定不得自动作用于 `a2`
- **And** `a2` 必须等待新的 `approval_id`

### REQ-APR-2: 审批提交必须 compare-and-consume 活跃请求

后端在处理审批提交时，必须命中一个“仍活跃、未过期、未消费、run 匹配”的审批请求，然后原子地消费它。

#### Scenario: stale approval 被拒绝

- **Given** 审批请求 `a1` 已过期或已被消费
- **When** 客户端再次提交 `approval_id=a1`
- **Then** 后端拒绝该提交
- **And** 不改变当前线程的审批状态

#### Scenario: pre-approval 被拒绝

- **Given** 当前线程尚无活跃审批请求
- **When** 客户端仅凭 `thread_id` 或伪造的 `approval_id` 提交批准
- **Then** 后端拒绝该提交
- **And** 不开启 `full_trust`

### REQ-APR-3: full_trust 只能由匹配活跃请求的决定开启

`full_trust` 只能在一个仍活跃的审批请求被明确允许后开启，且该开启必须绑定到当前 run/session。

#### Scenario: 旧 run 的审批决定不能放开新 run

- **Given** 线程 `t1` 的旧 run `r1` 发起审批请求 `a1`
- **And** 在 `a1` 被处理前，旧 run 已结束，新 run `r2` 已开始
- **When** 客户端再提交对 `a1` 的批准
- **Then** 后端拒绝该批准
- **And** `r2` 不得获得 `full_trust`

### REQ-APR-4: 前端 pending approval 恢复必须基于活跃请求

前端刷新、断线重连、恢复 polling 时，pending approval 的恢复源必须是“活跃审批请求注册表”，而不是历史上的“已提交决定”。

#### Scenario: 刷新后仍显示当前待审批请求

- **Given** 线程 `t1` 上存在一个活跃审批请求 `a1`
- **When** 用户刷新页面并重新进入该线程
- **Then** 前端能够恢复 `a1` 的待审批状态
- **And** 不会错误显示已经提交过但已失效的历史请求

### REQ-APR-5: 审批请求必须有 TTL 与清理语义

活跃审批请求必须带 TTL；过期后要被清理，并在恢复接口中表现为“无活跃审批”。

#### Scenario: 过期审批不再被恢复

- **Given** 审批请求 `a1` 超过 TTL
- **When** 前端查询线程 `t1` 的 pending approval
- **Then** 返回“无活跃审批”
- **And** 用户再提交 `a1` 的决定会被拒绝
