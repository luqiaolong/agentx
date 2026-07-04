## ADDED Requirements

### Requirement: Workspace 文件列表 API

系统 SHALL 提供 `GET /api/workspace/list?path=<dir>` 端点返回指定目录下的条目列表，每项含 `name` / `type`（"file" | "dir"）/ `size`（字节）/ `mtime`（Unix timestamp）。路径 MUST 在沙箱白名单内（`data/workspace` + `data/uploads` + 会话授权目录），否则返回 403。

#### Scenario: 列出工作区
- **WHEN** 调用 `GET /api/workspace/list?path=data/workspace`
- **THEN** 返回 `{"entries": [{"name": "out.txt", "type": "file", "size": 1024, "mtime": 1700000000}, ...]}`

#### Scenario: 未授权路径拒绝
- **WHEN** 调用 `GET /api/workspace/list?path=d:/secrets` 且未授权
- **THEN** 返回 403 `{"detail": "路径未授权"}`

#### Scenario: 路径不存在
- **WHEN** 调用 `GET /api/workspace/list?path=data/workspace/nonexistent`
- **THEN** 返回 404 `{"detail": "路径不存在"}`

### Requirement: 文件树组件

Renderer SHALL 在右侧 Workspace 面板"文件"Tab 渲染递归文件树，根目录为 `data/workspace/`。支持展开/折叠目录、点击文件触发 `shell:revealInFolder`、刷新按钮重新拉取列表。

#### Scenario: 渲染文件树
- **WHEN** Workspace 面板挂载
- **THEN** 调用 `GET /api/workspace/list?path=data/workspace`，递归渲染目录与文件

#### Scenario: 刷新文件树
- **WHEN** 用户点击刷新按钮
- **THEN** 重新调 `GET /api/workspace/list`，文件树更新

#### Scenario: 在资源管理器中打开
- **WHEN** 用户点击文件条目
- **THEN** 调 `window.api.shell.revealInFolder(path)`，系统资源管理器定位到该文件

### Requirement: 任务时间线组件

Renderer SHALL 在右侧 Workspace 面板"任务"Tab 渲染任务卡片列表，订阅 `todo_update` SSE 事件更新 `tasks.ts` store。每张卡片展示任务标题（首条 todo 或消息前 20 字）、状态（pending/running/done/failed）、todos 子项与耗时。

#### Scenario: 接收 todo_update
- **WHEN** SSE 推送 `{"event": "todo_update", "data": "{\"todos\": [{\"text\": \"调用工具: read_file\", \"done\": false}]}"}` 
- **THEN** `tasks.ts` store 新增/更新任务卡片，任务 Tab 实时显示

#### Scenario: 任务完成
- **WHEN** SSE 推送 `{"todos": [{"text": "工具 read_file 完成", "done": true}]}`
- **THEN** 任务卡片状态变为 `done`，todos 子项勾选

### Requirement: Workspace 面板 Tab 容器

Renderer SHALL 在右侧 Workspace 区域渲染 Tab 容器，含"文件"与"任务"两个 Tab，默认显示"文件"Tab。Tab 切换保留各组件状态。

#### Scenario: 默认显示文件 Tab
- **WHEN** Workspace 面板挂载
- **THEN** 默认激活"文件"Tab，显示文件树

#### Scenario: 切换到任务 Tab
- **WHEN** 用户点击"任务"Tab
- **THEN** 显示任务时间线组件，文件树组件卸载或隐藏

### Requirement: preload 暴露 workspace 与 shell API

系统 SHALL 在 preload 暴露 `window.api.workspace.list(path)` 调 `GET /api/workspace/list`，与 `window.api.shell.revealInFolder(path)` 调 Main 进程 `shell.showItemInFolder`。

#### Scenario: workspace.list
- **WHEN** Renderer 调 `window.api.workspace.list("data/workspace")`
- **THEN** 返回 `GET /api/workspace/list?path=data/workspace` 的 JSON

#### Scenario: shell.revealInFolder
- **WHEN** Renderer 调 `window.api.shell.revealInFolder("data/workspace/out.txt")`
- **THEN** Main 进程调 `shell.showItemInFolder`，系统资源管理器打开并定位到该文件
