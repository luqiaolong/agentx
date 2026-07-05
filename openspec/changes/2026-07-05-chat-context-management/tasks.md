# 任务追踪 — chat-context-management

> 本 change 聚焦聊天上下文管理：后端历史注入、滑动窗口截断、DeepAgent 共享 checkpointer、`/compact` 摘要压缩、前端容量保护。
> 每阶段独立 commit，验收以单测 + typecheck + smoke 测试为准。

## 预期修改/新增文件

### 后端 (backend/)

#### P0 — 历史注入 + 滑动窗口

- [x] `backend/app/config.py`（新增 `context_max_messages: int = 20` 与 `context_max_tokens: int = 16000` 字段，从 `AGENTX_CONTEXT_MAX_MESSAGES` / `AGENTX_CONTEXT_MAX_TOKENS` env 读取）
- [x] `backend/app/memory/context.py`（新建：`trim_messages_with_budget(messages, max_messages, max_tokens)` 函数，双层截断）
- [x] `backend/app/memory/__init__.py`（导出 `trim_messages_with_budget`）
- [x] `backend/app/router/graph.py`（`run_router` 接收 `checkpointer` 参数；从 checkpoint 加载历史 messages + append 当前；`_run_chat_path` / `_run_tool_path` / `_run_deep_path` 接收 `history` 参数 + 截断后传 LLM）
- [x] `backend/app/paths/deep_path.py`（`build_deep_agent` 用 `get_async_checkpointer()` 替换 `MemorySaver()`；`run_deep_path` 接收 `history` 参数拼到 inputs）
- [x] `backend/app/subagents/code_agent.py`（`run_code_agent` 接收 `history: list` 参数，拼到 `inputs["messages"]` 前）
- [x] `backend/app/subagents/rag_agent.py`（同上）
- [x] `backend/app/subagents/web_agent.py`（同上）
- [x] `backend/app/subagents/custom_agent.py`（同上）
- [x] `backend/app/main.py`（`_event_generator` 调 `run_router` 时传 `await get_async_checkpointer()`；`/api/chat/compact` 端点同样 `await`）

#### P1 — /compact 摘要压缩

- [x] `backend/app/memory/summarizer.py`（新建：`summarize_messages(messages) -> str` 调 LLM 生成摘要）
- [x] `backend/app/memory/__init__.py`（导出 `summarize_messages`）
- [x] `backend/app/main.py`（新增 `POST /api/chat/compact` 端点：读 checkpoint → 调 `summarize_messages` → 替换 checkpoint messages）

#### 测试

- [x] `tests/python/unit/test_router_graph.py`（修复 mock 签名：4 处 subagent mock + 2 处 deep_path mock 添加 `history` 参数）
- [x] `tests/python/integration/test_approval_flow.py`（修复 mock 签名：2 处 `_fake` 函数添加 `checkpointer` 参数）
- [x] 全量 pytest 通过：345 passed, 3 skipped, 2 xfailed

### 前端 (frontend/)

- [x] `frontend/renderer/components/chat/ChatView.tsx`（`/compact` 命令调 `POST /api/chat/compact`，显示压缩结果）
- [x] `frontend/renderer/stores/chat.ts`（`persist` 包裹 try/catch 捕获 `QuotaExceededError`，超限归档旧会话保留最近 10 个）
- [x] `frontend/preload/index.ts`（暴露 `chat.compact(threadId)` IPC 桥；补 `CompactResult` type import）
- [x] `frontend/shared/api-types.ts`（新增 `CompactResult` interface，`ElectronAPI.chat` 新增 `compact` 方法）
- [x] `npm run typecheck` 通过（node + web 两套配置）
- [x] `npm test` 通过：84 passed

## OpenSpec Tasks 映射

| ID | Capability | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|-----------|---------|---------|---------|------|
| T1 | chat-context-management | config 新增配置项 | config.py | `Settings.context_max_messages == 20`，`context_max_tokens == 16000`；env 覆盖生效 | ✅ |
| T2 | chat-context-management | 滑动窗口工具函数 | memory/context.py | `trim_messages_with_budget` 双层截断：超消息数硬截断，超 token 用 trim_messages | ✅ |
| T3 | chat-context-management | Router 历史注入 | router/graph.py, main.py | `run_router` 从 checkpoint 加载历史 + append 当前；三路径接收 history 参数 | ✅ |
| T4 | chat-context-management | DeepAgent 共享 checkpointer | paths/deep_path.py | `build_deep_agent` 用 `AsyncSqliteSaver` 而非 `MemorySaver`；interrupt/resume 仍可工作 | ✅ |
| T5 | chat-context-management | 子代理接收 history | subagents/*.py | `run_*_agent` 接收 `history` 参数拼到 inputs | ✅ |
| T6 | chat-context-management | /compact 摘要端点 | memory/summarizer.py, main.py | `POST /api/chat/compact` 生成摘要 + 替换 checkpoint messages；LLM 失败不破坏 | ✅ |
| T7 | chat-context-management | /compact 前端命令 | ChatView.tsx, preload, main | `/compact` 调端点 + 显示结果 | ✅ |
| T8 | chat-context-management | 前端 localStorage 保护 | stores/chat.ts | 捕获 `QuotaExceededError` + 归档旧会话保留 10 个 | ✅ |
| T9 | chat-context-management | 单测覆盖 | tests/ | 历史注入 + 截断 + 摘要 + 容量保护测试通过 | ✅ |
| T10 | chat-context-management | 测试验证（python + ts） | tests/ | pytest 全绿 + npm typecheck + npm test 全绿 | ✅ |

## Review 中发现并修复的额外 bug

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| B1 | `backend/app/main.py:513` | `_event_generator` 调 `checkpointer = get_async_checkpointer()` 缺 `await`，返回 coroutine 而非 saver 对象，导致 `RuntimeWarning: coroutine 'get_async_checkpointer' was never awaited` | 改为 `checkpointer = await get_async_checkpointer()` |
| B2 | `backend/app/main.py:564` | `/api/chat/compact` handler 同样缺 `await` | 改为 `checkpointer = await get_async_checkpointer()` |
| B3 | `frontend/preload/index.ts` | `compact` 方法引用 `CompactResult` 类型但未 import | 补 `CompactResult` 到 `import type { ... }` |
| B4 | `frontend/renderer/stores/chat.ts` | `createQuotaGuardedStorage` 用 `storage.setItem = ...` 重写时类型推断失败（`storage` possibly undefined + setItem 签名不匹配） | 重写为返回 `{ getItem, setItem, removeItem }` 的 `StateStorage` 对象，由 `createJSONStorage(() => createQuotaGuardedStorage())` 包裹 |
| B5 | `tests/python/integration/test_approval_flow.py` | `run_router` 新增 `checkpointer` 参数后，2 处 `_fake` mock 函数签名 `(message, tid)` 未同步更新，`main.py` 以关键字 `checkpointer=...` 调用时抛 `TypeError`，导致 `test_approval_request_event_shape` 收到 `error` 事件、`test_full_approval_flow_auto_resume` 超时 | 两处 `_fake` 签名加 `checkpointer: object = None` 参数 |
