# 侧边栏「日志」按钮 + tail-like 自动滚动设计

> 日期：2026-07-05
> 状态：已通过 brainstorming（方案 A），待 writing-plans 落地
> 关联文件：
> [SessionList.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/SessionList.tsx)、
> [LogViewer.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/settings/LogViewer.tsx)、
> [SettingsModal.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/settings/SettingsModal.tsx)、
> [main/logger.ts](file:///d:/java/agentprojects/agentx/frontend/main/logger.ts)、
> [preload/index.ts](file:///d:/java/agentprojects/agentx/frontend/preload/index.ts)

---

## 1. 背景与目标

调试时需要频繁查看运行时日志（python 后端 stdout + Electron 主进程的 appendLog）。当前入口只能「打开设置弹窗 → 切到日志 tab → 点刷新按钮」，过程繁琐且非实时。

**目标**：
1. 在侧边栏「设置」按钮正右方加一个「日志」按钮，一键直达日志 tab
2. 日志 tab 打开期间每 2 秒自动拉一次最新内容（IPC `logs:read` 已存在，零新后端逻辑）
3. 参考 `tail` 行为：默认自动滚动到底部；用户向上滚动时停止跟随；提供「跳到底部」按钮恢复跟随
4. 提高信息密度、减少边框（去掉多余圆角/留白，行高收紧）

## 2. 需求边界（已与用户对齐）

- 「日志」按钮位置：**侧边栏底部「设置」按钮正右方**（[SessionList.tsx:158-169](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/SessionList.tsx#L158-L169)）
- 点击行为：**复用现有 SettingsModal + 跳到日志 tab**（用 `setPendingSettingsTab("logs")` + `setSettingsOpen(true)`，ErrorBoundary 已用过的机制）
- 自动刷新频率：**每 2 秒一次**，弹窗关闭时停止
- 不做的事：新增独立浮窗（不拆 SettingsModal）、新增 fs.watch 推送（轮询够用）、日志级别过滤（保持原始全文）、tail 颜色高亮（不引依赖）、跨天日志自动加载（接受当天边界）

## 3. 方案选择

| 方案 | 改动 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A. 设置按钮旁加按钮 + LogViewer 加轮询/tail 行为** | SessionList 加按钮 + LogViewer 重构 | 改动小、复用 pendingSettingsTab、与 ErrorBoundary 跳转一致 | LogViewer 内部状态变多 | ✅ |
| B. 把日志 tab 提为默认首页 tab | TABS 数组顺序 + 容器放大 | 一打开就看日志 | 设置弹窗默认首 tab 改变影响其他用户；与 SettingsModal 的"配置"定位冲突 | ❌ |
| C. 新建独立 LogsModal | 新组件 + 重新挂 IPC | 视觉独立 | 重复 IPC、ErrorBoundary 要改两套入口 | ❌ |

**采用方案 A。** 理由：① 复用 `pendingSettingsTab` 跳转机制（已在 [ErrorBoundary.tsx:44-45](file:///d:/java/agentprojects/agentx/frontend/renderer/components/ErrorBoundary.tsx#L44-L45) 验证过）；② 轮询 + tail 行为都在 LogViewer 内部闭环，不需要向上扩散状态；③ SettingsModal 不动，零回归风险。

## 4. 详细设计

### 4.1 侧边栏按钮布局

[SessionList.tsx:158-169](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/SessionList.tsx#L158-L169) 现有底部按钮：

```tsx
<div className="mt-1 shrink-0 border-t border-default pt-1">
  <button onClick={() => setSettingsOpen(true)} ...>
    <Settings /> 设置
  </button>
</div>
```

改为按钮行（两个 icon 按钮并排，左侧 flex-1 占主要宽度，右侧日志紧凑）：

```tsx
<div className="mt-1 flex shrink-0 items-center gap-1 border-t border-default pt-1.5">
  <button
    type="button"
    onClick={() => setSettingsOpen(true)}
    className="flex flex-1 items-center gap-1.5 rounded-lg px-2 py-1.5 text-secondary-c transition-colors hover:bg-hover-soft hover:text-primary-c"
    aria-label="打开设置"
    title="设置"
  >
    <Settings className="h-3.5 w-3.5 text-muted-c" />
    <span className="text-xs font-medium">设置</span>
  </button>
  <button
    type="button"
    onClick={openLogsModal}
    className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-secondary-c transition-colors hover:bg-hover-soft hover:text-primary-c"
    aria-label="查看日志"
    title="查看日志"
  >
    <ScrollText className="h-3.5 w-3.5 text-muted-c" />
    <span className="text-xs font-medium">日志</span>
  </button>
</div>
```

**`openLogsModal` 实现**：

```ts
const setSettingsOpen = useSettingsStore((s) => s.setSettingsOpen);
const setPendingSettingsTab = useSettingsStore((s) => s.setPendingSettingsTab);

const openLogsModal = () => {
  setPendingSettingsTab("logs");
  setSettingsOpen(true);
};
```

**icon import**：在 [SessionList.tsx:1-12](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/SessionList.tsx#L1-L12) 的 lucide-react 引入中加 `ScrollText`。

### 4.2 LogViewer 重构

[LogViewer.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/settings/LogViewer.tsx) 当前只做"打开时一次性拉取 + 手动刷新按钮"。重构后：

**新增状态**：
- `autoScroll: boolean`（默认 `true`）：控制新数据到来时是否自动滚到底
- `intervalRef: useRef<number | null>`：保存 setInterval id，便于清理

**新增 refs**：
- `preRef: useRef<HTMLPreElement | null>`：拿到底层滚动容器

**轮询 effect**：

```ts
useEffect(() => {
  // 挂载即拉一次
  void refresh();
  // 每 2s 拉一次
  const id = window.setInterval(() => void refresh(), 2000);
  intervalRef.current = id;
  return () => {
    if (intervalRef.current !== null) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  };
}, []);
```

**滚动监听 + 自动滚动 effect**（依赖 `lines`）：

```ts
const preRef = useRef<HTMLPreElement | null>(null);
const autoScrollRef = useRef(true);

// 渲染后，若 autoScroll 为 true 则滚到底
useEffect(() => {
  const el = preRef.current;
  if (!el) return;
  if (autoScrollRef.current) {
    el.scrollTop = el.scrollHeight;
  }
}, [lines]);

// 用户滚动事件：判断是否还贴底
const handleScroll = () => {
  const el = preRef.current;
  if (!el) return;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 4;
  autoScrollRef.current = atBottom;
  setAutoScroll(atBottom); // 触发"跳到底部"按钮显隐
};
```

**「跳到底部」按钮**：仅在 `!autoScroll` 时显示，点击后立刻 `scrollTop = scrollHeight` 并 `autoScrollRef.current = true; setAutoScroll(true)`。

**紧凑样式调整**（参考用户"提高信息密度、减少边框"要求）：

| 项 | 旧 | 新 |
|---|---|---|
| `pre` border | `border border-default` | `border border-default/50` |
| `pre` 圆角 | `rounded-lg` | `rounded-md` |
| `pre` padding | `p-2.5` | `px-2.5 py-2` |
| `pre` 字号 | `text-[11px]` | 保持 `text-[11px]` |
| `pre` 行高 | `leading-relaxed` | `leading-snug` |
| `pre` 背景 | `#0a0a0a`（硬编码） | `bg-[var(--bg-subtle,theme aware)]` 或保留 `#0a0a0a`（与现有 dark 主题一致） |
| `pre` max-h | `max-h-96` | `max-h-[calc(100vh-220px)]`（撑满日志 tab 可用区域） |
| 顶部工具条 | `flex justify-between` | 紧凑 `flex items-center justify-between text-[11px]` |

**视觉草图**（日志 tab 内容区）：

```
┌─────────────────────────────────────────┐
│ 📄 实时日志 · 自动刷新中    [↻] [⤓]  │ ← 工具条更紧凑
├─────────────────────────────────────────┤
│ [13:42:01] chat request thread_id=t1   │
│ [13:42:01] router dispatch classification│
│ [13:42:05] deep agent stream failed...│
│ ... (高密度，行高 snug)               │
│                                         │
│                                    [⤓] │ ← 跟随按钮（仅在用户上滚时显示）
└─────────────────────────────────────────┘
```

### 4.3 IPC / preload / 后端不动

- [preload/index.ts:223-225](file:///d:/java/agentprojects/agentx/frontend/preload/index.ts#L223-L225) `window.api.logs.read(date?, maxLines)` 已有
- [main/logger.ts:66](file:///d:/java/agentprojects/agentx/frontend/main/logger.ts#L66) `readLogs(date?, maxLines=200)` 已有
- [main/index.ts:469](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L469) IPC `logs:read` handler 已有

**零后端改动**。

### 4.4 SettingsModal 不动

[SettingsModal.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/settings/SettingsModal.tsx) 的 `useEffect([isOpen, pendingSettingsTab])` 已在打开时消费 `pendingSettingsTab="logs"` 并渲染 LogViewer，无需改动。

ErrorBoundary 跳转流程也保持不变。

### 4.5 关闭/打开的清理

- 日志 tab 关闭（active 切走）时，LogViewer 仍挂载（只是隐藏），轮询不停 — **这是有意的**，避免来回切产生 IPC 抖动
- SettingsModal 整体关闭（`isOpen=false`）时，LogViewer 卸载，`useEffect` cleanup 清掉 setInterval
- 卸载→重开场景：重新挂载 + 重启 setInterval，行为正确

## 5. 测试

### 5.1 vitest（renderer）

新建 `tests/renderer/log-viewer.test.tsx`：

```tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { LogViewer } from "@/components/settings/LogViewer";

describe("LogViewer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // mock window.api.logs.read
    (global as any).window.api = {
      logs: { read: vi.fn().mockResolvedValue(["line1"]) },
    };
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("挂载后立即拉一次", async () => {
    render(<LogViewer />);
    await waitFor(() => {
      expect(window.api.logs.read).toHaveBeenCalledTimes(1);
    });
  });

  it("每 2 秒轮询", async () => {
    render(<LogViewer />);
    await waitFor(() => {
      expect(window.api.logs.read).toHaveBeenCalledTimes(1);
    });
    vi.advanceTimersByTime(2000);
    expect(window.api.logs.read).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(2000);
    expect(window.api.logs.read).toHaveBeenCalledTimes(3);
  });

  it("卸载后停止轮询", async () => {
    const { unmount } = render(<LogViewer />);
    await waitFor(() => {
      expect(window.api.logs.read).toHaveBeenCalledTimes(1);
    });
    unmount();
    vi.advanceTimersByTime(10000);
    expect(window.api.logs.read).toHaveBeenCalledTimes(1); // 不再增加
  });
});
```

### 5.2 vitest（SessionList）

补充 `tests/renderer/chat-store.test.ts` 或新建 `tests/renderer/sidebar-log-button.test.tsx`：

```tsx
it("点击日志按钮触发 setPendingSettingsTab('logs') + setSettingsOpen(true)", () => {
  render(<SessionList />);
  const btn = screen.getByRole("button", { name: "查看日志" });
  fireEvent.click(btn);
  expect(useSettingsStore.getState().pendingSettingsTab).toBe("logs");
  expect(useSettingsStore.getState().isSettingsOpen).toBe(true);
});
```

### 5.3 手动验收（按 §14.7 重启 SOP）

1. `npm run dev`，等 8123 起
2. 侧边栏底部「设置」按钮右侧出现「日志」按钮（icon `ScrollText`）
3. 点击「日志」→ 设置弹窗打开并直接定位到「日志」tab
4. 日志每 2 秒自动刷新（可触发一次 chat 验证新行出现）
5. 滚轮向上滚动 → 「跳到底部」按钮出现；新数据不再覆盖视图
6. 点击「跳到底部」按钮 → 视图跳到底部，新数据继续自动跟随
7. 关闭设置弹窗 → 日志不再变化；重新打开 → 轮询恢复
8. `npm run typecheck` 无报错

## 6. 不做的事（YAGNI）

- 不引入虚拟滚动（200 行以内 DOM 节点可承受）
- 不做 ANSI 颜色解析（不引依赖）
- 不做日志级别过滤（保留原文）
- 不做跨天日志切换（接受当天边界）
- 不改 ErrorBoundary（已用同机制）
- 不改 IPC / preload / 后端（接口已就绪）

## 7. 影响面

| 文件 | 改动类型 |
|---|---|
| [frontend/renderer/components/chat/SessionList.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/SessionList.tsx) | 改：底部按钮行结构 + 加「日志」按钮 + `openLogsModal` |
| [frontend/renderer/components/settings/LogViewer.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/settings/LogViewer.tsx) | 改：加轮询 + tail 自动滚动 + 紧凑样式 |
| `tests/renderer/log-viewer.test.tsx` | 新建 |
| `tests/renderer/sidebar-log-button.test.tsx`（或合并到 SessionList 测试） | 新建 |

**不动**：`preload/index.ts`、`main/index.ts`、`main/logger.ts`、`SettingsModal.tsx`、`stores/settings.ts`、`ErrorBoundary.tsx`、shared/api-types、后端任何文件。

## 8. 风险与回滚

- **风险 1**：2s 轮询 + 日志写盘（异步 appendFile）并发，可能出现"读到一半"的行截断。
  - **缓解**：`logger.ts::appendLog` 用 `enqueueWrite` 串行化，写盘是整行 `entry`（含 `\n`），单次 readFile 不会跨行截断；读到的尾行若是半行（罕见），下次轮询会自然覆盖。
- **风险 2**：autoScroll ref + state 双轨不一致。
  - **缓解**：ref 用于渲染 effect 同步读（避免闭包过期），state 用于触发按钮重渲染；两者在 `handleScroll` 同步设置。
- **风险 3**：卸载时 setInterval 未清 → 内存泄漏。
  - **缓解**：useEffect cleanup 强制 `clearInterval`；测试 5.1 已断言。
- **回滚**：移除 SessionList「日志」按钮 + LogViewer 退回一次性拉取即可，无破坏性变更。