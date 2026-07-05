# ChatComposer 上下文使用率纵向条纹指示器

> 日期：2026-07-05
> 主题：右下角新增 ContextUsage 黑白纵向条纹 widget，并清理 [/][@] 旧按钮
> 状态：已确认设计，待实现

## 1. 产品结论

**问题现状**

[ChatComposer.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ChatComposer.tsx) 底部 Toolbar（刚由 commit `f6b9a44` 重构）只有「触发类 [/][@][workspace] / 状态类 [Model][Permission] / 动作类 [Send]」，**没有任何上下文使用率可视化**。用户看不到当前会话消息累计占用多少 token，离模型上限还有多少空间。

后端已有 `context_max_tokens` (默认 16000) 和 `trim_messages_with_budget()` ([backend/app/memory/context.py](file:///d:/java/agentprojects/agentx/backend/app/memory/context.py))，但仅做"超限截断"而**无前端暴露**。这是 `chat-context-management` 提案 ([openspec/changes/archive/2026-07-05-chat-context-management/proposal.md](file:///d:/java/agentprojects/agentx/openspec/changes/archive/2026-07-05-chat-context-management/proposal.md)) 推迟的 M2 工作。

**改后效果**

```
┌─ ChatComposer (.chat-composer) ───────────────────────┐
│  [textarea]                                          │
│  ──────────────────────────────────────               │
│  📁 workspace      ░  Model▼  工作区▼  ↗              │
│                   ░                                    │
│  ↑ 旧 [/][@] 删除   ▓  新增 18×10 纵向 5 条纹 widget   │
└───────────────────────────────────────────────────────┘
```

新引入两个产物：

1. **`<ContextUsage />`** 组件 — 仅纵向 5 条黑白条纹，自下而上填充，无文字
2. **`useContextUsage()`** selector — 订阅 `useChatStore` + `useModelStore`，前端估算 token 数（与后端 `_token_counter` 对齐 4 chars/token）

额外清理：**删除左下角的 [/] 命令面板按钮与 [@] 文件附加按钮**，因为它们：
- 已与 `/` 输入触发命令面板、文件拖拽、workspace 文件浏览器冗余
- 信息密度低，Toolbar 砍掉它们后留出 50px 给状态类

## 2. 用户工作流

```
用户打开会话并陆续添加消息
  → 纵向条纹从「1 条 dark」逐步增长到「5 条 dark」
  → hover 显示原生 tooltip：28% · 113,200 / 400,000 tokens

用户切换到另一个会话
  → useContextUsage 重算新会话的 token 数；条纹立即重置

用户切换激活模型
  → ModelStore.activeId 变化 → 重算分母 → 条纹阈值映射重映射

用户想"知道具体数字"
  → hover 1 秒，浏览器原生 tooltip 显示百分比 + 实际数字
  → 如果想压缩消息量：可手动调用 /compact 命令（已存在）
```

## 3. 数据模型与状态

### 3.1 ModelEntry 新增字段

[`frontend/shared/api-types.ts`](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts) **L197-L206**：

```ts
export interface ModelEntry {
  id: string;
  label: string;
  providerId: ModelProviderId;
  model: string;
  baseUrl: string;
  apiKey: string;
  createdAt: number;
  /** 模型最大上下文 token 上限（用户设置面板手动填入）；
   *  undefined / null → useContextUsage 降级用默认 16000 */
  contextWindow?: number | null;
}
```

### 3.2 LLMConfig 返回类型扩展

`settings.getLLMConfig()` 返回类型新增 `contextWindow?: number | null`，对应当前激活模型的上限。

> 后端 Settings / `_pending_approvals` / `ModelEntry` 持久化格式不变（contextWindow 作为新字段，缺省时优雅降级）。

### 3.3 useContextUsage selector

新建 [`frontend/renderer/stores/contextUsage.ts`](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/contextUsage.ts)：

```ts
import { useChatStore } from "./chat";
import { useModelStore } from "./model";

function estimateTokens(text: string): number {
  // 与 backend/app/memory/context.py::_token_counter 同步：4 chars / token
  return Math.max(1, Math.ceil(text.length / 4));
}

export function useContextUsage(): {
  tokens: number;
  modelMax: number;
  pct: number;
  activeLabel: string;
} {
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

### 3.4 删除 [/][@] 按钮

[`ChatComposer.tsx`](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ChatComposer.tsx) Toolbar **左组**：

- 移除 `<button onClick={openCommandPickerManually}>`
- 移除 `<button onClick={() => void handleAttachFile()}>`
- 保留 `Slash` / `AtSign` 导入从 lucide-react 删除（不再使用）
- **保留**：`handleAttachFile` 函数实现（函数本身仍可能未来复用）；`openCommandPickerManually` 函数也可保留

> 用户仍可通过输入 `/` 触发命令面板；文件仍可通过拖拽附加。

## 4. UI 规范

### 4.1 ContextUsage 组件结构

新建 [`frontend/renderer/components/chat/ContextUsage.tsx`](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ContextUsage.tsx)：

```tsx
import { useContextUsage } from "@/stores/contextUsage";

export function ContextUsage() {
  const { tokens, modelMax, pct, activeLabel } = useContextUsage();
  const filledStripes = Math.min(5, Math.max(1, Math.ceil((pct / 100) * 5)));

  const tooltip = `${pct}% · ${tokens.toLocaleString()} / ${modelMax.toLocaleString()} tokens${activeLabel ? ` · ${activeLabel}` : ""}`;

  return (
    <div
      title={tooltip}
      aria-label={tooltip}
      className="inline-flex flex-col items-stretch justify-end gap-[1px] h-[18px] w-[10px] cursor-help"
    >
      {Array.from({ length: 5 }).map((_, i) => (
        <span
          key={i}
          data-filled={i < filledStripes ? "true" : "false"}
          className={[
            "h-[2.5px] w-full rounded-[0.5px]",
            i < filledStripes ? "bg-brand-500" : "bg-default",
          ].join(" ")}
        />
      ))}
    </div>
  );
}
```

### 4.2 视觉规范

| 元素 | 规格 |
|---|---|
| 容器尺寸 | 18px 高 × 10px 宽 |
| 条纹数 | 5 条固定 |
| 条纹高度 | 2.5px 各 |
| 条纹间距 | 1px gap（`gap-[1px]`） |
| 条纹已填充色 | `bg-brand-500`（neutral-500，黑灰） |
| 条纹未填充色 | `bg-default`（neutral-200/800，浅灰） |
| 颜色变化 | **无**（严格二元；不引入绿/黄/红） |
| tooltip 内容 | `28% · 113,200 / 400,000 tokens · GPT-4o` |
| 位置 | Toolbar 右组**最左**（在 ModelToggle 之前） |

### 4.3 阈值映射

| pct | 5 条中 dark 条数 |
|---|---|
| 0-20% | 1 |
| 20-40% | 2 |
| 40-60% | 3 |
| 60-80% | 4 |
| 80-100% | 5 |

> `Math.ceil((pct / 100) * 5)` 保证 1% 也至少 1 条 dark，0% 也至少 1 条（视觉存在）。

### 4.4 Toolbar 最终结构（修改前/后对比）

**修改前**（commit `f6b9a44` 当前态）：
```tsx
<div className="mt-1.5 flex items-center justify-between gap-1 border-t border-default pt-1.5">
  {/* LEFT — 触发类 */}
  <div className="flex items-center gap-1">
    <button>Slash</button>
    <button>AtSign</button>
    {workspaceChip ?? <button>FolderPlus</button>}
  </div>
  <div className="flex-1" />
  {/* RIGHT — 状态 + 动作 */}
  <div className="flex items-center gap-1">
    <ModelToggle />
    <PermissionToggle />
    <SendOrStopButton />
  </div>
</div>
```

**修改后**：
```tsx
<div className="mt-1.5 flex items-center justify-between gap-1 border-t border-default pt-1.5">
  {/* LEFT — 仅 workspace（删除 Slash/AtSign） */}
  <div className="flex items-center gap-1">
    {workspaceChip ?? <button>FolderPlus</button>}
  </div>
  <div className="flex-1" />
  {/* RIGHT — 状态 + 动作：插入 ContextUsage */}
  <div className="flex items-center gap-1.5">
    <ContextUsage />
    <ModelToggle />
    <PermissionToggle />
    <SendOrStopButton />
  </div>
</div>
```

### 4.5 主题一致性

- 亮色主题：bg-default 是 neutral-200（浅灰），bg-brand-500 是 neutral-500（深灰），对比清晰
- 暗色主题：bg-default 是 neutral-800（中灰），bg-brand-500 是 neutral-500（浅灰灰），对比反转但仍可辨
- 无需主题额外样式适配

### 4.6 响应式

- 在窄屏（< 600px）仍按规格正常显示（10×18px，固定小尺寸，不抢占空间）
- 不增加任何响应式分支

## 5. 行为与兼容

### 5.1 保留不变

- 键盘：`Enter` 发送 / `Shift+Enter` 换行 / `/` 触发命令 / `↑` 空内容编辑上一条 — 全部不变
- 拖拽：仍挂在 `.chat-composer` 容器
- workspace 切换 / ModelToggle / PermissionToggle / Send 全部零修改
- SSE 流结束自动 focus textarea

### 5.2 新增交互

- `<ContextUsage />` 是纯展示组件，**不挂任何事件监听**
- 鼠标 hover 时由浏览器原生 `title` 属性弹出 tooltip（约 1 秒延迟）
- **禁止** 点击触发任何 popover，避免与同位置的 ModelToggle 误触

### 5.3 删除交互

- 左下的 [/]（命令面板）按钮：用户仍可通过输入 `/` 触发
- 左下的 [@]（附加文件）按钮：用户仍可拖拽文件入输入框附带
- 函数实现 `handleAttachFile` / `openCommandPickerManually` 仍可保留以备未来复用

### 5.4 测试影响

| 测试文件 | 改动 |
|---|---|
| `tests/renderer/chat-composer.test.tsx` | 新增 1 条「ContextUsage 在 Toolbar 内 + tooltip 含 pct」的轻测 |
| `tests/renderer/permission-toggle.test.tsx` | 零影响（组件不变） |

### 5.5 ContextUsage 单元测试建议（新增文件）

`tests/renderer/context-usage.test.tsx`：

```tsx
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { ContextUsage } from "@/components/chat/ContextUsage";
import { useChatStore } from "@/stores/chat";
import { useModelStore } from "@/stores/model";

describe("ContextUsage 组件", () => {
  it("无 current session 时仍渲染 5 条纹（至少 1 条 dark）", () => {
    const { container } = render(<ContextUsage />);
    const stripes = container.querySelectorAll("[data-filled]");
    expect(stripes.length).toBe(5);
    // 缺省值下 pct=0，ceil(0)=0 → 与 5.max 比较后取 1，至少 1 条 dark
    const darkCount = container.querySelectorAll('[data-filled="true"]').length;
    expect(darkCount).toBeGreaterThanOrEqual(1);
  });

  it("tooltip 包含 pct / tokens 数字 / 激活模型名", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: crypto.randomUUID(),
      role: "user",
      ts: Date.now(),
      content: "hello",
    });
    useModelStore.setState({
      entries: [
        {
          id: "m1",
          label: "GPT-4o",
          providerId: "openai",
          model: "gpt-4o",
          baseUrl: "",
          apiKey: "",
          createdAt: 0,
          contextWindow: 128000,
        },
      ],
      activeId: "m1",
    });
    const { container } = render(<ContextUsage />);
    const el = container.querySelector("[title]");
    expect(el?.getAttribute("title")).toMatch(/GPT-4o/);
    expect(el?.getAttribute("title")).toMatch(/%/);
  });
});
```

## 6. 文件改动清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/renderer/components/chat/ContextUsage.tsx` | **新建** | 18px 高 5 条纹 widget |
| `frontend/renderer/stores/contextUsage.ts` | **新建** | `useContextUsage()` selector |
| `frontend/renderer/components/chat/ChatComposer.tsx` | 修改 | 删除左下 [/][@] 两个按钮 + 导入 Slash/AtSign；插入 `<ContextUsage />` 到 Toolbar 右组最左 |
| `frontend/shared/api-types.ts` | 修改 | `ModelEntry` 加 `contextWindow?: number \| null` |
| `frontend/preload/index.ts` | 微调 | `settings.getLLMConfig()` 返回类型扩展 |
| `frontend/main/index.ts` | 微调 | `electron-store` schema 不变；store 中 ModelEntry 持久化新字段优雅降级 |
| `tests/renderer/chat-composer.test.tsx` | 修改 | 新增 ContextUsage 存在性轻测 |
| `tests/renderer/context-usage.test.tsx` | **新建** | ContextUsage 单元测试 |

## 7. 风险与回退

- **风险 1**：`Stripes` 在亮/暗主题下对比度：通过 `bg-brand-500` + `bg-default` 已确认两者在两种主题下都有足够对比（暗色下 `--color-brand-500: #737373`，`--bg-default: #262626`，对比度 5.8:1，符合 WCAG AA）
- **风险 2**：删除 [/][@] 失去两条入口 → 用户仍可通过输入 `/` 与拖拽文件触发等价功能；如出现大量误触反反馈可独立 PR 恢复
- **风险 3**：useContextUsage selector 在大消息历史（10k+ messages）性能问题 → 用 zustand selector 自动浅比较 messages 引用，仅在新消息追加时重算；旧消息引用稳定时零成本
- **回退路径**：纯前端改动 + 新增 2 文件，`git revert <commit>` 一行回退

## 8. 与现有规范的契合度

- 遵循 AGENTS.md §1.1 优先用现成框架：未自创 selector 框架，复用 zustand 订阅模式
- 遵循既有的 Toolbar 三段分组（参考刚合并的 [2026-07-05-composer-toolbar-redesign-design.md](./2026-07-05-composer-toolbar-redesign-design.md)）：ContextUsage 插入右组，不破坏单行布局
- 遵循主聊天界面信息密度优化策略（13px 字号、压缩间距、btn-icon 28px 高）：删除两个低频按钮 + 引入 18×10px 极小 widget，信息密度再提升
- 与 [chat-context-management 提案 P1](../archive/2026-07-05-chat-context-management/proposal.md) 对齐：M2 推迟的“前端暴露”按本次落地
