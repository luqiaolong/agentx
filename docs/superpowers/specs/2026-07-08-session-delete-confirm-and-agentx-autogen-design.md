# 会话删除应用内确认弹窗 + 工作区首次对话后异步生成 .agentx

> 日期：2026-07-08
> 主题：替换 SessionList 删除会话的 `window.confirm`、将 `.agentx/` 的初始化从“选 workspace 即生成”迁移到“首次有效对话完成后再生成”，并在执行时主动检查 `.agentx/` 是否已存在以避免空跑
> 状态：已确认设计，待实现

## 1. 产品结论

**问题现状**

1. **删除会话的 UX 与代码风格不一致**：`SessionList.tsx` 使用 `window.confirm(...)`（浏览器原生同步对话框），与项目内统一的 `useModalDialog` + 应用内 Modal 风格（参见 `SubagentEditModal`、`SettingsModal`）脱节，且每次删除都要走一次原生弹窗，视觉割裂。
2. **首次选工作区即强制生成 `.agentx/` 阻塞了用户**：当前 `ChatComposer.tsx::handleAttachWorkspace` 在 OS 目录选择器返回后立即 `await initProjectConfig(...)`，流程长、错误信息占用 `dropError` 通道；若生成失败，用户尚未与 agent 对话就被错误打扰。
3. **生成时机过早违反 LLM 工程最小化**：用户在选完工作区后可能立刻放弃会话、切换会话、清空消息；`.agentx/` 在未实际发生任何有效对话前就被生成，浪费 IO 与后端校验。
4. **幂等防护薄弱**：缺少端到端的 `.agentx/` 存在性校验，仅靠后端 `skipped` 字段会导致多次空跑请求。

**改后效果**

- SessionList 删除会话走应用内 `ConfirmDialog`，ESC/点遮罩/点 X 关闭，确认按钮根据 `variant="danger"` 渲染 rose 色。
- `handleAttachWorkspace` 仅负责授权 + 把当前会话绑到目录，**不再**触发 `initProjectConfig`。
- `.agentx/` 生成统一收敛到「首次有效对话（LLM 首次 `done` 事件）完成后」异步触发，并在执行前主动 `getProjectConfig` 二次校验 `.agentx/` 是否真实存在。
- 会话级别布尔标记 `generatedAgentx` 提供**一次会话最多一次**的快速护栏，避免重复触发。

**核心改动**

- 新增 `frontend/renderer/components/ui/ConfirmDialog.tsx`（应用内通用确认 Modal）
- `SessionList.tsx` 把 `handleDelete` 改为打开 `ConfirmDialog` 的 `open` state；删除仍走 `deleteSession(id)`
- `ChatComposer.tsx` 移除 `await initProjectConfig(dirPath, tid!)` 与相关 `try/catch` + `logger.warn`
- `useChatStream.ts` 在 `case "done"` 分支末尾调用新增的 `ensureAgentxGenerated(tid)` action
- `stores/chat/index.ts` `Session` 类型增加 `generatedAgentx?: boolean`；新增 `ensureAgentxGenerated` action
- 后端：`project_config.py` 与 `getProjectConfig` API **零改动**（接口已完备）

## 2. 用户工作流

```
用户在右侧输入框首次选择 workspace
  → OS 目录选择器 → 沙箱授权 → moveSessionToWorkspace → workspace chip 显示
  → 不再触发生成 .agentx（零阻塞、零延迟）

用户在新工作区发送首条消息（任意 agent 模式）
  → SSE 流开始；用户继续输入或观察进度

首条消息的 LLM 生成完成（SSE done 事件）
  → 后台并发触发 ensureAgentxGenerated(tid)
    ├─ 同会话 generatedAgentx 已为 true → 立即 return
    └─ 未生成过：
       ├─ getProjectConfig 检查 exists？已存在 → 标记 generatedAgentx=true，结束
       └─ 否则 fire-and-forget 调 initProjectConfig
          ├─ 成功 → generatedAgentx=true
          └─ 失败 → logger.warn，保持 generatedAgentx=false（下次 done 事件可重试）

用户在同一 workspace 多轮对话
  → 仅首条 done 事件触发一次；后续 done 事件全部短路

用户在输入框切到 Home
  → workspacePath = null → 永远不会触发（isWorkspace 守卫）
```

## 3. 数据模型与状态

### 3.1 Session 新增字段

```ts
interface Session {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  workspacePath: string | null;
  manuallyRevokedPaths: string[];
  isRunning: boolean;
  hasNewResult: boolean;
  permissionMode: PermissionMode;
  /** 是否已为本会话的工作区生成过 .agentx/。仅用于"一次会话最多一次"快速护栏；
   *  真实存在性以 getProjectConfig 为准（见 §5.3 双层防护）。
   *  默认 false；旧数据迁移时缺省视为 false。 */
  generatedAgentx?: boolean;
}
```

**持久化兼容性**：zustand `persist` 中间件已开启（见 `stores/chat/index.ts`）。新增可选字段对老数据完全向后兼容——`generatedAgentx === undefined` 视同 `false`。

### 3.2 新增 Store Action

```ts
ensureAgentxGenerated: (sessionId: string) => Promise<void>;
```

实现要点（伪代码）：

```ts
ensureAgentxGenerated: async (sessionId) => {
  const sess = get().sessions[sessionId];
  if (!sess?.workspacePath || sess.generatedAgentx) return;
  const wsPath = sess.workspacePath;
  // 1) 真实存在性二次校验（避免老 .agentx + 标记丢失导致重复 init）
  try {
    const status = await getProjectConfig(wsPath, sessionId);
    if (status.exists) {
      set((s) => ({
        sessions: { ...s.sessions, [sessionId]: { ...sess, generatedAgentx: true } },
      }));
      return;
    }
  } catch (err) {
    // getProjectConfig 失败不阻塞，可能 init 也失败；
    // 抛给后续 init 步骤统一处理
    logger.warn("getProjectConfig precheck failed", err);
  }
  // 2) 立即抢占标记，杜绝并发 done 事件重复触发
  set((s) => ({
    sessions: { ...s.sessions, [sessionId]: { ...sess, generatedAgentx: true } },
  }));
  try {
    await initProjectConfig(wsPath, sessionId);
  } catch (err) {
    // 失败回滚标记，允许下次 done 重试
    logger.warn("initProjectConfig failed", err);
    set((s) => ({
      sessions: { ...s.sessions, [sessionId]: { ...sess, generatedAgentx: false } },
    }));
  }
},
```

**抢占语义**：先 `set(true)` 再 `await init`——`ensureAgentxGenerated` 多次并发调用时，前者后续全部短路（即"双层防护"中的第一层）。

### 3.3 无新增后端接口

- `POST /api/project-config/init` 已存在，已幂等
- `GET /api/project-config` 已存在，可返回 `exists`

## 4. UI 规范

### 4.1 ConfirmDialog 通用组件（新增）

`frontend/renderer/components/ui/ConfirmDialog.tsx`：

```text
API:
  open: boolean
  title: string
  message: ReactNode
  confirmLabel?: string        // 默认"确认"
  cancelLabel?: string         // 默认"取消"
  variant?: "danger" | "primary" // 默认 "danger"
  onConfirm: () => void
  onClose: () => void

DOM:
  <div fixed inset-0 z-50 flex items-center justify-center bg-black/50
       onClick={onClose} role="presentation">
    <div ref={dialogRef}
         className="w-[400px] rounded-lg border border-default bg-surface shadow-xl"
         onClick={stopPropagation}
         role="dialog" aria-modal="true" aria-label={title}>
      <!-- Header -->
      <div className="flex items-center justify-between border-b border-default px-4 py-3">
        <span className="font-semibold text-primary-c">{title}</span>
        <button ref={closeBtnRef} onClick={onClose} aria-label="关闭">
          <X className="h-4 w-4" />
        </button>
      </div>
      <!-- Body -->
      <div className="px-4 py-3 text-secondary-c">{message}</div>
      <!-- Footer -->
      <div className="flex items-center justify-end gap-2 border-t border-default px-4 py-3">
        <button onClick={onClose} className="btn-secondary">{cancelLabel}</button>
        <button onClick={onConfirm}
                className={variant === "danger" ? "btn-danger" : "btn-primary"}>
          {confirmLabel}
        </button>
      </div>
    </div>
  </div>
```

**a11y**：复用 `useModalDialog({ open, onClose })` 自动获得 ESC 关闭、Tab 焦点陷阱、触发元素焦点恢复；新建组件零自研。

### 4.2 SessionList 集成（修改）

把 `handleDelete` 改为打开 `ConfirmDialog`：

```tsx
const [pendingDelete, setPendingDelete] = useState<{
  id: string; title: string;
} | null>(null);

const handleDelete = (id: string, title: string) => {
  (document.activeElement as HTMLElement | null)?.blur();
  setPendingDelete({ id, title });
};

const confirmDelete = () => {
  if (!pendingDelete) return;
  const { id } = pendingDelete;
  setPendingDelete(null);
  deleteSession(id);
  window.setTimeout(() => {
    const composer = document.querySelector('textarea[aria-label="消息输入框"]') as HTMLTextAreaElement | null;
    composer?.focus();
  }, 50);
};

// 渲染末尾追加：
<ConfirmDialog
  open={pendingDelete !== null}
  title="删除会话"
  message={<>确认删除会话「<b>{pendingDelete?.title}</b>」？删除后无法恢复。</>}
  variant="danger"
  confirmLabel="删除"
  onConfirm={confirmDelete}
  onClose={() => setPendingDelete(null)}
/>
```

**焦点竞争**：保留现有 `(document.activeElement)?.blur()` 与 50ms 后的 `composer.focus()`——`useModalDialog` 关闭后会自己恢复焦点到 trigger，但 trigger 是删除按钮、即将被卸载，blur + 延迟恢复解决冲突。

**首条删会话的 UX 边界**：若 `pendingDelete.id === currentId`，`deleteSession` 内部已自动切到剩余第一会话，UI 不空。

### 4.3 ChatComposer 简化（修改）

```diff
- import { initProjectConfig } from "@/lib/api/projectConfig";
- import { logger } from "@/lib/logger";

  const handleAttachWorkspace = async () => {
    setDropError(null);
    const result = (await openFolder()) as ...;
    if (!result || result.canceled || !result.filePaths || result.filePaths.length === 0) {
      return;
    }
    const dirPath = result.filePaths[0]!;
    const store = useChatStore.getState();
    let tid = store.currentId;
    if (!tid) {
      tid = await store.createSession(dirPath);
    } else {
      await store.moveSessionToWorkspace(tid, dirPath);
    }
    try {
      await useChatStore.getState().authorizeAndUnmark(tid, dirPath, true);
    } catch (err) {
      setDropError(
        `授权目录「${dirPath}」失败：${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }
-   try {
-     await initProjectConfig(dirPath, tid!);
-   } catch (err) {
-     logger.warn("initProjectConfig failed", err);
-   }
  };
```

**移除 `best-effort 生成 .agentx`** 后，`handleAttachWorkspace` 的成功路径不再依赖后端 `.agentx` 状态，错误处理收敛到沙箱授权一类。

### 4.4 useChatStream 触发点（修改）

```diff
  case "done": {
    if (pendingIdRef.current) {
      markReasoningDone(pendingIdRef.current);
    }
    setStreaming(false);
    finishRunning(false);
    pendingIdRef.current = null;
    const tid = currentTaskIdRef.current;
    if (tid) {
      updateTask(tid, { status: "done" });
      currentTaskIdRef.current = null;
    }
    callbacksRef.current.setPaused?.(false);

+   // 首条有效对话完成后再异步收敛 .agentx 生成（fire-and-forget）
+   const activeTid = targetThreadId();
+   if (activeTid) {
+     void useChatStore.getState().ensureAgentxGenerated(activeTid);
+   }
    break;
  }
```

**触发口径**：`done` 事件由后端 LangGraph 在 `agent.invoke` / `astream` 终态时发出；不论 agent 类型（work / coding / coding_team），只要发生了一次有效对话，就视为该工作区被"真正"使用。

### 4.5 项目级配置徽章

`ProjectConfigBadge` 已能查询 `/api/project-config` 渲染"已配置 / 未配置"——本改动**不修改**该组件，但徽章会在以下时机自动刷新：

- 用户切换会话 → `useEffect([workspacePath])` → `getProjectConfig` → 徽章刷新
- 用户点徽章（手动 init）→ 已在 `ProjectConfigBadge.handleClick` 内 `await refresh` → 同上

`ensureAgentxGenerated` 不主动通知徽章刷新——用户首条对话完成后刷新会话时（即 1 秒内视觉感受不到差异），徽章已自动拉新值。

## 5. 行为与兼容

### 5.1 不破坏现有交互

- **删除会话**：API 数据流不变；只换确认 UX
- **选择工作区**：仍走 OS 选择器 + 沙箱授权 + `moveSessionToWorkspace`；只去掉末尾的 init 调用
- **SSE 流**：done 分支不变；只追加一次性 `ensureAgentxGenerated` 调用
- **首条对话感知**：用户在新工作区打开应用后第一次发消息，无论 agent 类型都会被覆盖

### 5.2 状态机 & 双层防护

```
会话 createdAt → session.generatedAgentx = false（缺省）
        │
        ▼  用户首条 LLM 完成 done
ensureAgentxGenerated(tid)
        │
        ├─ workspacePath 为 null           → return
        │
        ├─ generatedAgentx === true        → return              [第一层：会话级标记]
        │
        ├─ getProjectConfig(wsPath, tid)
        │     │
        │     ├─ exists === true           → 标记 generatedAgentx=true，return
        │     │                              [第二层：真实文件存在性]
        │     │
        │     └─ exists === false / 错误    → 继续
        │
        ├─ 立即 set generatedAgentx=true 抢占（防并发）
        │
        └─ fire-and-forget initProjectConfig(wsPath, tid)
              │
              ├─ 成功 → 保持 true
              └─ 失败 → set 回 false → 下次 done 事件允许重试
```

**为什么不只用一层**：
- 仅用标记：用户跨设备恢复会话、清理 localStorage 后，`.agentx/` 在磁盘上已存在但 session 重新创建为 `generatedAgentx=false`，会触发一次多余的 init（后端幂等无副作用，但前端能看到短暂 pending）
- 仅用 `getProjectConfig`：每次 done 都查一次后端，I/O 与延迟
- 双层：快路径走标记、慢路径兜底真实存在性，且 init 失败可重试

### 5.3 错误处理

| 场景 | 行为 |
|---|---|
| `getProjectConfig` 失败（网络 / 4xx） | `logger.warn` 后继续尝试 init；不抛出 |
| `initProjectConfig` 失败 | `logger.warn`；回滚 `generatedAgentx=false`；下次 done 重试 |
| 重复并发 `ensureAgentxGenerated` 调用 | 抢占后短路；不会触发多次 init |
| 用户在新工作区清空所有消息后退出 | `.agentx` 不生成；下次再发消息会触发 |

### 5.4 测试影响

| 文件 | 行为 |
|---|---|
| `tests/renderer/confirm-dialog.test.tsx` | **新增基础组件 smoke**（render / onConfirm / onClose / ESC） |
| `tests/renderer/ensure-agentx.test.ts` | **新增 store action 单元测试**：双层防护（标记短路 + exists 短路）、失败回滚、并发抢占 |
| `tests/renderer/session-list.test.tsx` *(若存在)* | 更新断言：删除按钮 click → 出现 `ConfirmDialog` → 点击确认 → `deleteSession` 被调 |
| `tests/renderer/chat-composer.test.tsx` *(若存在)* | 移除对 `initProjectConfig` mock 的 expect |
| `tests/renderer/use-chat-stream.test.tsx` *(若存在)* | 断言 `done` 事件触发 `ensureAgentxGenerated(activeTid)` |

**说明**：仅 ConfirmDialog 与 store action 测试为新增交付物；其他测试更新只在对应文件已存在时执行，避免 scope 蔓延到无关测试重构。

## 6. 文件改动清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/renderer/components/ui/ConfirmDialog.tsx` | **新增** | 通用应用内确认 Modal；复用 `useModalDialog` |
| `frontend/renderer/stores/chat/index.ts` | 修改 | Session 加 `generatedAgentx?: boolean`；新增 `ensureAgentxGenerated` action；持久化迁移兼容 |
| `frontend/renderer/stores/chat/types.ts`（或同文件内） | 微调 | Session 类型扩展 |
| `frontend/renderer/components/chat/SessionList.tsx` | 修改 | `handleDelete` 改用 `ConfirmDialog`；末尾挂载 `<ConfirmDialog />` |
| `frontend/renderer/components/chat/ChatComposer.tsx` | 修改 | 移除 `initProjectConfig` 调用与 `try/catch`；移除未用的 `initProjectConfig` 与 `logger` import |
| `frontend/renderer/hooks/useChatStream.ts` | 修改 | `case "done"` 末尾追加 `void useChatStore.getState().ensureAgentxGenerated(activeTid)` |
| `tests/renderer/confirm-dialog.test.tsx` | **新增** | ConfirmDialog smoke |
| `tests/renderer/session-list.test.tsx` | 微调 | 删除流程改为两次点击 |
| `tests/renderer/use-chat-stream.test.tsx` | 微调 | 新增 done 触发断言 |
| `tests/renderer/ensure-agentx.test.ts` | **新增** | store action 单元测试 |
| 后端 | **零改动** | project_config.py / api/project_config.py 不变 |

## 7. 风险与回退

**风险 1**：用户选完工作区但**永远不发消息**，`.agentx/` 始终不会生成。

- 缓解：徽章"未配置"持续显示直到首次对话完成，提示明确
- 替代方案：在 handleAttachWorkspace 增加延迟 5 秒的 best-effort 兜底 → 评估为过度复杂，**不做**

**风险 2**：`ensureAgentxGenerated` 在 `getProjectConfig` 阶段失败（沙箱过期）后继续走 init —— 后端 init 也会失败，浪费一次请求。

- 缓解：失败统一 `logger.warn`，下次 done 重试；用户可见的延迟 < 200ms
- 替代方案：在 `getProjectConfig` 失败时直接放弃，不再尝试 init → 评估"用户首次对话失败无重试路径"风险更高，**不采纳**

**风险 3**：旧的 localStorage 中 `sessions` 字段缺 `generatedAgentx`，切到此会话首条对话时会再次尝试生成；若磁盘上 `.agentx` 已存在，`getProjectConfig` 第二层校验会立即短路——无副作用。

**风险 4**：用户在 Home（workspacePath=null）发消息，不会触发；这与产品决策"Home 不视为工作区"一致，与 `ProjectConfigBadge` 的"Home 不展示徽章"语义相同。

**回退路径**：

- 单 commit 可一行 `git revert <commit>` 回退；store action 与 React state 均为纯增量
- 新增组件 ConfirmDialog 删除后无破坏（仅一处使用）
