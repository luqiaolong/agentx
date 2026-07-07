# 任务追踪 — context-panel-fixes

## 预期修改文件

- [ ] `frontend/renderer/components/workspace/extractFiles.ts`（修复去重 + 工作区过滤 + 删 tool-result 提取 + 删 extractWorkspaceFiles）
- [ ] `frontend/renderer/components/workspace/ContextTabPanel.tsx`（接通 4 Tab + 删冗余 useMemo）
- [ ] `frontend/renderer/hooks/useContextFiles.ts`（新增，数据获取层）
- [ ] `frontend/renderer/App.tsx`（传 onFileClick）

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | 修复 extractFiles 去重键污染 | `extractFiles.ts` | seen 拆为 seenPaths + seenNames；read_file 与 tool-result 不互相阻塞 | ⬜ |
| T2 | extractFiles 增加工作区过滤 | `extractFiles.ts`, `ContextTabPanel.tsx` | workspacePath 外的文件不进入列表 | ⬜ |
| T3 | 删除 tool-result 文件提取（保守方案） | `extractFiles.ts` | 移除正则匹配逻辑；仅依赖 tool-call args 的 path/directory | ⬜ |
| T4 | 删除 extractWorkspaceFiles（硬编码假数据） | `extractFiles.ts`, `ContextTabPanel.tsx` | 函数删除；技能 Tab 改用 useSkillsStore | ⬜ |
| T5 | 抽取 useContextFiles hook | `hooks/useContextFiles.ts`（新增）, `ContextTabPanel.tsx` | 4 类数据统一管理：tool（本地解析）/ skill（useSkillsStore）/ summary（useTasksStore）/ memory（HTTP） | ⬜ |
| T6 | 接通技能 Tab | `useContextFiles.ts`, `ContextTabPanel.tsx` | 用 useSkillsStore；SkillSummary 映射到 CategorizedFile（name=技能名，path=reconstruct，meta=trigger） | ⬜ |
| T7 | 接通记忆 Tab | `useContextFiles.ts`, `ContextTabPanel.tsx` | 调用 memory.getProfile()；ProfileEntry 映射到 CategorizedFile（name=key，meta=category） | ⬜ |
| T8 | 接通摘要 Tab | `useContextFiles.ts`, `ContextTabPanel.tsx` | 从 useTasksStore.tasks 聚合 todos；展示标题 + 完成度；映射到 CategorizedFile（name=任务标题，meta=完成数/总数） | ⬜ |
| T9 | 修复 onFileClick 链路 | `App.tsx`, `WorkspacePanel.tsx` | App 传入 onFileClick，调用 lib/api/shell.ts 的 revealInFolder(path) | ⬜ |
| T10 | 删除 ContextTabPanel 冗余 useMemo | `ContextTabPanel.tsx` | allFiles 直接三元表达式访问 | ⬜ |

## 规模判定

- 涉及文件数: 4（含 1 新增）→ 规模: M
- 涉及模块数: 1（前端 renderer/workspace）
- 流程: M 级简化（无 Worktree、单轨 Review、跳过部署）

## 关键依赖（已验证）

- `useSkillsStore`（stores/skills.ts）：已存在 fetchSkills() + skills: SkillSummary[]
- `memory.getProfile()`（lib/api/http.ts）：返回 `{ entries: ProfileEntry[] }`
- `useTasksStore`（stores/tasks.ts）：tasks[].todos 已存 todo_update/plan 数据
- `lib/api/shell.ts::revealInFolder(path)`：已封装 Tauri command

## 类型适配

- SkillSummary → CategorizedFile: `{ id: skill-<name>, name, path: <workspacePath>/.qoder/skills/<name>.md, category: skill_files, ts: Date.now(), meta: trigger }`
- ProfileEntry → CategorizedFile: `{ id: profile-<key>, name: key, path: "", category: memory_files, ts: Date.parse(updated_at), meta: category }`
- Task.todos → CategorizedFile: `{ id: task-<id>, name: title, path: "", category: session_summary, ts: createdAt, meta: <done>/<total> }`

## 验证

- `npm run typecheck` 通过
- `npm test` 通过
- 手动验证：4 Tab 有数据；点击文件触发 revealInFolder
