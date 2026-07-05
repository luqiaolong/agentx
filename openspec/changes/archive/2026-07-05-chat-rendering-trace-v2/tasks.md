# 任务追踪 — chat-rendering-trace-v2

> 本 change 把聊天框从扁平 `{content: string}` 升级为 parts-based 消息模型，引入 assistant-ui 渲染层，完整呈现 thinking / tool_call / tool_result / 子代理委派 / 最终回答的执行轨迹。
> 每阶段独立 commit，验收以单测 + typecheck + smoke 测试为准。

## 预期修改/新增文件

### 后端 (backend/)

#### P0 — SSE 事件契约扩展

- [ ] `backend/app/utils/text.py`（新增 `split_think(content) -> tuple[str, str]`，原 `strip_think` 改为 deprecated wrapper；`ThinkFilter.feed` 返回 `(reasoning_chunk, text_chunk)` 元组；`flush` 同理）
- [ ] `backend/app/subagents/code_agent.py`（`run_code_agent` 的 tool_call/tool_result 事件补 `source="code"` 字段）
- [ ] `backend/app/subagents/rag_agent.py`（同上，`source="rag"`）
- [ ] `backend/app/subagents/web_agent.py`（同上，`source="web"`）
- [ ] `backend/app/subagents/custom_agent.py`（同上，`source="custom-<key>"`）
- [ ] `backend/app/router/graph.py`（`_convert_subagent_event` 透传 tool_call/tool_result 为 SSE 事件，不再压扁为 todo_update；路径 B 入口 yield `delegation` 事件；token 事件经 ThinkFilter 分离后 yield `reasoning` + `token`）
- [ ] `backend/app/paths/deep_path.py`（`_stream_agent_events` AIMessage with tool_calls 时 yield `tool_call` 事件；ToolMessage 时 yield `tool_result` 事件；保留 `todo_update` 用于任务级进度）

#### 后端测试

- [ ] `tests/python/unit/test_text_filter.py`（split_think + ThinkFilter 分离模式；strip_think 向后兼容）
- [ ] `tests/python/unit/test_router_graph.py`（_convert_subagent_event 透传；delegation 事件；reasoning 分离）
- [ ] `tests/python/unit/test_sse_contract.py`（新事件类型 payload schema）
- [ ] `tests/python/integration/test_smoke.py`（路径 B SSE 流含 tool_call/tool_result 事件）

### 前端 (frontend/)

#### P0 — parts-based 消息模型

- [ ] `frontend/shared/api-types.ts`（ChatEvent 扩展 reasoning/tool_call/tool_result/delegation 类型 + source 字段）
- [ ] `frontend/renderer/stores/chat.ts`（ChatMessage.parts 模型；addPart/updatePart/appendPartText 操作；migrate v2→v3）
- [ ] `frontend/renderer/hooks/useChatStream.ts`（按 event.type 分发到 part 操作：token/reasoning/tool_call/tool_result/delegation/done）

#### P0 — assistant-ui 渲染层

- [ ] `package.json`（新增 `@assistant-ui/react` `@assistant-ui/react-markdown` `@assistant-ui/styles` 依赖）
- [ ] `frontend/renderer/components/chat/runtime/ExternalStoreAdapter.ts`（自定义 ExternalStoreRuntime，桥接 zustand ↔ assistant-ui）
- [ ] `frontend/renderer/components/chat/parts/TextPartView.tsx`（ReactMarkdown 渲染，复用 CodeBlock）
- [ ] `frontend/renderer/components/chat/parts/ReasoningBlock.tsx`（流式「思考中…」+ 完成自动收缩 + 点击展开）
- [ ] `frontend/renderer/components/chat/parts/ToolCallCard.tsx`（默认折叠单行 + 点击展开 args/result JSON；running/complete/error 三态）
- [ ] `frontend/renderer/components/chat/parts/DelegationCard.tsx`（header 标识子代理 + 预留嵌套 parts 数据结构）
- [ ] `frontend/renderer/components/chat/AssistantUIThread.tsx`（AssistantRuntimeProvider + Thread + 自定义 part 渲染）
- [ ] `frontend/renderer/components/chat/ChatView.tsx`（用 AssistantUIThread 替换 MessageList + TodoProgress）
- [ ] 删除 `frontend/renderer/components/chat/MessageBubble.tsx`
- [ ] 删除 `frontend/renderer/components/chat/MessageList.tsx`

#### P1 — thinking 流式 + 自动收缩

- [ ] `frontend/renderer/components/chat/parts/ReasoningBlock.tsx`（自动收缩 + sessionStorage 状态记忆按 message id 隔离）

#### 前端测试

- [ ] `tests/renderer/chat-store.test.ts`（parts 模型 + 迁移 v2→v3 + 配额保护）
- [ ] `tests/renderer/useChatStream.test.ts`（part 分发逻辑：token/reasoning/tool_call/tool_result/delegation/done）
- [ ] `tests/renderer/parts-rendering.test.tsx`（新增：ToolCallCard 三态 + ReasoningBlock 流式/收缩 + DelegationCard header + AssistantUIThread 配对合并）
- [ ] `tests/renderer/smoke.test.tsx`（整体渲染不崩溃）

## OpenSpec Tasks 映射

| ID | Capability | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|-----------|---------|---------|---------|------|
| T1 | chat-rendering-trace | split_think 工具函数 | utils/text.py | `split_think` 返回 `(reasoning, text)` 元组；跨 chunk 标签正确；`strip_think` 向后兼容 | ⬜ |
| T2 | chat-rendering-trace | ThinkFilter 分离模式 | utils/text.py | `feed` 返回 `(reasoning_chunk, text_chunk)`；`flush` 同理；状态机逻辑不变 | ⬜ |
| T3 | chat-rendering-trace | 子代理 source 字段 | subagents/*.py | tool_call/tool_result 事件含 `source` 字段标识子代理类型 | ⬜ |
| T4 | chat-rendering-trace | _convert_subagent_event 透传 | router/graph.py | tool_call/tool_result 不再压扁为 todo_update；路径 B 入口 yield delegation 事件 | ⬜ |
| T5 | chat-rendering-trace | DeepAgent tool 事件 | paths/deep_path.py | AIMessage with tool_calls yield tool_call SSE；ToolMessage yield tool_result SSE；保留 todo_update | ⬜ |
| T6 | chat-rendering-trace | ChatEvent 类型扩展 | shared/api-types.ts | 新增 reasoning/tool_call/tool_result/delegation 类型；source 字段 | ⬜ |
| T7 | chat-rendering-trace | parts 消息模型 + 迁移 | stores/chat.ts | ChatMessage.parts 模型；addPart/updatePart/appendPartText；migrate v2→v3 | ⬜ |
| T8 | chat-rendering-trace | useChatStream part 分发 | hooks/useChatStream.ts | token/reasoning/tool_call/tool_result/delegation/done 按 type 分发到 part 操作 | ⬜ |
| T9 | chat-rendering-trace | assistant-ui 依赖 + adapter | package.json, runtime/ExternalStoreAdapter.ts | 依赖安装；ExternalStoreRuntime 桥接 zustand ↔ assistant-ui | ⬜ |
| T10 | chat-rendering-trace | part 组件 | parts/*.tsx | TextPartView/ReasoningBlock/ToolCallCard/DelegationCard 渲染正确 | ⬜ |
| T11 | chat-rendering-trace | AssistantUIThread + ChatView 替换 | AssistantUIThread.tsx, ChatView.tsx | 替换 MessageList + TodoProgress；删除 MessageBubble/MessageList | ⬜ |
| T12 | chat-rendering-trace | thinking 流式 + 自动收缩 | parts/ReasoningBlock.tsx | 流式显示「思考中…」；done=true 自动收缩；sessionStorage 状态记忆 | ⬜ |
| T13 | chat-rendering-trace | 后端单测 | tests/python/ | test_text_filter + test_router_graph + test_sse_contract 通过 | ⬜ |
| T14 | chat-rendering-trace | 前端单测 | tests/renderer/ | chat-store + useChatStream + parts-rendering + smoke 通过 | ⬜ |
| T15 | chat-rendering-trace | 全量验证 | - | `uv run pytest` + `npm run typecheck` + `npm test` 全绿 | ⬜ |

## 规模判定

- 涉及文件数：~20（后端 8 + 前端 12）
- 涉及模块数：2（backend + frontend）
- 规模：**L（大改）** — 跨前后端，引入新依赖，重构消息模型

## 执行顺序

按 Migration Plan 阶段 1→4 顺序执行，每阶段独立 commit。T1-T5 后端先行，T6-T8 前端 store 跟进，T9-T11 渲染层收尾，T12-T15 体验 + 测试。
