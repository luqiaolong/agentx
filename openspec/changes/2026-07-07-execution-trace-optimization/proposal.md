## Why

上一轮 `chat-rendering-trace-v2`（archive 2026-07-05）落地了 parts-based 消息模型与基础卡片渲染，但实际使用中暴露出 **性能、交互、架构** 三类问题（详见 2026-07-07 会话分析，共 16 项）：

### 性能问题（P0）

1. **流式 token 写入触发全量重渲 + 全量持久化** — `appendPartText`（[chat/index.ts#L424](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/chat/index.ts)）每次 token 都 `map` 整个 messages 数组、调用 `findSessionIdByMessageId` 全量扫描所有 sessions、调用 `deriveContent` 拼接 content、写入 localStorage。长会话下每秒几十次 token 会卡顿。
2. **`findSessionIdByMessageId` 全量扫描** — [messageOps.ts#L18-L28](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/chat/messageOps.ts) 每次 part 操作都遍历所有 sessions × 所有 messages 定位，O(sessions × messages)。
3. **`team_plan` N 次写入** — [useChatStream.ts#L260-L280](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) 先 upsert 整个 plan，再循环为每个 agent 单独 upsert，N 个 agent 触发 N 次 `set`，每次重建整个 messages 数组。
4. **`buildRenderItems` 每次重渲都重算** — [AssistantUIThread.tsx#L148](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/AssistantUIThread.tsx) `useMemo` 依赖 `message.parts`，而 `appendPartText` 每次 token 都生成新 parts 数组，memo 失效。
5. **消息列表无虚拟化** — [AssistantUIThread.tsx#L372-L387](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/AssistantUIThread.tsx) 全量渲染所有消息，长会话（100+ 消息 + Markdown + 多卡片）卡死。

### 交互/UX 问题（P1）

6. **Text parts 强制排末尾导致 UI 跳动** — [AssistantUIThread.tsx#L128](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/AssistantUIThread.tsx) 把 text parts 收集后追加在末尾，模型「先输出一段 text → 调工具 → 继续 text」时已显示文本突然跳到工具卡片下方。
7. **ReasoningBlock 思考时长不准** — [ReasoningBlock.tsx#L53](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/parts/ReasoningBlock.tsx) 用组件挂载时间作为开始时间，组件卸载重挂后耗时重置为 0。
8. **流式思考过程不可见** — [ReasoningBlock.tsx#L82-93](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/parts/ReasoningBlock.tsx) 流式时只显示"思考中"三个跳动圆点，无法实时查看 reasoning 文本。
9. **ToolCallCard 信息不足** — [ToolCallCard.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/parts/ToolCallCard.tsx) args 预览只取首个标量字段、result 截断后无展开、无复制按钮、无 source 展示、无执行耗时。
10. **无批量折叠能力** — 多个连续 tool-call（如 10 次文件读取）占据大量纵向空间，无法折叠为汇总行。
11. **右侧导航条进度不更新** — [ChatView.tsx#L449-L455](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ChatView.tsx) 进度条 `style` 依赖 `scrollTop` 命令式读取，不触发重渲。
12. **`useAutoScroll` 每次依赖变化都 smooth 滚动** — [useAutoScroll.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useAutoScroll.ts) 流式时每个 token 都触发 `scrollIntoView({ behavior: "smooth" })`，动画堆积卡顿。

### 数据/架构问题（P2）

13. **`MessageParts` 职责过重** — [AssistantUIThread.tsx#L133-L353](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/AssistantUIThread.tsx) 同时处理 user 编辑气泡、tool 系统提示、assistant parts 渲染三种分支，user 编辑逻辑占了近一半代码。
14. **TeamNodeCard key 不稳定** — [TeamNodeCard.tsx#L76](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/parts/TeamNodeCard.tsx) `key={`${a.agent}-${i}`}` 用索引，agent 重排或增删会丢失内部状态。
15. **sessionStorage 残留** — [ReasoningBlock.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/parts/ReasoningBlock.tsx) `reasoning-expanded:*` key 在消息删除后不清理。
16. **兼容字段 `content` 冗余** — [chat/index.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/chat/index.ts) 渲染层已全量迁移到 parts，但每次写仍调 `deriveContent` 维护 `content`，浪费开销。

## What Changes

### P0 — 性能治理

- **MODIFY** `frontend/renderer/stores/chat/index.ts`：
  - `appendPartText` 走**非持久化快路径**：更新内存 state 但不立即触发 `persist` 写入；用 `requestIdleCallback`（fallback `setTimeout 300ms`）debounce 批量落 localStorage
  - 流式期间（`isStreaming=true`）`partialize` 输出空 sessions 增量，流式结束后才同步落盘
- **NEW** `frontend/renderer/stores/chat/messageIndex.ts`：维护 `messageId → sessionId` 反向索引，`addMessage` 写入、`deleteMessage` / `deleteSession` / `clearMessages` 清除，`appendPartText` / `addPart` / `updatePart` / `upsertTeamNode` / `markReasoningDone` 全部 O(1) 查找
- **MODIFY** `frontend/renderer/hooks/useChatStream.ts`：`team_plan` 事件改为单次 `upsertTeamNode` 调用，传入 `plan + initialAgents[]`（status 全为 pending），由 store 一次 `set` 完成
- **MODIFY** `frontend/renderer/components/chat/AssistantUIThread.tsx`：`buildRenderItems` 结果缓存到 `useMemo` + 稳定依赖（仅 parts 引用变化时重算），同时让 token 追加到 text part 时**不触发** tool-call 卡片重渲（用 selector 精细化订阅）
- **NEW** 引入 `@tanstack/react-virtual` 对消息列表做窗口化渲染，仅渲染视口内 + 上下缓冲区 5 条消息

### P1 — 交互/UX 优化

- **MODIFY** `frontend/renderer/components/chat/AssistantUIThread.tsx`：取消「text parts 强制末尾」规则，按真实 parts 顺序渲染；若需「结论在后」视觉，用 `<div className="border-t">` 分隔而非重排
- **MODIFY** `frontend/renderer/components/chat/parts/ReasoningBlock.tsx`：
  - part 数据模型增加 `startedAt` 字段（首个 reasoning 事件时由 store 写入）；`elapsedSec = (doneAt ?? now) - startedAt`
  - 流式时（`!done`）若有 text，展示**可滚动预览区**（max-height: 120px + overflow-auto），完成后自动收缩
- **MODIFY** `frontend/renderer/components/chat/parts/ToolCallCard.tsx`：
  - result 超长（>1000 字符）时加「显示完整」按钮（展开后无截断）
  - args / result 加复制按钮（`navigator.clipboard.writeText`）
  - 展示 `source`（子代理名 chip）
  - 展示执行耗时（`complete` 时显示 `· 1.2s`）
- **NEW** `frontend/renderer/components/chat/parts/ToolCallGroup.tsx`：连续 ≥3 个同类 tool-call 自动折叠为汇总行「执行了 N 个 {toolName} 调用（M 成功 / K 失败）」，点击展开
- **MODIFY** `frontend/renderer/components/chat/ChatView.tsx`：右侧导航条进度用 `rAF` throttle 的 `scrollTop` state 驱动，订阅 `scroll` 事件独立更新
- **MODIFY** `frontend/renderer/hooks/useAutoScroll.ts`：流式期间（`isStreaming=true`）用 `behavior: "auto"`，非流式时才用 `"smooth"`

### P2 — 架构清理

- **MODIFY** `frontend/renderer/components/chat/AssistantUIThread.tsx`：拆分 `MessageParts` 为三个独立组件：
  - `UserMessageBubble`（含编辑逻辑，迁移自原 L150-L253）
  - `ToolSystemMessage`（tool role 系统提示，迁移自原 L256-L268）
  - `AssistantMessageParts`（assistant parts 列表，迁移自原 L270-L353）
- **MODIFY** `frontend/renderer/components/chat/parts/TeamNodeCard.tsx`：`key` 改用 `agent` 名（重名时拼接 plan 中的 input 哈希）
- **MODIFY** `frontend/renderer/components/chat/parts/ReasoningBlock.tsx`：组件卸载时若对应 messageId 已不在 store 中，清理对应 sessionStorage key（best-effort）
- **MODIFY** `frontend/renderer/stores/chat/index.ts` + `frontend/renderer/stores/chat/migrations.ts`：移除兼容字段 `content`，新增迁移 v5→v6 删除 `content` 字段；`addMessage` / `appendPartText` / `addPart` / `upsertTeamNode` / `updatePart` 不再调用 `deriveContent`
- **MODIFY** `frontend/renderer/stores/chat/messageOps.ts`：移除 `deriveContent`（不再需要）

## Capabilities

### Modified Capabilities

- `chat-rendering-trace`（archive 2026-07-05-chat-rendering-trace-v2）：从「基础 parts 渲染」升级为「高性能 parts 渲染 + 增强交互 + 清理架构」

## Impact

- **代码影响**：
  - store 层：`stores/chat/index.ts`（debounce 持久化 + messageIndex + 移除 content）、`stores/chat/messageOps.ts`（移除 deriveContent）、`stores/chat/migrations.ts`（v5→v6）、新增 `stores/chat/messageIndex.ts`
  - SSE 层：`hooks/useChatStream.ts`（team_plan 单次 upsert）
  - 渲染层：`components/chat/AssistantUIThread.tsx`（拆分 + 取消 text 重排 + 虚拟化）、新增 `components/chat/UserMessageBubble.tsx` / `ToolSystemMessage.tsx` / `AssistantMessageParts.tsx`
  - 卡片层：`parts/ReasoningBlock.tsx`（startedAt + 流式预览）、`parts/ToolCallCard.tsx`（复制 + 展开 + source + 耗时）、新增 `parts/ToolCallGroup.tsx`、`parts/TeamNodeCard.tsx`（key 稳定）
  - 容器层：`components/chat/ChatView.tsx`（导航条进度）、`hooks/useAutoScroll.ts`（流式 auto）
  - 类型层：`shared/api-types.ts`（reasoning 事件增加 startedAt）
- **API 影响**：无（SSE 事件类型不变，仅扩展 reasoning 事件的 `startedAt` 字段）
- **依赖影响**：新增 `@tanstack/react-virtual` npm 包
- **数据影响**：localStorage 持久化的 `ChatMessage` 结构移除 `content` 字段，需迁移 v5→v6；流式期间不持久化中间态
- **测试影响**：
  - 更新 `tests/renderer/parts-rendering.test.tsx`（覆盖新增的 ToolCallCard 复制/展开/source/耗时、ReasoningBlock 流式预览、ToolCallGroup 折叠）
  - 新增 `tests/renderer/store-perf.test.ts`（验证 debounce 持久化、messageIndex O(1) 查找、team_plan 单次 upsert）
  - 新增 `tests/renderer/message-index.test.ts`（messageIndex 增删改查一致性）
