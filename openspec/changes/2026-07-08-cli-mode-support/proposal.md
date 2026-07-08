# Proposal: CLI 模式支持 — PowerShell 终端交互

## Why

当前 AgentX 仅提供 Tauri 桌面 GUI 入口，用户必须通过图形界面与 agent 交互。开发者和高级用户希望在 PowerShell / Terminal 中直接输入 `agentx` 命令，以 CLI 方式快速处理任务，例如：

- `agentx "帮我把这段代码改成异步的"` — 单次任务直接出结果
- `agentx` — 进入 REPL 交互式会话
- `git diff | agentx "写 commit message"` — 管道输入处理

这能显著提升脚本化、自动化场景下的使用效率，同时降低对 GUI 的依赖。

## What Changes

### 后端层

- 新增 `backend/app/cli.py`：CLI 主模块，提供 REPL + One-shot 双模式
- 修改 `pyproject.toml`：注册 `[project.scripts] agentx = "app.cli:main"`，`pip install` 后生成 `agentx.exe`
- 新增 `backend/app/cli_store.py`：Tauri store 配置读取器（解析 `%APPDATA%/agentx/config.json`）
- 新增 `backend/app/cli_render.py`：终端事件渲染器（token / reasoning / tool_call / tool_result / approval_request / done / error）

### 配置层

- CLI 启动时自动读取 Tauri store（`config.json`），提取 `models.activeId`、`apikey.*`、`llm.*` 等映射为 `AGENTX_*` 环境变量
- 若 Tauri store 不存在，降级到：当前目录 `.env` → 系统环境变量 → 交互式提示用户输入 API Key（仅第一次）

### 交互层

- **REPL 模式**：启动后显示 banner，循环读取用户输入，支持 `/reset` `/mode` `/quit` `/help` 内建命令
- **One-shot 模式**：`agentx "提问内容"` 直接输出结果后退出
- **管道模式**：`cat file | agentx "总结内容"` 读取 stdin 作为上下文
- **JSON 输出**：`agentx "提问" --json` 输出结构化 JSON（供脚本解析）

### 审批层

- 终端收到 `approval_request` 事件时，暂停生成器，打印审批详情（tool_name / args / preview）
- 阻塞等待用户输入：`y`（批准）/`n`（拒绝）/`o`（仅一次）/`s`（会话级批准）
- 输入后通过 `app.approval.state.submit_approval()` 写入，恢复生成器

## Capabilities

### New Capabilities

- `cli-mode`：命令行交互模式，支持 REPL / One-shot / Pipe / JSON 输出四种使用形态

### Modified Capabilities

- `sse-event-contract`：CLI 消费与前端相同的事件流，无需新增事件类型
- `dangerous-operation-approval`：审批流从 GUI 弹窗扩展到终端阻塞输入

## Impact

- **后端**:
  - 新增 `backend/app/cli.py`（~300 行）
  - 新增 `backend/app/cli_store.py`（~80 行）
  - 新增 `backend/app/cli_render.py`（~120 行）
  - 修改 `pyproject.toml` 增加 `[project.scripts]`
- **前端**: 无改动
- **API**: 无新增端点，直连 `run_router` 复用现有逻辑
- **配置**: 更新 `.env.example`，添加 CLI 相关环境变量说明
- **测试**: 新增 CLI 模块单元测试（配置加载、事件渲染、审批交互模拟）
- **文档**: 更新 README，添加 CLI 安装和使用说明

## Future Extensibility

- 未来可扩展 `--file` 参数支持文件上传（如 `agentx --file ./data.csv "分析数据"`）
- 未来可扩展 `agentx config` 子命令，在终端直接管理配置（无需 GUI）
