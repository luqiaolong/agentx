# Spec: Memory Safety Contract

## Purpose

定义记忆读写、注入、缓存刷新和前端 API 的安全一致性契约，避免未授权 workspace_path、敏感信息入库、profile prompt 漏注入和过量/陈旧记忆进入上下文。

## ADDED Requirements

### Requirement: workspace_path 必须授权后才能用于记忆读写

The system MUST satisfy this requirement.

所有接受 `workspace_path` 的 memory/profile API 必须验证路径属于当前会话授权 workspace 或 sandbox root。

#### Scenario: 未授权路径被拒绝

- **Given** 当前会话只授权 workspace `D:/project/a`
- **When** 客户端请求写入 `D:/project/b/.agentx/memory`
- **Then** 后端拒绝请求
- **And** 不创建或修改任何文件

### Requirement: 入库前必须过滤 secrets 与凭证

The system MUST satisfy this requirement.

记忆存储前必须检测 API key、token、password、private key、带凭证连接串等敏感内容。高置信 secret 不得持久化为普通记忆。

#### Scenario: API key 不进入 prompt memory

- **Given** 用户消息包含疑似 API key
- **When** 自动抽取器生成记忆候选
- **Then** store 层拒绝或脱敏该内容
- **And** 该内容不会出现在后续 `profile_prompt`

### Requirement: 敏感级别必须影响注入

The system MUST satisfy this requirement.

记忆条目必须支持 `sensitivity` 或等价字段。标记为 private/secret 的条目默认不注入 LLM prompt，除非后续有明确授权策略。

#### Scenario: private 记忆可存储但不注入

- **Given** 一条记忆标记 `sensitivity="private"`
- **When** 构建 `profile_prompt`
- **Then** 默认输出不包含该记忆正文
- **And** audit/debug 信息能解释其被跳过

### Requirement: profile_prompt 必须覆盖所有执行链

The system MUST satisfy this requirement.

Work、Coding、Coding Team planner、Team role subtask、Team aggregator 必须一致接收 profile prompt。不得出现参数已传入但执行器忽略的链路。

#### Scenario: Team role 能看到用户偏好

- **Given** global profile 中有“用户偏好中文回复”
- **When** `coding_team` 分派 role subtask
- **Then** role prompt/context 包含该 profile prompt
- **And** aggregator 也能看到相同 profile context

### Requirement: DeepAgents memory 缓存必须感知 workspace 变更

The system MUST satisfy this requirement.

当 `.agentx/memory/*.md`、`.agentx/AGENTS.md` 或 `.agentx/rules/*.md` 在 workspace 中变更时，同一 thread 的后续运行必须能重新加载变更后的内容。

#### Scenario: 修改 workspace memory 后下一轮生效

- **Given** thread `t1` 已加载 workspace memory
- **And** 用户修改 `.agentx/memory/project.md`
- **When** 用户在 `t1` 发起下一轮对话
- **Then** agent 能看到更新后的 memory 内容
- **And** 不继续使用旧的 `memory_contents` 缓存

### Requirement: 前端 memory API 必须检查 HTTP 错误

The system MUST satisfy this requirement.

前端所有 memory/profile API 调用必须统一执行 `assertOk` 或等价错误处理，禁止失败响应被当成功渲染。

#### Scenario: 保存失败能反馈给用户

- **Given** 后端返回 403 未授权 workspace
- **When** 前端调用保存项目记忆
- **Then** promise reject 或 store 进入 error 状态
- **And** UI 不显示保存成功

### Requirement: 无 workspace 时不得写 project memory

The system MUST satisfy this requirement.

project/workspace 记忆必须绑定真实 workspace。无 workspace 时，前端和后端都不得把 project 条目写进 global profile。

#### Scenario: 无 workspace 写项目记忆被拒绝

- **Given** 当前会话没有 workspace
- **When** 用户尝试新增 `category="project"` 的记忆
- **Then** 后端拒绝该写入
- **And** global profile 不新增 project 条目

### Requirement: 记忆注入应限制为核心记忆 + 相关检索

The system MUST satisfy this requirement.

系统不得长期把所有记忆无差别注入 prompt。应优先注入 pinned/core 记忆，并利用 `keywords`、`scenarios`、embedding 相似度检索当前请求相关 Top-K。

#### Scenario: 无关项目记忆不进入当前 prompt

- **Given** workspace 有大量历史项目记忆
- **When** 用户询问与其中大部分无关的问题
- **Then** prompt 只包含核心记忆和 Top-K 相关记忆
- **And** 无关记忆不会占用上下文窗口


