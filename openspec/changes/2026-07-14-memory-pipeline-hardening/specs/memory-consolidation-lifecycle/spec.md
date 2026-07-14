# Spec: Memory Consolidation Lifecycle

## Purpose

定义 Dream/记忆整理链路的安全生命周期，防止 LLM 漏项、无效 target、导入错误或半写入导致记忆误删、跨作用域污染或后台任务崩溃。

## ADDED Requirements

### Requirement: Dream 默认不得自动运行不安全整理

The system MUST satisfy this requirement.

在 Dream 具备 schema 校验、diff preview、显式删除、事务化应用和回滚之前，系统必须默认关闭自动 Dream 整理。

#### Scenario: 默认配置不会启动 Dream

- **Given** 用户未显式开启 Dream
- **When** 后端启动并加载 settings
- **Then** Dream 整理任务不会自动修改任何 global 或 workspace 记忆

### Requirement: Dream 输出必须使用严格 patch schema

The system MUST satisfy this requirement.

Dream 不得接收任意字符串 `target` 或把 LLM 输出当作完整最终状态。输出必须表达为受控 patch，且 scope/operation 使用枚举。

#### Scenario: 无效 target 被拒绝

- **Given** 当前存在 global 与 workspace 记忆
- **When** Dream LLM 返回 `target="unknown"` 的条目
- **Then** 系统拒绝应用本次整理
- **And** 原有记忆保持不变
- **And** 日志记录 schema validation error

### Requirement: 删除必须显式且可预览

The system MUST satisfy this requirement.

LLM 没有返回某条已有记忆，不得被解释为删除。删除只能来自显式 `operation="delete"` patch，并在应用前出现在 preview diff 中。

#### Scenario: LLM 漏项不会删除记忆

- **Given** 当前 global 记忆包含 `user_pref_editor`
- **When** Dream 输出中没有该 key
- **Then** `user_pref_editor` 仍然保留
- **And** preview diff 不包含删除该 key 的操作

### Requirement: Dream 应用必须具备 snapshot/rollback

The system MUST satisfy this requirement.

Dream 在写入、移动、删除任一条记忆前必须创建 snapshot。任一操作失败时必须回滚到整理前状态。

#### Scenario: workspace 写入失败后全局记忆也回滚

- **Given** Dream patch 同时修改 global 记忆与 workspace 记忆
- **And** workspace 写入发生异常
- **When** Dream 应用 patch
- **Then** global 修改也不会保留
- **And** 最终状态等于应用前 snapshot

### Requirement: Dream 必须复用 store API 并正确 await

The system MUST satisfy this requirement.

Dream 对 global/workspace 的 upsert/delete 必须复用 `profile_store` / `memory_store` 的并发安全 API。所有 async 删除/写入必须被 `await`。

#### Scenario: global 删除完成后才返回成功

- **Given** Dream patch 显式删除 global key `old_pref`
- **When** Dream 返回成功
- **Then** `old_pref` 已从 global store 中删除
- **And** 不存在未 awaited coroutine warning

### Requirement: Dream 结果必须可审计

The system MUST satisfy this requirement.

每次 Dream 尝试必须记录输入摘要、preview diff、应用结果、失败原因和回滚状态。

#### Scenario: Dream 校验失败可定位原因

- **Given** Dream LLM 返回重复 key 的冲突 patch
- **When** 系统拒绝应用
- **Then** audit log 包含 change id、冲突 key、失败阶段
- **And** 不记录为成功整理


