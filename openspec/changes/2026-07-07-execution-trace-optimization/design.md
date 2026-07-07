## Context

执行轨迹 v1（chat-rendering-trace-v2）已上线，但暴露出性能、交互、架构三类共 16 项问题。本设计文档记录关键决策。

## Goals / Non-Goals

**Goals**
- 流式 token 写入不卡顿（长会话 100+ 消息下 60fps）
- 思考过程可实时查看
- ToolCallCard 信息完整可读
- 长会话消息列表虚拟化
- 移除冗余的 `content` 兼容字段

**Non-Goals**
- 不重写 SSE 协议（仅扩展 reasoning 事件增加 startedAt）
- 不引入新的 UI 框架（保留 ReactMarkdown + 自研卡片）
- 不做后端性能优化（仅前端）
- 不重构 TodoProgress（任务级进度，与 part 级轨迹并存）

## Decisions

### D1: 流式 token 持久化策略 — `partialize` + `isStreaming` 闸门

**决策**：在 zustand `persist` 的 `partialize` 中检查 `isStreaming`，流式期间输出空 sessions 增量；流式结束（`setStreaming(false)`）时一次性同步落盘。

**理由**：
- 不需要修改 zustand 内部机制，复用 `partialize` 钩子
- 流式期间 token 写入只更新内存 state，不触发 localStorage I/O
- 流式结束的 `setStreaming(false)` 会自动触发 `partialize` 重新计算并落盘

**实现**：
```ts
partialize: (s) => {
  if (s.isStreaming) {
    // 流式期间不持久化 sessions 增量（仅保留 currentId 等元数据）
    return { currentId: s.currentId, homeWorkspacePath: s.homeWorkspacePath };
  }
  return { sessions: s.sessions, currentId: s.currentId, homeWorkspacePath: s.homeWorkspacePath };
}
```

**风险**：流式中途崩溃会丢失流式期间的 token。可接受（用户可重新发送）。

### D2: messageIndex 反向索引 — 独立模块 + store 内联维护

**决策**：新建 `stores/chat/messageIndex.ts` 提供 `Map<messageId, sessionId>`，作为 module-level 单例（非 zustand state），由 store actions 在 `set` 内同步维护。

**理由**：
- 不污染 zustand state（避免反向索引触发重渲）
- module-level 单例保证跨 action 共享
- store action 内同步维护保证一致性（addMessage 时写入、deleteMessage 时清除）

**API**：
```ts
// messageIndex.ts
export const messageIndex = new Map<string, string>();
export function lookupSessionId(messageId: string): string | null { ... }
export function indexMessage(messageId: string, sessionId: string): void { ... }
export function unindexMessage(messageId: string): void { ... }
export function unindexSession(sessionId: string): void { ... }  // 删除 session 时批量清理
```

**一致性保证**：所有 message 增删改的 store action 必须同步维护 index。`findSessionIdByMessageId` 标记为 deprecated，新代码用 `lookupSessionId`。

### D3: team_plan 单次 upsert — 扩展 upsertTeamNode 签名

**决策**：扩展 `upsertTeamNode` 的 `updaters` 参数，增加可选 `initialAgents: TeamAgentState[]` 字段；store 在首次创建 team part 时一次性写入 plan + agents。

**实现**：
```ts
upsertTeamNode: (messageId, updaters) => {
  // 若 team part 不存在且提供了 initialAgents，一次性创建
  if (teamIdx === -1 && updaters.initialAgents) {
    parts.push({
      type: "team",
      ...
      plan: updaters.plan ?? [],
      agents: updaters.initialAgents,  // 一次性写入
      ...
    });
    return;
  }
  // 已存在 team part：按原逻辑增量更新
}
```

useChatStream 调用改为：
```ts
case "team_plan": {
  const plan = Array.isArray(e.plan) ? e.plan : [];
  const initialAgents = plan.map(t => ({
    agent: String(t?.agent ?? ""),
    purpose: String(t?.purpose ?? ""),
    status: "pending" as const,
  }));
  upsertTeamNode(pendingIdRef.current, {
    plan: plan.map(...),
    reasoning: String(e.reasoning ?? ""),
    initialAgents,  // 一次性传入
  });
  break;  // 不再循环
}
```

### D4: buildRenderItems 缓存 — selector 精细化订阅

**决策**：不缓存 `buildRenderItems` 结果（parts 引用每次 token 都变），改为让卡片组件用 `useChatStore` selector 精细化订阅**自己关心的 part**，避免 token 追加到 text part 时触发 tool-call 卡片重渲。

**实现**：
- `AssistantMessageParts` 接收 `messageId`，用 selector 订阅 `m.parts` 引用（必须重渲以更新列表）
- 但 tool-call 卡片改为订阅**具体 partId 的 status/result/error**，不订阅整个 parts 数组
- 卡片用 `React.memo` 包裹，仅 props 变化时重渲

**权衡**：列表组件本身仍会重渲（parts 引用变化），但子卡片用 memo + selector 避免无谓重渲。这是最小改动方案。

### D5: 消息列表虚拟化 — `@tanstack/react-virtual`

**决策**：引入 `@tanstack/react-virtual` 对消息列表做窗口化渲染，仅渲染视口内 + 上下缓冲区 5 条。

**理由**：
- 业界成熟方案，零侵入式接入
- 支持动态高度（消息内容高度不一）
- 与现有 `useAutoScroll` 兼容

**实现**：
```tsx
const parentRef = useRef<HTMLDivElement>(null);
const virtualizer = useVirtualizer({
  count: messages.length,
  getScrollElement: () => parentRef.current,
  estimateSize: () => 200,  // 估算高度，实际由 CSS 测量
  overscan: 5,
});
```

### D6: Text parts 顺序 — 按真实 parts 顺序渲染

**决策**：取消 [AssistantUIThread.tsx#L128](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/AssistantUIThread.tsx) 的「text parts 收集到 textItems 末尾追加」规则，改为按真实 parts 顺序渲染。

**理由**：
- 模型实际输出顺序是 `text → tool-call → tool-result → text`，强制重排破坏时间轴
- 用户期望「先看到模型说"让我查一下"，再看到工具调用，再看到结论」
- 重排导致已显示文本突然跳到工具卡片下方，视觉抖动严重

**视觉分隔**：若需「结论在后」视觉，由模型自己控制输出顺序（先输出工具调用相关 text，再输出最终结论 text），前端不强制重排。

### D7: ReasoningBlock 流式预览 — max-height 滚动区

**决策**：流式时（`!done`）若有 text，展示**可滚动预览区**（max-height: 120px + overflow-auto + monospace），完成后自动收缩为「已思考 N 秒」。

**实现**：
```tsx
{!done && text.length > 0 && (
  <div className="max-h-[120px] overflow-auto ...">
    <pre className="whitespace-pre-wrap font-mono">{text}</pre>
  </div>
)}
{done && (
  <button onClick={toggle}>已思考 {elapsedSec} 秒</button>
)}
```

### D8: ToolCallCard 增强 — 复制 / 展开 / source / 耗时

**决策**：
- result 超长（>1000 字符）：折叠时显示截断 + 「显示完整」按钮；展开后无截断
- 复制按钮：args 区和 result 区各一个，用 `navigator.clipboard.writeText` + 复制成功反馈（✓ 图标 2s）
- source chip：toolName 右侧显示 source（如「code」「rag」）作为小标签
- 耗时：需要 store 在 tool-result 到达时计算 `completedAt - startedAt`；tool-call part 增加 `startedAt` 字段

**part 数据模型扩展**：
```ts
{ type: "tool-call"; id: string; toolName: string; args: unknown; source: string;
  status: "running" | "complete" | "error";
  startedAt: number;  // 新增
  completedAt?: number;  // 新增（tool-result 到达时写入）
}
```

### D9: ToolCallGroup 折叠 — 连续 ≥3 个同类自动折叠

**决策**：在 `AssistantMessageParts` 的 `buildRenderItems` 阶段，连续 ≥3 个相同 `toolName` 的 tool-call 合并为 `ToolCallGroup` 渲染项。

**实现**：
```ts
type RenderItem =
  | { kind: "tool-call"; part: PairedToolCall }
  | { kind: "tool-call-group"; toolName: string; items: PairedToolCall[] }
  | ...

function buildRenderItems(parts) {
  // ... 原配对逻辑
  // 额外扫描：连续 ≥3 个相同 toolName 的 tool-call 合并为 group
}
```

`ToolCallGroup` 默认折叠为「执行了 N 个 {toolName} 调用（M 成功 / K 失败 / L 运行中）」，点击展开为多个 ToolCallCard。

### D10: 移除 content 字段 — v5→v6 迁移

**决策**：移除 `ChatMessage.content` 兼容字段；新增 `migrateV5toV6` 删除 `content` 字段；store actions 不再调用 `deriveContent`。

**风险**：若有渲染组件仍读 `message.content`，会编译失败（TS 类型已移除）。这是期望行为（强制迁移）。

**前置检查**：全仓搜索 `message.content` / `.content` 引用，确保都已迁移到 parts。

### D11: MessageParts 拆分 — 三个独立组件

**决策**：`AssistantUIThread` 中的 `MessageParts` 拆为：
- `UserMessageBubble`（含编辑逻辑）
- `ToolSystemMessage`（tool role 系统提示）
- `AssistantMessageParts`（assistant parts 列表）

**理由**：单一职责，便于独立测试和优化。

### D12: useAutoScroll 流式期间用 auto

**决策**：`useAutoScroll` 接收 `isStreaming` 参数，流式期间用 `behavior: "auto"`，非流式时用 `"smooth"`。

**实现**：
```ts
export function useAutoScroll(dep: unknown, isStreaming: boolean) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = bottomRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: isStreaming ? "auto" : "smooth" });
    }
  }, [dep, isStreaming]);
  return bottomRef;
}
```

### D13: 右侧导航条进度 — rAF throttle state

**决策**：把 `scrollTop` 存入 state，用 `rAF` throttle 更新；进度条 `style` 订阅 state 而非命令式读取。

**实现**：
```tsx
const [scrollProgress, setScrollProgress] = useState({ top: 0, height: 100 });
useEffect(() => {
  const el = scrollContainerRef.current;
  if (!el) return;
  let rafId = 0;
  const update = () => {
    const top = el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight);
    const height = el.clientHeight / Math.max(1, el.scrollHeight);
    setScrollProgress({ top: top * 100, height: height * 100 });
    rafId = 0;
  };
  const onScroll = () => {
    if (!rafId) rafId = requestAnimationFrame(update);
  };
  el.addEventListener("scroll", onScroll, { passive: true });
  update();
  return () => {
    el.removeEventListener("scroll", onScroll);
    if (rafId) cancelAnimationFrame(rafId);
  };
}, [messages.length]);
```

## Risks

| 风险 | 缓解 |
|------|------|
| 流式期间崩溃丢失 token | 可接受，用户可重新发送 |
| messageIndex 与 store 不一致 | 所有 message 增删改 action 必须同步维护 index；测试覆盖 |
| 虚拟化导致 ReasoningBlock sessionStorage 状态丢失 | sessionStorage 不依赖组件挂载（按 messageId:partId 隔离） |
| 移除 content 字段破坏未迁移代码 | TS 类型移除会编译失败，强制全量迁移 |
| ToolCallGroup 折叠隐藏重要信息 | 默认折叠但点击展开；用户可手动展开查看每个 tool-call |
