## Context

当前项目状态（截至 2026-07-05）：
- 后端 M1 闭环已通：Router 三路径 + 子代理 + 沙箱 + 审批 + SSE + 单测
- 前端骨架已就位：多会话 store、ChatView、SettingsModal
- **核心缺陷**：后端无历史注入，三条路径每次只传当前单条消息给 LLM；DeepAgent 用 `MemorySaver` 每次新建，跨轮次失忆；无滑动窗口、无 token 预算、无摘要压缩
- SQLite checkpointer 已就位（`backend/app/memory/checkpointer.py`），但 Router 主图未接入，DeepAgent 自建 MemorySaver

设计约束（来自 `project_memory.md`）：
- 开发阶段无需考虑灰度和兼容老版本，直接推倒重来
- 禁止全局修改 Jackson 配置（本次不涉及）
- 凭证走 `electron-store` + `safeStorage`，本次新增配置项走 env 注入
- 路径 B 命中禁用子代理时静默退回路径 A

## Goals / Non-Goals

**Goals:**
- 后端三条路径都能拿到完整历史 messages（含当前消息）
- DeepAgent 跨轮次看到历史（共享 SQLite checkpointer）
- 滑动窗口截断：超 `context_max_messages` 或 `context_max_tokens` 时丢弃最早消息
- `/compact` 命令真实工作：LLM 摘要 + checkpoint 替换
- 前端 localStorage 容量保护：超限自动归档旧会话
- 所有改动有单测覆盖

**Non-Goals:**
- 不做分层记忆（短期/中期/长期 RAG 检索历史）—— 留给后续 change
- 不做设置页 UI 调整 `context_max_messages` / `context_max_tokens` —— 仅 config 字段，UI 留 M2
- 不重构 Router 三路径核心分类逻辑 —— 仅扩展 messages 传递
- 不做跨会话历史检索 —— 仅同 thread_id 内的历史
- 不做流式 token 级别的历史写入 —— 历史在 `run_router` 入口一次性加载

## Decisions

### D1. 历史加载位置：run_router 入口统一加载

**选择**：在 `run_router` 入口从 checkpointer 加载历史，append 当前消息后传给三条路径。

**理由**：
- 三条路径都需要历史，统一加载避免重复查询
- `run_router` 已有 `thread_id` 参数，加载逻辑自然
- 路径 A / B / C 的 `messages` 传递方式一致，便于测试

**备选**：
- 各路径自己加载 → 重复查询，且路径 B 子代理需额外传 thread_id 给 checkpointer，破坏隔离
- 前端把历史随 `/api/chat` 请求体传入 → 不可信，前端可能丢失或篡改，且 payload 膨胀

### D2. DeepAgent checkpointer：共享 AsyncSqliteSaver

**选择**：`build_deep_agent` 改用 `get_async_checkpointer()` 获取全局 `AsyncSqliteSaver` 单例，替换每次新建的 `MemorySaver`。

**理由**：
- `MemorySaver` 是进程内内存，`run_deep_path` 每次调用新建实例 → 跨轮次必然失忆
- `AsyncSqliteSaver` 与 Router 共享同一 SQLite 文件，`thread_id` 作为 checkpoint key 天然隔离
- `interrupt_before` 的中断恢复依赖 checkpointer，SQLite 持久化后即使进程重启也能恢复（虽然当前不要求）

**风险**：
- `AsyncSqliteSaver` 单例连接的并发安全 → SQLite `check_same_thread=False` + aiosqlite 内部序列化，LangGraph 已验证
- DeepAgent 与 Router 用同一 `thread_id` 写 checkpoint → 两者 state schema 不同（RouterState vs create_react_agent 默认 state），但 LangGraph 按 `thread_id` + `checkpoint_ns` 隔离，不冲突

**备选**：
- DeepAgent 用独立 SQLite 文件 → 增加运维复杂度，且无法与 Router 共享 thread_id 视图
- 保留 MemorySaver 但每次从 Router checkpoint 手动加载历史 → 重复持久化，且 interrupt/resume 状态丢失

### D3. 滑动窗口实现：trim_messages + 消息数硬截断

**选择**：`backend/app/memory/context.py::trim_messages_with_budget` 双层截断：
1. 先用 `langchain_core.messages.trim_messages(messages, max_tokens=max_tokens, strategy="last")` 按 token 截断
2. 再按消息数 `max_messages` 硬截断（`messages[-max_messages:]`）

**理由**：
- `trim_messages` 是 LangChain 官方工具，支持 `strategy="last"`（保留最近）+ `token_counter` 回调
- 单纯按消息数截断无法应对超长单消息（如粘贴大文件）
- 单纯按 token 截断在消息数极多时仍有性能问题（序列化开销）
- 双层截断兼顾 token 预算与消息数上限

**token_counter 实现**：
- 用 `langchain_core.messages.get_num_tokens_from_messages`（基于 `tiktoken`，与模型 tokenizer 对齐）
- 若模型非 OpenAI 系（如 DeepSeek），`tiktoken` 估算略有偏差，但 16000 默认值留足余量

**备选**：
- 只按消息数截断 → 单条大消息可撑爆上下文
- 只按 token 截断 → 消息数极多时序列化慢
- 自研 token 计数 → 重复造轮子

### D4. /compact 摘要策略：替换 checkpoint messages

**选择**：`POST /api/chat/compact` 读取 checkpoint messages，调 LLM 生成摘要，把 checkpoint 的 messages 替换为 `[SystemMessage(summary), 最近 2 条消息]`。

**理由**：
- 替换 checkpoint 而非前端 store → 后端是 SSOT，前端只展示
- 保留最近 2 条 → 保留即时上下文连续性（上一轮问答 + 当前轮）
- 摘要作为 `SystemMessage` → 不影响 `HumanMessage` / `AIMessage` 的角色交替

**摘要 prompt**：
```
请将以下对话历史压缩成一段简洁的摘要，保留关键信息（用户意图、已完成的操作、重要结论）：
{messages_text}
输出格式：纯文本摘要，不超过 500 字。
```

**备选**：
- 前端删除旧消息 → 后端 checkpoint 仍有，下次加载又回来了
- 摘要作为 `HumanMessage` → 破坏角色交替，LLM 可能困惑
- 不保留最近消息 → 上下文连续性丢失

### D5. 前端 localStorage 保护：归档旧会话

**选择**：`persist` 中间件 `partialize` 包裹 try/catch，捕获 `QuotaExceededError` 后按 `createdAt` 降序保留最近 10 个会话，其余移除后重试。

**理由**：
- localStorage 配额 5-10MB，全量持久化所有会话消息极易触顶
- 归档旧会话比丢当前会话更合理（用户最近用的最重要）
- 后端 checkpoint 仍保留完整历史，前端丢失只是 UI 不显示，可从 `/api/memory/checkpointer` 恢复（M2 落地）

**备选**：
- 迁移到 IndexedDB → 配额更大但实现复杂，且 zustand `persist` 默认不支持
- 不持久化消息只存会话元数据 → 用户体验差，每次刷新都丢消息
- 压缩存储（gzip）→ zustand 不原生支持，且调试困难

## Risks / Trade-offs

- **[DeepAgent state schema 与 Router 不同]** → LangGraph 按 `checkpoint_ns` 隔离，DeepAgent 用 `create_react_agent` 默认 state，Router 用 `RouterState`，两者 checkpoint 不冲突
- **[trim_messages token 计数偏差]** → 非 OpenAI 模型 `tiktoken` 估算不准，但 16000 默认值留足余量（多数模型上下文 32K+）
- **[/compact 后历史丢失]** → 摘要可能丢失细节，但用户主动触发，且保留最近 2 条，可接受
- **[前端归档后旧会话不可见]** → 后端 checkpoint 仍有，M2 可从 `/api/memory/checkpointer` 恢复
- **[AsyncSqliteSaver 并发写入]** → aiosqlite 内部序列化写入，LangGraph 已验证

## Migration Plan

**阶段 1（P0）：后端历史注入 + DeepAgent 共享 checkpointer**
1. `config.py` 新增 2 个配置项（独立 commit）
2. `memory/context.py` 新建 `trim_messages_with_budget`（独立 commit）
3. `router/graph.py` 三路径接收 history + 截断（独立 commit，影响面大）
4. `paths/deep_path.py` 共享 SQLite checkpointer（独立 commit）
5. `main.py` 传入 checkpointer（独立 commit）
6. `subagents/*.py` 接收 history 参数（独立 commit）

**阶段 2（P1）：/compact + 前端保护**
1. `memory/summarizer.py` 新建摘要器（独立 commit）
2. `main.py` 新增 `/api/chat/compact` 端点（独立 commit）
3. `ChatView.tsx` `/compact` 命令实现（独立 commit）
4. `chat.ts` localStorage 容量保护（独立 commit）

## Test Plan

- `tests/python/unit/test_context_management.py`：
  - 历史注入：mock checkpointer 返回 2 条历史，验证路径 A 传 4 条 messages
  - 截断：30 条历史 + `max_messages=20`，验证传 20 条
  - token 截断：mock `get_num_tokens_from_messages` 返回超限，验证丢弃最早
  - DeepAgent 共享 checkpointer：验证 `build_deep_agent` 用 `AsyncSqliteSaver` 而非 `MemorySaver`
- `tests/python/unit/test_compact.py`：
  - 摘要成功：mock LLM 返回摘要，验证 checkpoint messages 替换
  - 消息不足：3 条不压缩
  - LLM 失败：checkpoint 不变
- `tests/renderer/chat-store.test.ts`：
  - 容量超限归档：mock localStorage 抛 `QuotaExceededError`，验证保留 10 个会话
