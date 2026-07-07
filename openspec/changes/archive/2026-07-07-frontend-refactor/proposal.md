# Proposal: 前端代码全面重构

## 背景

前端 renderer 层经过多次迭代（Router、Subagents、Team、Memory、Tauri 迁移等）已积累大量技术债。全面 review 发现：

- **6 个潜在 bug**：GitPanel 操作按钮缺失、SubagentsSettings 类型断言导致校验失效、WorkspacePanel 文件按钮无 onClick、CodeBlock 高亮不更新、StatusIndicator 健康检查只跑一次、useChatStream 闭包陷阱
- **12+ 处重复代码模式**：formatTime/formatSize 在 9 个文件重复、NAME_RE 在 6 个文件重复、Modal 逻辑在 2 个文件重复 ~100 行、确认删除按钮在 9 个组件重复、错误提示 UI 在 10+ 个组件重复
- **5 个超大文件**：ModelProviderSettings.tsx (1208 行)、SubagentsSettings.tsx (999 行)、stores/chat.ts (936 行)、McpSettings.tsx (767 行)、WorkspacePanel.tsx (474 行)
- **12 处静默吞错**：`catch {}` 完全吞掉错误，违反 P3 可观测原则
- **8 个列表项未 memo**：父组件 state 变化触发全量重渲染
- **RHF + zod 引入未充分使用**：仅 MilvusCredentialsForm 使用且不彻底，其余 8 个表单组件手写 useState

## 目标

通过分阶段、可独立验证的重构，消除上述技术债，使前端代码达到 AGENTS.md P2（单一职责）/ P3（可观测）/ P4（类型安全）/ P6（可测试）标准。

## 范围

本次变更仅限 `frontend/renderer/` 与 `frontend/shared/` 目录，不涉及 `backend/` 与 `src-tauri/`。

包含 7 个阶段：
1. 修复 6 个 P0 bug
2. 抽取公共工具层（format/validators/ErrorBanner/ConfirmButton）
3. 抽取 Popover + Modal 基础组件
4. 抽取 `lib/api/request.ts` 统一 HTTP 边界并补错误处理
5. 拆分 5 个超大文件 + 加 React.memo
6. 统一 settings memory 子组件 CRUD + 表单 RHF 迁移
7. 拆 chat.ts store + 修 useChatStream 闭包

## 预期收益

- 修复 6 个功能 bug，消除用户可见缺陷
- 减少 ~1500 行重复代码（5 个 memory 子组件从 ~300 行/个 → 1 个共享组件 + 5 个薄包装）
- 减少 ~300 行 Modal/Popover 重复
- 消除 12 处静默吞错，关键路径错误可观测
- 8 个列表项加 React.memo，减少不必要重渲染
- 单文件最大行数从 1208 → < 400
- 表单校验统一为 RHF + zod，schema 集中管理

## 风险

| 风险 | 缓解 |
|---|---|
| 重构范围大，可能引入回归 | 分 7 阶段推进，每阶段独立 commit + 跑测试 + typecheck |
| 拆分超大文件可能破坏既有导入路径 | 使用 `@/` 路径别名，重构后跑全量 typecheck |
| memory 子组件合并可能丢失差异 | 用 `useCrudList(category)` hook + 配置对象区分 CONTENT_MAX/Icon/labels |
| RHF 迁移可能改变表单 UX | 保留原校验时机（onBlur / onSubmit），逐个组件迁移并对比行为 |
| 项目约束：禁止兼容老代码 | 直接删除旧代码，不保留 shim/deprecated |

## 非目标

- 不重构 backend Python 代码
- 不重构 src-tauri Rust 代码
- 不引入新依赖（react-hook-form / zod / @assistant-ui/* 已存在）
- 不改 SSE 事件契约
- 不改 API 路径
- 不优化打包体积（独立课题）
- 不引入虚拟列表（独立课题，仅 FileTree 注释 TODO）
