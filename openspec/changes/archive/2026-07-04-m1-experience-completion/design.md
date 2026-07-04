## Context

当前项目状态（截至 2026-07-04）：
- 后端 M1 闭环已通：Router 三路径 + 子代理 + 沙箱 + 审批 + SSE + 138 单测通过
- 前端骨架已就位：三栏布局、ChatView、ApprovalDialog、ApiKeySettings、MilvusCredentialsForm、SandboxSettings、StatusIndicator
- 缺口集中在"用户可感知的体验层"：
  1. 技能后端能加载但前端无入口（`skills_loader.py` 有 `get_skills()` 但无 API 端点、无 UI）
  2. 右侧 Workspace 是 placeholder（[App.tsx#L41-L44](file:///d:/java/agentprojects/agent-py/frontend/renderer/App.tsx) 显示"任务与审批区域"文字）
  3. 消息渲染是纯文本（[ChatView.tsx#L222-L254](file:///d:/java/agentprojects/agent-py/frontend/renderer/components/chat/ChatView.tsx) `MessageBubble` 用 `whitespace-pre-wrap`）
  4. 只持久化单会话（[chat.ts#L52-L55](file:///d:/java/agentprojects/agent-py/frontend/renderer/stores/chat.ts) `partialize` 只存当前 messages + threadId）
  5. `tasks.ts` store 存在但未被任何组件使用
  6. 文件拖拽未实现
  7. `spawn.ts` 直接 spawn，无 ready 检测、无退避重试、日志只到 console
  8. 设置页缺 LLM provider/model/system prompt/auto_approve 滑块/embedding 地址
  9. 无 Error Boundary、无日志查看器
  10. 路径 A 无 thinking 状态，approval 超时硬编码 300s

设计文档约束（来自 [2026-07-03-agent-py-design.md](file:///d:/java/agentprojects/agent-py/docs/superpowers/specs/2026-07-03-agent-py-design.md)）：
- §13.1 M1 必做：ChatPanel + MessageList + InputBar（@技能、拖文件）、Workspace 面板文件树、任务时间线可视化
- §3.2 路径 A：TTFT < 1s / 总响应 < 2s
- §10：Python 进程崩溃自动重启 + 系统通知
- §12：危险操作 interrupt_on 无限期暂停，可选 `auto_approve_after_seconds`

## Goals / Non-Goals

**Goals:**
- P0 六项全部落地，M1 验收标准（§13.2）可达成
- P1 四项落地，应用达到"可用 + 稳定"状态
- 所有新增端点有单测覆盖
- 前端 typecheck + smoke 测试通过

**Non-Goals:**
- 不做 M2 打包（PyInstaller / electron-builder / 自动更新）
- 不做 E2E Playwright 测试（M2）
- 不做多语言界面
- 不做 IM 通道集成
- 不重构后端 Router 三路径核心逻辑（仅做 `@skill` 注入与配置项扩展）

## Decisions

### D1. 技能系统：`@skill` 语法 + system prompt 注入

**选择**：用户在输入框输入 `@` 弹出技能选择浮层，选中后插入 `@skill:<name>` 标记；Router 在 `run_router` 入口解析该标记，从 `get_skills()` 取 `skill.content`，拼接到原 system prompt 前；移除标记后剩余文本作为用户消息。

**API**：
- `GET /api/skills` → `[{name, description, trigger, tools, content_preview}]`（`content_preview` 截前 200 字，完整 content 在触发时注入）
- `POST /api/skills/reload` → 强制重载（开发态用，调用 `reload_skills()`）

**备选**：
- 技能作为独立工具暴露给 LLM → LLM 可能不主动调用，体验不可控
- 技能写入 RouterState 作为独立字段 → 过度设计，system prompt 注入足够

### D2. Workspace 面板：文件树 + 任务时间线分 Tab

**选择**：右侧 Workspace 面板分两个 Tab：
- "文件" Tab：`FileTree` 组件，调 `GET /api/workspace/list?path=data/workspace` 递归展示，支持刷新/在资源管理器中打开（复用 preload `shell:revealInFolder`，当前未暴露，本次补 `shell:revealInFolder` IPC）
- "任务" Tab：`TaskTimeline` 组件，订阅 `todo_update` 事件更新 `tasks.ts` store，展示任务卡片（状态、todos、耗时）

**API**：
- `GET /api/workspace/list?path=<dir>` → `{entries: [{name, type: "file"|"dir", size, mtime}]}`，限沙箱白名单内
- 复用 [filesystem.py](file:///d:/java/agentprojects/agent-py/backend/app/tools/filesystem.py) `list_dir` 的沙箱校验逻辑（但 `list_dir` 返回条目名，这里需要 type/size/mtime，新增独立函数）

**备选**：
- 用 `@xyflow/react` 画 LangGraph 风格图 → M1 过度，留 M2
- 文件树 + 任务合为一页 → 信息密度过高，Tab 分离更清晰

### D3. 聊天渲染：react-markdown + shiki，拖拽走 Main 进程 IPC

**选择**：
- `MessageBubble` assistant 分支改用 `<ReactMarkdown components={{code: CodeBlock}}>`，`CodeBlock` 用 `shiki` 高亮 + 复制按钮
- 输入区 `onDrop` 拦截文件，通过新增 IPC `dialog:saveDroppedFile` 把文件复制到 `data/uploads/{uuid}_{filename}`，返回相对路径，插入消息文本 `<file>data/uploads/xxx</file>` 供 agent 引用

**为什么拖拽走 Main 进程**：
- Renderer sandbox=true 无 Node fs 权限
- 通过 preload `dialog:saveDroppedFile` 调 Main 进程 `fs.copyFile`，安全且可控

**文件大小限制**：默认 50MB（`settings.ts` 增加 `maxUploadBytes` 配置，可在设置页调整）

### D4. 多会话：store 重构为 `sessions: Record<id, Session>`

**选择**：`chat.ts` store 结构改为：
```ts
interface Session { id: string; title: string; messages: ChatMessage[]; createdAt: number; }
interface ChatState {
  sessions: Record<string, Session>;
  currentId: string | null;
  // ...
}
```
- 持久化整个 `sessions`（localStorage，M2 迁 SQLite）
- 新建会话：生成 UUID，`currentId` 切换
- 删除会话：调 `/reset` 清后端 checkpoint + 从 `sessions` 移除
- 会话标题：取首条用户消息前 20 字
- 左侧栏渲染会话列表，支持点击切换、右键删除

**备选**：
- 后端 SQLite 存会话元数据 → M1 过度，localStorage 够用
- 会话标题用 LLM 生成 → 增加延迟，首条消息截断足够

### D5. 进程韧性：启动握手 + 退避重试 + 日志落盘

**选择**：`spawn.ts` 重写：
1. spawn 后启动轮询协程，每 200ms 调 `GET /api/health`，连续 2 次 200 视为 ready
2. ready 前前端显示"启动中..."遮罩；超时 30s 显示"启动失败，查看日志"
3. 进程非零退出：指数退避重试（1s/2s/4s），最多 3 次，每次发 `python:status` 事件
4. stdout/stderr 写入 `data/logs/agent-py-{YYYYMMDD}.log`，按日滚动，保留 7 天
5. Main 进程暴露 `logs:read(date?)` IPC，返回最近 N 行

**Error Boundary**：
- 新增 `ErrorBoundary.tsx` 包裹 `<Routes>`，捕获渲染错误显示 fallback UI + "查看日志"按钮
- 后端不可用时 `ChatView` 显示"后端未启动或已崩溃，请重启应用"提示

### D6. 设置页：分组 + 新增 LLM/系统提示词/审批滑块

**选择**：设置页分 4 组：
1. **LLM**：provider 下拉（OpenAI/DeepSeek/MiniMax 兼容）+ model 输入 + base_url（MiniMax 兼容用）+ 对应 API Key（复用现有 `ApiKeySettings` 扩展）
2. **知识库**：embedding URL + Milvus host/port/db/collection + Milvus 凭证（复用 `MilvusCredentialsForm`）
3. **安全**：`autoApproveAfterSeconds` 滑块（0-60s，0=禁用）+ `persistAuthorizedDirs` 开关 + `maxUploadBytes` 输入
4. **系统提示词**：textarea 编辑 `default_system_prompt`，持久化到 `electron-store`

**配置注入**：新增配置项通过 `electron-store` 持久化，spawn 时注入 `AGENT_PY_*` env（扩展 `spawn.ts` 的 `PythonCredentials`）

### D7. 路径 A 延迟：分类器规则覆盖 + thinking 状态 + approval 超时可配置

**选择**：
- `classifier.py` 扩展规则关键词：增加"翻译/解释/计算/对比"→ CHAT；"打开/查看/显示"→ SINGLE_TOOL
- 前端 `ChatView` 在 `setStreaming(true)` 后立即显示"思考中..."占位，首个 token 到达后替换
- `deep_path.py` `_APPROVAL_MAX_WAIT` 改为从 `settings.approval_max_wait` 读取（默认 300s，可在设置页调整，0=无限等待）
- token 流式：`ThinkFilter._MAX_HOLD` 可配置（当前硬编码 6），避免短 chunk 合并延迟

## Risks / Trade-offs

- **[多会话 store 重构引发数据迁移]** → localStorage 加版本号，启动时检测旧结构（单 messages + threadId）自动迁移为新 `sessions` 结构
- **[shiki 首次加载慢]** → 用 `shiki/bundle/web` 轻量包，懒加载高亮器（仅 assistant 消息渲染时加载）
- **[文件拖拽大文件卡顿]** → 50MB 限制 + 拖拽时显示进度；超限拒绝并提示
- **[日志文件无清理]** → Main 进程启动时清理 7 天前的日志文件
- **[启动握手超时误判]** → 30s 超时后不杀进程，让用户选择"继续等待"或"重启"
- **[技能 content 过长撑爆 system prompt]** → 限制单 skill content < 4000 字符，超出截断并提示
- **[设置页配置未保存就关闭]** → 表单 dirty 状态 + 离开提示
- **[Error Boundary 吞错误]** → 边界内记录错误到 `data/logs/renderer-error.log` + 显示错误 ID 供排查

## Migration Plan

**阶段 1（P0）：聊天与渲染**
1. `chat-rendering` spec：Markdown + shiki + 拖拽（独立 commit）
2. `session-management` spec：store 重构 + 迁移逻辑（独立 commit，因影响面大）
3. `workspace-panel` spec：文件树 + 任务时间线（独立 commit）
4. `skill-system` spec：API + UI + Router 注入（独立 commit）

**阶段 2（P1）：韧性与配置**
5. `process-resilience` spec：spawn 重写 + Error Boundary + 日志（独立 commit）
6. `settings-completion` spec：设置页 4 组（独立 commit）
7. `latency-optimization` spec：分类器 + thinking + 超时可配置（独立 commit）

**回滚策略**：
- 每阶段独立 commit，单阶段失败可 `git revert`
- `session-management` store 重构若数据迁移出错，回滚后旧 localStorage 仍在（迁移是只读转写）
- `process-resilience` 若新 spawn 协议导致启动失败，回滚到直接 spawn 版本

## Open Questions

| # | 问题 | 默认假设 |
|---|---|---|
| Q1 | 技能 `@` 触发是否需要模糊搜索？ | M1 用精确匹配 + 列表选择，M2 评估模糊搜索 |
| Q2 | Workspace 文件树是否支持编辑？ | M1 只读 + 在资源管理器中打开；编辑留 M2 |
| Q3 | 多会话 localStorage 容量上限？ | 默认单会话 1MB、总 10MB；超限提示用户清理或迁 M2 SQLite |
| Q4 | 日志查看器是否支持过滤？ | M1 只支持按日期查看最近 N 行；过滤留 M2 |
| Q5 | 设置页 system prompt 是否支持多套？ | M1 单一全局 system prompt；多套留 M2 |
| Q6 | 路径 A thinking 状态是否区分分类阶段？ | M1 统一显示"思考中..."；细分阶段留 M2 |
