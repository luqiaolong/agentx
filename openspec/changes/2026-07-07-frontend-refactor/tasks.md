# 任务追踪 — 2026-07-07-frontend-refactor

## 预期修改文件

### Phase 1: 修复 6 个 P0 Bug
- [ ] `frontend/renderer/components/workspace/GitPanel.tsx`
- [ ] `frontend/renderer/components/settings/SubagentsSettings.tsx`
- [ ] `frontend/renderer/components/workspace/WorkspacePanel.tsx`
- [ ] `frontend/renderer/components/chat/CodeBlock.tsx`
- [ ] `frontend/renderer/components/StatusIndicator.tsx`
- [ ] `frontend/renderer/hooks/useChatStream.ts`

### Phase 2: 抽取公共工具层
- [ ] `frontend/renderer/lib/format.ts` (新建)
- [ ] `frontend/renderer/lib/validators.ts` (新建)
- [ ] `frontend/renderer/lib/logger.ts` (新建)
- [ ] `frontend/renderer/lib/errors.ts` (新建)
- [ ] `frontend/renderer/lib/subagentConstants.ts` (新建)
- [ ] `frontend/renderer/components/ui/ErrorBanner.tsx` (新建)
- [ ] `frontend/renderer/components/ui/ConfirmButton.tsx` (新建)
- [ ] 9 个文件重构用 formatTime/formatSize
- [ ] 6 个文件重构用 KEY_RE/NAME_RE
- [ ] 10+ 个组件用 ErrorBanner
- [ ] 9 个组件用 ConfirmButton

### Phase 3: 抽取 Popover + Modal
- [ ] `frontend/renderer/components/ui/hooks/usePopover.ts` (新建)
- [ ] `frontend/renderer/components/ui/hooks/useModalDialog.ts` (新建)
- [ ] `frontend/renderer/components/ui/Popover.tsx` (新建)
- [ ] `frontend/renderer/components/ui/Modal.tsx` (新建)
- [ ] 4 个 Toggle 重构
- [ ] 3 个 Modal 重构

### Phase 4: 抽取 lib/api/request.ts
- [ ] `frontend/renderer/lib/api/request.ts` (新建)
- [ ] 6 个 api 文件重构
- [ ] 12 处吞错修复

### Phase 5: 拆分超大文件
- [ ] `ModelProviderSettings.tsx` → 4 文件
- [ ] `SubagentsSettings.tsx` → 3 文件
- [ ] `McpSettings.tsx` → 4 文件
- [ ] `WorkspacePanel.tsx` → 4 文件
- [ ] 8 个列表项加 React.memo

### Phase 6: 统一 memory CRUD + RHF
- [ ] `frontend/renderer/hooks/useCrudList.ts` (新建)
- [ ] `frontend/renderer/hooks/useConfigSave.ts` (新建)
- [ ] `frontend/renderer/components/settings/memory/MemoryList.tsx` (新建)
- [ ] 5 个 memory 子组件重构为薄包装
- [ ] 8 个 lib/schemas/*.ts (新建)
- [ ] 8 个表单组件迁移到 RHF + zod

### Phase 7: 拆 chat.ts store
- [ ] `frontend/renderer/stores/chat/migrations.ts` (新建)
- [ ] `frontend/renderer/stores/chat/quotaStorage.ts` (新建)
- [ ] `frontend/renderer/stores/chat/messageOps.ts` (新建)
- [ ] `frontend/renderer/stores/chat/index.ts` (新建)
- [ ] 删除 `frontend/renderer/stores/chat.ts`

## OpenSpec Tasks
| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| P1-1 | 修复 GitPanel 空按钮 | GitPanel.tsx | hover 显示 stage/unstage/discard 按钮 | ⬜ |
| P1-2 | 修复 SubagentsSettings 类型断言 | SubagentsSettings.tsx | allToolsOff 校验生效 | ⬜ |
| P1-3 | 修复 WorkspacePanel 无 onClick | WorkspacePanel.tsx | 文件按钮可点击 | ⬜ |
| P1-4 | 修复 CodeBlock 高亮不更新 | CodeBlock.tsx | 展开后高亮重新生成 | ⬜ |
| P1-5 | 修复 StatusIndicator 单次检查 | StatusIndicator.tsx | 60s 定时轮询 | ⬜ |
| P1-6 | 修复 useChatStream 闭包 | useChatStream.ts | done 不误标会话 | ⬜ |
| P2 | 抽公共工具层 | lib/* + 9+ 文件 | 重复代码消除 | ⬜ |
| P3 | 抽 Popover + Modal | components/ui/* + 7 文件 | 重复 useEffect 消除 | ⬜ |
| P4 | 抽 request.ts + 修吞错 | lib/api/* + 12 处 | HTTP 错误可观测 | ⬜ |
| P5 | 拆超大文件 + memo | 5 文件拆分 + 8 memo | 单文件 < 400 行 | ⬜ |
| P6 | 统一 memory CRUD + RHF | hooks/* + memory/* + schemas/* | 5 子组件 < 30 行 | ⬜ |
| P7 | 拆 chat.ts + 修闭包 | stores/chat/* | store API 不变 | ⬜ |

## 规模判定
- 涉及文件数: 30+ → 规模: **L**
- 涉及模块数: 4 (stores/hooks/lib/api/components)
- 流程: 全流程（Worktree + TDD + 双轨 Review + 部署验证 + 归档）
- 适配说明: 前端 TypeScript 项目，"部署验证" = typecheck + vitest；GitNexus 影响分析跳过
