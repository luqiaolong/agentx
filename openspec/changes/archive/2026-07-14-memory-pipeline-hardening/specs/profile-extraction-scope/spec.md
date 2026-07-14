# Spec: Profile Extraction Scope

## Purpose

定义自动画像抽取队列、抽取失败语义和记忆作用域分类，防止 LLM 失败被静默丢弃，以及用户长期偏好被错误写成 workspace-local 记忆。

## ADDED Requirements

### Requirement: 抽取失败必须与成功空结果区分

The system MUST satisfy this requirement.

`extract_profile_via_llm` 必须能表达 `failed` 与 `success_empty` 两种结果。异常不得被吞成 `[]` 后让队列删除任务。

#### Scenario: LLM 超时不会删除任务

- **Given** 队列中存在抽取任务 `j1`
- **When** LLM 调用超时
- **Then** `j1` 不会被标记为成功
- **And** `j1.attempts` 增加
- **And** `j1.last_error` 记录超时信息

### Requirement: 队列任务必须具备 retry/dead-letter 状态

The system MUST satisfy this requirement.

`profile_extract_queue` 必须持久化任务状态、重试次数、最近错误和 lease 信息，避免失败静默丢失或重复消费。

#### Scenario: 多次失败进入死信

- **Given** 抽取任务 `j1` 连续失败达到最大次数
- **When** worker 再次处理失败
- **Then** `j1.status="dead_letter"`
- **And** worker 不再自动重试
- **And** 管理/日志路径可看到失败原因

### Requirement: worker claim 必须原子化

The system MUST satisfy this requirement.

worker 读取任务时必须以原子 claim/lease 标记所有权，shutdown drain 不得处理仍被其他 worker lease 的任务。

#### Scenario: drain 不重复消费活跃任务

- **Given** worker A 已 lease 任务 `j1`
- **When** shutdown drain 启动
- **Then** drain 不会处理 `j1`
- **And** `j1` 最多被一个执行者消费

### Requirement: 自动作用域必须由分类结果决定

The system MUST satisfy this requirement.

系统不得仅凭 `workspace_path` 是否存在决定写 global 还是 workspace。抽取结果必须包含 `scope`、`confidence`、`sensitivity`。

#### Scenario: 工作区内表达长期用户偏好仍写 global

- **Given** 用户在某 workspace 对话中说“我以后都喜欢中文回复”
- **When** 自动抽取该信息
- **Then** 分类为 `category="preference"`
- **And** `scope="global"`
- **And** 不因为当前有 workspace 而只写入 workspace memory

### Requirement: 项目知识必须写 workspace

The system MUST satisfy this requirement.

与当前项目结构、命令、约定、任务背景相关的记忆必须写入 workspace scope，不得污染 global profile。

#### Scenario: 项目启动命令写入 workspace

- **Given** 用户在 workspace 中说明“本项目必须用 scripts/start.ps1 启动”
- **When** 自动抽取该信息
- **Then** 分类为 `category="project"`
- **And** `scope="workspace"`
- **And** 写入 `<workspace>/.agentx/memory/`

### Requirement: 三条主执行链必须一致处理抽取

The system MUST satisfy this requirement.

`work`、`coding`、`coding_team` 对话完成后必须一致触发自动抽取，或在配置中显式声明某条链路不抽取。

#### Scenario: coding_team 产出也可抽取项目记忆

- **Given** 用户在 `coding_team` 模式中确认一条项目约定
- **When** team run 完成
- **Then** 自动抽取队列收到对应任务
- **Or** 系统有明确配置说明 team mode 不做抽取并记录原因


