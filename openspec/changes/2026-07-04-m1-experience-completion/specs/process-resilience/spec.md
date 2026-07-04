## ADDED Requirements

### Requirement: 启动握手协议

Main 进程 spawn Python 后 SHALL 轮询 `GET /api/health`（每 200ms 一次），连续 2 次 HTTP 200 视为 ready，向 Renderer 推送 `python:status` 事件 `{status: "ready"}`。30s 未 ready 推送 `{status: "timeout"}`。

#### Scenario: 正常启动
- **WHEN** Main 进程 spawn Python 子进程
- **THEN** 轮询 `/api/health`，连续 2 次 200 后推送 `python:status {status: "ready"}`

#### Scenario: 启动超时
- **WHEN** 30s 内 `/api/health` 未返回 200
- **THEN** 推送 `python:status {status: "timeout"}`，不杀进程，Renderer 显示"启动失败，查看日志"

### Requirement: 崩溃退避重试

Python 子进程非零退出时，Main 进程 SHALL 按指数退避重试（1s / 2s / 4s），最多 3 次。每次重试推送 `python:status {status: "restarting", attempt: n}`。3 次仍失败推送 `{status: "failed"}`。

#### Scenario: 首次崩溃重试
- **WHEN** Python 进程退出码非 0 且非主动退出（`quitting=false`）
- **THEN** 1s 后重启，推送 `python:status {status: "restarting", attempt: 1}`

#### Scenario: 3 次失败
- **WHEN** 第 3 次重启后进程仍非零退出
- **THEN** 推送 `python:status {status: "failed"}`，不再重试，Renderer 显示"Python 后端启动失败，请查看日志或手动重启"

#### Scenario: 主动退出不重试
- **WHEN** 用户退出应用（`quitting=true`）导致进程退出
- **THEN** 不触发重试

### Requirement: 日志落盘与滚动

Main 进程 SHALL 将 Python 子进程 stdout/stderr 写入 `data/logs/agent-py-{YYYYMMDD}.log`，按日滚动。启动时清理 7 天前的日志文件。

#### Scenario: 日志写入
- **WHEN** Python 子进程输出 stdout `INFO: ...`
- **THEN** 写入 `data/logs/agent-py-20260704.log`，格式 `[HH:MM:SS] [python] <line>`

#### Scenario: 日志清理
- **WHEN** Main 进程启动且 `data/logs/` 含 8 天前的日志
- **THEN** 删除 7 天前的日志文件

### Requirement: 日志查看 IPC

Main 进程 SHALL 注册 `logs:read` IPC handler，接收 `{date?: string, lines?: number}`，返回指定日期日志的最后 N 行（默认 200 行）。不指定 date 时返回当天日志。

#### Scenario: 读取当天日志
- **WHEN** Renderer 调 `window.api.logs.read({lines: 100})`
- **THEN** 返回 `data/logs/agent-py-{today}.log` 的最后 100 行

#### Scenario: 读取指定日期
- **WHEN** Renderer 调 `window.api.logs.read({date: "20260703", lines: 50})`
- **THEN** 返回 `data/logs/agent-py-20260703.log` 的最后 50 行

#### Scenario: 日志文件不存在
- **WHEN** Renderer 调 `window.api.logs.read({date: "20260101"})` 且文件不存在
- **THEN** 返回 `{lines: []}`

### Requirement: 前端 Error Boundary

Renderer SHALL 用 `ErrorBoundary` 组件包裹 `<Routes>`，捕获渲染错误时显示 fallback UI（错误 ID + "查看日志"按钮 + "重启应用"按钮），并将错误写入 `data/logs/renderer-error.log`。

#### Scenario: 捕获渲染错误
- **WHEN** 某组件渲染抛出异常
- **THEN** ErrorBoundary 捕获，显示 fallback UI `应用发生错误（ID: xxx），请查看日志或重启`，错误详情写入 `data/logs/renderer-error.log`

#### Scenario: 重启应用
- **WHEN** 用户在 fallback UI 点击"重启应用"
- **THEN** 调 `window.api.app.restart()`，应用重启

### Requirement: 后端不可用提示

Renderer SHALL 在 `python:status` 非 `ready` 时，ChatView 输入区显示禁用状态 + 提示"后端启动中..."或"后端不可用，请重启应用"。

#### Scenario: 启动中
- **WHEN** `python:status` 为 `restarting` 或未收到 `ready`
- **THEN** ChatView 输入框禁用，显示"后端启动中..."，发送按钮置灰

#### Scenario: 启动失败
- **WHEN** `python:status` 为 `failed` 或 `timeout`
- **THEN** ChatView 显示"后端不可用，请重启应用"，输入框禁用

### Requirement: preload 暴露 python:status、logs 与 app.restart API

系统 SHALL 在 preload 暴露：
- `window.api.python.onStatus(handler)` 订阅 `python:status` 事件，返回取消订阅函数
- `window.api.logs.read(opts)` 调 `logs:read` IPC
- `window.api.app.restart()` 调 `app:restart` IPC，触发 `app.relaunch()` + `app.exit(0)`

#### Scenario: 订阅状态
- **WHEN** Renderer 调 `window.api.python.onStatus(handler)`
- **THEN** Main 进程 `python:status` 事件触发 handler，返回取消订阅函数

#### Scenario: 读取日志
- **WHEN** Renderer 调 `window.api.logs.read({lines: 100})`
- **THEN** 返回 `logs:read` IPC 的结果

#### Scenario: 重启应用
- **WHEN** Renderer 调 `window.api.app.restart()`
- **THEN** Main 进程调 `app.relaunch()` + `app.exit(0)`，应用重启
