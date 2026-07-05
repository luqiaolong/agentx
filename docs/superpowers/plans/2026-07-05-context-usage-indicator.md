# ContextUsage 指示器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ChatComposer Toolbar 右组最左插入 ContextUsage（5 条纵向黑白条纹 widget），同时删除左下角的 [/][@] 两个冗余按钮

**Architecture:** 用 zustand selector（`useContextUsage`）订阅 useChatStore + useModelStore，前端按 4 chars/token 公式估算当前会话 token 数；按 active model.contextWindow 取分母；pure presentation 组件（无 click handler，仅 hover tooltip）

**Tech Stack:** React 18 + TypeScript + Tailwind v4 + zustand + vitest

**Spec:** [`docs/superpowers/specs/2026-07-05-context-usage-indicator-design.md`](../specs/2026-07-05-context-usage-indicator-design.md)

---

## File Structure

| 文件 | 类型 | 职责 |
|---|---|---|
| `frontend/renderer/stores/contextUsage.ts` | **新建** | `useContextUsage()` selector + `estimateTokens()` |
| `frontend/renderer/components/chat/ContextUsage.tsx` | **新建** | 18×10px 5 条纹纯展示组件 |
| `frontend/renderer/components/chat/ChatComposer.tsx` | 修改 | 删除 Slash/AtSign 按钮 + 插入 `<ContextUsage />` |
| `frontend/shared/api-types.ts` | 修改 | `ModelEntry` 加 `contextWindow?: number \| null` |
| `tests/renderer/context-usage.test.tsx` | **新建** | ContextUsage 单元测试 |
| `tests/renderer/chat-composer.test.tsx` | 修改 | 新增 ContextUsage 存在性轻测 |

> 注：`settings.getLLMConfig()` 返回类型在 `api-types.ts` 中通过 `ElectronAPI` 索引签名已"宽松"，无需单独标注 `contextWindow`（后端实现尚未支持，本次纯前端）

---

## Task 1: ModelEntry 类型新增 contextWindow 可选字段

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\shared\api-types.ts:197-206`

- [ ] **Step 1: 修改 ModelEntry 接口**

找到（约 197-206 行）：

```ts
export interface ModelEntry {
  id: string;
  label: string;
  providerId: ModelProviderId;
  model: string;
  baseUrl: string;
  /** 加密后的 API Key（enc:... 或 plain:...），renderer 视为不透明字符串 */
  apiKey: string;
  createdAt: number;
}
```

在 `createdAt: number;` 后新增一行：

```ts
export interface ModelEntry {
  id: string;
  label: string;
  providerId: ModelProviderId;
  model: string;
  baseUrl: string;
  /** 加密后的 API Key（enc:... 或 plain:...），renderer 视为不透明字符串 */
  apiKey: string;
  createdAt: number;
  /** 模型最大上下文 token 上限（用户在「设置 → 模型」可选填入）；
   *  undefined / null → useContextUsage 降级使用默认 16000 */
  contextWindow?: number | null;
}
```

- [ ] **Step 2: TypeScript 类型校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无新增错误。

- [ ] **Step 3: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/shared/api-types.ts && git commit -m "feat(types): ModelEntry 新增 contextWindow 可选字段"
```

---

## Task 2: 新建 useContextUsage selector（含 estimator 与 selector）

**Files:**
- Create: `d:\java\agentprojects\agentx\frontend\renderer\stores\contextUsage.ts`

- [ ] **Step 1: 写失败测试**

新建 `d:\java\agentprojects\agentx\tests\renderer\context-usage.test.tsx`：

```tsx
import { beforeEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";

// jsdom + 内存版 localStorage（zustand persist 需要）
vi.hoisted(() => {
  const store = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k) => store.get(k) ?? null,
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); },
    clear: () => store.clear(),
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() { return store.size; },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage, configurable: true, writable: true,
  });
});

import { useContextUsage } from "@/stores/contextUsage";
import { useChatStore } from "@/stores/chat";
import { useModelStore } from "@/stores/model";

function Probe() {
  const r = useContextUsage();
  return (
    <div
      data-testid="probe"
      data-tokens={String(r.tokens)}
      data-pct={String(r.pct)}
      data-max={String(r.modelMax)}
      data-label={r.activeLabel}
    />
  );
}

beforeEach(() => {
  useChatStore.setState({
    sessions: {}, currentId: null, homeWorkspacePath: null,
    isStreaming: false, approvalRequest: null,
  });
  useModelStore.setState({
    entries: [], activeId: null, defaultModel: "", loaded: false, loading: false,
  });
});

describe("useContextUsage selector", () => {
  it("空 session + 无 model 时 pct=0, modelMax=16000", () => {
    const { getByTestId } = render(<Probe />);
    expect(Number(getByTestId("probe").dataset.pct)).toBe(0);
    expect(Number(getByTestId("probe").dataset.max)).toBe(16000);
  });

  it("session 有 user 消息时 tokens > 0", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: crypto.randomUUID(),
      role: "user",
      ts: Date.now(),
      content: "a".repeat(1000),  // 1000 chars → ~250 tokens
    });
    const { getByTestId } = render(<Probe />);
    const tokens = Number(getByTestId("probe").dataset.tokens);
    expect(tokens).toBeGreaterThanOrEqual(200);
    expect(tokens).toBeLessThanOrEqual(300);
  });

  it("active model 有 contextWindow 时使用该值", () => {
    useModelStore.setState({
      entries: [{
        id: "m1",
        label: "BigContext",
        providerId: "custom",
        model: "x",
        baseUrl: "",
        apiKey: "",
        createdAt: 0,
        contextWindow: 400000,
      }],
      activeId: "m1",
    });
    const { getByTestId } = render(<Probe />);
    expect(Number(getByTestId("probe").dataset.max)).toBe(400000);
    expect(getByTestId("probe").dataset.label).toBe("BigContext");
  });
});
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/context-usage.test.tsx
```

期望：FAIL with "Cannot find module '@/stores/contextUsage'"。

- [ ] **Step 3: 实现 selector**

新建 `d:\java\agentprojects\agentx\frontend\renderer\stores\contextUsage.ts`：

```ts
import { useChatStore } from "./chat";
import { useModelStore } from "./model";

/**
 * 与 backend/app/memory/context.py::_token_counter 同步：4 chars / token。
 * 非 OpenAI 模型有偏差但本项目后端默认值 16000 留足余量；前端估算保持
 * 同样公式以避免前后端「同一个对话算出来百分比不一致」。
 */
function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

export interface ContextUsageInfo {
  tokens: number;
  modelMax: number;
  pct: number;
  activeLabel: string;
}

export function useContextUsage(): ContextUsageInfo {
  const sessions = useChatStore((s) => s.sessions);
  const currentId = useChatStore((s) => s.currentId);
  const entries = useModelStore((s) => s.entries);
  const activeId = useModelStore((s) => s.activeId);

  const activeEntry = entries.find((e) => e.id === activeId) ?? null;
  const modelMax = activeEntry?.contextWindow ?? 16000;
  const activeLabel = activeEntry?.label ?? "";

  const sess = currentId ? sessions[currentId] : null;
  const allText = (sess?.messages ?? [])
    .map((m) => m.content ?? "")
    .join("\n");
  const tokens = estimateTokens(allText);
  const pct = Math.min(100, Math.round((tokens / modelMax) * 100));
  return { tokens, modelMax, pct, activeLabel };
}
```

- [ ] **Step 4: 重新运行测试**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/context-usage.test.tsx
```

期望：3/3 PASS。

- [ ] **Step 5: 提交**

```bash
cd d:\java\agentprojects\agentx && git add tests/renderer/context-usage.test.tsx frontend/renderer/stores/contextUsage.ts && git commit -m "feat(context-usage): 新建 useContextUsage selector + 单元测试"
```

---

## Task 3: 新建 ContextUsage 组件（5 条纵向条纹）

**Files:**
- Create: `d:\java\agentprojects\agentx\frontend\renderer\components\chat\ContextUsage.tsx`
- Modify: `d:\java\agentprojects\agentx\tests\renderer\context-usage.test.tsx`

- [ ] **Step 1: 追加组件渲染测试**

在 `tests/renderer/context-usage.test.tsx` 末尾追加：

```tsx
import { ContextUsage } from "@/components/chat/ContextUsage";

describe("ContextUsage 组件", () => {
  it("无 session 时也渲染 5 条纹（至少 1 条 filled）", () => {
    const { container } = render(<ContextUsage />);
    const stripes = container.querySelectorAll("[data-filled]");
    expect(stripes.length).toBe(5);
    const filled = container.querySelectorAll('[data-filled="true"]');
    expect(filled.length).toBeGreaterThanOrEqual(1);
  });

  it("title 属性包含百分比与激活模型标签", () => {
    useModelStore.setState({
      entries: [{
        id: "m1",
        label: "GPT-4o",
        providerId: "openai",
        model: "gpt-4o",
        baseUrl: "",
        apiKey: "",
        createdAt: 0,
        contextWindow: 128000,
      }],
      activeId: "m1",
    });
    const { container } = render(<ContextUsage />);
    const el = container.querySelector("[title]")!;
    expect(el.getAttribute("title")).toMatch(/%/);
    expect(el.getAttribute("title")).toMatch(/GPT-4o/);
  });
});
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/context-usage.test.tsx
```

期望：FAIL "Cannot find module '@/components/chat/ContextUsage'"。

- [ ] **Step 3: 实现组件**

新建 `d:\java\agentprojects\agentx\frontend\renderer\components\chat\ContextUsage.tsx`：

```tsx
import { useContextUsage } from "@/stores/contextUsage";

/**
 * 上下文使用率纯展示组件：5 条纵向黑白条纹 widget。
 *
 * 设计要点：
 * - 无任何事件监听（pure presentation），避免与同位置 ModelToggle 误触
 * - 严格二元色：已填充 = bg-brand-500（neutral-500 黑灰），未填充 = bg-default（浅灰）
 * - 严禁引入绿/黄/红警示色（违反「黑白二元」克制视觉语言）
 * - tooltip 仅展示完整数字（percentage + 实际 token 数 + 上限 + 模型名）
 * - 5 条 ceil 映射保证 0% 也至少显示 1 条（视觉存在）
 */
export function ContextUsage() {
  const { tokens, modelMax, pct, activeLabel } = useContextUsage();
  const filledStripes = Math.min(5, Math.max(1, Math.ceil((pct / 100) * 5)));

  const tooltip =
    `${pct}% · ${tokens.toLocaleString()} / ${modelMax.toLocaleString()} tokens` +
    (activeLabel ? ` · ${activeLabel}` : "");

  return (
    <div
      title={tooltip}
      aria-label={tooltip}
      className="inline-flex h-[18px] w-[10px] flex-col items-stretch justify-end gap-[1px] cursor-help"
    >
      {Array.from({ length: 5 }).map((_, i) => {
        const filled = i < filledStripes;
        return (
          <span
            key={i}
            data-filled={filled ? "true" : "false"}
            className={[
              "h-[2.5px] w-full rounded-[0.5px]",
              filled ? "bg-brand-500" : "bg-default",
            ].join(" ")}
          />
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/context-usage.test.tsx
```

期望：5/5 PASS（3 selector + 2 component）。

- [ ] **Step 5: 提交**

```bash
cd d:\java\agentprojects\agentx && git add tests/renderer/context-usage.test.tsx frontend/renderer/components/chat/ContextUsage.tsx && git commit -m "feat(context-usage): 新建 5 条纵向条纹 widget 组件"
```

---

## Task 4: ChatComposer 改造（删除左下 [/][@] + 插入 ContextUsage）

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\components\chat\ChatComposer.tsx:1-12, 375-482`
- Modify: `d:\java\agentprojects\agentx\tests\renderer\chat-composer.test.tsx`

- [ ] **Step 1: 修改 ChatComposer.tsx 顶部 import**

找到（约 1-12 行）：

```tsx
import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Square,
  Paperclip,
  Slash,
  AtSign,
  Folder,
  FolderPlus,
  X,
  Send,
} from "lucide-react";
```

改为（删除 `Slash`、`AtSign`、`ArrowUp` 三个未用 import）：

```tsx
import { useEffect, useRef, useState } from "react";
import {
  Square,
  Paperclip,
  Folder,
  FolderPlus,
  X,
  Send,
} from "lucide-react";
import { ContextUsage } from "./ContextUsage";
```

> 注：`ArrowUp`、`Slash`、`AtSign` 这三个都是底部 Toolbar 用到的，但删除按钮后即不再需要。`Square` / `Paperclip` / `Folder` / `FolderPlus` / `X` / `Send` 仍被引用（中止按钮、拖拽 overlay、workspace icon、关闭按钮、发送按钮）。

- [ ] **Step 2: 修改 Toolbar 左组（删除 Slash / AtSign 按钮）**

找到（约 377-443 行，左组的 Slash / AtSign 两个 `<button>` 与外层 `<div className="flex items-center gap-1">` 包装）：

```tsx
              {/* LEFT — 触发类 */}
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className="btn-icon"
                  onClick={openCommandPickerManually}
                  title="调用命令或技能 (/ 命令)"
                  aria-label="调用命令或技能"
                >
                  <Slash className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  className="btn-icon"
                  onClick={() => void handleAttachFile()}
                  title="附加文件 (@)"
                  aria-label="附加文件"
                >
                  <AtSign className="h-3.5 w-3.5" />
                </button>
                {showWorkspaceChip && workspacePath ? (
                  <span
                    ...
```

改为：

```tsx
              {/* LEFT — 仅 workspace（[/][@] 已在本次变更中删除） */}
              <div className="flex items-center gap-1">
                {showWorkspaceChip && workspacePath ? (
                  <span
                    ...
```

> 保留 `handleAttachFile` 与 `openCommandPickerManually` 函数定义（仍可未来复用，对应 `Slash` icon 不再 import）。如果 TypeScript 报告 unused，可在函数前加 `eslint-disable-next-line` 注释。

- [ ] **Step 3: 在 Toolbar 右组最左插入 ContextUsage**

找到（约 448-451 行）：

```tsx
              {/* RIGHT — 状态 + 动作 */}
              <div className="flex items-center gap-1">
                <div className="flex items-center">
                  <ModelToggle />
                </div>
```

改为：

```tsx
              {/* RIGHT — 状态 + 动作：ContextUsage → Model → Permission → Send */}
              <div className="flex items-center gap-1.5">
                <ContextUsage />
                <div className="flex items-center">
                  <ModelToggle />
                </div>
```

> gap 由 `gap-1` 改为 `gap-1.5` 是为了给 ContextUsage（18px 高）和按钮（28px 高）之间留更舒适的呼吸感。

- [ ] **Step 4: TypeScript 校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无 TS 错误。如果出现 `ArrowUp`/`Slash`/`AtSign` 仍被引用警告，说明未删除干净，重新检查。

- [ ] **Step 5: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/components/chat/ChatComposer.tsx && git commit -m "refactor(chat-composer): 删除左下[/][@]按钮，Toolbar 右组插入 ContextUsage"
```

---

## Task 5: 增强 chat-composer 测试（ContextUsage 存在性轻测）

**Files:**
- Modify: `d:\java\agentprojects\agentx\tests\renderer\chat-composer.test.tsx`

- [ ] **Step 1: 在现有 Toolbar 测试块后追加**

在文件最末（第 183 行 `});` 后），已在的 Toolbar 测试块后追加：

```tsx
describe("ChatComposer 含 ContextUsage", () => {
  it("Toolbar 右组最左渲染 ContextUsage widget", () => {
    useChatStore.getState().createSession();
    const { container, getByRole } = render(
      <ChatComposer
        isStreaming={false}
        setDropError={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );
    // 工具栏右组存在 ContextUsage（title 属性以百分号开头）
    const ctx = container.querySelector("[title]");
    expect(ctx).toBeTruthy();
    expect(ctx?.getAttribute("title")).toMatch(/%/);
  });
});
```

- [ ] **Step 2: 运行测试验证通过**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/chat-composer.test.tsx
```

期望：原有 4 + 新增 1 = 5/5 PASS。

- [ ] **Step 3: 提交**

```bash
cd d:\java\agentprojects\agentx && git add tests/renderer/chat-composer.test.tsx && git commit -m "test(chat-composer): 新增 ContextUsage 存在性轻测"
```

---

## Task 6: 全量验证（typecheck + tests + 跑一次 grep 验证 icon import 已清理）

**Files:** —
- 验收：跑命令验证

- [ ] **Step 1: 全量 typecheck**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无 TS 错误。

- [ ] **Step 2: 全量 renderer 测试**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/chat-composer.test.tsx tests/renderer/context-usage.test.tsx tests/renderer/permission-toggle.test.tsx
```

期望：全部 PASS（chat-composer 5/5、context-usage 5/5、permission-toggle 16/16，合计 26/26）。

- [ ] **Step 3: 验证 toolbar 不再引用 Slash/AtSign 图标**

```bash
cd d:\java\agentprojects\agentx && grep -n "Slash\|AtSign" frontend/renderer/components/chat/ChatComposer.tsx
```

期望：无匹配（或仅匹配注释中说明已删除）。

- [ ] **Step 4: 视觉走查清单**（运行 `npm run dev` 后）

- [ ] Toolbar 左下不再有 [/] [@] 两个图标按钮
- [ ] Toolbar 右组最左出现 ContextUsage 5 条纵向条纹 widget
- [ ] 增加会话消息后条纹数逐步增长
- [ ] hover 1 秒显示原生 tooltip「X% · used/max tokens · 模型名」
- [ ] 切换激活模型后条纹阈值映射基于新 `contextWindow` 重算
- [ ] ModelToggle / PermissionToggle / Send 按钮位置与功能不变

- [ ] **Step 5: 列出本任务全部 commit 概览**

```bash
cd d:\java\agentprojects\agentx && git log --oneline -10
```

期望：5 个新 commit 在最近历史中。

- [ ] **Step 6: 提交（如有 Step 4 视觉微调）**

若有 padding / 间距微调，独立 `style:` 提交。无则跳过。

---

## Self-Review

**Spec coverage check:**

| Spec 章节 | 对应任务 |
|---|---|
| §1 产品结论 | Task 4（左下删除 + 右组插入） |
| §2 用户工作流 | Task 2/3（订阅触发 + hover tooltip） |
| §3 数据模型（ModelEntry 字段） | Task 1 |
| §3 数据模型（useContextUsage selector） | Task 2 |
| §4 ContextUsage 组件 18×10 + 5 条 + 黑白二元 | Task 3 |
| §4 Toolbar 整体结构 | Task 4 |
| §5 保留/删除/新增交互 | Task 4（删除按钮）+ Task 3（无 click handler） |
| §5 ContextUsage 单元测试 | Task 2 (3 cases) + Task 3 (2 cases) |
| §6 文件改动清单 | Task 1-5 完整覆盖 6 个文件 |

**Type consistency:**
- `ModelEntry.contextWindow` 在 Task 1 定义，`useContextUsage` 在 Task 2 / Task 3 引用 ✓
- `activeLabel` 在 Task 2 selector 返回，Task 3 组件 tooltip 引用 ✓
- `useChatStore.addMessage` 在 Task 2 测试 fixture 调用，与 chat store 签名一致 ✓

**Placeholders:** 无 TBD / TODO / 待补内容。

**Known risks to monitor:**
- Task 4 步骤 2：删除两个 button 后若 `handleAttachFile` / `openCommandPickerManually` 仍保留可能触发 TypeScript `noUnusedLocals` 警告 → 视情况加注释
- Task 6 步骤 3：grep 仍匹配可能是因为组件 import 区域残留 → 检查 1-12 行 import 段
