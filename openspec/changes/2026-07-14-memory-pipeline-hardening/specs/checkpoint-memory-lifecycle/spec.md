# Spec: Checkpoint Memory Lifecycle

## Purpose

定义聊天历史、压缩、回退、重发和 reset 的 checkpoint 语义，确保会话记忆不被错误 parent、namespace、rowid 推断或前端竞态破坏。

## ADDED Requirements

### Requirement: compact 必须遵守 LangGraph saver config 语义

The system MUST satisfy this requirement.

后端 `/api/chat/compact` 与 CLI `/compact` 写 checkpoint 时必须提供正确 `thread_id`、`checkpoint_ns`、parent config 和 channel versions。

#### Scenario: compact 写入具备合法 namespace

- **Given** 线程 `t1` 已有可压缩 checkpoint
- **When** 用户调用 compact
- **Then** 新 checkpoint config 包含 `configurable.thread_id="t1"`
- **And** 包含 `configurable.checkpoint_ns`
- **And** parent 指向旧 checkpoint config
- **And** saver 不会创建 self-parent checkpoint

### Requirement: compact 不得与活跃 run 并发写 checkpoint

The system MUST satisfy this requirement.

同一 thread 上存在 live run 时，compact 必须拒绝、排队或等待稳定快照，不得与 streaming run 同时写 saver。

#### Scenario: 活跃聊天期间 compact 不写入

- **Given** 线程 `t1` 正在流式运行
- **When** 客户端调用 `/api/chat/compact`
- **Then** 后端不会写入新 checkpoint
- **And** 返回可解释的 busy/queued 状态

### Requirement: 历史回退必须基于真实 message/checkpoint id

The system MUST satisfy this requirement.

系统不得用“UI 消息数量 * 固定 checkpoint 数”推断回退点。回退必须绑定真实 message id、checkpoint id 或 LangGraph time-travel 分支点。

#### Scenario: 编辑中间消息回退到正确分支

- **Given** 线程 `t1` 有 6 条 UI 消息
- **And** 用户编辑第 3 条用户消息
- **When** 前端请求回退
- **Then** 请求包含明确 message/checkpoint 标识
- **And** 后端回退到该消息之前的真实 checkpoint
- **And** 不依赖固定 checkpoint 数量假设

### Requirement: writes 与 checkpoints 不得用跨表 rowid 比较删除

The system MUST satisfy this requirement.

`writes.rowid` 与 `checkpoints.rowid` 没有可比较语义。删除 writes 必须按 checkpoint id、thread id、namespace 或 saver 官方 API 关联。

#### Scenario: 删除后 writes/checkpoints 保持一致

- **Given** 某 thread 的 checkpoints 与 writes 插入顺序不同
- **When** 用户回退历史
- **Then** 后端不会执行 `writes.rowid > checkpoint.rowid` 类删除
- **And** 剩余 writes 与剩余 checkpoint 集合一致

### Requirement: 前端 resend 必须等待后端 rewind/branch 成功

The system MUST satisfy this requirement.

前端删除消息后立即重发会造成 UI 与 checkpoint 竞态。必须等待后端确认回退或分支成功后，才能发送新消息。

#### Scenario: rewind 失败时不 resend

- **Given** 用户点击某条历史消息重新发送
- **And** 后端 rewind 返回失败
- **When** 前端处理该失败
- **Then** 不会发起新的 chat stream
- **And** UI 显示可恢复错误

### Requirement: reset 子线程清理必须使用 saver 官方接口

The system MUST satisfy this requirement.

reset 清理 child threads 时，优先使用 `adelete_thread`。需要枚举时必须按 saver API 要求传入 config，并正确读取 `CheckpointTuple.config`。

#### Scenario: reset 可以清理 prefixed child threads

- **Given** 主 thread `t1` 有多个 child thread checkpoint
- **When** 用户 reset `t1`
- **Then** 后端能枚举并删除 child thread
- **And** 不因 `alist()` 参数缺失或 tuple 解构错误失败


