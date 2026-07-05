## Why

当前 agentx 的聊天消息上下文管理存在严重缺陷，本质上是"单轮问答 + 单次 DeepAgent 执行"的壳，无法支撑多轮对话：

1. **后端无历史注入** — `backend/app/router/graph.py::run_router` 每次只把当前消息写入 `state["messages"]`（[graph.py#L469-L473](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py)），三条路径（CHAT / SINGLE_TOOL / DEEP_TASK）传给 LLM 的都只有 `SystemMessage + 当前 HumanMessage`，用户问"刚才那个呢"模型完全不知道在说什么。
2. **DeepAgent 跨轮次失忆** — `backend/app/paths/deep_path.py::build_deep_agent` 每次 `run_deep_path` 调用都 `MemorySaver()` 新建实例（[deep_path.py#L150](file:///d:/java/agentprojects/agentx/backend/app/paths/deep_path.py)），仅用于同一次调用内 `interrupt_before` 中断恢复，**跨轮次不保留历史**，与 Router 共享的 SQLite checkpointer 完全脱节。
3. **无滑动窗口 / Token 预算** — 三条路径均未对消息历史做截断，长对话会直接超过模型上下文窗口，导致 API 报错或输出质量暴跌。配置项 `context_max_messages` / `context_max_tokens` 完全不存在。
4. **无消息摘要压缩** — 前端 `/compact` 命令是占位提示（[ChatView.tsx#L109-L116](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ChatView.tsx)），后端无摘要端点，无法把早期消息压缩进 prompt。
5. **前端 localStorage 无保护** — `frontend/renderer/stores/chat.ts` 全量持久化所有会话所有消息（[chat.ts#L256-L288](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/chat.ts)），消息多了会触发 `QuotaExceededError`，spec 里已提到但未实现（archive 中的 `session-management/spec.md` 提到容量超限要捕获但代码里没做）。
6. **checkpointer 形同虚设** — `backend/app/memory/checkpointer.py` 用 SQLite 存了 LangGraph checkpoint，但 Router 主图 `build_router_graph` 没传 checkpointer（`main.py` 调用处未传入），且存的内容里 `messages` 只有当前消息，即使恢复 checkpoint 也拿不到历史。

本次 change 把"上下文管理"补齐到可用状态：后端维护真实历史、滑动窗口截断、DeepAgent 跨轮次持久化、`/compact` 摘要压缩、前端容量保护。

## What Changes

### P0 — 后端历史注入与持久化

- **MODIFY** `backend/app/main.py`：`/api/chat` 入口构建 Router 时传入 `AsyncSqliteSaver` checkpointer，使 `thread_id` 对应的会话状态（含 `messages`）跨轮次持久化
- **MODIFY** `backend/app/router/graph.py`：`run_router` 入口从 checkpoint 加载历史 `messages`，append 当前消息后传给三条路径；`_run_chat_path` / `_run_tool_path` / `_run_deep_path` 接收完整 `messages` 列表而非单条消息
- **MODIFY** `backend/app/paths/deep_path.py`：`build_deep_agent` 改用共享的 `AsyncSqliteSaver`（与 Router 同一实例），替换每次新建的 `MemorySaver`，使 DeepAgent 跨轮次看到历史
- **MODIFY** `backend/app/subagents/code_agent.py` / `rag_agent.py` / `web_agent.py`：`run_*_agent` 接收 `history: list` 参数，拼到 `inputs["messages"]` 前面

### P0 — 滑动窗口与 Token 预算

- **NEW** `backend/app/config.py`：新增 `context_max_messages: int = 20`（滑动窗口消息数上限）与 `context_max_tokens: int = 16000`（token 预算上限，保守值兼容多数模型）
- **NEW** `backend/app/memory/context.py`：`trim_messages_with_budget(messages, max_messages, max_tokens)` 工具函数，先用 `langchain_core.messages.trim_messages` 按 token 截断，再按消息数硬截断，保证 system prompt + 最近 N 条不越界
- **MODIFY** `backend/app/router/graph.py`：三条路径入口调用 `trim_messages_with_budget` 截断后再传 LLM

### P1 — /compact 摘要压缩

- **NEW** `backend/app/memory/summarizer.py`：`summarize_messages(messages) -> str` 调 LLM 把消息列表压缩成一段摘要，返回 `SystemMessage` 内容
- **NEW** `POST /api/chat/compact` 端点：接收 `thread_id`，从 checkpointer 读取历史，调 `summarize_messages` 生成摘要，把 checkpoint 的 `messages` 替换为 `[SystemMessage(summary), 最近 2 条消息]`，返回 `{ok: true, summary: "..."}`
- **MODIFY** `frontend/renderer/components/chat/ChatView.tsx`：`/compact` 命令调 `POST /api/chat/compact`，成功后前端提示"已压缩 N 条消息为摘要"

### P1 — 前端容量保护

- **MODIFY** `frontend/renderer/stores/chat.ts`：`persist` 中间件 `partialize` 时检测 localStorage 剩余配额，超 8MB 时自动归档旧会话（保留最近 10 个，其余移除）；`addMessage` 时捕获 `QuotaExceededError`，提示用户用 `/compact` 或删除旧会话

## Capabilities

### New Capabilities

- `chat-context-management`: 聊天上下文管理 — 后端历史注入、滑动窗口截断、Token 预算、`/compact` 摘要压缩、前端容量保护

### Modified Capabilities

- 无（本次为新增 capability，不修改已有 spec 的 requirement）

## Impact

- **代码影响**：
  - 后端：`backend/app/config.py` 新增 2 个配置项；`backend/app/memory/context.py` 新建滑动窗口工具；`backend/app/memory/summarizer.py` 新建摘要器；`backend/app/router/graph.py` 三路径接收历史 messages + 截断；`backend/app/paths/deep_path.py` 共享 SQLite checkpointer；`backend/app/subagents/*.py` 接收 history 参数；`backend/app/main.py` 传入 checkpointer + 新增 `/api/chat/compact` 端点
  - 前端：`frontend/renderer/stores/chat.ts` 容量保护；`frontend/renderer/components/chat/ChatView.tsx` `/compact` 命令实现
- **API 影响**：新增 `POST /api/chat/compact` 端点
- **依赖影响**：无新增依赖（`langchain_core.messages.trim_messages` 已在 langchain-core 中）
- **数据影响**：`data/agentx.db` 的 checkpoint 表开始真正存储多轮 messages（之前只存单条）；DeepAgent 不再用 MemorySaver，改用 SQLite
- **测试影响**：新增 `test_context_management.py`（历史注入 + 截断 + 摘要）；`test_smoke.py` 增加多轮对话断言
- **运维影响**：`context_max_tokens` 默认 16000，用户可在设置页调整（M2 落地，本次仅 config 字段）
