# Design: CLI 模式支持

## Context

当前 AgentX 仅通过 Tauri 桌面应用提供交互入口。Tauri 主进程启动 Python FastAPI 后端（端口 8123），前端通过 HTTP SSE 与后端通信。所有配置（API Key、模型、MCP 等）存储在 Tauri `tauri-plugin-store` 的 `%APPDATA%/agentx/config.json` 中，Tauri 启动 Python 子进程时通过 `env` 注入 `AGENTX_*` 变量。

用户希望在 PowerShell / Terminal 中直接输入 `agentx` 命令与系统交互，无需启动 GUI。这要求 CLI 能够：
1. 独立获取配置（不依赖 Tauri 主进程注入环境变量）
2. 直接调用后端逻辑（不依赖 HTTP 服务已启动）
3. 在终端渲染流式输出和审批交互

## Goals / Non-Goals

**Goals:**
- `pip install -e .` 后 PowerShell 输入 `agentx` 可直接运行
- 支持 REPL 交互模式（持续对话）
- 支持 One-shot 单次任务模式（`agentx "提问"`）
- 支持管道输入（`echo "xxx" | agentx "处理"`）
- 支持 JSON 结构化输出（`--json`）
- 直连 `run_router`，不启动 uvicorn，不占端口
- 读取 Tauri store 配置作为默认配置源
- 终端审批交互（阻塞等待键盘输入 y/n/once/session）
- 支持 `/reset` `/mode` `/quit` `/help` 内建命令

**Non-Goals:**
- 不修改现有后端业务逻辑（router、agent、tool 等保持不变）
- 不新增 HTTP API 端点
- 不修改前端代码
- 不支持图片/文件上传（终端限制，未来扩展）
- 不做向后兼容的 GUI 模式切换（CLI 是新增入口，不影响现有 Tauri 应用）

## Decisions

### Decision 1: 直连 `run_router` 而非 HTTP 客户端模式

**选择**: CLI 作为 Python 模块直接导入并调用 `run_router()`，在进程内消费 `AsyncIterator[dict]` 事件流，不启动 uvicorn，也不通过 HTTP 调用 `/api/chat`。

**理由**:
- 无需端口占用，避免与 Tauri 启动的后端冲突
- 无需 SSE 解析，直接消费原生事件流
- 审批交互更容易实现：终端阻塞等待输入后直接调用 `submit_approval()`，无需跨进程/跨 HTTP 的状态同步
- 启动速度快（省去 uvicorn 启动和 HTTP 握手时间）
- 单进程调试更简单

**替代方案**:
- HTTP Client 模式（CLI 启动 uvicorn 子进程，再作为客户端调用）→ 拒绝，增加复杂度，端口管理麻烦，审批状态跨进程同步困难
- Tauri `--cli` 参数（改 Rust main.rs 跳过 GUI）→ 拒绝，需要改 Rust 代码，且仍需解决配置注入问题

### Decision 2: 配置来源三级降级策略

**选择**: CLI 启动时按以下优先级加载配置：
1. **Tauri store**（`%APPDATA%/agentx/config.json`）→ 解析并写入 `os.environ`
2. **环境变量**（已有的 `AGENTX_*`）→ 直接复用
3. **交互式提示**（若以上均无 API Key）→ 终端询问用户输入，可选保存到本地文件

**理由**:
- Tauri store 是现有配置的唯一真实来源，CLI 必须能读取它才能保证配置一致性
- 环境变量覆盖允许高级用户通过 `export AGENTX_OPENAI_API_KEY=xxx` 临时切换
- 交互式提示保证首次使用体验（用户未装 GUI 时也能直接用）

**Tauri store 解析规则**:
- `models.entries[]` + `models.activeId` → 提取 `model` / `base_url` / `api_key` → 映射到 `AGENTX_DEFAULT_MODEL` / `AGENTX_OPENAI_BASE_URL` / `AGENTX_OPENAI_API_KEY`
- `llm.defaultModel` / `llm.openaiBaseUrl`（legacy）→ 作为降级来源
- `apikey.openai` / `apikey.deepseek` → 映射到对应 provider 的 key
- `knowledge.*` / `approval.*` / `tools` / `mcp.servers` 等 → 按规则 JSON 序列化后映射到 `AGENTX_*`

### Decision 3: 审批交互终端阻塞模式

**选择**: 当 CLI 收到 `approval_request` 事件时，暂停事件生成器，在终端打印工具名、参数预览和操作提示，阻塞等待用户输入：
- `y` / `yes` → `approval=True, decision="approve"`
- `n` / `no` → `approval=False, decision="deny"`
- `o` / `once` → `approval=True, decision="once"`（directory_extension 场景）
- `s` / `session` → `approval=True, decision="session"`（directory_extension 场景）

**理由**:
- 与前端 ApprovalDialog 行为对齐，只是交互介质从 GUI 变为终端
- 阻塞等待是最简单的实现，避免多线程/异步竞争问题
- 输入后立即通过 `app.approval.state.submit_approval()` 写入，生成器恢复消费

**替代方案**:
- 非阻塞轮询（后台线程等待输入，主线程继续渲染）→ 拒绝，增加复杂度，且审批通常需要用户阅读预览信息后再决定，阻塞更符合直觉

### Decision 4: REPL + One-shot 双模式设计

**选择**: CLI 根据参数自动判断模式：
- 无位置参数且无管道输入 → REPL 模式（交互式会话）
- 有位置参数（如 `agentx "hello"`）→ One-shot 模式（执行后退出）
- 无位置参数但有管道输入（如 `cat x | agentx`）→ One-shot 模式（stdin 作为消息内容）

**模式选择**:
- `--work` → work 模式（Supervisor 全能 agent）
- `--coding` → coding 模式（Coding Expert），**默认**
- `--coding-team` → coding_team 模式（AgentTeam 协作）
- 三个 flag 互斥，后指定者覆盖；均未指定时默认 coding

**内建命令（REPL 模式下以 `/` 开头）**:
- `/reset` → 清空当前 thread 的 checkpointer 和沙箱
- `/mode {work|coding|coding_team}` → 切换 agent_mode（默认 coding）
- `/quit` / `/q` / `Ctrl+C` → 退出
- `/help` → 显示可用命令

**理由**:
- 双模式覆盖两种主要使用场景：持续对话和脚本化调用
- 内建命令与前端功能对齐（/reset、模式切换）
- 默认 coding 模式：CLI 场景下用户多为开发者，coding 模式更符合高频使用场景

### Decision 5: thread_id 管理策略

**选择**:
- REPL 模式：启动时生成新的 `thread_id`（`uuid.uuid4().hex[:12]`），整个会话复用同一个 id；退出后 thread 保留在 checkpointer 中，下次可通过 `--thread <id>` 恢复
- One-shot 模式：每次执行生成新的 `thread_id`，执行完成后不主动清理（由 checkpointer 自动管理）

**理由**:
- REPL 需要会话连续性，同 thread_id 保证历史消息加载
- One-shot 默认隔离，避免历史污染；高级用户可通过 `--thread` 指定已有会话

### Decision 6: 终端事件渲染风格

**选择**:
- `token` → `print(text, end="", flush=True)` 增量输出，模拟打字效果
- `reasoning` → 若 `--verbose` 则灰色显示（Windows 终端用 dim 颜色），默认隐藏
- `tool_call` → 单行显示 `[调用: {name}]`
- `tool_result` → 结果折叠显示，若 `--verbose` 则展开
- `approval_request` → 高亮显示工具名和参数，提示输入 y/n/o/s
- `team_plan` / `team_progress` / `team_result` / `team_done` → 简单文本提示（如 `[团队计划: 3 个子任务]`）
- `done` → 换行，REPL 模式下打印新的 `>` 提示符
- `error` → 红色输出错误信息

**理由**:
- 保持终端输出简洁，默认不显示 reasoning 和完整 tool_result（避免刷屏）
- `--verbose` 开关满足调试需求
- 颜色使用 `colorama` 保证 Windows PowerShell 兼容

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| [Risk] Tauri store 路径在不同 Windows 版本/配置下可能不一致 | [Mitigation] 同时尝试 `%APPDATA%/agentx/config.json` 和 `%LOCALAPPDATA%/agentx/config.json`；均失败时降级到环境变量和交互提示 |
| [Risk] Windows PowerShell 默认编码（GBK）导致中文输出乱码 | [Mitigation] CLI 启动时调用 `chcp 65001`（通过 `subprocess`）或打印 `colorama` 初始化；Python `print` 使用 `utf-8` |
| [Risk] 终端审批阻塞时，SSE 事件生成器挂起可能导致后端超时 | [Mitigation] 审批超时由后端 `approval_max_wait` 控制（默认 300s），CLI 只是消费方；阻塞时间由用户决定，不额外设限 |
| [Risk] CLI 和 GUI 同时运行时，Milvus/checkpointer 等资源竞争 | [Mitigation] CLI 直连模式下使用相同的单例（`get_checkpointer()` / `get_milvus_client()`），SQLite checkpointer 支持多进程读；写操作由 SQLite 锁保证安全 |
| [Risk] `run_router` 的 AsyncIterator 在同步 CLI 主循环中调用复杂 | [Mitigation] CLI 主入口使用 `asyncio.run()` 包装，REPL 循环内部用 `async for` 消费事件流 |
| [Risk] 工具输出过长导致终端刷屏 | [Mitigation] `cli_render.py` 对 tool_result 做截断（默认 2000 字符），`--verbose` 时放宽到 10000 |

## Migration Plan

### Phase 1: CLI 骨架和配置读取
- 创建 `backend/app/cli_store.py`，实现 `load_tauri_store()` 解析 `config.json`
- 创建 `backend/app/cli.py` 主入口，实现参数解析（`argparse`）和模式判断
- 修改 `pyproject.toml` 注册 `[project.scripts] agentx = "app.cli:main"`
- 编写配置加载单元测试

### Phase 2: REPL 交互循环
- 实现 REPL 输入循环（`asyncio` + `input()`）
- 实现 `/reset` `/mode` `/quit` `/help` 内建命令
- 实现 `thread_id` 生成和 `--thread` 参数支持
- 编写 REPL 循环单元测试

### Phase 3: 事件流渲染
- 创建 `backend/app/cli_render.py`，实现各类 SSE 事件的终端渲染
- 集成 `colorama` 做跨平台颜色输出
- 实现 `--verbose` 和 `--json` 输出开关
- 编写渲染器单元测试

### Phase 4: 审批交互
- 实现 `approval_request` 事件的终端阻塞等待
- 集成 `app.approval.state.submit_approval()`
- 支持 `y/n/o/s` 四种输入语义
- 编写审批交互模拟测试

### Phase 5: One-shot 和管道模式
- 实现位置参数读取（`agentx "提问"`）
- 实现 stdin 管道读取（`cat file | agentx`）
- 实现 `--json` 结构化输出
- 编写集成测试

### Phase 6: 测试与文档
- 端到端测试：REPL 简单对话、One-shot 单次任务、管道输入、审批交互
- 更新 `.env.example` 添加 CLI 说明
- 更新 README 添加 CLI 安装和使用章节

## Open Questions

1. **CLI 是否支持多模态输入（图片）？** → 当前不支持，终端限制。未来可通过 `--file` 参数支持文本文件，图片需等终端图像协议支持。
2. **CLI 和 Tauri GUI 同时运行时，Milvus 连接是否冲突？** → 不冲突，Milvus 是服务端，多客户端可并发；checkpointer 用 SQLite，读并发安全，写由锁保证。
3. **是否需要 CLI 独占配置（不读 Tauri store）？** → 不需要。Tauri store 是配置唯一来源，CLI 读取它保证一致性。若用户无 GUI，通过交互提示和环境变量也能工作。
