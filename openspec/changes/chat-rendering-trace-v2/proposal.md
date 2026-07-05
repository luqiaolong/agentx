## Why

当前 agentx 聊天框只能呈现"用户消息 + assistant 文本回复 + 底部悬浮 TodoProgress"三种形态，无法表达真实 agent 执行轨迹：

1. **thinking 被整段剥离** — `backend/app/utils/text.py` 的 `strip_think` 与 `ThinkFilter`（[text.py#L28-L173](file:///d:/java/agentprojects/agentx/backend/app/utils/text.py)）把 `<think>...</think>` 内容直接丢弃，前端永远看不到推理过程。用户无法判断 agent 推理是否合理，调试困难，信任度低。
2. **工具调用被压扁** — 路径 B 子代理原本 yield `tool_call`/`tool_result` 事件（[code_agent.py#L109-L112](file:///d:/java/agentprojects/agentx/backend/app/subagents/code_agent.py)），但 `_convert_subagent_event`（[graph.py#L343-L378](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py)）把它们压扁成 `todo_update`，**args 和 result 直接丢弃**；路径 C DeepAgent 只 yield `_make_todo_event("调用工具: xxx", done=False)`，同样无 args/result。
3. **消息模型扁平** — `ChatMessage = {id, role, content, ts}`（[chat.ts#L4-L9](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/chat.ts)），只有一个 `content: string`，无法表达"一个 assistant turn = N 个工具调用 + thinking + 最终回答"的层次结构。多轮工具调用的中间状态无处存放。
4. **子代理无可见性** — Router 静态分类后调用 code/rag/web/custom 子代理，前端只看到 assistant 文本流，完全不知道当前在跟哪个子代理交互，也无法区分主 agent 与子代理的产出。
5. **TodoProgress 与消息流割裂** — 工具调用进度悬浮在底部（[TodoProgress.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/TodoProgress.tsx)），与消息流时间轴脱节，无法回看历史 turn 的工具执行细节。
6. **UI 自研违反 AGENTS.md §1.1** — 当前 `MessageBubble` / `MessageList` 自研渲染逻辑，而业界已有成熟 parts-based chat UI 框架（assistant-ui 7k+ stars，原生支持 tool-call 卡 / reasoning 块 / 嵌套），符合"优先用现成框架"原则。

本次 change 把聊天框升级为 parts-based 消息模型 + assistant-ui 渲染层，完整呈现 thinking / tool_call / tool_result / 子代理委派 / 最终回答的执行轨迹。

## What Changes

### P0 — 后端 SSE 事件契约扩展

- **MODIFY** `backend/app/utils/text.py`：`strip_think` 改为"分离"而非"丢弃" — 新增 `split_think(content) -> tuple[str, str]` 返回 `(reasoning, visible_text)`，原 `strip_think` 保留为 deprecated wrapper；`ThinkFilter.feed(chunk)` 改为返回 `(reasoning_chunk, text_chunk)` 元组，`flush()` 返回 `(reasoning_tail, text_tail)`
- **MODIFY** `backend/app/router/graph.py`：`_convert_subagent_event` 不再压扁 tool_call/tool_result，改为透传为 `tool_call` / `tool_result` SSE 事件，附带 `source` 字段（`"code"` / `"rag"` / `"web"` / `"custom-xxx"` / `"deep"`）
- **MODIFY** `backend/app/paths/deep_path.py`：`_stream_agent_events` 在 AIMessage with tool_calls 时 yield `tool_call` 事件（含 `name`/`args`/`source="deep"`），ToolMessage 时 yield `tool_result` 事件（含 `name`/`result`）；保留 `todo_update` 用于 DeepAgent todo 列表（与 tool_call 事件并存）
- **MODIFY** `backend/app/subagents/code_agent.py` / `rag_agent.py` / `web_agent.py` / `custom_agent.py`：`run_*_agent` 的 `tool_call` / `tool_result` 事件补 `source` 字段标识子代理类型
- **NEW** SSE 事件类型：`reasoning`（流式 reasoning token）、`tool_call`（工具调用开始，含 name/args/source）、`tool_result`（工具调用结束，含 name/result/source）、`delegation`（主 agent 委派子代理，含 target/source/message）— 与现有 `token`/`todo_update`/`approval_request`/`done`/`error` 并存
- **MODIFY** `backend/app/router/graph.py`：路径 B 入口 yield `delegation` 事件标识"委派给 code/rag/web/custom 子代理"；路径 C DeepAgent 内部子代理调用（如有）同样 yield `delegation`

### P0 — 前端 parts-based 消息模型

- **MODIFY** `frontend/renderer/stores/chat.ts`：`ChatMessage` 扩展为 parts-based：
  ```ts
  interface ChatMessage {
    id: string;
    role: "user" | "assistant" | "tool";
    parts: MessagePart[];   // 替换原 content: string
    ts: number;
  }
  type MessagePart =
    | { type: "text"; id: string; text: string }
    | { type: "reasoning"; id: string; text: string; done: boolean }
    | { type: "tool-call"; id: string; toolName: string; args: unknown; source: string; status: "running" | "complete" | "error" }
    | { type: "tool-result"; id: string; toolName: string; result: unknown; source: string }
    | { type: "delegation"; id: string; target: string; source: string; message: string };
  ```
  - 用户消息保持 `parts: [{type:"text", text: content}]`（向后兼容内部命令）
  - `addMessage` / `appendMessageContent` 改为 `addPart` / `updatePart` / `appendPartText`
  - 持久化迁移 v2→v3：旧 `{content: string}` 自动转为 `[{type:"text", text: content}]`
- **MODIFY** `frontend/shared/api-types.ts`：`ChatEvent` 扩展 `reasoning` / `tool_call` / `tool_result` / `delegation` 类型，附 `source` 字段
- **MODIFY** `frontend/renderer/hooks/useChatStream.ts`：按 event.type 分发到对应 part 操作（`token`→appendTextPart，`reasoning`→appendReasoningPart，`tool_call`→addToolCallPart，`tool_result`→updateToolCallPartWithResult，`delegation`→addDelegationPart，`todo_update`→保留底部 TodoProgress）

### P0 — assistant-ui 渲染层

- **NEW** `frontend/renderer/components/chat/AssistantUIThread.tsx`：用 `@assistant-ui/react` 的 `AssistantRuntimeProvider` + `Thread` 替换 `MessageList` + `MessageBubble`
- **NEW** `frontend/renderer/components/chat/runtime/ExternalStoreAdapter.ts`：自定义 `ExternalStoreRuntime` 适配器，桥接 zustand store ↔ assistant-ui（不使用 `useChatRuntime` / `useLangGraphRuntime`，因项目 SSE 协议是自定义的）
- **NEW** `frontend/renderer/components/chat/parts/`：
  - `ReasoningBlock.tsx`：默认折叠「思考中…」可展开块，流式时显示动态省略号，完成后自动收缩（点击展开回看）
  - `ToolCallCard.tsx`：默认折叠单行「🔧 tool_name(args 预览) ✓/⏳」，点击展开 args/result JSON
  - `DelegationCard.tsx`：标识"由 xxx agent 执行"，header 显示子代理图标 + 名称，body 嵌套该子代理的 parts
  - `TextPartView.tsx`：替换原 `MessageBubble` 的 ReactMarkdown 渲染（保留 CodeBlock）
- **MODIFY** `frontend/renderer/components/chat/ChatView.tsx`：用 `AssistantUIThread` 替换 `MessageList`，移除 `TodoProgress`（tool 调用进度已内联到消息流）
- **DELETE** `frontend/renderer/components/chat/MessageBubble.tsx` / `MessageList.tsx`（功能被 AssistantUIThread + parts 替代）
- **MODIFY** `package.json`：新增依赖 `@assistant-ui/react` `@assistant-ui/react-markdown` `@assistant-ui/styles`
- **MODIFY** `frontend/renderer/components/chat/CodeBlock.tsx`：保留，作为 `TextPartView` 的子组件复用

### P1 — thinking 流式 + 自动收缩

- **MODIFY** `frontend/renderer/components/chat/parts/ReasoningBlock.tsx`：
  - 流式时（`done=false`）：显示「思考中…」+ 动态省略号 + 实时流式文本（折叠状态下不展示文本，仅展示状态）
  - 完成时（`done=true`）：自动收缩为单行「💡 已思考 N 秒」，点击展开回看完整 reasoning
  - 用户可手动展开/折叠，状态记忆到 sessionStorage（按 message id 隔离）

### P1 — 子代理委派嵌套展示

- **NEW** `frontend/renderer/components/chat/parts/DelegationCard.tsx`：
  - 路径 B 场景：整条 assistant message 的 header 标记"🔧 由 code agent 执行"，body 嵌套该子代理的所有 parts（reasoning / tool-call / tool-result / text）
  - 未来 DeepAgent 动态委派子代理时：用 `delegation` part 包裹子 agent 的所有 parts，实现真正嵌套
  - 当前架构是 Router 静态分类，不是主 agent 动态委派，故本次仅落地"header 标记"形态，预留嵌套 parts 数据结构

## Capabilities

### New Capabilities

- `chat-rendering-trace`: parts-based 聊天渲染与执行轨迹展示 — SSE 事件契约扩展、parts-based 消息模型、assistant-ui 渲染层、thinking 流式折叠、工具调用卡片、子代理委派嵌套

### Modified Capabilities

- `chat-rendering`（archive 2026-07-04-m1-experience-completion）：从 `{content: string}` 升级为 parts-based，原 markdown 渲染要求保留但迁移到 `TextPartView`

## Impact

- **代码影响**：
  - 后端：`backend/app/utils/text.py`（split_think + ThinkFilter 分离模式）、`backend/app/router/graph.py`（_convert_subagent_event 透传 + delegation）、`backend/app/paths/deep_path.py`（tool_call/tool_result 事件）、`backend/app/subagents/*.py`（source 字段）
  - 前端：`frontend/renderer/stores/chat.ts`（parts 模型 + 迁移）、`frontend/shared/api-types.ts`（ChatEvent 扩展）、`frontend/renderer/hooks/useChatStream.ts`（part 分发）、`frontend/renderer/components/chat/`（AssistantUIThread + parts/ + runtime/）、`frontend/renderer/components/chat/ChatView.tsx`（替换 MessageList）
  - 删除：`MessageBubble.tsx` / `MessageList.tsx`
- **API 影响**：SSE 事件类型扩展（新增 reasoning/tool_call/tool_result/delegation），不改 `/api/chat` 端点签名
- **依赖影响**：新增 `@assistant-ui/react` `@assistant-ui/react-markdown` `@assistant-ui/styles` 三个 npm 包
- **数据影响**：localStorage 持久化的 `ChatMessage` 结构从 `{content}` 变为 `{parts}`，需迁移 v2→v3
- **测试影响**：
  - 后端：`tests/python/unit/test_text_filter.py`（split_think + ThinkFilter 分离）、`tests/python/unit/test_router_graph.py`（_convert_subagent_event 透传）、`tests/python/unit/test_sse_contract.py`（新事件类型）
  - 前端：`tests/renderer/chat-store.test.ts`（parts 模型 + 迁移）、`tests/renderer/useChatStream.test.ts`（part 分发）、新增 `tests/renderer/parts-rendering.test.tsx`（ToolCallCard / ReasoningBlock / DelegationCard 渲染）
- **运维影响**：无新配置项（thinking 流式默认开启，折叠状态走 sessionStorage）
