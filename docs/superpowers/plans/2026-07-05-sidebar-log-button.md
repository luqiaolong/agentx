# 侧边栏「日志」按钮 + tail-like 自动滚动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在侧边栏底部「设置」按钮右方加一个「日志」按钮，点击后复用现有 SettingsModal 直接跳转到「日志」tab，并以 2s 间隔自动拉取日志，参考 `tail` 行为支持自动滚动 + 手动滚动查看历史。

**Architecture:** 复用现有 `useSettingsStore.pendingSettingsTab` 跳转机制（已被 ErrorBoundary 使用）。点击「日志」按钮 = `setPendingSettingsTab("logs") + setSettingsOpen(true)`，SettingsModal 打开后 LogViewer 渲染并启动 2s 轮询；用户手动上滚后停止跟随，提供「跳到底部」按钮恢复。零后端改动，零 preload 改动，零 store 改动。

**Tech Stack:** React 18 + TypeScript + zustand + lucide-react + vitest + @testing-library/react

---

## File Structure

| 文件 | 职责 |
|---|---|
| `frontend/renderer/components/chat/SessionList.tsx` | 改：底部按钮行结构（左右并排），新增「日志」按钮 + `openLogsModal` 工具函数 |
| `frontend/renderer/components/settings/LogViewer.tsx` | 改：新增 2s 轮询、tail-like 自动滚动、`preRef` + `autoScrollRef` 双轨状态、紧凑样式 |
| `tests/renderer/log-viewer.test.tsx` | 新建：验证 mount-拉一次 / 每 2s 轮询 / 卸载停止 |
| `tests/renderer/sidebar-log-button.test.tsx` | 新建：验证点击「日志」按钮触发 `pendingSettingsTab="logs"` + `isSettingsOpen=true` |

不变动的文件：`preload/index.ts`、`main/index.ts`、`main/logger.ts`、`SettingsModal.tsx`、`stores/settings.ts`、`ErrorBoundary.tsx`、shared/api-types、后端任意文件。

---

## Task 1: 编写并通过 LogViewer 轮询单元测试

**Files:**
- Test: `tests/renderer/log-viewer.test.tsx`（新建）

- [ ] **Step 1: 写测试（Vitest + jsdom）**

创建 `tests/renderer/log-viewer.test.tsx`，内容如下：

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, waitFor } from "@testing-library/react";
import { LogViewer } from "@/components/settings/LogViewer";

declare global {
  // eslint-disable-next-line no-var
  var api:
    | {
        logs: { read: (date?: string, maxLines?: number) => Promise<string[]> };
      }
    | undefined;
}

describe("LogViewer 轮询", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.api = {
      logs: { read: vi.fn().mockResolvedValue(["[12:00:00] line 1"]) },
    };
  });
  afterEach(() => {
    vi.useRealTimers();
    window.api = undefined as unknown as typeof window.api;
  });

  it("挂载后立即拉一次日志", async () => {
    render(<LogViewer />);
    await waitFor(() => {
      expect(window.api!.logs.read).toHaveBeenCalledTimes(1);
    });
  });

  it("每 2 秒拉一次", async () => {
    render(<LogViewer />);
    await waitFor(() => {
      expect(window.api!.logs.read).toHaveBeenCalledTimes(1);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(window.api!.logs.read).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(window.api!.logs.read).toHaveBeenCalledTimes(3);
  });

  it("卸载后停止轮询", async () => {
    const { unmount } = render(<LogViewer />);
    await waitFor(() => {
      expect(window.api!.logs.read).toHaveBeenCalledTimes(1);
    });
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(window.api!.logs.read).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: 运行测试，验证失败**

Run:
```bash
npm test -- --run tests/renderer/log-viewer.test.tsx
```
Expected: FAIL（`@/components/settings/LogViewer` 模块尚未实现轮询，但本测试只要求 mount 后调用一次 `window.api.logs.read`，所以挂载时调用 1 次的断言可能已经通过 —— 关键是后续两个用例：每 2s 拉取、卸载后停止，目前组件里没有 `setInterval`，后者会失败）

- [ ] **Step 3: 重构 LogViewer 加入轮询**

修改 `frontend/renderer/components/settings/LogViewer.tsx`，完整内容如下：

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, FileText, ArrowDown } from "lucide-react";

const POLL_INTERVAL_MS = 2000;

export function LogViewer() {
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const preRef = useRef<HTMLPreElement | null>(null);
  const autoScrollRef = useRef(true);
  const intervalRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const result = await window.api.logs.read(undefined, 200);
      setLines(result);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // 挂载即拉一次 + 启动 2s 轮询；卸载时清理
  useEffect(() => {
    void refresh();
    intervalRef.current = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [refresh]);

  // 新数据到来时，若 autoScroll 开启则滚到底
  useEffect(() => {
    const el = preRef.current;
    if (!el) return;
    if (autoScrollRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [lines]);

  const handleScroll = () => {
    const el = preRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 4;
    autoScrollRef.current = atBottom;
    setAutoScroll(atBottom);
  };

  const jumpToBottom = () => {
    const el = preRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    autoScrollRef.current = true;
    setAutoScroll(true);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-[11px] text-muted-c">
        <span className="inline-flex items-center gap-1.5">
          <FileText className="h-3.5 w-3.5" />
          实时日志 · 每 2 秒刷新
        </span>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="btn-ghost"
          aria-label="刷新"
          title="刷新"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>
      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          {err}
        </div>
      )}
      <div className="relative">
        <pre
          ref={preRef}
          onScroll={handleScroll}
          className="max-h-[calc(100vh-220px)] overflow-auto rounded-md border border-default/50 bg-[#0a0a0a] px-2.5 py-2 font-mono text-[11px] leading-snug text-neutral-300"
        >
          {lines.length === 0 ? "暂无日志" : lines.join("\n")}
        </pre>
        {!autoScroll && (
          <button
            type="button"
            onClick={jumpToBottom}
            className="absolute bottom-2 right-2 inline-flex items-center gap-1 rounded-md border border-default/60 bg-surface/95 px-2 py-1 text-[11px] font-medium text-primary-c shadow-pop backdrop-blur hover:bg-hover-soft"
            aria-label="跳到底部"
            title="跳到底部"
          >
            <ArrowDown className="h-3 w-3" />
            跳到底部
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 重新运行测试，验证通过**

Run:
```bash
npm test -- --run tests/renderer/log-viewer.test.tsx
```
Expected: PASS（3 tests passed）

- [ ] **Step 5: 提交**

```bash
git add frontend/renderer/components/settings/LogViewer.tsx tests/renderer/log-viewer.test.tsx
git commit -m "feat(logs): tail-like 2s polling + auto scroll + compact UI"
```

---

## Task 2: 编写并通过侧边栏「日志」按钮单元测试

**Files:**
- Test: `tests/renderer/sidebar-log-button.test.tsx`（新建）

- [ ] **Step 1: 写测试**

创建 `tests/renderer/sidebar-log-button.test.tsx`：

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest";

// vitest jsdom localStorage 不可用 → 在 import store 之前替换为内存版
vi.hoisted(() => {
  const store = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => {
      store.set(key, String(value));
    },
    removeItem: (key) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
    key: (index) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage,
    configurable: true,
    writable: true,
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { SessionList } from "@/components/chat/SessionList";
import { useSettingsStore } from "@/stores/settings";

describe("侧边栏日志按钮", () => {
  beforeEach(() => {
    useSettingsStore.setState({
      isSettingsOpen: false,
      pendingSettingsTab: null,
    });
  });

  it("点击「日志」按钮触发跳转（日志 tab + 打开设置弹窗）", () => {
    render(<SessionList />);
    const btn = screen.getByRole("button", { name: "查看日志" });
    fireEvent.click(btn);
    const s = useSettingsStore.getState();
    expect(s.pendingSettingsTab).toBe("logs");
    expect(s.isSettingsOpen).toBe(true);
  });

  it("仍然存在「设置」按钮", () => {
    render(<SessionList />);
    expect(screen.getByRole("button", { name: "打开设置" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试，验证失败**

Run:
```bash
npm test -- --run tests/renderer/sidebar-log-button.test.tsx
```
Expected: FAIL — 找不到「查看日志」按钮（`getByRole` 抛错）

- [ ] **Step 3: 改造 SessionList 底部按钮行**

修改 `frontend/renderer/components/chat/SessionList.tsx`：

**3a** — 在 [SessionList.tsx:1-12](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/SessionList.tsx#L1-L12) 的 lucide-react 引入中加入 `ScrollText`：

```tsx
import {
  Plus,
  MessageSquare,
  Trash2,
  Loader2,
  Settings,
  Home,
  Folder,
  ChevronDown,
  ChevronRight,
  ScrollText,
} from "lucide-react";
```

**3b** — 在 [SessionList.tsx:34](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/SessionList.tsx#L34) 之后新增 store selector 与跳转函数：

```tsx
const setSettingsOpen = useSettingsStore((s) => s.setSettingsOpen);
const setPendingSettingsTab = useSettingsStore((s) => s.setPendingSettingsTab);

const openLogsModal = () => {
  setPendingSettingsTab("logs");
  setSettingsOpen(true);
};
```

**3c** — 替换 [SessionList.tsx:157-169](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/SessionList.tsx#L157-L169) 的底部按钮容器：

```tsx
      {/* 底部入口：设置 + 日志（固定在左下角，左右并排） */}
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

- [ ] **Step 4: 重新运行测试，验证通过**

Run:
```bash
npm test -- --run tests/renderer/sidebar-log-button.test.tsx
```
Expected: PASS（2 tests passed）

- [ ] **Step 5: 提交**

```bash
git add frontend/renderer/components/chat/SessionList.tsx tests/renderer/sidebar-log-button.test.tsx
git commit -m "feat(sidebar): log button next to settings, jumps to logs tab"
```

---

## Task 3: 端到端校验 + 类型检查

**Files:** 无（仅校验）

- [ ] **Step 1: 跑全部 renderer 测试**

Run:
```bash
npm test -- --run
```
Expected: 所有先前通过的测试 + 新增 5 个测试（3 LogViewer + 2 sidebar）全部 PASS

- [ ] **Step 2: TypeScript 类型检查**

Run:
```bash
npm run typecheck
```
Expected: 无 error

- [ ] **Step 3: 手动端到端验收（按 AGENTS.md §14.7 SOP）**

1. 重启前后端：
   ```powershell
   taskkill /T /F /IM electron.exe
   Get-Process -Name python,uv -ErrorAction SilentlyContinue | Stop-Process -Force
   netstat -ano | Select-String ':8123 '   # 必须为空
   npm run dev
   ```
2. 看到 `start electron app...` 后等 10s
3. 侧边栏底部应出现两个按钮：「设置」「日志」（并排）
4. 点击「日志」→ 设置弹窗打开并直接定位到「日志」tab
5. 在另一个会话发消息 → 日志列表每 2 秒自动出现新行，且视图始终跟到底
6. 滚轮向上滚动 → 右下角出现「跳到底部」按钮；新数据不再覆盖视图
7. 点击「跳到底部」→ 视图跳到底部，新数据继续跟随
8. 关闭设置弹窗 → 日志不再变化；重新打开 → 轮询恢复

- [ ] **Step 4: 提交（如有自动修复）**

若手动验收发现小问题（如样式微调），用 `git add -u && git commit -m "fix(logs): ..."` 修复并提交。

---

## Self-Review Checklist

- [x] Spec coverage：每个 spec 章节都有 task 覆盖（按钮位置/点击行为/自动刷新/tail 行为/紧凑样式/测试）
- [x] No placeholders：所有代码块均为可执行内容，无 TBD/TODO
- [x] Type consistency：Task 1 用 `globalThis.api` mock，与测试中的 `window.api` 用同一类型（`window.api` 在 vite/webpack 等环境等同 `globalThis`）；实际生产代码用 `window.api.logs.read`，未变更
- [x] Commit granularity：每个 Task 独立 commit，便于 review 与回滚
- [x] 零后端改动、零 preload 改动、零 store 改动 —— 与 spec §7 影响面一致