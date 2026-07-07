# Spec — execution-trace-optimization

## Overview

升级 `chat-rendering-trace` capability：从「基础 parts 渲染」升级为「高性能 parts 渲染 + 增强交互 + 清理架构」。覆盖性能、交互、架构三类共 16 项优化。

## Requirements

### REQ-001: 流式 token 写入性能

**Must**: 流式 token 写入不触发 localStorage I/O；流式结束后一次性落盘。
**Must**: `appendPartText` / `addPart` / `updatePart` / `upsertTeamNode` / `markReasoningDone` 用 O(1) 反向索引定位 session。
**Must**: `team_plan` 事件单次 `set` 完成 plan + initialAgents 写入。
**Should**: 卡片组件用 `React.memo` 避免无关 part 更新时重渲。

### REQ-002: 消息列表虚拟化

**Must**: 消息列表 100+ 条仍流畅（60fps）。
**Must**: 仅渲染视口内 + 上下缓冲区 5 条消息。
**Must**: 与 `useAutoScroll` 兼容（滚动到底部仍生效）。

### REQ-003: 思考过程可见

**Must**: 流式时（`!done`）若有 text，展示可滚动预览区（max-height: 120px）。
**Must**: 完成后自动收缩为「已思考 N 秒」。
**Must**: 思考时长用 part 的 `startedAt` / `doneAt` 计算，不依赖组件挂载时间。

### REQ-004: ToolCallCard 信息完整

**Must**: result 超长（>1000 字符）时加「显示完整」按钮。
**Must**: args 区和 result 区各加复制按钮。
**Must**: 展示 `source` chip。
**Must**: complete 状态显示执行耗时。
**Should**: 连续 ≥3 个同类 tool-call 自动折叠为汇总行。

### REQ-005: 消息顺序真实

**Must**: text parts 按真实 parts 顺序渲染，不强制重排到末尾。
**Must**: 模型「text → tool-call → text」输出顺序在 UI 中保持一致。

### REQ-006: 滚动行为

**Must**: 流式期间 `scrollIntoView` 用 `behavior: "auto"`。
**Must**: 非流式时用 `behavior: "smooth"`。
**Must**: 右侧导航条进度条随滚动实时更新（rAF throttle）。

### REQ-007: 架构清理

**Must**: `MessageParts` 拆分为 `UserMessageBubble` / `ToolSystemMessage` / `AssistantMessageParts` 三个独立组件。
**Must**: `TeamNodeCard` 的 `key` 用 `agent` 名而非索引。
**Must**: 移除 `ChatMessage.content` 兼容字段（v5→v6 迁移）。
**Should**: `ReasoningBlock` 卸载时清理已删除消息的 sessionStorage key。

## Scenarios

### Scenario 1: 流式输出长会话

```
Given 用户在 100+ 消息的会话中发送新消息
When SSE 流式 token 持续到达（每秒 30 次）
Then UI 保持 60fps 流畅
And localStorage 不被频繁写入（流式期间无 I/O）
And 流式结束后 localStorage 一次性落盘
```

### Scenario 2: 思考过程实时查看

```
Given 模型开始 reasoning
When 第一个 reasoning token 到达
Then 显示「思考中」+ 跳动圆点
When reasoning text 持续累积
Then 显示可滚动预览区（max-h-120px）
When reasoning 完成（done=true）
Then 自动收缩为「已思考 N 秒」（N 基于 startedAt/doneAt 计算）
And 点击展开可回看完整 reasoning
```

### Scenario 3: 工具调用信息完整

```
Given 模型调用工具 `read_file({path: "/a/b/c.ts"})`
When tool_call 事件到达
Then 显示「🔧 read_file(/a/b/c.ts) ⏳ 运行中」+ source chip「code」
When tool_result 事件到达（result 1500 字符）
Then 显示「✓ 完成 · 1.2s」
And 折叠状态下 result 截断显示 + 「显示完整」按钮
And 点击「显示完整」展开后无截断
And args 区和 result 区各有复制按钮，点击后 ✓ 反馈 2s
```

### Scenario 4: 连续同类工具调用折叠

```
Given 模型连续调用 5 次 `read_file`
When 5 个 tool-call 都已 complete
Then 显示「执行了 5 个 read_file 调用（5 成功）」折叠行
When 用户点击折叠行
Then 展开为 5 个 ToolCallCard
```

### Scenario 5: 消息顺序真实

```
Given 模型输出顺序为：text「让我查一下」→ tool-call → tool-result → text「结论是...」
When parts 渲染
Then UI 顺序为：text「让我查一下」→ 工具卡片 → text「结论是...」
And 无视觉跳动（text 不被强制重排到末尾）
```

### Scenario 6: content 字段移除

```
Given localStorage 中有 v5 格式的 ChatMessage（含 content 字段）
When 应用启动加载持久化数据
Then migrateV5toV6 删除 content 字段
And 所有渲染基于 parts，无 .content 引用
```

## API / Data Changes

### MessagePart 类型扩展

```ts
// reasoning part 增加 startedAt
| { type: "reasoning"; id: string; text: string; done: boolean;
    startedAt: number; doneAt?: number }

// tool-call part 增加 startedAt + completedAt
| { type: "tool-call"; id: string; toolName: string; args: unknown; source: string;
    status: "running" | "complete" | "error";
    startedAt: number; completedAt?: number }
```

### ChatMessage 移除 content

```ts
// Before (v5)
interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  parts: MessagePart[];
  ts: number;
  content: string;  // 移除
}

// After (v6)
interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  parts: MessagePart[];
  ts: number;
}
```

### SSE 事件扩展

`reasoning` 事件无需扩展（startedAt 由前端 store 在首个 reasoning token 到达时写入，不从后端获取）。

## Dependencies

- 新增 npm 包：`@tanstack/react-virtual`
