## ADDED Requirements

### Requirement: 后端历史消息加载与注入

`backend/app/router/graph.py::run_router` SHALL 在分发前从 SQLite checkpointer 加载 `thread_id` 对应的历史 `messages`，把当前用户消息 append 到历史末尾，再将完整 `messages` 列表传给三条路径。三条路径（`_run_chat_path` / `_run_tool_path` / `_run_deep_path`）SHALL 接收 `history: list` 参数，拼到 LLM 输入的 `messages` 前面，而非只传当前单条消息。

#### Scenario: 多轮对话历史注入

- **WHEN** thread_id "abc" 的 checkpoint 含历史 `[{role: "user", content: "我叫张三"}, {role: "assistant", content: "你好张三"}]`，用户发送"我叫什么"
- **THEN** 路径 A 传给 LLM 的 messages 为 `[SystemMessage, HumanMessage("我叫张三"), AIMessage("你好张三"), HumanMessage("我叫什么")]`，LLM 能正确回答"张三"

#### Scenario: 首次会话无历史

- **WHEN** thread_id "xyz" 的 checkpoint 不存在或 `messages` 为空
- **THEN** 传给 LLM 的 messages 仅含 `SystemMessage + 当前 HumanMessage`，行为与改造前一致

### Requirement: Router 主图接入 checkpointer

`backend/app/main.py::_event_generator` SHALL 在调用 `run_router` 前构建带 `AsyncSqliteSaver` checkpointer 的 Router 图，使 `state["messages"]` 随 checkpoint 持久化。`run_router` SHALL 接收 `checkpointer` 参数并传给 `build_router_graph`。

#### Scenario: 消息持久化到 checkpoint

- **WHEN** 用户在 thread_id "abc" 发送"你好"，路径 A 返回"你好，有什么可以帮你"
- **THEN** SQLite checkpoint 表中 thread_id "abc" 的 `messages` 含 `[{role: "user", content: "你好"}, {role: "assistant", content: "你好，有什么可以帮你"}]`

#### Scenario: 跨轮次恢复

- **WHEN** 用户在 thread_id "abc" 第二轮发送"刚才我说了什么"
- **THEN** `run_router` 从 checkpoint 加载到第一轮的 user + assistant 消息，LLM 能正确引用第一轮内容

### Requirement: DeepAgent 共享 SQLite checkpointer

`backend/app/paths/deep_path.py::build_deep_agent` SHALL 使用与 Router 共享的 `AsyncSqliteSaver` 实例（通过 `get_async_checkpointer()` 获取），替换每次新建的 `MemorySaver`。`run_deep_path` SHALL 接收 `history: list` 参数，把历史 messages 拼到 `inputs["messages"]` 前面。

#### Scenario: DeepAgent 跨轮次记忆

- **WHEN** thread_id "deep-1" 第一轮用户说"在 D:/work 下创建 hello.txt"，DeepAgent 审批后写入；第二轮用户说"再写一个 world.txt 到同目录"
- **THEN** DeepAgent 从 SQLite checkpoint 加载第一轮的 messages（含工具调用历史），LLM 能引用"同目录"为 `D:/work`

#### Scenario: interrupt/resume 仍可工作

- **WHEN** DeepAgent 在 `interrupt_before=["tools"]` 处暂停等待审批
- **THEN** 审批通过后 `agent.astream(None, config)` 能从 SQLite checkpoint 恢复，继续执行工具调用

### Requirement: 滑动窗口消息截断

`backend/app/memory/context.py` SHALL 提供 `trim_messages_with_budget(messages, max_messages, max_tokens)` 函数，使用 `langchain_core.messages.trim_messages` 按 token 数截断（保留最近消息），再按消息数硬截断到 `max_messages` 条。`run_router` SHALL 在传给三条路径前调用此函数截断。

#### Scenario: 超过消息数上限

- **WHEN** 历史 messages 含 30 条，`context_max_messages = 20`
- **THEN** 截断后传给 LLM 的 messages 为最近 20 条（含当前消息），早期 10 条丢弃

#### Scenario: 超过 token 上限

- **WHEN** 历史 messages 总 token 数 20000，`context_max_tokens = 16000`
- **THEN** `trim_messages` 从最早消息开始丢弃，直到总 token ≤ 16000，保留 SystemMessage + 最近消息

#### Scenario: 未超上限不截断

- **WHEN** 历史 messages 含 10 条，总 token 5000，`max_messages = 20`，`max_tokens = 16000`
- **THEN** 不截断，原样传给 LLM

### Requirement: 上下文管理配置项

`backend/app/config.py::Settings` SHALL 新增两个字段：`context_max_messages: int = 20`（滑动窗口消息数上限）与 `context_max_tokens: int = 16000`（token 预算上限）。两个字段从 `AGENTX_CONTEXT_MAX_MESSAGES` / `AGENTX_CONTEXT_MAX_TOKENS` env 读取，支持 `reload_settings` 热更新。

#### Scenario: 默认值

- **WHEN** 未设置 env
- **THEN** `context_max_messages = 20`，`context_max_tokens = 16000`

#### Scenario: env 覆盖

- **WHEN** `AGENTX_CONTEXT_MAX_MESSAGES=10` `AGENTX_CONTEXT_MAX_TOKENS=8000`
- **THEN** `get_settings().context_max_messages == 10`，`get_settings().context_max_tokens == 8000`

### Requirement: /compact 摘要压缩端点

后端 SHALL 提供 `POST /api/chat/compact` 端点，接收 `{thread_id}`，从 checkpointer 读取历史 messages，调 LLM 生成摘要，把 checkpoint 的 messages 替换为 `[SystemMessage(summary), 最近 2 条消息]`，返回 `{ok: true, summary: str, compressed_count: int}`。

#### Scenario: 压缩多轮对话

- **WHEN** thread_id "abc" 含 20 条 messages，调 `POST /api/chat/compact` body `{thread_id: "abc"}`
- **THEN** 后端调 LLM 把前 18 条压缩成摘要 `SystemMessage`，checkpoint 更新为 `[SystemMessage(summary), 第 19 条, 第 20 条]`，返回 `{ok: true, summary: "...", compressed_count: 18}`

#### Scenario: 消息不足不压缩

- **WHEN** thread_id "xyz" 含 3 条 messages
- **THEN** 返回 `{ok: false, error: "消息不足，无需压缩"}`，checkpoint 不变

#### Scenario: LLM 失败不破坏 checkpoint

- **WHEN** 摘要 LLM 调用失败
- **THEN** 返回 `{ok: false, error: "摘要生成失败: ..."}`，checkpoint 的 messages 保持原样

### Requirement: /compact 前端命令

`frontend/renderer/components/chat/ChatView.tsx` 的 `/compact` 命令 SHALL 调 `POST /api/chat/compact`，成功后显示"已压缩 N 条消息为摘要"，失败显示错误信息。

#### Scenario: 成功压缩

- **WHEN** 用户输入 `/compact`，后端返回 `{ok: true, compressed_count: 18}`
- **THEN** 前端追加 assistant 消息"已压缩 18 条消息为摘要"

#### Scenario: 无需压缩

- **WHEN** 用户输入 `/compact`，后端返回 `{ok: false, error: "消息不足"}`
- **THEN** 前端追加 assistant 消息"消息不足，无需压缩"

### Requirement: 前端 localStorage 容量保护

`frontend/renderer/stores/chat.ts` SHALL 在 `persist` 写入时捕获 `QuotaExceededError`，超限时自动归档旧会话（按 `createdAt` 降序保留最近 10 个，其余从 `sessions` 移除）后重试写入。归档后 SHALL 追加一条 system 提示消息到当前会话。

#### Scenario: 容量超限自动归档

- **WHEN** localStorage 写入触发 `QuotaExceededError`，`sessions` 含 25 个会话
- **THEN** 自动保留最近 10 个会话，移除其余 15 个，重试写入成功，当前会话追加 assistant 消息"已自动清理 15 个旧会话以释放存储空间"

#### Scenario: 归档后仍超限

- **WHEN** 归档到 10 个会话后写入仍触发 `QuotaExceededError`
- **THEN** 当前会话追加 assistant 消息"会话存储已满，请手动删除旧会话或使用 /compact 压缩"
