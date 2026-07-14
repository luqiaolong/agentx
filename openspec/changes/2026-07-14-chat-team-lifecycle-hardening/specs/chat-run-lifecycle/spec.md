# Spec: Chat Run Lifecycle

## Purpose

为聊天主链定义统一的 run/session 生命周期，解决 stream cleanup race、abort 无终态、
`compact` 与活跃流竞争、多 session 串线，以及 `system_prompt` 只进 API 不进执行器的问题。

## Requirements

### REQ-CHAT-1: 每个线程同一时刻最多一个 live run

系统必须为每个 `thread_id` 维护一个 live run lifecycle 对象，至少包含 `run_id`、generation、run state、
cleanup 状态和活跃连接所有权。

#### Scenario: 新 run 必须等待旧 run cleanup 完成

- **Given** 线程 `t1` 上已有 live run `r1`
- **And** `r1` 已停止产出 token，但 cleanup 尚未完成
- **When** 用户再次向 `t1` 发送消息，创建候选 run `r2`
- **Then** `r2` 不能在 `r1.cleanup_complete=true` 之前开始执行

### REQ-CHAT-2: stream lock 必须在 cleanup 完成后释放

后端不得先释放 stream lock、再清理 abort/pause/approval/checkpoint 相关状态。

#### Scenario: 旧 run 清理不会误伤新 run

- **Given** 线程 `t1` 上的 run `r1` 结束并开始 cleanup
- **And** 新 run `r2` 在排队等待
- **When** cleanup 逻辑执行
- **Then** cleanup 只能作用于 `r1`
- **And** 不得清掉 `r2` 的 abort/pending approval/connection 状态

### REQ-CHAT-3: 每个 run 必须产生唯一终态

无论 run 正常完成、用户 abort、审批超时、异常中断或恢复完成，都必须向前端发出唯一 terminal signal。

兼容要求：

- `done` 事件新增可选 `reason`
- `reason` 取值至少包含 `completed`、`aborted`、`recovered`、`error`

#### Scenario: 用户 abort 也能稳定收尾

- **Given** 线程 `t1` 上的 run `r1` 正在流式输出
- **When** 用户发出 abort
- **Then** 前端最终能收到一次 terminal signal
- **And** 该 terminal signal 明确标记 `reason="aborted"`

### REQ-CHAT-4: compact/reset 必须与 live run 互斥

`/api/chat/compact` 与同线程 live run 不得并发写同一份 checkpoint；`reset` 也必须遵守同样的互斥规则。

#### Scenario: 活跃流期间 compact 被拒绝或排队

- **Given** 线程 `t1` 上存在 live run `r1`
- **When** 客户端对 `t1` 发起 `compact`
- **Then** 后端要么拒绝该请求，要么在 `r1` 结束后基于稳定快照执行
- **And** 不得与 `r1` 的 checkpoint 写入并发交错

### REQ-CHAT-5: 前端流状态必须按 thread_id 隔离

前端连接对象、事件回调、pending message、running state、approval state 必须以 `thread_id` 为主键管理，而不是共享 singleton ref。

#### Scenario: 后台线程的流事件不会丢失

- **Given** 线程 `t1` 在后台继续流式执行
- **And** 用户切换到前台线程 `t2`
- **When** `t1` 后续收到 token / tool / terminal 事件
- **Then** 这些事件仍然写回 `t1`
- **And** 不会被 `t2` 的连接状态覆盖

### REQ-CHAT-6: system_prompt 必须进入实际执行链

API 层接收到的 `system_prompt` 不得被静默忽略；它必须进入 router，并被传给最终执行器。

#### Scenario: 请求级 system_prompt 可影响执行

- **Given** 客户端向线程 `t1` 发起请求，并带 `system_prompt="只用英文回答"`
- **When** router 将请求分发到对应执行器
- **Then** 执行器能收到该 `system_prompt`
- **And** 该提示会影响最终生成

### REQ-CHAT-7: 恢复逻辑必须绑定 run_id 而不是仅 trace/thread

前端恢复轮询与后端结果查询必须优先绑定到具体 `run_id`；若同线程已开始新 run，旧 run 恢复不得覆盖新 run UI。

#### Scenario: 旧 run 的恢复结果不会覆盖新 run

- **Given** 线程 `t1` 的旧 run `r1` 断流后进入恢复轮询
- **And** 用户随后又在 `t1` 上启动新 run `r2`
- **When** `r1` 的恢复结果晚到
- **Then** 前端不会把 `r1` 的终态写到 `r2` 的 pending message 上
