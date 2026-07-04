## ADDED Requirements

### Requirement: 多会话 store 结构

`chat.ts` store SHALL 采用 `sessions: Record<string, Session>` + `currentId: string | null` 结构，每个 `Session` 含 `id` / `title` / `messages: ChatMessage[]` / `createdAt`。`threadId` 字段废弃，由 `currentId` 替代。

#### Scenario: 新建会话
- **WHEN** 用户点击"新建会话"
- **THEN** 生成 UUID，`sessions[uuid] = {id: uuid, title: "新会话", messages: [], createdAt: Date.now()}`，`currentId = uuid`

#### Scenario: 切换会话
- **WHEN** 用户点击会话列表中的会话 B
- **THEN** `currentId = B.id`，ChatView 渲染 `sessions[B.id].messages`

#### Scenario: 删除会话
- **WHEN** 用户点击会话 A 的"删除"
- **THEN** 调 `window.api.chat.send({role: "user", content: "/reset"}, {threadId: A.id})` 清后端 checkpoint，`delete sessions[A.id]`，若 `currentId === A.id` 则切到首个剩余会话或 null

### Requirement: 会话标题自动生成

系统 SHALL 在会话首条用户消息发送后，取消息前 20 字符作为会话标题（截断加 `...`）。

#### Scenario: 首条消息设置标题
- **WHEN** 会话标题为"新会话"且用户发送首条消息 `帮我分析这个 PDF 文件的内容`
- **THEN** 会话标题变为 `帮我分析这个 PDF 文件...`（前 20 字符 + `...`）

#### Scenario: 后续消息不更新标题
- **WHEN** 会话已有非"新会话"标题且用户发送第 2 条消息
- **THEN** 会话标题不变

### Requirement: 会话列表组件

Renderer SHALL 在左侧栏渲染 `SessionList` 组件，展示所有会话（按 `createdAt` 降序），每项显示标题 + 创建时间。支持点击切换、右键/按钮删除、"新建会话"按钮。

#### Scenario: 渲染会话列表
- **WHEN** 左侧栏挂载且 `sessions` 含 3 个会话
- **THEN** 按创建时间降序渲染 3 个会话项 + 顶部"新建会话"按钮

#### Scenario: 当前会话高亮
- **WHEN** `currentId` 指向会话 B
- **THEN** 会话 B 项高亮显示

### Requirement: localStorage 持久化与迁移

store SHALL 持久化整个 `sessions` + `currentId` 到 localStorage（key `agent-py-chat`）。启动时检测旧结构（含 `threadId` + `messages` 但无 `sessions`）并自动迁移为新结构：将旧 `messages` 作为单个会话 `sessions[oldThreadId]`。

#### Scenario: 持久化
- **WHEN** store 状态变更（新建/删除/消息追加）
- **THEN** `sessions` + `currentId` 写入 localStorage

#### Scenario: 旧结构迁移
- **WHEN** 启动时 localStorage `agent-py-chat` 含 `{threadId: "abc", messages: [...]}` 但无 `sessions`
- **THEN** 自动迁移为 `sessions: {abc: {id: "abc", title: "迁移会话", messages: [...], createdAt: now}}`，`currentId: "abc"`，旧字段清除

#### Scenario: 容量超限
- **WHEN** localStorage 写入超 10MB
- **THEN** 捕获 `QuotaExceededError`，提示用户"会话存储已满，请删除旧会话"，不崩溃

### Requirement: /reset 命令保持当前会话

`/reset` 命令 SHALL 清空后端 checkpoint 但保留会话在前端列表中（消息清空，会话不删除）。

#### Scenario: /reset 清空消息
- **WHEN** 用户在会话 A 中输入 `/reset`
- **THEN** 调后端清 checkpoint，`sessions[A.id].messages = []`，会话 A 仍在列表中，`currentId` 不变
