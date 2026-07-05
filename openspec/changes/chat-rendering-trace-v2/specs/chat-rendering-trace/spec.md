## ADDED Requirements

### Requirement: parts-based 消息模型

Renderer SHALL 用 `ChatMessage.parts: MessagePart[]` 替代扁平 `content: string`，`MessagePart` 是 typed union（discriminated union on `type` 字段），支持 `text` / `reasoning` / `tool-call` / `tool-result` / `delegation` 五种 part 类型。每条消息按 parts 数组顺序表达时间轴。

#### Scenario: 用户消息转为 parts

- **WHEN** 用户发送消息「hello」
- **THEN** store 中 `ChatMessage.parts === [{type:"text", id:"<uuid>", text:"hello"}]`

#### Scenario: assistant turn 含多 part

- **WHEN** assistant 执行一轮含 thinking + 2 次工具调用 + 最终回答
- **THEN** `ChatMessage.parts` 顺序为 `[reasoning, tool-call, tool-result, tool-call, tool-result, text]`

#### Scenario: 持久化迁移 v2→v3

- **WHEN** localStorage 中存在旧格式 `{content: "hello"}`
- **THEN** 加载时自动迁移为 `{parts: [{type:"text", id:"<uuid>", text:"hello"}]}`

### Requirement: SSE 事件契约扩展

Backend SHALL 在 `/api/chat` SSE 流中新增 4 种事件类型：`reasoning` / `tool_call` / `tool_result` / `delegation`，与现有 `token` / `todo_update` / `approval_request` / `done` / `error` 并存。事件 payload 遵循以下 schema：

- `reasoning`: `{"content": "<chunk>", "source": "<agent>"}`
- `tool_call`: `{"id": "<tc_id>", "name": "<tool>", "args": <obj>, "source": "<agent>"}`
- `tool_result`: `{"id": "<tc_id>", "name": "<tool>", "result": <obj>, "source": "<agent>", "error": "<msg>"?}`
- `delegation`: `{"target": "<agent>", "source": "router"|"deep", "message": "<说明>"}`

#### Scenario: reasoning 事件流式下发

- **WHEN** DeepAgent 输出 `<think>分析中</think>最终回答`
- **THEN** SSE 流先 yield `event: reasoning\ndata: {"content":"分析中","source":"deep"}`，再 yield `event: token\ndata: 最终回答`

#### Scenario: tool_call 事件含 args

- **WHEN** 子代理调用 `read_file(path="/tmp/a.txt")`
- **THEN** SSE 流 yield `event: tool_call\ndata: {"id":"<tc_id>","name":"read_file","args":{"path":"/tmp/a.txt"},"source":"code"}`

#### Scenario: tool_result 事件含 result

- **WHEN** `read_file` 返回文件内容
- **THEN** SSE 流 yield `event: tool_result\ndata: {"id":"<tc_id>","name":"read_file","result":"<content>","source":"code"}`

#### Scenario: delegation 事件标识子代理

- **WHEN** Router 静态分类委派给 code agent
- **THEN** SSE 流先 yield `event: delegation\ndata: {"target":"code","source":"router","message":"委派给代码子代理"}`，后续事件均来自 code agent

#### Scenario: 三处同步（AGENTS.md §13）

- **WHEN** 新增 SSE 事件类型
- **THEN** `backend/app/main.py`（_event_generator 透传）+ `frontend/preload/index.ts`（streamChat 解析）+ `frontend/renderer/hooks/useChatStream.ts`（part 分发）三处同步更新

### Requirement: thinking 分离而非丢弃

Backend SHALL 用 `split_think(content) -> tuple[str, str]` 替代 `strip_think` 的丢弃行为，返回 `(reasoning, visible_text)` 元组。`ThinkFilter.feed(chunk)` 返回 `(reasoning_chunk, text_chunk)` 元组，reasoning 内容作为 `reasoning` SSE 事件下发，text 内容作为 `token` SSE 事件下发。`strip_think` 保留为 deprecated wrapper（`return split_think(content)[1]`）。

#### Scenario: 纯文本无 think 标签

- **WHEN** content 为 `"hello world"`
- **THEN** `split_think` 返回 `("", "hello world")`

#### Scenario: 含 think 标签

- **WHEN** content 为 `"<think>分析</think>回答"`
- **THEN** `split_think` 返回 `("分析", "回答")`

#### Scenario: 跨 chunk think 标签

- **WHEN** chunk1 为 `"<thi"`，chunk2 为 `"nk>分析</think>回答"`
- **THEN** `ThinkFilter.feed(chunk1)` 返回 `("", "")`，`ThinkFilter.feed(chunk2)` 返回 `("分析", "回答")`

#### Scenario: strip_think 向后兼容

- **WHEN** 调用 `strip_think("<think>分析</think>回答")`
- **THEN** 返回 `"回答"`（等价于 `split_think(content)[1]`）

### Requirement: _convert_subagent_event 透传

Backend SHALL 把子代理的 `tool_call` / `tool_result` 标准化事件透传为同名 SSE 事件（不再压扁为 `todo_update`），保留 `name` / `args` / `result` 字段，并附 `source` 字段标识子代理类型。

#### Scenario: tool_call 透传

- **WHEN** 子代理 yield `{"type":"tool_call","name":"read_file","args":{"path":"/tmp"}}`
- **THEN** `_convert_subagent_event` yield `{"event":"tool_call","data":"{\"id\":\"<id>\",\"name\":\"read_file\",\"args\":{\"path\":\"/tmp\"},\"source\":\"code\"}"}`

#### Scenario: tool_result 透传

- **WHEN** 子代理 yield `{"type":"tool_result","name":"read_file","result":"<content>"}`
- **THEN** `_convert_subagent_event` yield `{"event":"tool_result","data":"{\"id\":\"<id>\",\"name\":\"read_file\",\"result\":\"<content>\",\"source\":\"code\"}"}`

#### Scenario: token 经 ThinkFilter 分离

- **WHEN** 子代理 yield `{"type":"token","content":"<think>分析</think>回答"}`
- **THEN** `_convert_subagent_event` yield `reasoning` 事件（content="分析"）+ `token` 事件（data="回答"）

### Requirement: DeepAgent tool 事件下发

Backend SHALL 在 `paths/deep_path.py::_stream_agent_events` 中，AIMessage with tool_calls 时 yield `tool_call` SSE 事件（含 name/args/source="deep"），ToolMessage 时 yield `tool_result` SSE 事件（含 name/result/source="deep"）。保留 `todo_update` 用于任务级 todo 列表（与 tool_call 事件并存，语义不同）。

#### Scenario: AIMessage with tool_calls

- **WHEN** DeepAgent 输出 AIMessage 含 `tool_calls=[{"name":"read_file","args":{"path":"/tmp"}}]`
- **THEN** yield `tool_call` SSE 事件 + `todo_update` 事件（任务进度）

#### Scenario: ToolMessage 工具完成

- **WHEN** DeepAgent 收到 ToolMessage(name="read_file", content="<file content>")
- **THEN** yield `tool_result` SSE 事件 + `todo_update` 事件（标记完成）

### Requirement: useChatStream part 分发

Renderer SHALL 在 `useChatStream` hook 中按 `event.type` 分发到对应 part 操作：

- `token` → 找最后一个 `type==="text"` part，append text；若无则新建 text part
- `reasoning` → 找最后一个 `type==="reasoning" && done===false` part，append text；若无则新建 reasoning part
- `tool_call` → 新建 tool-call part（status="running"）
- `tool_result` → 新建 tool-result part（与 tool-call 同 id）
- `delegation` → 新建 delegation part
- `done` → 标记所有 `reasoning` part 的 `done=true`（触发自动收缩）
- `todo_update` → 保留底部 TodoProgress（任务级进度，与 tool_call 事件并存）

#### Scenario: token 事件 append 到 text part

- **WHEN** useChatStream 收到 `{type:"token", data:"hello"}`
- **THEN** 当前 pending message 的最后一个 text part 的 text 追加 "hello"

#### Scenario: reasoning 事件 append 到 reasoning part

- **WHEN** useChatStream 收到 `{type:"reasoning", content:"分析"}`
- **THEN** 当前 pending message 的最后一个 reasoning part（done=false）的 text 追加 "分析"

#### Scenario: done 事件标记 reasoning 完成

- **WHEN** useChatStream 收到 `{type:"done"}`
- **THEN** 当前 pending message 所有 reasoning part 的 `done=true`

#### Scenario: tool_call 新建 part

- **WHEN** useChatStream 收到 `{type:"tool_call", id:"tc1", name:"read_file", args:{...}, source:"code"}`
- **THEN** 当前 pending message 新增 `{type:"tool-call", id:"tc1", toolName:"read_file", args:{...}, source:"code", status:"running"}` part

#### Scenario: tool_result 配对

- **WHEN** useChatStream 收到 `{type:"tool_result", id:"tc1", name:"read_file", result:"<content>", source:"code"}`
- **THEN** 当前 pending message 新增 `{type:"tool-result", id:"tc1", toolName:"read_file", result:"<content>", source:"code"}` part（与 tool-call part 同 id）

### Requirement: assistant-ui 渲染层集成

Renderer SHALL 用 `@assistant-ui/react` 的 `ExternalStoreRuntime` 替换自研 `MessageBubble` / `MessageList`，通过自定义 adapter 桥接 zustand store ↔ assistant-ui。`AssistantUIThread` 组件作为新的消息列表入口，`ChatView` 用其替换 `MessageList`。

#### Scenario: ExternalStoreRuntime 适配

- **WHEN** ChatView 渲染
- **THEN** `AssistantRuntimeProvider` 包裹 `AssistantUIThread`，runtime 由 `useExternalStoreRuntime({messages, isStreaming, onSend, onAbort})` 创建

#### Scenario: 自定义 part 渲染

- **WHEN** ChatMessage 含 tool-call part
- **THEN** assistant-ui 用 `ToolCallCard` 组件渲染（非默认 text 渲染）

#### Scenario: MessageBubble/MessageList 删除

- **WHEN** 渲染层集成完成
- **THEN** `MessageBubble.tsx` / `MessageList.tsx` 文件删除，无残留引用

### Requirement: thinking 流式 + 自动收缩

Renderer SHALL 在 `ReasoningBlock` 组件中：流式时（`done=false`）显示「思考中…」+ 动态省略号；完成时（`done=true`）自动收缩为单行「💡 已思考 N 秒」；点击展开回看完整 reasoning；展开/折叠状态记忆到 sessionStorage（按 message id 隔离）。

#### Scenario: 流式展示

- **WHEN** reasoning part 的 `done=false` 且 text 非空
- **THEN** 显示「思考中…」+ 三个跳动圆点（不展示流式文本，避免干扰）

#### Scenario: 完成自动收缩

- **WHEN** reasoning part 的 `done` 从 false 变为 true
- **THEN** 自动收缩为单行「💡 已思考 N 秒」（N 为该 part 持续秒数，从 part 创建到 done=true）

#### Scenario: 点击展开

- **WHEN** 用户点击已收缩的 ReasoningBlock
- **THEN** 展开显示完整 reasoning 文本；状态记忆到 sessionStorage

#### Scenario: 状态记忆

- **WHEN** 用户刷新页面
- **THEN** 每个 ReasoningBlock 的展开/折叠状态从 sessionStorage 恢复（key 含 message id）

### Requirement: ToolCallCard 折叠卡片

Renderer SHALL 把 tool-call part 和 tool-result part 按 `id` 配对合并为单个 `ToolCallCard`，默认折叠单行「🔧 tool_name(args 预览) ✓/⏳/✗」，点击展开 args/result JSON。三态展示：running（无配对 tool-result，⏳）/ complete（有配对 tool-result 且无 error，✓）/ error（tool-result 有 error 字段，✗）。

#### Scenario: running 状态

- **WHEN** tool-call part 存在但无同 id 的 tool-result part
- **THEN** 显示「🔧 read_file(path="/tmp/a.txt") ⏳」

#### Scenario: complete 状态

- **WHEN** tool-call part 有同 id 的 tool-result part 且无 error
- **THEN** 显示「🔧 read_file(path="/tmp/a.txt") ✓」

#### Scenario: error 状态

- **WHEN** tool-result part 有 error 字段
- **THEN** 显示「🔧 read_file(path="/tmp/a.txt") ✗」

#### Scenario: 点击展开

- **WHEN** 用户点击 ToolCallCard 单行
- **THEN** 展开显示 args 和 result 的 JSON（result 截断 1000 字符，超出显示「... truncated」）

#### Scenario: args 预览

- **WHEN** tool-call part 的 args 为 `{path:"/tmp/a.txt", encoding:"utf-8"}`
- **THEN** 单行预览取第一个标量字段值，显示 `path="/tmp/a.txt"`（截断 50 字符）

### Requirement: DelegationCard 子代理委派标记

Renderer SHALL 在 assistant message 含 `delegation` part 时，用 `DelegationCard` 组件渲染，header 显示子代理图标 + 名称（如「🔧 由 code agent 执行」），body 渲染该 message 后续的所有 parts（reasoning/tool-call/tool-result/text）。当前架构是 Router 静态分类，DelegationCard 仅做 header 标记；预留 `children: MessagePart[]` 字段用于未来 DeepAgent 动态委派时的真正嵌套。

#### Scenario: 路径 B 委派标记

- **WHEN** Router 静态分类委派给 code agent，SSE 流先发 delegation 事件
- **THEN** assistant message 第一个 part 是 delegation，DelegationCard header 显示「🔧 由 code agent 执行」

#### Scenario: 子代理图标区分

- **WHEN** delegation part 的 target 为 "code" / "rag" / "web" / "custom-xxx"
- **THEN** DelegationCard header 显示对应图标（code: </> / rag: 📚 / web: 🌐 / custom: 🤖）

#### Scenario: 预留嵌套数据结构

- **WHEN** 未来 DeepAgent 动态委派子代理
- **THEN** delegation part 可扩展 `children: MessagePart[]` 字段，DelegationCard body 渲染 children（本次不实现，仅预留）

## MODIFIED Requirements

### Requirement: Markdown 渲染（chat-rendering archive）

Renderer SHALL 用 `react-markdown` 渲染 assistant 消息的 `text` part 内容，支持标题、列表、表格、链接、代码块等 Markdown 语法。用户消息保持纯文本（`whitespace-pre-wrap`）。原 `MessageBubble` 的 ReactMarkdown 渲染迁移到 `TextPartView` 组件，作为 assistant-ui 的自定义 text part 渲染器。

#### Scenario: 渲染 Markdown

- **WHEN** assistant 消息的 text part 内容为 `# 标题\n\n- 列表项\n\n\`\`\`python\nprint("hi")\n\`\`\``
- **THEN** TextPartView 渲染为 H1 标题、无序列表、带高亮的代码块

#### Scenario: 用户消息保持纯文本

- **WHEN** 用户消息的 text part 内容包含 Markdown 语法
- **THEN** 用 `whitespace-pre-wrap` 纯文本显示，不解析 Markdown
