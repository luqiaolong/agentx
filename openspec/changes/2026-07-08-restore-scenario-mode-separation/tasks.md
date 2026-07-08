# Tasks: 恢复场景/模式双层 UI 交互

## 1. 前端 store 层

- [x] 1.1 新增 `frontend/renderer/stores/scene.ts`
  - 定义 `Scene = "work" | "coding"` 类型
  - 实现 `useSceneStore` (zustand + persist + devtools)
  - 持久化 key: `agentx-scene`
  - 提供 `scene` / `setScene` 接口
  - **关键约束**：`scene` 实际由 `useAgentModeStore.mode` 派生；`setScene` 调用时**联动修改** `mode`（切到 work 强制 mode="work"；切到 coding 时若 mode==="work" 升级为 "coding"，其余保持）
  - 提供 `getSceneFromMode(mode)` 派生工具

- [x] 1.2 新增 `tests/renderer/stores/scene.test.ts`
  - 验证 scene 从 mode 派生的正确性
  - 验证 setScene 联动修改 mode 的规则

## 2. App.tsx 顶部场景 tab

- [x] 2.1 修改 `frontend/renderer/App.tsx`
  - import `useAgentModeStore` + `useSceneStore`
  - 在 `<header>` 内、`v0.1` badge 之后，插入场景 tablist
  - tablist 复用重构前样式：`app-no-drag ml-1.5 inline-flex items-center rounded-md border border-default bg-surface`
  - 两个 button（Work / Coding），`bg-brand-700 text-brand-200` 高亮当前 scene
  - `onClick` 调用 `setScene(s)`
  - 添加 `aria-label="场景切换"` + `role="tablist"`

## 3. ModeToggle 改造

- [x] 3.1 修改 `frontend/renderer/components/chat/ModeToggle.tsx`
  - import `useSceneStore`
  - 移除 `ALL_GROUPS` 数组（含「Work 场景」「Coding 场景」分组标题）
  - 改为单一 `OPTIONS_BY_SCENE: Record<Scene, Option[]>` 表
  - 组件内根据 `scene` 取出当前场景的选项
  - trigger button 仍展示当前 mode 的 icon + 短名 + chevron（行为不变）
  - popover 仅渲染当前场景下的 agent 类型 option（无分组标题、无分隔线）
  - 保留 `coding_team_enabled=false` 时隐藏 Team 选项的逻辑

- [x] 3.2 更新 `tests/renderer/mode-toggle.test.tsx`
  - 新增 scene=work / scene=coding 下的选项数量断言
  - 验证 Coding Team 在 coding_team_enabled=false 时不出现

## 4. 验证后端契约兼容

- [x] 4.1 验证 `frontend/shared/api-types.ts::AgentMode` 未变更
- [x] 4.2 验证 `frontend/renderer/components/chat/ChatView.tsx` 透传 `agentMode` 给 `chat.send()` 的逻辑不变
- [x] 4.3 验证 `backend/app/api/schemas.py` 的 `agent_mode` 枚举不变
- [x] 4.4 验证后端 `backend/app/router/graph.py` 三路分发不变

## 5. 文档更新

- [x] 5.1 更新 `AGENTS.md`
  - §9.5 前端界面概念定义：补充「场景 tab 位于左上角 Bot icon 旁」「agent 类型选择器位于输入框旁」
  - §12 Router 场景+模式分发：补充「场景（UI 维度，顶部 tab）与模式（UI 维度，ModeToggle）的派生关系」
- [x] 5.2 更新 `claude.md`
  - §13 SSE 事件契约：保留
  - §9 项目一句话定位：保留

## 6. 测试与验证

- [x] 6.1 运行 `tests/renderer/stores/scene.test.ts`
- [x] 6.2 运行 `tests/renderer/mode-toggle.test.tsx`
- [x] 6.3 运行 `pnpm test` (vitest) 全套前端单测
- [x] 6.4 运行 `pnpm typecheck` 验证 TS 类型
- [x] 6.5 手动验证：开发模式下进入应用，确认左上角出现 `[Work | Coding]` 场景 tab，确认 ModeToggle popover 不再含「场景」分组标题