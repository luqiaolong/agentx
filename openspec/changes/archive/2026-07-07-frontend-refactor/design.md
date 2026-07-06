# Design: 前端代码全面重构

## Context

截至 2026-07-07，前端 renderer 层存在以下问题（详见 review 报告）：

- 6 个潜在 bug（GitPanel 空按钮 / SubagentsSettings 类型断言 / WorkspacePanel 无 onClick / CodeBlock 高亮不更新 / StatusIndicator 单次检查 / useChatStream 闭包）
- 12+ 处重复代码模式（format/formatSize/NAME_RE/ALL_TOOLS/Modal/Popover/ConfirmButton/ErrorBanner/热更新流程/CRUD 模式）
- 5 个超大文件（>470 行）
- 12 处静默吞错
- 8 个列表项未 memo
- RHF + zod 引入但未充分使用

技术栈：React 18 + TypeScript + Vite + Tailwind v4 + Zustand + assistant-ui + react-hook-form + zod。

## Goals / Non-Goals

**Goals:**
- 修复 6 个 P0 bug
- 抽取 6 类公共工具/组件（format/validators/ErrorBanner/ConfirmButton/Popover/Modal）
- 抽取 `lib/api/request.ts` 统一 HTTP 边界
- 拆分 5 个超大文件到 < 400 行
- 8 个列表项加 React.memo
- 5 个 memory 子组件合并为 1 个共享组件 + 5 个薄包装
- 8 个表单组件迁移到 RHF + zod
- 修复 12 处静默吞错为 `logger.warn + setErrMsg`
- 拆 chat.ts (936 行) 为 4 个文件
- 修 useChatStream 闭包陷阱
- 所有变更通过 `npm run typecheck` + `npm run test`

**Non-Goals:**
- 不改 backend / src-tauri
- 不引入新依赖
- 不改 SSE 契约 / API 路径
- 不做虚拟列表
- 不做打包体积优化

## Decisions

### D1. 公共工具层抽取

**选择**：新建以下文件：

```
frontend/renderer/lib/
├── format.ts         # formatTime / formatSize / formatMtime / formatDate
├── validators.ts     # KEY_RE / NAME_RE / validateKey / validateName
├── logger.ts         # warn/error 统一封装（console + 预留 Langfuse 上报）
└── subagentConstants.ts  # ALL_TOOLS / BUILTIN_AGENTS
```

**理由**：纯函数 + 常量，无副作用，最容易抽取且收益最高。

**D1-1. format.ts API**

```ts
export function formatTime(iso: string, mode: "absolute" | "relative" | "hhmm" = "absolute"): string;
export function formatSize(bytes: number): string;  // 统一含 GB 分支
export function formatMtime(ms: number): string;
```

**D1-2. validators.ts API**

```ts
export const KEY_RE = /^[a-zA-Z0-9_-]{1,64}$/;
export const NAME_RE = KEY_RE;
export function validateKey(key: string): string | null;  // 返回错误信息或 null
```

### D2. UI 基础组件抽取

**选择**：新建 `frontend/renderer/components/ui/`：

```
components/ui/
├── Modal.tsx          # 包装 useModalDialog
├── Popover.tsx        # 包装 usePopover
├── ErrorBanner.tsx    # 错误提示横幅
├── ConfirmButton.tsx  # 确认删除按钮
└── hooks/
    ├── useModalDialog.ts
    └── usePopover.ts
```

**D2-1. useModalDialog API**

```ts
function useModalDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  // 内部管理: triggerRef / closeBtnRef / dialogRef
  // 自动处理: ESC keydown / body overflow lock / focus trap / focus restore
  return { triggerRef, closeBtnRef, dialogRef };
}
```

**D2-2. usePopover API**

```ts
function usePopover() {
  // 内部管理: open state / triggerRef / popoverRef
  // 自动处理: ESC keydown / clickOutside
  return { open, setOpen, triggerRef, popoverRef };
}
```

**D2-3. ConfirmButton API**

```ts
<ConfirmButton
  onConfirm={handleDelete}
  label="删除"
  confirmLabel="确认删除"
  variant="danger"
/>
```

**理由**：消除 SettingsModal/LogsModal 重复的 ~100 行 useEffect；让 SubagentEditModal 补齐缺失的 a11y；3 个 Toggle 共享 ESC/clickOutside 逻辑。

### D3. HTTP 边界统一

**选择**：新建 `frontend/renderer/lib/api/request.ts`：

```ts
export class ApiError extends Error {
  constructor(public status: number, public body: string) {
    super(`HTTP ${status}: ${body}`);
  }
}

export async function apiRequest<T>(
  path: string,
  opts: { method?: "GET" | "POST" | "PUT" | "DELETE"; body?: unknown; signal?: AbortSignal } = {}
): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: opts.method ?? "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });
  if (!r.ok) throw new ApiError(r.status, await r.text());
  return r.status === 204 ? (undefined as T) : r.json();
}

export const apiGet = <T>(path: string, signal?: AbortSignal) => apiRequest<T>(path, { signal });
export const apiPost = <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "POST", body });
export const apiPut = <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "PUT", body });
export const apiDelete = <T>(path: string) => apiRequest<T>(path, { method: "DELETE" });
```

**理由**：消除 [http.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/http.ts) 中 15 个方法重复的 fetch + headers + body 模板；补齐 HTTP 错误处理（当前全部缺失）。

### D4. 超大文件拆分策略

**D4-1. ModelProviderSettings.tsx (1208 → 4 文件)**

```
components/settings/model-provider/
├── index.tsx          # 主组件，~200 行
├── ModelRow.tsx       # 列表项，memo
├── ModelEditor.tsx    # 编辑面板
└── TokenField.tsx     # token 输入组件
```

**D4-2. SubagentsSettings.tsx (999 → 3 文件)**

```
components/settings/subagents/
├── index.tsx          # 主组件
├── SubagentCard.tsx   # 合并 BuiltinCard + CustomCard（variant prop）
└── constants.ts       # EMPTY_TEAM_CONFIG + ALL_TOOLS
```

**D4-3. McpSettings.tsx (767 → 4 文件)**

```
components/settings/mcp/
├── index.tsx
├── ServerRow.tsx      # memo
├── ServerEditor.tsx
└── utils.ts           # argsToText / parseArgsText / envToText / parseEnvText
```

**D4-4. stores/chat.ts (936 → 4 文件)**

```
stores/chat/
├── index.ts           # create store + 主要 actions
├── migrations.ts      # migrateV1toV2 / V3toV4 / 通用 migrate
├── quotaStorage.ts    # quota 持久化
└── messageOps.ts      # appendPartText / appendMessageContent 等
```

**D4-5. WorkspacePanel.tsx (474 → 4 文件)**

```
components/workspace/
├── WorkspacePanel.tsx  # 主组件，~150 行
├── CompactTaskList.tsx
├── ContextTabPanel.tsx
└── extractFiles.ts     # extractCategorizedFiles / extractWorkspaceFiles
```

### D5. memory 子组件合并

**选择**：抽取 `hooks/useCrudList.ts` + `components/settings/memory/MemoryList.tsx`：

```ts
function useCrudList<T extends { key: string }>(opts: {
  category: string;
  api: { list: () => Promise<T[]>; save: (item: T) => Promise<void>; delete: (key: string) => Promise<void> };
}) {
  // 共享: items / loaded / errMsg / confirmDelete / refresh / saveDraft / remove
}
```

5 个 memory 子组件变为：

```tsx
export function PreferenceManager() {
  return <MemoryList category="preference" icon={Star} contentMax={500} ... />;
}
```

**理由**：5 个文件 90% 结构相同，仅 category / CONTENT_MAX / Icon / sourceLabel 不同。合并后减少 ~1500 行重复。

### D6. 表单 RHF 迁移

**选择**：8 个表单组件迁移到 `react-hook-form + zodResolver`：

| 组件 | schema 文件 |
|---|---|
| ApprovalSettings | `lib/schemas/approval.ts` |
| McpSettings (ServerEditor) | `lib/schemas/mcp-server.ts` |
| ModelProviderSettings (ModelEditor) | `lib/schemas/model-entry.ts` |
| SubagentEditModal | `lib/schemas/subagent.ts` |
| SubagentsSettings | 复用 subagent.ts |
| SystemPromptSettings | `lib/schemas/system-prompt.ts` |
| ToolsSettings | `lib/schemas/tools.ts` |
| SandboxSettings | `lib/schemas/sandbox.ts` |
| MilvusCredentialsForm | `lib/schemas/milvus.ts`（修复现有不彻底用法） |

### D7. 错误处理统一

**选择**：所有 `catch {}` 改为：

```ts
catch (e) {
  logger.warn("ApprovalSettings.load failed", e);
  setErrMsg(humanizeError(e));
}
```

`humanizeError` 放在 `lib/errors.ts`，区分 ApiError / ZodError / 普通 Error。

### D8. 列表项 memo 化

**选择**：8 个列表项加 `React.memo` + 自定义 `areEqual`：

- ServerRow / ModelRow / SubagentCard / TaskCard / TreeNode / StatusRow / MessageParts / SessionItem

### D9. 6 个 P0 bug 修复方案

| Bug | 修复方案 |
|---|---|
| GitPanel 空按钮 | 在 StatusRow 的 hover div 内渲染 stage/unstage/discard 按钮，调用 props |
| SubagentsSettings 类型断言 | 用 `getToolsConfig()` 返回完整 ToolsConfig，初始化用 `emptyToolsConfig()` 工厂函数 |
| WorkspacePanel 无 onClick | 给文件按钮加 `onClick={() => onFileClick(file)}`，prop 由父组件传入 |
| CodeBlock 高亮不更新 | useEffect deps 改为 `[displayCode, language]` |
| StatusIndicator 单次检查 | 改为定时轮询（60s 间隔，因 `/api/health` 可能 5s+ 耗时），用 setInterval + cleanup |
| useChatStream 闭包 | 用 `useChatStore.getState().currentId` 在 done/error 事件内实时读取（替代闭包变量） |

### D10. 热更新流程抽取

**选择**：新建 `hooks/useConfigSave.ts`：

```ts
function useConfigSave<T>(saveFn: (v: T) => Promise<void>) {
  const [saved, setSaved] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const save = async (v: T) => {
    try {
      await saveFn(v);
      await reloadBackendConfig();
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      logger.warn("useConfigSave failed", e);
      setErrMsg(humanizeError(e));
    }
  };
  return { save, saved, errMsg, clearError: () => setErrMsg(null) };
}
```

7 个 settings 组件复用。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 重构范围大，回归风险高 | 分 7 阶段，每阶段独立 commit + 测试 |
| memory 合并可能丢失细节差异 | useCrudList 配置对象显式声明差异点 |
| RHF 迁移改变 UX | 保留原校验时机，逐个迁移对比 |
| 拆分后导入路径变多 | 用 `@/` 别名，typecheck 守护 |
| 项目约束禁止兼容老代码 | 直接删旧代码，不保留 shim |

## Migration Plan

按 7 阶段推进，每阶段独立可验证：

1. **Phase 1: 修 6 个 P0 bug** — 最小改动，消除功能缺陷
2. **Phase 2: 抽公共工具层** — format/validators/ErrorBanner/ConfirmButton/logger
3. **Phase 3: 抽 Popover + Modal** — usePopover/useModalDialog + UI 组件
4. **Phase 4: 抽 lib/api/request.ts** — 统一 HTTP 边界 + 修吞错
5. **Phase 5: 拆超大文件 + memo** — 5 个文件拆分 + 8 个列表项 memo
6. **Phase 6: 统一 memory CRUD + RHF 迁移** — useCrudList + 8 个表单 RHF
7. **Phase 7: 拆 chat.ts + 修闭包** — store 拆分 + useChatStream 修复

## Open Questions

| # | 问题 | 默认假设 |
|---|---|---|
| Q1 | logger 是否接入 Langfuse？ | M1 仅 console.warn/error，预留接口；M2 接 Langfuse |
| Q2 | Modal 是否替换为 Radix Dialog？ | 否，保持自研以避免新依赖 |
| Q3 | 拆分后旧文件是否保留 re-export？ | 否，直接删除，按项目约束不保留兼容 |
| Q4 | RHF 迁移是否一次性完成？ | 是，Phase 6 一次性迁移 8 个组件 |
| Q5 | chat.ts 拆分是否改 store API？ | 否，对外 API 保持不变，仅内部文件拆分 |
