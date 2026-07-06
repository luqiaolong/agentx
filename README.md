# AgentX

本地优先的个人助理桌面应用，基于 Tauri 2.x + React + FastAPI + LangGraph 构建。

![主界面](docs/agentx.png)

## 特性

- **本地优先**：数据与模型配置均保存在本地（tauri-plugin-store），隐私可控
- **多智能体编排**：基于 LangGraph 的四路径路由（CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam）
- **AgentTeam 多代理协作**：Orchestrator 拆任务 + 并行子代理 + Blackboard + Aggregator 汇总
- **长期记忆**：会话检查点、技能、画像持久化（SQLite）
- **工具生态**：文件系统沙箱、RAG 检索、MCP 协议扩展、自定义子代理
- **流式交互**：SSE 实时推送，支持人在回路审批（危险工具 + 目录越界扩展）
- **配置热更新**：前端改 → tauri-plugin-store → 后端即时生效，无需重启进程

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Tauri 2.x + Rust 1.77+（tokio async runtime） |
| 前端 UI | React 18 + TypeScript + Tailwind CSS v4 + Zustand |
| 主进程 | Rust（10 个官方插件 + 50+ `#[tauri::command]`） |
| 后端 API | FastAPI + Uvicorn |
| AI 编排 | LangGraph `StateGraph` + DeepAgents + LangChain |
| 向量存储 | Milvus（TEI BGE-M3 嵌入） |
| 检查点 | LangGraph `SqliteSaver` / `AsyncSqliteSaver` |
| 观测 | LangSmith / Langfuse + loguru |
| 依赖管理 | 前端 npm + Vite 5，Rust cargo，后端 uv + pyproject.toml |

## 项目结构

```
agentx/
├── backend/app/                 # Python 后端（FastAPI）
│   ├── main.py                  # FastAPI 入口：lifespan + 全部 REST + SSE
│   ├── config.py                # pydantic-settings（AGENTX_* 前缀，env_file=None）
│   ├── llm.py                   # ChatModel 单例
│   ├── router/                  # 消息分类 + StateGraph（仅编排，不嵌路径实现）
│   ├── chat/                    # 路径 A：LLM 直答 + ThinkFilter
│   ├── deep/                    # 路径 C：DeepAgent + interrupt_before 审批
│   ├── team/                    # 路径 D：AgentTeam 多代理协作
│   ├── subagents/               # 路径 B：code / rag / web / 自定义子代理 + dispatch
│   ├── tools/                   # filesystem + rag_retrieve
│   ├── memory/                  # skills / profile / checkpointer / sandbox / context
│   ├── vectorstore/             # Milvus 客户端
│   ├── embedding/               # TEI 客户端
│   ├── mcp/                     # MCP 协议客户端 + 配置
│   ├── observability/           # LangSmith + logger
│   └── utils/                   # security(沙箱) + text(ThinkFilter) + sse_events + prompts
├── frontend/
│   ├── renderer/                # React UI（chat / settings / workspace 组件 + 12 个 API 模块）
│   └── shared/                  # renderer/shared 共享类型
├── src-tauri/                   # Tauri 2.x Rust 主进程
│   ├── src/
│   │   ├── lib.rs               # run() 入口 + setup() 启动 Python + 注册命令
│   │   ├── backend/             # PythonHandle（spawn + 健康握手 + 崩溃退避）+ env 注入
│   │   ├── commands/            # settings / dialog / shell / window / clipboard / notify / logs / app / git
│   │   ├── store/               # tauri-plugin-store 封装（enc:/plain: 凭证）+ 自定义子代理存储
│   │   ├── migration/           # electron-store → tauri-plugin-store 数据迁移
│   │   ├── git/                 # git2 crate 封装
│   │   └── logger/              # log 落盘
│   ├── Cargo.toml
│   └── tauri.conf.json          # 窗口 / 打包 / updater 骨架
├── tests/
│   ├── python/{unit,integration}/ # pytest（asyncio_mode=auto）
│   └── renderer/                # vitest
├── scripts/                     # 构建、图标生成、Tauri 冒烟脚本
├── openspec/changes/            # OpenSpec 提案（archive/ 历史）
├── docs/                        # 设计文档与截图
├── AGENTS.md                    # AI 代理协作规范（含工程文化 + Claude 工作手册）
└── claude.md                    # Claude 入口指针（每次会话必读）
```

> **路径重构（2026-07-06）**：`backend/app/paths/` 包已删除，各路径按能力域拆分为
> `chat/` / `deep/` / `team/` / `subagents/`。`router/graph.py` 仅负责分类 + 调度，
> 不再内嵌路径实现。

## 快速开始

### 环境要求

- Node.js >= 20
- Python >= 3.11
- uv（Python 包管理器）
- Rust stable（>= 1.77）+ Cargo
- WebView2 Runtime（Windows，Edge 自动安装）

### 安装依赖

```bash
npm install
uv sync
```

### 开发模式

```bash
npm run dev          # 等价于 tauri dev（vite renderer + Rust 主进程 + Python 后端）
```

> 重启前后端的完整 SOP（进程清理 + 8123 端口探测 + 健康验证脚本）见
> [AGENTS.md §14.7](file:///d:/java/agentprojects/agentx/AGENTS.md#147-重启前后端踩坑沉淀)。
> 入口永远走 `npm run dev`，**不要**直接 `uv run python -m app.main`——
> `AGENTX_*` 凭证 + 配置由 Rust 主进程通过
> [backend/env.rs::build_env](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs) 注入。

### 测试

```bash
# 前端（vitest）
npm run test

# Rust 单测
cd src-tauri && cargo test --lib

# Tauri 迁移冒烟脚本（10 项检查）
pwsh scripts/smoke-tauri.ps1

# Python 单元测试
uv run pytest tests/python/unit -v

# Python 集成测试（需要 myserver 连通性：TEI 8093 / Milvus 19530）
uv run pytest tests/python/integration -v -m requires_myserver
```

### 构建

```bash
npm run build        # 等价于 tauri build（生成 NSIS 安装包）
npm run dist:win     # 同上，显式 Windows 目标
```

## 架构与运行模式

### Router 四路径

聊天主入口 `backend/app/router/graph.py::run_router`：

| 分类 / 模式 | 路径 | 文件 | 典型场景 |
|---|---|---|---|
| `CHAT` | A | [chat/run.py](file:///d:/java/agentprojects/agentx/backend/app/chat/run.py) | 闲聊、问答、翻译 |
| `SINGLE_TOOL` | B | [subagents/dispatch.py](file:///d:/java/agentprojects/agentx/backend/app/subagents/dispatch.py) | 单工具调用（读文件 / 搜索 / 联网） |
| `DEEP_TASK` | C | [deep/agent.py](file:///d:/java/agentprojects/agentx/backend/app/deep/agent.py) | 多步规划 + 工具，**含危险工具审批** |
| `agent_team` 模式 | D | [team/orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py) | 多代理协作（Orchestrator + 并行子代理 + Blackboard + Aggregator） |

### AgentTeam 模式

聊天输入区左下角可在 `Agent`（默认）与 `AgentTeam` 之间切换。`AgentTeam` 启用
完整多代理协作链路：

- **Orchestrator**：把用户任务拆分成子任务计划（JSON：`{agent, input, purpose}`）
- **基础专家**：`code` / `rag` / `web`（只读 / 安全工具）
- **软件开发专家团**：`frontend_dev` / `backend_dev` / `tester` / `architect` /
  `devops` / `ui_designer` / `product_manager`
- **Blackboard**：子代理结果按 key 隔离写入共享黑板
- **Aggregator**：综合黑板内容生成最终回复
- **安全**：写/编辑/shell 任务必须分配为 `deep` 子任务，走 `interrupt_before` 审批

SSE 事件：`team_plan` / `team_progress` / `team_result` / `team_done`。

### 危险工具审批

`FORBIDDEN_SUBAGENT_TOOLS = {"write_file", "edit_file", "shell_exec"}` **只在路径 C
暴露**，配合 LangGraph `interrupt_before=["tools"]` 触发用户审批；路径 B 子代理
与自定义子代理严禁暴露写工具。审批类型：

- `dangerous_tool`：写 / 编辑 / shell
- `directory_extension`：路径越界扩展授权（含 `requestedPath` + `writable`）

### SSE 事件契约

| event | 说明 |
|---|---|
| `token` | 增量 token（visible text，已剥离 `<think>` 块） |
| `reasoning` | 思考过程 chunk |
| `tool_call` / `tool_result` | 工具调用配对 |
| `delegation` | 子代理委派标记（路径 B 入口） |
| `todo_update` | DeepAgent 任务进度 |
| `approval_request` | 危险工具 / 目录越界审批请求 |
| `team_plan` / `team_progress` / `team_result` / `team_done` | AgentTeam 协作事件 |
| `done` / `error` | 流结束 / 错误 |

修改任一事件必须同步更新 `backend/app/main.py` + `frontend/renderer/lib/api/chat.ts`
+ `frontend/renderer/hooks/useChatStream.ts` 三处。

## 配置

![设置界面](docs/agentx-config.png)

配置通过前端设置面板实时保存，由 Rust 主进程通过 tauri-plugin-store 持久化
（凭证用 `enc:` / `plain:` 前缀格式）并注入后端进程，无需手动编辑 `.env`：

- **模型**：LLM 服务商、API Key、激活模型（支持多模型切换）
- **记忆**：系统提示词、上下文窗口（消息数 / token 上限）、技能、画像、会话
- **技能**：Markdown 技能文件管理
- **MCP**：MCP Server 配置与工具发现
- **子代理**：内置子代理（code / rag / web）开关与参数、AgentTeam 团队角色、自定义子代理
- **工具**：各工具启用状态
- **知识库**：Milvus 连接配置
- **审批与安全**：自动审批倒计时、审批最长等待、沙箱授权目录

**配置变更即时生效**（无需重启后端）：
Renderer 保存配置 → `invoke("settings_set_*")` → tauri-plugin-store
→ `invoke("app_reload_backend_config")` → Rust 主进程从 store 读最新配置
→ `POST /api/config/reload` → 后端 `reload_settings()` 清除 `lru_cache`。
MCP 配置变更额外触发 `get_mcp_manager().refresh()` 重连。

如遇异常可手动「重启后端」（`invoke("app_restart_backend")`，仅重启 Python 进程）。
全量重启 Tauri（`invoke("app_restart")`）仅用于 ErrorBoundary 渲染错误恢复。

### 从旧版 Electron 迁移

首次启动 Tauri 版本时，`migration::migrate_electron_store()` 会自动检测旧 Electron
配置文件（`%APPDATA%/agentx/config.json`）并迁移到 tauri-plugin-store。明文配置
（`plain:` 前缀或裸字符串）自动迁移；`enc:` 加密凭证（safeStorage）无法跨进程解密，
会在前端设置页提示用户重新输入。

## 协议

私有项目，未经授权不得转载或分发。