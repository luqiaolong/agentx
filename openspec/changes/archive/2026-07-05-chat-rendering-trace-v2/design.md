## Context

当前项目状态（截至 2026-07-05）：
- 后端 M1 闭环 + chat-context-management 已合并：Router 三路径 + 历史注入 + 滑动窗口 + 子代理 + 沙箱 + 审批 + SSE
- 前端骨架：多会话 zustand store（v2 持久化）、ChatView + MessageBubble + MessageList + TodoProgress + ChatComposer
- **核心缺陷**：聊天框只能呈现扁平 `{content: string}`，thinking 被剥离，工具调用 args/result 丢弃，子代理无可见性，TodoProgress 与消息流割裂
- 业界事实标准：Vercel AI SDK parts-based message model（`message.parts: typed[]`）、assistant-ui 7k+ stars React 组件库、LangGraph `astream_events` v2 原生支持 on_chat_model_stream/on_tool_start/on_tool_end

设计约束（来自 `project_memory.md` + AGENTS.md）：
- 开发阶段无需考虑灰度和兼容老版本，直接推倒重来
- AGENTS.md §1.1 优先用现成框架，禁止自研 chat UI 组件
- AGENTS.md §13 SSE 事件契约修改必须同步 main.py + preload/index.ts + useChatStream.ts 三处
- AGENTS.md §14.6 路由别名 `@` → `frontend/renderer`
- 凭证走 `electron-store` + `safeStorage`，本次不涉及凭证
- 禁止热路径 `new ObjectMapper()`（Python 端等价：禁止热路径 `json.dumps` 重复构造，复用 `_sse` helper）
- ThinkFilter 实际位置：`backend/app/utils/text.py`（与 `strip_think` 同文件，非 `memory/skills_loader.py`）

## Goals / Non-Goals

**Goals:**
- 后端 SSE 完整下发 reasoning / tool_call / tool_result / delegation 事件，不再丢弃任何执行轨迹信息
- 前端 ChatMessage 升级为 parts-based，能完整表达"一个 turn = N 个工具调用 + thinking + 最终回答"
- 引入 assistant-ui 替换自研 MessageBubble/MessageList，原生支持 tool-call 卡 / reasoning 块 / 嵌套
- thinking 流式展示，完成后自动收缩，可点击展开回看
- 工具调用内联为折叠卡片（默认单行，点击展开 args/result JSON）
- 子代理委派用 DelegationCard 标识来源，预留嵌套 parts 数据结构
- 所有改动有单测覆盖

**Non-Goals:**
- 不重构 Router 三路径分类逻辑 — 仅扩展 SSE 事件下发
- 不做 DeepAgent 动态委派子代理 — 当前架构是 Router 静态分类，DelegationCard 仅落地"header 标记"形态
- 不做设置页开关（thinking 显示/隐藏）— YAGNI，默认流式 + 折叠即可
- 不做跨会话执行轨迹检索 — 仅当前 turn 内的 parts 展示
- 不做工具调用的人工审批 UI 改造 — ApprovalDialog 保留不动
- 不做 ChatComposer 改造 — 输入区不变
- 不做 Session 多会话结构改造 — 仅 ChatMessage 内部结构升级

## Decisions

### D1. SSE 事件契约扩展方式：新增事件类型，不改协议格式

**选择**：保留 `event: <type>\ndata: <json>` 的 SSE 格式，仅新增 4 种事件类型 `reasoning` / `tool_call` / `tool_result` / `delegation`，与现有 `token` / `todo_update` / `approval_request` / `done` / `error` 并存。

**理由**：
- AGENTS.md §13 要求"修改任一事件类型或字段名，必须同步更新 main.py + preload/index.ts + useChatStream.ts 三处" — 新增类型比修改现有类型风险低
- preload 的 `streamChat` 解析逻辑是通用的（按 `event:` / `data:` 行解析），无需改解析器，只新增前端分发逻辑
- `todo_update` 保留 — DeepAgent 的 todo 列表（任务级进度）与 `tool_call`（单次工具调用）语义不同，前者是规划，后者是执行

**事件 payload 规范**：
```
reasoning:    {"content": "<chunk>", "source": "deep"|"code"|"rag"|"web"|"custom-xxx"}
tool_call:    {"id": "<tc_id>", "name": "<tool>", "args": <obj>, "source": "<agent>"}
tool_result:  {"id": "<tc_id>", "name": "<tool>", "result": <obj>, "source": "<agent>", "error": "<msg>"?}
delegation:   {"target": "code"|"rag"|"web"|"custom-xxx", "source": "router"|"deep", "message": "<说明>"}
token:        "<text>"  (纯字符串，不变)
todo_update:  {"todos": [{"text": "...", "done": bool}]}  (不变)
```

**备选**：
- 改用 Vercel AI SDK 的 data-stream protocol — 协议更标准，但需重写后端 SSE 生成器 + preload 解析器，改动面过大，YAGNI
- 把所有事件塞进 `token` 事件用 JSON 区分 — 破坏现有 `token` 语义，前端解析复杂化

### D2. thinking 分离而非丢弃：split_think + ThinkFilter 分离模式

**选择**：`backend/app/utils/text.py` 新增 `split_think(content) -> tuple[str, str]`，返回 `(reasoning, visible_text)`；同文件内 `ThinkFilter.feed(chunk)` 改为返回 `(reasoning_chunk, text_chunk)` 元组。`_convert_subagent_event` 把 reasoning_chunk 作为 `reasoning` 事件 yield，text_chunk 作为 `token` 事件 yield。

**理由**：
- 流式场景下 `<think>` 标签可能跨 chunk，需要状态机（ThinkFilter 已有 `in_think` 状态）
- 改为分离模式后，ThinkFilter 的状态机逻辑不变，只是输出从"丢弃 reasoning"变为"分离 reasoning"
- `strip_think` 保留为 deprecated（仅向后兼容，新代码用 `split_think`）

**ThinkFilter 状态机**（与现有实现一致，仅输出方式变）：
- 初始 `in_think=False`
- 遇到 `<think>` → `in_think=True`，后续 chunk 进 reasoning 通道
- 遇到 `</think>` → `in_think=False`，后续 chunk 进 text 通道
- 跨 chunk 的 `<think>` / `</think>` 标签用缓冲区拼接处理（已有逻辑）

**备选**：
- 前端解析 `<think>` 标签 — 流式 chunk 边界不可控，前端状态机复杂，且 reasoning 内容本就不该下发到前端再剥离
- 完全不下发 reasoning — 用户明确要求"流式输出思考过程"，违反需求

### D3. 前端 parts 模型：typed union + 迁移 v2→v3

**选择**：`ChatMessage.parts: MessagePart[]`，`MessagePart` 是 typed union（discriminated union on `type` 字段）。zustand store 持久化版本 v2→v3，`migrate` 函数把旧 `{content: string}` 转为 `[{type:"text", id: uuid, text: content}]`。

**理由**：
- discriminated union 类型安全，TS 编译期穷尽检查，避免运行时 typo
- parts 顺序天然表达时间轴（按数组顺序）
- 持久化迁移 v2→v3 复用现有 `migrate` 函数模式（已有 v0→v1→v2）
- 用户消息也用 parts（`[{type:"text"}]`）— 统一模型，避免 role-based 分支

**Part id 生成**：
- text/reasoning part：`crypto.randomUUID()`，append 时创建新 part
- tool-call part：用 SSE `tool_call.id` 作为 part id（保证 tool_result 能回填到对应 tool-call）
- delegation part：`crypto.randomUUID()`

**append 语义**（useChatStream 分发逻辑）：
- `token` → 找最后一个 `type==="text"` part，append text；若无则新建 text part
- `reasoning` → 找最后一个 `type==="reasoning" && done===false` part，append text；若无则新建 reasoning part
- `tool_call` → 新建 tool-call part（status="running"）
- `tool_result` → 找 `type==="tool-call" && id===event.id` part，新增 tool-result part 在其后（或更新 tool-call status="complete"）— **决策：新增独立 tool-result part**（保留 args 和 result 各自完整，避免 part 内字段污染）
- `delegation` → 新建 delegation part
- `done` 事件 → 标记所有 `reasoning` part 的 `done=true`（触发自动收缩）

**备选**：
- tool_result 回填到 tool-call part 的 `result` 字段 — part 状态变复杂（running/complete），且 args/result 共存一个 part 不利于分别渲染
- 每个工具调用合并为单个 part 含 args+result — 流式时 result 还没来就要先展示 args，需要 status 字段，复杂度等价但 part 数量少一半
- **最终决策**：tool-call 和 tool-result 分开两个 part，按时间顺序排列，UI 渲染时按 `id` 配对合并展示（卡片内同时显示 args 和 result）

### D4. assistant-ui 集成方式：ExternalStoreRuntime 自定义适配器

**选择**：使用 `@assistant-ui/react` 的 `ExternalStoreRuntime`，自定义 adapter 桥接 zustand store ↔ assistant-ui。不用 `useChatRuntime`（绑定 Vercel AI SDK 协议）、不用 `useLangGraphRuntime`（绑定 LangGraph Server 协议）。

**理由**：
- 项目 SSE 协议是自定义的（FastAPI EventSourceResponse），不兼容 Vercel AI SDK 或 LangGraph Server 协议
- `ExternalStoreRuntime` 允许自定义 store 接口，zustand store 可直接适配
- 保留 zustand 的多会话持久化 + 配额保护逻辑（createQuotaGuardedStorage）
- assistant-ui 的 Thread/Message/ToolCallCard 等组件可按需组合，不强制全量替换

**adapter 接口**：
```ts
// runtime/ExternalStoreAdapter.ts
export function useExternalStoreRuntime(options: {
  messages: ChatMessage[];
  isStreaming: boolean;
  onSend: (text: string) => void;
  onAbort: () => void;
}): ExternalStoreRuntime {
  // 把 ChatMessage.parts 映射为 assistant-ui 的 ThreadMessage
  // assistant-ui 原生支持 parts: [{type:"text"}, {type:"tool-call"}, {type:"reasoning"}, ...]
  // 自定义 part type 用 makeAssistantTool 注册
}
```

**备选**：
- `useLangGraphRuntime` — 绑定 LangGraph Server 协议（需后端实现 LangGraph Server API），改动后端过大
- `useChatRuntime` + Vercel AI SDK — 需后端改为 Vercel AI SDK stream protocol，改动 SSE 生成器过大
- 完全自研 UI — 违反 AGENTS.md §1.1"优先用现成框架"

### D5. 工具调用渲染：内联折叠卡 + 配对合并

**选择**：tool-call part 和 tool-result part 在 UI 渲染时按 `id` 配对合并为单个 `ToolCallCard`，默认折叠单行「🔧 tool_name(args 预览) ✓/⏳」，点击展开 args/result JSON。

**理由**：
- parts 模型按时间顺序存储（tool-call 在前，tool-result 在后），但 UI 展示需合并为单个卡片
- 配对逻辑：`parts.find(p => p.type==="tool-result" && p.id === toolCall.id)`
- 默认折叠：长 args/result（如 read_file 返回大文件内容）会刷屏
- args 预览：取 args 对象的第一个标量字段值（如 `path` / `pattern`），截断 50 字符

**状态展示**：
- `running`（无配对 tool-result）：⏳ + 工具名 + args 预览
- `complete`（有配对 tool-result）：✓ + 工具名 + args 预览
- `error`（tool-result 有 error 字段）：✗ + 工具名 + 错误预览

**备选**：
- 侧边轨迹面板（Cline 风格）— Electron 窗口窄，挤压聊天区
- 底部 TodoProgress 增强 — 与消息流割裂问题未解决

### D6. 子代理委派展示：DelegationCard header 标记 + 预留嵌套

**选择**：路径 B 子代理执行时，`run_router` 入口 yield `delegation` 事件，前端在 assistant message 开头插入 `delegation` part，UI 渲染为 DelegationCard 标识"🔧 由 code agent 执行"。该 message 后续的所有 parts（reasoning/tool-call/tool-result/text）都属于该子代理。

**当前架构限制**：
- Router 是静态分类（classifier → 路径 A/B/C），不是主 agent 动态委派
- 路径 B 子代理执行时，主 agent 不参与，所以"委派"是 Router 的路由决策，不是 agent 的动态委派
- 因此 DelegationCard 当前仅做 header 标记，不做嵌套 parts

**未来扩展**（本次预留数据结构，不实现）：
- 当 DeepAgent 支持动态委派子代理时，`delegation` part 增加 `children: MessagePart[]` 字段，body 嵌套子 agent 的所有 parts
- DelegationCard 的 body 渲染 `children`，实现真正的嵌套展示

**source 字段值**：
- `"router"` — Router 静态分类委派
- `"deep"` — DeepAgent 动态委派（未来）

**备选**：
- 不做 DelegationCard，仅用 source 字段在 ToolCallCard 上标识 — 子代理级别信息丢失，用户看不到"这条消息由谁执行"
- 完整嵌套 parts — 当前架构不支持，过度设计

### D7. 持久化迁移：v2→v3 parts 转换

**选择**：zustand `persist` 的 `version: 3`，`migrate` 函数 v2→v3 把 `ChatMessage.content: string` 转为 `parts: [{type:"text", id: crypto.randomUUID(), text: content}]`。

**理由**：
- 复用现有 `migrateV0toV1` / `migrateV1toV2` 模式
- 用户消息和 assistant 消息都用 `[{type:"text"}]` 兼容
- tool 消息（如 /reset 提示）同样转为 text part

**迁移逻辑**：
```ts
function migrateV2toV3(persisted: unknown): Partial<ChatState> {
  const p = (persisted ?? {}) as Record<string, unknown>;
  const rawSessions = (p.sessions ?? {}) as Record<string, Record<string, unknown>>;
  const sessions: Record<string, Session> = {};
  for (const [id, raw] of Object.entries(rawSessions)) {
    if (!raw || typeof raw !== "object") continue;
    const oldMessages = Array.isArray(raw.messages) ? raw.messages as Array<Record<string, unknown>> : [];
    const newMessages: ChatMessage[] = oldMessages.map(m => ({
      id: typeof m.id === "string" ? m.id : crypto.randomUUID(),
      role: m.role as "user" | "assistant" | "tool",
      parts: [{ type: "text", id: crypto.randomUUID(), text: String(m.content ?? "") }],
      ts: typeof m.ts === "number" ? m.ts : Date.now(),
    }));
    sessions[id] = {
      id: typeof raw.id === "string" ? raw.id : id,
      title: typeof raw.title === "string" ? raw.title : DEFAULT_TITLE,
      messages: newMessages,
      createdAt: typeof raw.createdAt === "number" ? raw.createdAt : Date.now(),
      workspacePath: typeof raw.workspacePath === "string" && raw.workspacePath.length > 0 ? raw.workspacePath : null,
    };
  }
  return { sessions, currentId: typeof p.currentId === "string" ? p.currentId : null };
}
```

## Risks / Trade-offs

- **[assistant-ui 适配层复杂度]** → ExternalStoreRuntime 需自定义 store 接口映射，初次集成有学习成本。缓解：先做最小适配（仅 text + tool-call + reasoning parts），DelegationCard 用自定义 part type
- **[parts 模型与 assistant-ui 内部模型映射]** → assistant-ui 的 `ThreadMessage` 有自己的 parts 类型定义，需做映射层。缓解：映射层集中在 `runtime/ExternalStoreAdapter.ts`，不污染业务 store
- **[SSE 事件增多对性能影响]** → reasoning + tool_call + tool_result 事件量增加，但单 turn 总量仍可控（< 100 事件）。缓解：前端 useDeferredValue + React.memo 优化重渲染
- **[持久化体积增加]** → parts 模型比 content 字符串体积大（每 part 有 id + type 字段）。缓解：localStorage 配额保护已有，超限归档
- **[ThinkFilter 分离模式向后兼容]** → 现有 `strip_think` 调用方（如 chat_path.py）需迁移到 `split_think`。缓解：保留 `strip_think` 为 deprecated wrapper（`return split_think(content)[1]`），逐步迁移
- **[tool_call / tool_result 配对失败]** → 如果 SSE 事件丢失或顺序错乱，tool-result 可能找不到对应 tool-call。缓解：part id 用 SSE event.id 强匹配，配对失败时 tool-result 单独渲染为「孤儿结果」卡片

## Migration Plan

**阶段 1（P0 后端）：SSE 事件契约扩展**
1. `backend/app/utils/text.py` 新增 `split_think`（独立 commit）
2. `backend/app/utils/text.py` ThinkFilter 改分离模式（独立 commit）
3. `backend/app/subagents/*.py` 事件补 `source` 字段（独立 commit）
4. `backend/app/router/graph.py` `_convert_subagent_event` 透传 tool_call/tool_result + delegation（独立 commit）
5. `backend/app/paths/deep_path.py` tool_call/tool_result 事件（独立 commit）
6. 后端单测更新：test_text_filter / test_router_graph / test_sse_contract（独立 commit）

**阶段 2（P0 前端 store）：parts 模型 + 迁移**
7. `frontend/shared/api-types.ts` ChatEvent 扩展（独立 commit）
8. `frontend/renderer/stores/chat.ts` parts 模型 + migrate v2→v3（独立 commit）
9. `frontend/renderer/hooks/useChatStream.ts` part 分发逻辑（独立 commit）
10. 前端单测更新：chat-store / useChatStream（独立 commit）

**阶段 3（P0 渲染层）：assistant-ui 集成**
11. `package.json` 新增 assistant-ui 依赖（独立 commit）
12. `frontend/renderer/components/chat/runtime/ExternalStoreAdapter.ts`（独立 commit）
13. `frontend/renderer/components/chat/parts/` 各 part 组件（独立 commit）
14. `frontend/renderer/components/chat/AssistantUIThread.tsx`（独立 commit）
15. `frontend/renderer/components/chat/ChatView.tsx` 替换 MessageList（独立 commit）
16. 删除 `MessageBubble.tsx` / `MessageList.tsx`（独立 commit）

**阶段 4（P1 体验）：thinking 流式 + DelegationCard**
17. `ReasoningBlock.tsx` 自动收缩 + sessionStorage 状态（独立 commit）
18. `DelegationCard.tsx` header 标记 + 预留嵌套（独立 commit）
19. 前端渲染单测：parts-rendering.test.tsx（独立 commit）

## Test Plan

### 后端测试

- `tests/python/unit/test_text_filter.py`：
  - `split_think`：纯文本返回 `("", text)`；含 `<think>` 返回 `(reasoning, text)`；跨 chunk 标签正确拼接
  - `ThinkFilter.feed` 分离模式：reasoning_chunk 和 text_chunk 分别 yield；`flush` 返回 `(reasoning_tail, text_tail)`
  - 向后兼容：`strip_think` 仍返回纯 text（调用 `split_think` 取 [1]）
- `tests/python/unit/test_router_graph.py`：
  - `_convert_subagent_event` 透传：tool_call 事件 yield `tool_call` SSE（含 name/args/source）；tool_result 事件 yield `tool_result` SSE（含 name/result/source）
  - 路径 B 入口 yield `delegation` 事件
  - token 事件经 ThinkFilter 分离后 yield `reasoning` + `token` 两个事件
- `tests/python/unit/test_sse_contract.py`：
  - 新事件类型 payload schema 校验：reasoning/tool_call/tool_result/delegation 字段完整
  - 与 preload `streamChat` 解析兼容（payload 是 JSON 对象时展开到 ChatEvent 顶层）
- `tests/python/integration/test_smoke.py`：
  - 路径 B 子代理执行时，SSE 流包含 tool_call/tool_result 事件（不再被压扁为 todo_update）

### 前端测试

- `tests/renderer/chat-store.test.ts`：
  - parts 模型：addPart / updatePart / appendPartText 操作正确
  - 迁移 v2→v3：旧 `{content: "hello"}` 转为 `parts: [{type:"text", text:"hello"}]`
  - 配额保护：parts 模型下仍生效
- `tests/renderer/useChatStream.test.ts`：
  - `token` 事件 append 到 text part
  - `reasoning` 事件 append 到 reasoning part，`done` 事件标记 reasoning.done=true
  - `tool_call` 事件新建 tool-call part
  - `tool_result` 事件新建 tool-result part（与 tool-call 同 id）
  - `delegation` 事件新建 delegation part
- `tests/renderer/parts-rendering.test.tsx`（新增）：
  - `ToolCallCard`：running/complete/error 三态渲染；args/result JSON 折叠展开
  - `ReasoningBlock`：流式时显示「思考中…」；done=true 自动收缩；点击展开
  - `DelegationCard`：header 显示子代理图标 + 名称
  - `AssistantUIThread`：parts 按顺序渲染；tool-call 和 tool-result 配对合并为单卡片
- `tests/renderer/smoke.test.tsx`：
  - 整体渲染不崩溃；多会话切换正常

### 验收命令

```bash
# 后端
uv run pytest tests/python/unit -m "not integration" --tb=short
uv run ruff check backend/

# 前端
npm run typecheck
npm test
```
