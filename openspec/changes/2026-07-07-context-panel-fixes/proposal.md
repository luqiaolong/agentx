# Proposal: 工作区上下文面板修复

## 背景

`ContextTabPanel`（工作区 → 任务 Tab 下半屏）当前为半成品：

- **2/4 Tab 是死按钮**：`session_summary` 与 `memory_files` 硬编码 `[]`，永远显示"暂无记录"。后端 `/api/memory/profile` `/api/memory/skills` 已存在，前端 `lib/api/http.ts` 也封装好，但面板未接通。
- **`onFileClick` 链路断裂**：[App.tsx:225](file:///d:/java/agentprojects/agentx/frontend/renderer/App.tsx#L225) 渲染 `<WorkspacePanel />` 时未传 `onFileClick`，文件点击无任何反馈。
- **技能数据是硬编码假数据**：[extractFiles.ts:107-110](file:///d:/java/agentprojects/agentx/frontend/renderer/components/workspace/extractFiles.ts#L107-L110) 写死 `AGENTS.md` + `.qoder/skills` 两条假记录。
- **文件去重键污染**：`seen` Set 混用 `path`（完整路径）和 `match[1]`（裸文件名），导致 read_file 与 tool-result 互相阻塞。
- **tool-result 文件提取正则脆弱**：[extractFiles.ts:75](file:///d:/java/agentprojects/agentx/frontend/renderer/components/workspace/extractFiles.ts#L75) 不能匹配引号/逗号包围路径，Windows 路径丢盘符，且任何 `.py` 字样都会被误识别。
- **不区分工作区内外**：`/tmp/foo.py` 也会塞进工作区上下文。

## 目标

把上下文面板从"半成品"升级为"可用"：4 个 Tab 都接通真实数据；文件点击有反馈；数据提取准确、不污染。

## 范围

仅限 `frontend/renderer/` 目录，不涉及 `backend/` 与 `src-tauri/`。

## 非目标

- 不做可见性提升（保持 ContextTabPanel 嵌套在 tasks Tab 内）
- 不做虚拟滚动 / a11y / 与 ContextUsage 联动（后续迭代）
- 不做技能/摘要 Tab 的 CRUD UI（只展示列表）

## 方案

### 数据获取层

抽 `hooks/useContextFiles.ts`，集中管理 4 类文件的获取：

| Tab | 数据源 | Hook |
|---|---|---|
| tool_files | `extractCategorizedFiles(messages)`（本地解析） | `useToolFiles(messages, workspacePath)` |
| skill_files | `memory.listSkills()` 或 `skills.list()`（HTTP） | `useSkillFiles()` |
| session_summary | `useChatStore` 的 todo_update / plan 事件聚合（本地） | `useSessionSummary(messages)` |
| memory_files | `memory.getProfile()`（HTTP） | `useMemoryFiles()` |

### 文件提取修复

- `seen` 拆为 `seenPaths` + `seenNames`，按语义去重
- `extractCategorizedFiles` 增加 `workspacePath` 参数，过滤工作区外文件
- tool-result 文件提取：仅匹配行首 + 缩进格式，并要求包含路径分隔符或扩展名前的目录段，避免误匹配随机 `.py` 字样

### onFileClick 链路

- `App.tsx` 传入 `onFileClick` 回调：调用 Tauri `shell.open(path)` 打开文件所在目录（最小可用）
- 不做"发送到对话作为 @ 引用"（后续迭代）

## 验收

- 4 个 Tab 都能展示真实数据，无"暂无记录"死按钮
- 点击文件能触发回调（打开文件管理器）
- 长会话中 tool-result 不会污染 read_file 列表
- 工作区外文件不进入列表
- `npm run typecheck` 通过
- `npm test` 通过
