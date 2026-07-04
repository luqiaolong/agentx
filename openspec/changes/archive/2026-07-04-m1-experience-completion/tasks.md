# 任务追踪 — m1-experience-completion

> **注**：本 change 聚焦 M1 体验收尾（P0）+ 体验加固（P1），共 7 个 capability。
> 每阶段独立 commit，验收以单测 + typecheck + smoke 测试为准。

## 预期修改/新增文件

### 后端 (backend/)

#### skill-system
- [ ] `backend/app/main.py`（新增 `GET /api/skills` `POST /api/skills/reload` 端点）
- [ ] `backend/app/router/graph.py`（`run_router` 入口解析 `@skill:<name>` 标记，注入 system prompt）
- [ ] `tests/python/unit/test_skills_api.py`（mock skills_loader，验证端点返回 + `@skill` 注入）

#### workspace-panel
- [ ] `backend/app/main.py`（新增 `GET /api/workspace/list` 端点，复用沙箱校验）
- [ ] `backend/app/tools/filesystem.py`（新增 `list_workspace(path)` 返回 type/size/mtime）
- [ ] `tests/python/unit/test_workspace_api.py`（mock filesystem，验证端点 + 沙箱拒绝）

#### settings-completion + latency-optimization
- [ ] `backend/app/config.py`（新增 `approval_max_wait: float = 300.0` `default_system_prompt: str` `max_upload_bytes: int` `think_filter_max_hold: int = 6` 配置项）
- [ ] `backend/app/paths/deep_path.py`（`_APPROVAL_MAX_WAIT` 改为从 settings 读取；`_MAX_HOLD` 可配置）
- [ ] `backend/app/router/classifier.py`（扩展规则关键词：翻译/解释/计算 → CHAT；打开/查看 → SINGLE_TOOL）
- [ ] `tests/python/unit/test_classifier.py`（新增规则覆盖用例）

### 前端 (frontend/)

#### chat-rendering
- [ ] `frontend/renderer/components/chat/ChatView.tsx`（`MessageBubble` 接入 `react-markdown` + `shiki`；输入区 `onDrop` 拖拽处理）
- [ ] `frontend/renderer/components/chat/CodeBlock.tsx`（新建，shiki 高亮 + 复制按钮）
- [ ] `frontend/preload/index.ts`（暴露 `dialog:saveDroppedFile` IPC 桥）
- [ ] `frontend/main/index.ts`（注册 `dialog:saveDroppedFile` handler，`fs.copyFile` 到 `data/uploads/`）
- [ ] `frontend/renderer/stores/settings.ts`（新增 `maxUploadBytes` 字段）

#### session-management
- [ ] `frontend/renderer/stores/chat.ts`（重构为 `sessions: Record<id, Session>` + `currentId` + 迁移逻辑）
- [ ] `frontend/renderer/components/chat/SessionList.tsx`（新建，左侧栏会话列表 + 新建/删除）
- [ ] `frontend/renderer/App.tsx`（左侧栏替换为 `SessionList`）
- [ ] `tests/renderer/smoke.test.tsx`（新增多会话切换用例）

#### workspace-panel
- [ ] `frontend/renderer/components/workspace/FileTree.tsx`（新建，递归文件树 + 刷新）
- [ ] `frontend/renderer/components/workspace/TaskTimeline.tsx`（新建，订阅 todo_update 更新 tasks store）
- [ ] `frontend/renderer/components/workspace/WorkspacePanel.tsx`（新建，Tab 容器）
- [ ] `frontend/renderer/App.tsx`（右侧 placeholder 替换为 `WorkspacePanel`）
- [ ] `frontend/preload/index.ts`（暴露 `workspace.list` `shell.revealInFolder`）
- [ ] `frontend/main/index.ts`（注册 `shell:revealInFolder` handler）

#### skill-system
- [ ] `frontend/renderer/components/chat/SkillPicker.tsx`（新建，`@` 触发技能选择浮层）
- [ ] `frontend/renderer/components/chat/ChatView.tsx`（输入框 `@` 检测 + `SkillPicker` 挂载）
- [ ] `frontend/preload/index.ts`（暴露 `skills.list` `skills.reload`）
- [ ] `frontend/renderer/stores/skills.ts`（新建，技能列表缓存）

#### process-resilience
- [ ] `frontend/main/python/spawn.ts`（重写：启动握手 + 退避重试 + 日志落盘）
- [ ] `frontend/main/index.ts`（`python:status` IPC 事件 + 日志清理 + `logs:read` handler + `app:restart` handler 调 `app.relaunch`）
- [ ] `frontend/main/logger.ts`（新建，`data/logs/agent-py-{date}.log` 按日滚动）
- [ ] `frontend/preload/index.ts`（暴露 `python.onStatus` `logs.read` `app.restart` IPC 桥）
- [ ] `frontend/renderer/components/ErrorBoundary.tsx`（新建，包裹 Routes）
- [ ] `frontend/renderer/components/settings/LogViewer.tsx`（新建，读取 `data/logs/` 展示）
- [ ] `frontend/renderer/App.tsx`（接入 ErrorBoundary + 启动中遮罩）

#### settings-completion
- [ ] `frontend/renderer/components/settings/LLMSettings.tsx`（新建，provider/model/base_url）
- [ ] `frontend/renderer/components/settings/SystemPromptSettings.tsx`（新建，textarea 编辑）
- [ ] `frontend/renderer/components/settings/ApprovalSettings.tsx`（新建，auto_approve 滑块 + max_upload + approval_max_wait）
- [ ] `frontend/renderer/components/settings/MilvusCredentialsForm.tsx`（扩展 host/port/db/collection 字段）
- [ ] `frontend/renderer/components/settings/ApiKeySettings.tsx`（扩展 provider 列表 + minimax）
- [ ] `frontend/main/store.ts`（新增 `getLLMConfig` `setLLMConfig` `getSystemPrompt` `setSystemPrompt` `getApprovalConfig` `setApprovalConfig` `getKnowledgeConfig` `setKnowledgeConfig`）
- [ ] `frontend/main/python/spawn.ts`（`PythonCredentials` 扩展 `defaultModel` `openaiBaseUrl` `systemPrompt` `approvalMaxWait` `maxUploadBytes` `thinkFilterMaxHold` `embeddingUrl` `milvusHost` `milvusPort` `milvusDb`，spawn 时注入对应 `AGENT_PY_*` env）

#### latency-optimization
- [ ] `frontend/renderer/components/chat/ChatView.tsx`（thinking 状态占位）
- [ ] `backend/app/utils/text.py`（`ThinkFilter._MAX_HOLD` 改为构造参数）

## OpenSpec Tasks 映射

| ID | Capability | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|-----------|---------|---------|---------|------|
| T1 | chat-rendering | Markdown 渲染 + 代码高亮 | ChatView.tsx, CodeBlock.tsx | assistant 消息支持 MD + 代码块高亮 + 复制 | ⬜ |
| T2 | chat-rendering | 文件拖拽上传 | ChatView.tsx, preload, main/index.ts | 拖拽文件到输入区 → 落 data/uploads/ → 消息附带路径 | ⬜ |
| T3 | session-management | store 重构 + 迁移 | stores/chat.ts, SessionList.tsx, App.tsx | 多会话新建/切换/删除；旧 localStorage 自动迁移 | ⬜ |
| T4 | workspace-panel | 文件树 + 任务时间线 | FileTree.tsx, TaskTimeline.tsx, WorkspacePanel.tsx | 右侧 Tab 显示文件树 + 任务卡片 | ⬜ |
| T5 | skill-system | API + UI + 注入 | main.py, graph.py, SkillPicker.tsx, preload | `GET /api/skills` 返回列表；`@` 弹浮层；skill content 注入 system prompt | ⬜ |
| T6 | process-resilience | spawn 重写 + 日志 + 边界 | spawn.ts, logger.ts, ErrorBoundary.tsx, LogViewer.tsx | 启动握手 ready 检测；崩溃重试 3 次；日志落盘；Error Boundary 兜底 | ⬜ |
| T7 | settings-completion | 设置页 4 组 | LLMSettings.tsx, SystemPromptSettings.tsx, ApprovalSettings.tsx, store.ts | LLM provider/model/system prompt/auto_approve 可配置并持久化 | ⬜ |
| T8 | latency-optimization | 分类器 + thinking + 超时可配置 | classifier.py, ChatView.tsx, deep_path.py, config.py | 规则覆盖提升；thinking 状态显示；approval 超时可配置 | ⬜ |

## 规模判定
- 涉及文件数: 30+ → 规模: **L**
- 涉及模块数: backend(main/router/paths/config/classifier) + frontend(main/preload/renderer/stores/components) + tests → 跨模块

## 验证说明
- 每阶段独立 commit 后跑：`cd backend && uv run pytest tests/python/unit` + `npm run typecheck` + `npm test`
- 全部完成后跑 `tests/python/integration/test_smoke.py`（需 myserver 可用）验证端到端
- 验收标准：设计文档 §13.2 M1 验收 4 项全部可手动复现
