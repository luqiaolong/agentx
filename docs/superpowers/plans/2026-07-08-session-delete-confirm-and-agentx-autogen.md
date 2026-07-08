# Session 删除应用内确认弹窗 + 工作区首次对话后异步生成 .agentx - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `window.confirm` in SessionList with an in-app ConfirmDialog; defer `.agentx/` initialization from "select workspace" to "first LLM `done` event", with double-layer protection (session-level flag + real disk existence check).

**Architecture:** Add one new component (`ConfirmDialog`) and one new chat store action (`ensureAgentxGenerated`). Three existing files get surgical edits. No backend changes.

**Tech Stack:** React 18, TypeScript, zustand, Tailwind, vitest + @testing-library/react, existing `useModalDialog` a11y hook.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/renderer/components/ui/ConfirmDialog.tsx` (new) | In-app confirmation modal; wraps `useModalDialog` |
| `frontend/renderer/styles/globals.css` (modify) | Add `.btn-danger` class |
| `frontend/renderer/stores/chat/index.ts` (modify) | Add `generatedAgentx?: boolean` to Session; add `ensureAgentxGenerated` action |
| `frontend/renderer/stores/chat/migrations.ts` (verify, modify only if needed) | Confirm session-shape migration gracefully handles missing `generatedAgentx` (undefined → false) |
| `frontend/renderer/components/chat/SessionList.tsx` (modify) | Replace `window.confirm` with `<ConfirmDialog />` |
| `frontend/renderer/components/chat/ChatComposer.tsx` (modify) | Remove `await initProjectConfig` from `handleAttachWorkspace` |
| `frontend/renderer/hooks/useChatStream.ts` (modify) | In `case "done"`, fire `ensureAgentxGenerated` |
| `tests/renderer/confirm-dialog.test.tsx` (new) | Smoke: render / onConfirm / onClose / ESC |
| `tests/renderer/ensure-agentx-generated.test.ts` (new) | Action: short-circuit, exists check, failure rollback, concurrent guard |
| `tests/renderer/useChatStream.test.ts` (modify, optional) | Add `done` → `ensureAgentxGenerated` assertion |

---

## Task 1: Add `.btn-danger` CSS class

**Files:**
- Modify: `frontend/renderer/styles/globals.css` (append after `.btn-secondary` block)

- [ ] **Step 1: Locate the end of the `.btn-secondary` block** (around line 316 in current file).

- [ ] **Step 2: Append the danger button styles**

Append immediately after the `.btn-secondary:active { transform: scale(0.97); }` closing brace:

```css
  /* Danger button (destructive confirmations) */
  .btn-danger {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.375rem;
    border-radius: 0.5rem;
    background-color: var(--color-rose-700, #be123c);
    padding: 0.375rem 0.75rem;
    font-size: var(--text-xs);
    font-weight: 500;
    color: var(--text-secondary);
    cursor: pointer;
    transition: background-color 200ms ease, transform 120ms ease;
  }
  .btn-danger:hover {
    background-color: var(--color-rose-600, #e11d48);
  }
  .btn-danger:active {
    transform: scale(0.97);
  }
  .btn-danger:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
```

- [ ] **Step 3: Verify the CSS file is well-formed** (no duplicate closing braces).

Open the file and confirm there's exactly one `}` closing the `.btn-danger:disabled` rule.

- [ ] **Step 4: Commit**

```bash
git add frontend/renderer/styles/globals.css
git commit -m "feat(ui): add btn-danger class for destructive confirmations"
```

---

## Task 2: Create ConfirmDialog component

**Files:**
- Create: `frontend/renderer/components/ui/ConfirmDialog.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/renderer/components/ui/ConfirmDialog.tsx`:

```tsx
import { X } from "lucide-react";
import { useModalDialog } from "@/components/ui/hooks/useModalDialog";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "primary";
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * 应用内通用确认 Modal；替换 window.confirm/window.prompt 等同步原生对话框。
 *
 * - a11y：复用 useModalDialog 自动获得 ESC 关闭 / Tab 焦点陷阱 / 触发元素焦点恢复；
 *         使用 document.activeElement.blur() 由调用方主动避免与 ChatComposer 的
 *         textarea 自动聚焦竞争。
 * - variant="danger" 时确认按钮使用 rose 红色（btn-danger）；
 *   否则使用 btn-primary（品牌色）。
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "确认",
  cancelLabel = "取消",
  variant = "danger",
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const { closeBtnRef, dialogRef } = useModalDialog({ open, onClose });

  if (!open) return null;

  const confirmClass = variant === "danger" ? "btn-danger" : "btn-primary";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="w-[400px] rounded-lg border border-default bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="flex items-center justify-between border-b border-default px-4 py-3">
          <span className="font-semibold text-primary-c">{title}</span>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-primary-c"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="px-4 py-3 text-secondary-c">{message}</div>
        <div className="flex items-center justify-end gap-2 border-t border-default px-4 py-3">
          <button type="button" onClick={onClose} className="btn-secondary">
            {cancelLabel}
          </button>
          <button type="button" onClick={onConfirm} className={confirmClass}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify the file compiles**

Run: `cd d:\java\agentprojects\agentx && npx tsc --noEmit frontend/renderer/components/ui/ConfirmDialog.tsx 2>&1 | head -40`
Expected: No errors related to ConfirmDialog.tsx (pre-existing project-wide errors OK).

- [ ] **Step 3: Commit**

```bash
git add frontend/renderer/components/ui/ConfirmDialog.tsx
git commit -m "feat(ui): add ConfirmDialog component for in-app confirmations"
```

---

## Task 3: Write failing tests for ConfirmDialog (TDD red)

**Files:**
- Create: `tests/renderer/confirm-dialog.test.tsx`

- [ ] **Step 1: Create test file**

Create `tests/renderer/confirm-dialog.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

// jsdom 不能正确实现 scrollIntoView
if (typeof HTMLElement !== "undefined") {
  if (!HTMLElement.prototype.scrollIntoView) {
    HTMLElement.prototype.scrollIntoView = function () {};
  }
}

beforeEach(() => {
  document.body.style.overflow = "";
});

describe("ConfirmDialog", () => {
  it("open=false 时不渲染", () => {
    const { container } = render(
      <ConfirmDialog
        open={false}
        title="删除会话"
        message="确认删除"
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  it("open=true 时渲染 title + message + 两个按钮", () => {
    render(
      <ConfirmDialog
        open
        title="删除会话"
        message={<span data-testid="msg">确认删除会话「xxx」？</span>}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("dialog", { name: "删除会话" })).toBeInTheDocument();
    expect(screen.getByTestId("msg")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
  });

  it("点击取消触发 onClose", () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        onConfirm={() => {}}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("点击确认触发 onConfirm", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        confirmLabel="删除"
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("ESC 键触发 onClose", () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        onConfirm={() => {}}
        onClose={onClose}
      />,
    );
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("variant=danger 时确认按钮使用 btn-danger class", () => {
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        variant="danger"
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "确认" }).className).toContain("btn-danger");
  });

  it("variant=primary 时确认按钮使用 btn-primary class", () => {
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        variant="primary"
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "确认" }).className).toContain("btn-primary");
  });
});
```

- [ ] **Step 2: Run test (expect PASS — already implemented in Task 2)**

Run: `cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/confirm-dialog.test.tsx 2>&1 | tail -30`
Expected: All 7 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/renderer/confirm-dialog.test.tsx
git commit -m "test(ui): add ConfirmDialog smoke tests"
```

---

## Task 4: Extend Session type and add `ensureAgentxGenerated` action

**Files:**
- Modify: `frontend/renderer/stores/chat/index.ts`

- [ ] **Step 1: Add `generatedAgentx?: boolean` field**

In `frontend/renderer/stores/chat/index.ts`, locate the `Session` interface (around line ~108). Find the closing `};` block. **Add the new field as the last property before the closing brace**:

```ts
  /**
   * 会话权限模式：
   * - "standard"：标准审批流
   * - "full_trust"：session 内全放行
   */
  permissionMode: PermissionMode;
  /**
   * 是否已为本会话的工作区生成过 .agentx/。
   * 仅作为"一次会话最多一次"的快速护栏；真实存在性以 getProjectConfig 为准。
   * 缺省视为 false；旧 localStorage 数据迁移时不需特殊处理（undefined 兼容）。
   */
  generatedAgentx?: boolean;
}
```

- [ ] **Step 2: Add `ensureAgentxGenerated` to the `ChatState` interface**

Locate the `ChatState` interface. Find a good insertion point near other session-modifying actions (e.g., after `moveSessionToWorkspace`, around line ~170). **Add**:

```ts
  /**
   * 在 SSE done 事件触发后调用：
   * 1) 若会话已 generatedAgentx=true → return
   * 2) 若 workspacePath 为 null → return
   * 3) 先调 getProjectConfig 检查 .agentx/ 是否真实存在；存在 → 标记 true, return
   * 4) 立即抢占 set(true) 防并发；
   * 5) fire-and-forget initProjectConfig；失败 → set 回 false 允许下次重试
   */
  ensureAgentxGenerated: (sessionId: string) => Promise<void>;
```

- [ ] **Step 3: Add imports**

At the top of the file, with the other API imports (alongside `import { sandbox, memory } from "@/lib/api/http";`), **add**:

```ts
import {
  initProjectConfig,
  getProjectConfig,
} from "@/lib/api/projectConfig";
import { logger } from "@/lib/logger";
```

- [ ] **Step 4: Implement the action**

In the store implementation, locate the `deleteSession` action block (around line ~321). **Add `ensureAgentxGenerated` immediately after `renameSession`** (around line ~348):

```ts

        ensureAgentxGenerated: async (sessionId) => {
          // 局部再取一次最新 session，避免并发 set 覆盖
          const sess = get().sessions[sessionId];
          if (!sess?.workspacePath) return;
          if (sess.generatedAgentx) return;
          const wsPath = sess.workspacePath;

          // 第二层防护：先用 getProjectConfig 真实检查 .agentx/ 是否存在
          try {
            const status = await getProjectConfig(wsPath, sessionId);
            if (status?.exists) {
              set((s) => {
                const cur = s.sessions[sessionId];
                if (!cur) return s;
                return {
                  sessions: {
                    ...s.sessions,
                    [sessionId]: { ...cur, generatedAgentx: true },
                  },
                };
              });
              return;
            }
          } catch (err) {
            // getProjectConfig 失败不阻塞；继续尝试 init
            logger.warn("getProjectConfig precheck failed", err);
          }

          // 抢占：先 set(true) 防并发 done 事件重复触发
          set((s) => {
            const cur = s.sessions[sessionId];
            if (!cur) return s;
            return {
              sessions: {
                ...s.sessions,
                [sessionId]: { ...cur, generatedAgentx: true },
              },
            };
          });

          // fire-and-forget；失败回滚 generatedAgentx=false
          try {
            await initProjectConfig(wsPath, sessionId);
          } catch (err) {
            logger.warn("initProjectConfig failed", err);
            set((s) => {
              const cur = s.sessions[sessionId];
              if (!cur) return s;
              return {
                sessions: {
                  ...s.sessions,
                  [sessionId]: { ...cur, generatedAgentx: false },
                },
              };
            });
          }
        },
```

- [ ] **Step 5: Verify type-check**

Run: `cd d:\java\agentprojects\agentx && npx tsc --noEmit -p frontend/renderer/tsconfig.web.json 2>&1 | head -40`
Expected: No new errors related to `stores/chat/index.ts`.

- [ ] **Step 6: Commit**

```bash
git add frontend/renderer/stores/chat/index.ts
git commit -m "feat(chat): add generatedAgentx field + ensureAgentxGenerated action"
```

---

## Task 5: Write failing tests for `ensureAgentxGenerated` (TDD red)

**Files:**
- Create: `tests/renderer/ensure-agentx-generated.test.ts`

- [ ] **Step 1: Create test file**

Create `tests/renderer/ensure-agentx-generated.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  const m = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => m.set(k, String(v)),
    removeItem: (k: string) => m.delete(k),
    clear: () => m.clear(),
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() {
      return m.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage,
    configurable: true,
    writable: true,
  });
});

const projectConfigMock = vi.hoisted(() => ({
  initProjectConfig: vi.fn(),
  getProjectConfig: vi.fn(),
}));

const loggerMock = vi.hoisted(() => ({
  warn: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/lib/api/projectConfig", () => projectConfigMock);
vi.mock("@/lib/logger", () => ({ logger: loggerMock }));

import { useChatStore } from "@/stores/chat";

beforeEach(() => {
  projectConfigMock.initProjectConfig.mockReset();
  projectConfigMock.getProjectConfig.mockReset();
  loggerMock.warn.mockReset();
  useChatStore.setState({
    sessions: {},
    currentId: null,
    homeWorkspacePath: null,
    isStreaming: false,
    approvalQueue: [],
  });
});

describe("ensureAgentxGenerated", () => {
  it("workspacePath=null 时立即 return，不调用任何 API", async () => {
    const id = await useChatStore.getState().createSession(null);
    await useChatStore.getState().ensureAgentxGenerated(id);
    expect(projectConfigMock.getProjectConfig).not.toHaveBeenCalled();
    expect(projectConfigMock.initProjectConfig).not.toHaveBeenCalled();
  });

  it("不存在会话 id 时立即 return", async () => {
    await useChatStore.getState().ensureAgentxGenerated("nonexistent");
    expect(projectConfigMock.getProjectConfig).not.toHaveBeenCalled();
    expect(projectConfigMock.initProjectConfig).not.toHaveBeenCalled();
  });

  it("第一层护栏：generatedAgentx 已为 true 时短路", async () => {
    const id = await useChatStore.getState().createSession("/ws/proj");
    useChatStore.setState((s) => ({
      sessions: {
        ...s.sessions,
        [id]: { ...s.sessions[id]!, generatedAgentx: true },
      },
    }));
    await useChatStore.getState().ensureAgentxGenerated(id);
    expect(projectConfigMock.getProjectConfig).not.toHaveBeenCalled();
    expect(projectConfigMock.initProjectConfig).not.toHaveBeenCalled();
  });

  it("第二层护栏：getProjectConfig 返回 exists=true 时短路且标记", async () => {
    projectConfigMock.getProjectConfig.mockResolvedValue({ exists: true, files: [], agents_md_preview: null });

    const id = await useChatStore.getState().createSession("/ws/proj");
    await useChatStore.getState().ensureAgentxGenerated(id);

    expect(projectConfigMock.getProjectConfig).toHaveBeenCalledWith("/ws/proj", id);
    expect(projectConfigMock.initProjectConfig).not.toHaveBeenCalled();
    expect(useChatStore.getState().sessions[id]?.generatedAgentx).toBe(true);
  });

  it("exists=false 时调 initProjectConfig 并标记成功", async () => {
    projectConfigMock.getProjectConfig.mockResolvedValue({ exists: false, files: [], agents_md_preview: null });
    projectConfigMock.initProjectConfig.mockResolvedValue({ ok: true, path: "/ws/proj", created: ["AGENTS.md"], skipped: [] });

    const id = await useChatStore.getState().createSession("/ws/proj");
    await useChatStore.getState().ensureAgentxGenerated(id);

    expect(projectConfigMock.initProjectConfig).toHaveBeenCalledWith("/ws/proj", id);
    expect(useChatStore.getState().sessions[id]?.generatedAgentx).toBe(true);
    expect(loggerMock.warn).not.toHaveBeenCalled();
  });

  it("init 失败时回滚 generatedAgentx=false 并 warn", async () => {
    projectConfigMock.getProjectConfig.mockResolvedValue({ exists: false, files: [], agents_md_preview: null });
    projectConfigMock.initProjectConfig.mockRejectedValue(new Error("boom"));

    const id = await useChatStore.getState().createSession("/ws/proj");
    await useChatStore.getState().ensureAgentxGenerated(id);

    expect(useChatStore.getState().sessions[id]?.generatedAgentx).toBe(false);
    expect(loggerMock.warn).toHaveBeenCalledWith(
      "initProjectConfig failed",
      expect.any(Error),
    );
  });

  it("并发调用：第二次进入时已被抢占，不会二次调 API", async () => {
    // 让 getProjectConfig 一直 pending，第一次无法立即 set(true)
    let resolveGet: (v: { exists: boolean }) => void = () => {};
    projectConfigMock.getProjectConfig.mockImplementation(
      () => new Promise((r) => { resolveGet = r; }),
    );
    projectConfigMock.initProjectConfig.mockResolvedValue({ ok: true, path: "/ws/proj", created: [], skipped: [] });

    const id = await useChatStore.getState().createSession("/ws/proj");
    const p1 = useChatStore.getState().ensureAgentxGenerated(id);
    // 第二次进入 —— 此时 sess.generatedAgentx 仍为 false（getProjectConfig 还没 resolve）
    const p2 = useChatStore.getState().ensureAgentxGenerated(id);
    expect(projectConfigMock.getProjectConfig).toHaveBeenCalledTimes(1);

    // 让 p1 完成
    resolveGet({ exists: false });
    await Promise.all([p1, p2]);

    expect(projectConfigMock.initProjectConfig).toHaveBeenCalledTimes(1);
    expect(useChatStore.getState().sessions[id]?.generatedAgentx).toBe(true);
  });
});
```

- [ ] **Step 2: Run the tests**

Run: `cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/ensure-agentx-generated.test.ts 2>&1 | tail -40`
Expected: All 7 tests PASS (the action was already implemented in Task 4).

- [ ] **Step 3: Commit**

```bash
git add tests/renderer/ensure-agentx-generated.test.ts
git commit -m "test(chat): add ensureAgentxGenerated action tests"
```

---

## Task 6: Wire `ensureAgentxGenerated` into `useChatStream` `done` event

**Files:**
- Modify: `frontend/renderer/hooks/useChatStream.ts`

- [ ] **Step 1: Locate the `case "done"` block** in `useChatStream.ts` (around lines 192-209).

The block ends with `callbacksRef.current.setPaused?.(false);` followed by `break;`.

- [ ] **Step 2: Append the trigger**

Inside the `case "done"` block, **after** `callbacksRef.current.setPaused?.(false);` and **before** `break;`, insert:

```ts
          // 首条有效对话（任一 agent 模式）完成后异步收敛 .agentx/ 生成（fire-and-forget）。
          // generatedAgentx 标记 + getProjectConfig 真实存在性构成双层防护；
          // 详见 stores/chat/index.ts::ensureAgentxGenerated 注释。
          const activeTid = targetThreadId();
          if (activeTid) {
            void useChatStore.getState().ensureAgentxGenerated(activeTid);
          }
```

- [ ] **Step 3: Verify hooks file still compiles**

Run: `cd d:\java\agentprojects\agentx && npx tsc --noEmit -p frontend/renderer/tsconfig.web.json 2>&1 | grep -E "useChatStream|error TS" | head -20`
Expected: No new TS errors in `useChatStream.ts`.

- [ ] **Step 4: Commit**

```bash
git add frontend/renderer/hooks/useChatStream.ts
git commit -m "feat(stream): trigger ensureAgentxGenerated on SSE done event"
```

---

## Task 7: Replace `window.confirm` in SessionList with `ConfirmDialog`

**Files:**
- Modify: `frontend/renderer/components/chat/SessionList.tsx`

- [ ] **Step 1: Add imports**

At the top of `SessionList.tsx`, **add**:

```tsx
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
```

after the existing `lucide-react` import block.

- [ ] **Step 2: Add `pendingDelete` state**

Inside `SessionList()` component body (near the top of the function), **add**:

```tsx
  // 待确认的删除项；非 null 时打开 ConfirmDialog
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null);
```

place it next to other `useState` declarations (around the existing `devMode` state).

- [ ] **Step 3: Replace `handleDelete` body**

Find the existing `handleDelete` function (around lines 124-137) and **replace its body**:

```tsx
  const handleDelete = (id: string, title: string) => {
    // 先清除焦点，避免 confirm 关闭后浏览器恢复焦点到即将被卸载的删除按钮，
    // 与 ChatComposer useEffect 里的 textareaRef.current?.focus() 产生竞争，
    // 导致输入框无法获得焦点（切换应用后恢复）。
    (document.activeElement as HTMLElement | null)?.blur();
    setPendingDelete({ id, title });
  };
```

(保留 blur() 不变，仅把 `window.confirm(...)` 替换为 set state。)

- [ ] **Step 4: Add `confirmDelete` next to `handleDelete`**

**Add** immediately after `handleDelete`:

```tsx
  const confirmDelete = () => {
    const pending = pendingDelete;
    setPendingDelete(null);
    if (!pending) return;
    deleteSession(pending.id);
    // 与原 window.confirm 关闭后延迟让 ChatComposer 的 focus 生效逻辑一致。
    // ConfirmDialog 内部 useModalDialog 会尝试恢复焦点到删除按钮（trigger），
    // 但删除按钮即将随 SessionItem 卸载，所以这里手动 blur + 延迟 focus。
    window.setTimeout(() => {
      const composer = document.querySelector('textarea[aria-label="消息输入框"]') as HTMLTextAreaElement | null;
      composer?.focus();
    }, 50);
  };
```

- [ ] **Step 5: Mount `<ConfirmDialog>` at the end of the return JSX**

Find the closing `</div>` of the outermost `flex h-full` wrapper (right before `return (...);` closes). **Add** as the last child **inside** that wrapper:

```tsx
      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除会话"
        message={
          pendingDelete ? (
            <>
              确认删除会话「<b>{pendingDelete.title}</b>」？删除后无法恢复。
            </>
          ) : null
        }
        variant="danger"
        confirmLabel="删除"
        onConfirm={confirmDelete}
        onClose={() => setPendingDelete(null)}
      />
```

- [ ] **Step 6: Verify SessionList compiles**

Run: `cd d:\java\agentprojects\agentx && npx tsc --noEmit -p frontend/renderer/tsconfig.web.json 2>&1 | grep -E "SessionList|error TS" | head -20`
Expected: No new TS errors in `SessionList.tsx`.

- [ ] **Step 7: Commit**

```bash
git add frontend/renderer/components/chat/SessionList.tsx
git commit -m "feat(ui): replace window.confirm with ConfirmDialog in SessionList"
```

---

## Task 8: Remove `initProjectConfig` from `ChatComposer.handleAttachWorkspace`

**Files:**
- Modify: `frontend/renderer/components/chat/ChatComposer.tsx`

- [ ] **Step 1: Remove unused import**

In `ChatComposer.tsx`, locate the import block around lines 28-30:

```tsx
import { openFile, openFolder, saveDroppedFile } from "@/lib/api/dialog";
import { initProjectConfig } from "@/lib/api/projectConfig";
import { logger } from "@/lib/logger";
```

**Remove** the `initProjectConfig` and `logger` import lines:

```tsx
import { openFile, openFolder, saveDroppedFile } from "@/lib/api/dialog";
```

- [ ] **Step 2: Remove the trailing best-effort block in `handleAttachWorkspace`**

Locate `handleAttachWorkspace` (around lines 383-420). Find the trailing block:

```tsx
    // best-effort：授权成功后静默生成 .agentx/ 项目级配置目录。
    // 失败不阻塞工作区绑定，仅记录告警。
    // tid 来自上方 createSession / currentId，必为非空 string。
    try {
      await initProjectConfig(dirPath, tid!);
    } catch (err) {
      logger.warn("initProjectConfig failed", err);
    }
  };
```

**Delete those 8 lines** so the function closes immediately after the `authorizeAndUnmark` try/catch:

```tsx
      setDropError(
        `授权目录「${dirPath}」失败：${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }
    // .agentx/ 的初始化已迁移到「首条对话完成后」由 stores/chat/index.ts::ensureAgentxGenerated 处理。
  };
```

- [ ] **Step 3: Verify ChatComposer compiles**

Run: `cd d:\java\agentprojects\agentx && npx tsc --noEmit -p frontend/renderer/tsconfig.web.json 2>&1 | grep -E "ChatComposer|error TS" | head -20`
Expected: No new TS errors in `ChatComposer.tsx`.

- [ ] **Step 4: Commit**

```bash
git add frontend/renderer/components/chat/ChatComposer.tsx
git commit -m "refactor(composer): drop initProjectConfig call (moved to ensureAgentxGenerated)"
```

---

## Task 9: Optional — extend `useChatStream.test.ts` with `done`-event-trigger assertion

**Files:**
- Modify (only if file exists): `tests/renderer/useChatStream.test.ts`

- [ ] **Step 1: Locate the existing `it("done 事件设置 isStreaming=false", ...)` test** (around line 135).

- [ ] **Step 2: Add a mock for `projectConfig` module**

At the top of the file, after the existing `vi.mock("@/lib/api/chat", ...)`, **add** (before any `import` statements that consume projectConfig):

```ts
const projectConfigMock = vi.hoisted(() => ({
  initProjectConfig: vi.fn(),
  getProjectConfig: vi.fn(),
}));
vi.mock("@/lib/api/projectConfig", () => projectConfigMock);
```

and inside `beforeEach`, **add**:

```ts
  projectConfigMock.initProjectConfig.mockReset().mockResolvedValue({ ok: true, path: "", created: [], skipped: [] });
  projectConfigMock.getProjectConfig.mockReset().mockResolvedValue({ exists: true, files: [], agents_md_preview: null });
```

(These defaults make `ensureAgentxGenerated` short-circuit on the second-layer check, so the test only verifies that it was invoked.)

- [ ] **Step 3: Append the new test**

At the end of the existing `describe("useChatStream hook", ...)`, **add**:

```ts
  it("done 事件触发 ensureAgentxGenerated(activeTid)", async () => {
    const id = await useChatStore.getState().createSession("/ws/proj");

    renderHook(() =>
      useChatStream({
        activeThreadIdRef: { current: id },
        pendingIdRef: { current: null },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    await act(async () => {
      emitEvent({ type: "done", data: {} });
    });

    // getProjectConfig 必被调用（第二层防护的真实存在性检查）
    expect(projectConfigMock.getProjectConfig).toHaveBeenCalledWith("/ws/proj", id);
  });
```

- [ ] **Step 4: Run the suite**

Run: `cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/useChatStream.test.ts 2>&1 | tail -30`
Expected: All tests (original + new) PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/renderer/useChatStream.test.ts
git commit -m "test(stream): verify done event triggers ensureAgentxGenerated"
```

If the file is in a state that makes adding the mock fragile, **skip this task** — the existing useChatStream tests still cover the SSE pipeline; the action is unit-tested in Task 5.

---

## Task 10: Final verification — run full frontend unit-test suite

**Files:** none

- [ ] **Step 1: Run the full vitest suite**

Run: `cd d:\java\agentprojects\agentx && npx vitest run tests/renderer 2>&1 | tail -50`
Expected: All tests pass; if any pre-existing test breaks, revert with `git revert <last-commit>` per task and open an issue.

- [ ] **Step 2: Run TypeScript type-check across the workspace**

Run: `cd d:\java\agentprojects\agentx && npx tsc --noEmit -p frontend/renderer/tsconfig.web.json 2>&1 | tail -20`
Expected: No errors (pre-existing errors OK; new errors mean a step was missed).

- [ ] **Step 3: Manual smoke checklist** (read-only review)

Open `docs/superpowers/specs/2026-07-08-session-delete-confirm-and-agentx-autogen-design.md` §3-§7 and tick each requirement against the diff:

- [ ] Session 删除使用 ConfirmDialog，ESC/点遮罩/X 关闭
- [ ] handleAttachWorkspace 不再 await initProjectConfig
- [ ] useChatStream done 末尾触发 ensureAgentxGenerated
- [ ] generatedAgentx 字段持久化兼容（缺省 false）
- [ ] 双层防护（标记 + getProjectConfig.exists）

---

## Self-Review Checklist

1. **Spec coverage**: §3.1 (generatedAgentx 字段) → Task 4 ✓; §3.2 (action) → Task 4 ✓; §4.1 (ConfirmDialog) → Task 2+3 ✓; §4.2 (SessionList 集成) → Task 7 ✓; §4.3 (ChatComposer 移除 init) → Task 8 ✓; §4.4 (useChatStream 触发) → Task 6 ✓; §5.2 (双层防护) → Task 5 tests ✓
2. **Placeholder scan**: All code blocks contain complete code; no "TBD" / "TODO"
3. **Type consistency**: `ensureAgentxGenerated`, `generatedAgentx`, `pendingDelete`, `confirmDelete` are introduced once and reused consistently
