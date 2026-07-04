## Why

M1 后端闭环已通（路径 A/B/C + 子代理 + 沙箱授权 + 危险操作审批 + SSE 流式 + 138 单测通过），但设计文档 §13.1 的 M1 必做项仍有 6 项前端体验未落地，§13.2 验收标准中"读 README.md""分析 d:/docs PDF"等场景因缺少 Workspace 文件树、Markdown 渲染、文件拖拽而体验残缺。同时 §10 错误处理与韧性、§11 测试策略中的 E2E/日志项也尚未开始。

本次 change 把 M1 收尾（P0，6 项）与体验加固（P1，4 项）一次性收敛为 7 个 capability，使应用达到"可用 + 稳定"的发布前状态。

## What Changes

### P0 — M1 收尾

- **NEW** `skill-system`：后端 `skills_loader` 已能加载 `data/skills/*.md`，本次新增 `GET /api/skills` 端点 + preload `skills.list` + 左侧栏技能展示 + 输入框 `@` 触发技能选择 + Router 将 skill.content 注入 system prompt
- **NEW** `workspace-panel`：右侧 Workspace 从 placeholder 升级为真实文件树（`GET /api/workspace/list`）+ 任务时间线（复用现有 `tasks.ts` store，接入 `todo_update` 事件）+ 在资源管理器中打开
- **NEW** `chat-rendering`：`MessageBubble` 改用 `react-markdown` + `shiki`，代码块高亮 + 复制按钮；输入区支持文件拖拽，文件落 `data/uploads/`，消息附带路径供 agent 引用
- **NEW** `session-management`：`chat.ts` store 从"单会话消息"改为"会话列表 + 当前会话 ID"，左侧栏渲染会话列表，支持新建/切换/删除，删除时调 `/reset` 清后端 checkpoint

### P1 — 体验加固

- **NEW** `process-resilience`：`spawn.ts` 增加启动握手（轮询 `/api/health` 至 ready）+ 崩溃退避重试（最多 3 次）+ 日志落盘 `data/logs/agent-py-{date}.log` + renderer 可见的 `python:status` 事件 + 前端 Error Boundary + 日志查看器
- **NEW** `settings-completion`：设置页新增 LLM provider/model 选择 + system prompt 编辑 + `autoApproveAfterSeconds` 滑块（当前 store 已支持但 UI 缺失）+ embedding URL + Milvus host/port 配置
- **NEW** `latency-optimization`：分类器规则覆盖优化（减少 LLM 调用）+ 路径 A "thinking..." 状态 + `_APPROVAL_MAX_WAIT` 可配置 + token 流式平滑

## Capabilities

### New Capabilities
- `skill-system`: 技能系统前端化 —— 加载展示、`@` 触发选择、content 注入 system prompt、`/api/skills` 端点
- `workspace-panel`: 右侧 Workspace 面板 —— 文件树（`/api/workspace/list`）+ 任务时间线（todo_update 接入 tasks store）+ 在资源管理器中打开
- `chat-rendering`: 聊天渲染体验 —— Markdown + 代码高亮 + 复制按钮 + 文件拖拽上传到 `data/uploads/`
- `session-management`: 多会话历史管理 —— 会话列表持久化、新建/切换/删除、删除时清后端 checkpoint
- `process-resilience`: 进程韧性与可观测 —— 启动握手 + 崩溃重试 + 日志落盘 + Error Boundary + 日志查看器
- `settings-completion`: 设置页完善 —— LLM provider/model + system prompt + auto_approve 滑块 + embedding/Milvus 地址
- `latency-optimization`: 路径 A 延迟优化 —— 分类器规则覆盖 + thinking 状态 + approval 超时可配置

### Modified Capabilities
<!-- 本次 change 全部为新增 capability，无已有 spec 修改 -->

## Impact

- **代码影响**：
  - 后端：`backend/app/main.py` 新增 `/api/skills` `/api/skills/reload` `/api/workspace/list` 端点；`backend/app/router/graph.py` 支持 `@skill` 解析与 system prompt 注入；`backend/app/config.py` 新增 `approval_max_wait` `default_system_prompt` `max_upload_bytes` `think_filter_max_hold` 配置项
  - 前端：`frontend/preload/index.ts` 暴露 `skills.list` `workspace.list` `python.status` `logs.read`；`frontend/renderer/stores/chat.ts` 重构为多会话结构；`frontend/renderer/components/chat/ChatView.tsx` 接入 Markdown + 拖拽；新增 `workspace/FileTree.tsx` `workspace/TaskTimeline.tsx` `settings/LLMSettings.tsx` `settings/SystemPromptSettings.tsx` 等
  - `frontend/main/python/spawn.ts` 重写启动协议；`frontend/main/index.ts` 增加 `python:status` IPC + 日志文件管理
- **API 影响**：新增 `GET /api/skills` `POST /api/skills/reload` `GET /api/workspace/list` 三个端点
- **依赖影响**：`react-markdown` `shiki` 已在 `package.json`，无新增依赖
- **运维影响**：`data/logs/` 目录自动创建，日志按日滚动（默认保留 7 天）
- **文档影响**：设计文档 §13.1 M1 必做项全部勾选；§10 错误处理表补充"Python 进程未 ready 时前端显示启动中"
- **测试影响**：新增 `test_skills_api.py` `test_workspace_api.py` 单测；`test_smoke.py` 增加技能触发与 Workspace 列表断言
