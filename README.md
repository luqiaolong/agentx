# AgentX

本地优先的个人助理桌面应用，基于 Tauri 2.x + React + FastAPI + LangGraph 构建。

![主界面](docs/agentx.png)

## 特性

- **本地优先**：数据与模型配置均保存在本地（tauri-plugin-store + SQLite），隐私可控
- **场景化多智能体架构**：Work Supervisor（全能） + Coding Expert（专家） + Coding Team（多代理协作） + Subagent（rag / web / 自定义）四级分工
- **agent_mode 单字段三态**：`work` / `coding` / `coding_team`，Router 按场景直接分发到对应执行体
- **长期记忆**：会话检查点（LangGraph `SqliteSaver`）+ 技能 + 画像持久化
- **工具生态**：14 项内置工具（filesystem 读写 + grep + glob + web_search + rag_retrieve + git_* + cli_execute）+ RAG 检索 + MCP 协议（stdio / sse / streamable_http）扩展 + 自定义子代理
- **人在回路审批**：危险工具 + 目录越界扩展双类型审批，支持 approve / once / session / deny 四种决策；以及 pause / resume 手动暂停流并保留 checkpointer 状态
- **配置热更新**：前端改 → tauri-plugin-store → 后端即时生效，无需重启进程
- **观测**：LangSmith trace + Langfuse 接入 + loguru 结构化日志

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Tauri 2.x + Rust 1.77+（tokio async runtime） |
| 前端 UI | React 18 + TypeScript + Tailwind CSS v4 + zustand |
| 主进程 | Rust（10 个官方插件 + `commands/<domain>.rs`） |
| 后端 API | FastAPI + Uvicorn |
| AI 编排 | LangGraph `StateGraph` + `create_react_agent`（DeepAgents）+ LangChain |
| 智能体分层 | `agents/{supervisor,expert,team}/` 场景化执行体 + `subagents/{base,custom_agent,rag_agent,web_agent}.py` 轻量子代理 |
| 向量存储 | Milvus（TEI BGE-M3 嵌入，部署在 myserver） |
| 检查点 | LangGraph `SqliteSaver` / `AsyncSqliteSaver` |
| 观测 | LangSmith + Langfuse + loguru |
| 依赖管理 | 前端 npm + Vite 5，Rust cargo，后端 uv + pyproject.toml |

## 项目结构

```
agentx/
├── backend/app/                          # Python 后端（FastAPI）
│   ├── __init__.py
│   ├── main.py                           # FastAPI 入口：lifespan + 中间件 + register_routes()
│   ├── llm.py                            # ChatModel 单例
│   ├── api/                              # REST + SSE 端点（按职责拆分，register_*_routes() 聚合）
│   │   ├── __init__.py                   # register_routes(app) 聚合入口
│   │   ├── schemas.py                    # 15 个 Pydantic 请求/响应模型
│   │   ├── health.py                     # / + /api/health（聚合 BGE-M3 + Milvus 状态）
│   │   ├── sandbox.py                    # /api/sandbox/{authorize,revoke,authorized/{tid}}
│   │   ├── chat.py                       # /api/chat + approve/abort/pause/resume + _event_generator
│   │   ├── memory.py                     # skills/profile/checkpointer CRUD
│   │   ├── mcp.py                        # MCP servers/tools/test/refresh
│   │   ├── skills.py                     # skills list/reload
│   │   ├── workspace.py                  # workspace list/read
│   │   ├── config_reload.py              # 配置热重载
│   │   └── models_test.py                # 模型连通性测试
│   ├── approval/                         # 审批层：决策数据类 + 跨请求状态（与 deep 解耦）
│   │   ├── decision.py                   # ApprovalDecision dataclass
│   │   ├── state.py                      # submit/pop_approval + set/is/clear_abort + pause/resume
│   │   └── __init__.py
│   ├── config/                           # pydantic-settings 配置包（替代单文件 config.py）
│   │   ├── __init__.py                   # 聚合导出（向后兼容 from app.config import get_settings）
│   │   ├── settings.py                   # Settings + get_settings + 路径常量
│   │   ├── subagents.py                  # SubagentSettings + FORBIDDEN_SUBAGENT_TOOLS + 默认子代理
│   │   ├── agents.py                     # AgentsConfig + SupervisorSettings + ExpertSettings + ScenarioTeamSettings
│   │   └── prompts/                      # 内置 system prompt + trigger 描述
│   │       ├── builtin.py                # code/rag/web 子代理默认值
│   │       ├── team.py                   # 7 个团队专家默认值
│   │       └── agent.py                  # 场景化 agent system prompt
│   ├── router/                           # 消息入口（仅场景分发，不嵌路径实现）
│   │   ├── graph.py                      # Router 主入口 run_router + @skill:<name> 解析
│   │   ├── state.py                      # RouterState TypedDict
│   │   └── __init__.py
│   ├── agents/                           # 场景化智能体包（Supervisor + Expert + Team）
│   │   ├── __init__.py
│   │   ├── supervisor/
│   │   │   ├── work_supervisor.py        # run_work_supervisor（work 场景全能 agent）
│   │   │   ├── delegation.py             # delegate_to_expert / delegate_to_subagent
│   │   │   ├── mention.py                # @coding @rag @web mention 解析
│   │   │   └── __init__.py
│   │   ├── expert/
│   │   │   ├── coding.py                 # run_coding_expert（基于 build_deep_agent + 审批）
│   │   │   └── __init__.py
│   │   └── team/
│   │       ├── coding_team.py            # run_coding_team（场景级 AgentTeam）
│   │       └── __init__.py
│   ├── deep/                             # DeepAgent 工具 + 流式 + 审批 + 恢复（路径 C 基础）
│   │   ├── __init__.py
│   │   ├── agent.py                      # build_deep_agent + run_deep_path（编排层主入口）
│   │   ├── tools.py                      # DANGEROUS_TOOLS + _make_deep_tools + _load_mcp_tools
│   │   ├── streaming.py                  # _stream_agent_events
│   │   ├── approval.py                   # wait_for_approval + _handle_directory_extension
│   │   └── recovery.py                   # _inject_tool_error_messages + _sanitize_message_history
│   ├── team/                             # AgentTeam 多代理协作框架（被 agents/team/ 调用）
│   │   ├── __init__.py
│   │   ├── orchestrator.py               # run_team_path（主入口）
│   │   ├── planner.py                    # _build_orchestrator_prompt + _parse_plan
│   │   ├── scheduler.py                  # _run_subtask + 队列驱动
│   │   ├── blackboard.py                 # Blackboard + TeamPlanTask + TeamSubtaskResult
│   │   └── aggregator.py                 # _run_aggregator + _quality_gate
│   ├── subagents/                        # 基础子代理（rag / web / 自定义工厂）
│   │   ├── __init__.py
│   │   ├── base.py                       # make_fs_tools / make_rag_tools / make_web_tools + extract_text
│   │   ├── rag_agent.py                  # rag 子代理（ReAct）
│   │   ├── web_agent.py                  # web 子代理（ReAct）
│   │   └── custom_agent.py               # 自定义子代理工厂
│   ├── tools/                            # filesystem + cli + rag_retrieve
│   │   ├── __init__.py
│   │   ├── filesystem.py
│   │   ├── cli.py
│   │   └── rag_retrieve.py
│   ├── memory/                           # skills / profile / checkpointer / sandbox / context / summarizer
│   │   ├── __init__.py
│   │   ├── skills_loader.py              # get_skills（@skill 注入用）
│   │   ├── skills_store.py
│   │   ├── profile_extractor.py          # extract_profile_via_llm
│   │   ├── profile_store.py              # upsert_from_llm + build_profile_prompt
│   │   ├── checkpointer.py               # LangGraph checkpointer
│   │   ├── checkpointer_view.py          # thread checkpoint 查询/清理
│   │   ├── context.py                    # trim_messages_with_budget
│   │   ├── sandbox_store.py              # 授权目录持久化
│   │   └── summarizer.py
│   ├── vectorstore/milvus_client.py
│   ├── embedding/tei_client.py           # TEI（BGE-M3）HTTP 客户端
│   ├── mcp/                              # MCP 客户端 + 配置
│   │   ├── __init__.py
│   │   ├── client.py
│   │   └── config.py
│   ├── observability/                    # LangSmith + logger
│   │   ├── __init__.py
│   │   ├── langsmith.py                  # trace_span helper
│   │   └── logger.py                     # loguru 封装
│   └── utils/                            # security(沙箱) + text(ThinkFilter) + chunks + sse_events + paths + prompts + plan_extraction
├── backend/pythonlearning/               # 教学示例（独立目录，不被生产加载）
│   └── algorithms/basic/concurrency/crud_demo/fileio/functional/oop/
├── frontend/
│   ├── renderer/                         # React UI（chat/settings/workspace/code/components）
│   │   ├── App.tsx / main.tsx / log-window.tsx
│   │   ├── components/
│   │   │   ├── chat/                     # 聊天主区（输入框 / 消息流 / ModeToggle 等）
│   │   │   ├── code/                     # 代码视图（CodeViewer）
│   │   │   ├── settings/                 # 设置主面板
│   │   │   │   ├── model-provider/       # 模型服务商 + 模型条目
│   │   │   │   ├── mcp/                  # MCP server 列表 + 工具发现
│   │   │   │   ├── memory/               # 系统提示 + 上下文窗口 + 技能 + 画像
│   │   │   │   └── subagents/            # 基础子代理 + AgentTeam + 自定义
│   │   │   ├── workspace/                # 工作区面板（任务 / 文件 / Git）
│   │   │   └── ui/                       # 通用 UI 原子（ErrorBanner / ConfirmButton / ...）
│   │   ├── hooks/                        # 自定义 React hooks
│   │   │   ├── useChatStream.ts          # SSE 流解析（直连 fetch 8123）
│   │   │   ├── useAutoScroll.ts          # 消息流自动滚动到底
│   │   │   ├── useContextFiles.ts        # 上下文文件选择
│   │   │   ├── useConfigSave.ts          # 配置保存 hook
│   │   │   ├── useCrudList.ts            # 通用 CRUD 列表 hook
│   │   │   └── useFixedTextarea.ts       # 输入框自适应高度
│   │   ├── lib/
│   │   │   ├── api/                      # 14 个 Tauri invoke / fetch 模块
│   │   │   │   ├── request.ts            # apiGet / apiPost / apiPut / apiDelete 通用封装
│   │   │   │   └── agents / app / chat / settings / mcp / memory / events / git / logs / notify / dialog / shell / window / clipboard
│   │   │   ├── schemas/                  # zod schemas（approval / mcp-server / model-entry / ...）
│   │   │   ├── format.ts / validators.ts / logger.ts / errors.ts
│   │   │   ├── subagentConstants.ts      # ALL_TOOLS / emptyToolsConfig
│   │   │   └── (api-constants / modelCatalog / mention / utils ...)
│   │   ├── stores/                       # zustand 状态
│   │   │   ├── chat/                     # chat store 拆分（index + migrations + quotaStorage + messageOps + messageIndex）
│   │   │   ├── agentMode.ts              # work / coding / coding_team 单字段
│   │   │   ├── scene.ts                  # 场景 tab 派生 + applySceneChange
│   │   │   └── settings / skills / tasks / permission / commands / contextUsage / git / mention / model
│   │   └── styles/
│   └── shared/api-types.ts               # renderer / shared 共享类型（含 SSE 事件 discriminated union）
├── src-tauri/                            # Tauri 2.x Rust 主进程
│   ├── src/
│   │   ├── lib.rs                        # run() 入口 + setup() 启动 Python + 注册命令
│   │   ├── main.rs / build.rs
│   │   ├── backend/                      # PythonHandle + env 注入
│   │   │   ├── handle.rs                 # spawn + 健康握手 + 崩溃退避
│   │   │   └── env.rs                    # build_env（凭证通过 tokio Command::env 注入）
│   │   ├── commands/                     # 10 个 .rs（app/settings/dialog/shell/window/clipboard/notify/logs/git + mod）
│   │   ├── store/                        # tauri-plugin-store 封装
│   │   │   ├── credentials.rs            # 凭证加密/解密（safeStorage，enc: / plain: 前缀）
│   │   │   ├── custom_subagents.rs       # 自定义子代理持久化
│   │   │   └── mod.rs
│   │   ├── git/                          # git2 crate 封装
│   │   ├── logger/                       # log 落盘
│   │   └── migration/                    # electron-store → tauri-plugin-store 迁移
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   └── icons/                            # 标准 Tauri 多尺寸图标
├── tests/
│   ├── python/{unit,integration,e2e}/    # pytest（asyncio_mode=auto）
│   └── renderer/                         # vitest
├── scripts/                              # 构建、图标生成、Tauri 冒烟脚本
├── openspec/changes/                     # OpenSpec 提案（archive/ 历史）
├── docs/                                 # 设计文档与截图
├── AGENTS.md                             # AI 代理协作规范（含工程文化 + Claude 工作手册）
└── claude.md                             # Claude 入口指针（每次会话必读）
```

> **路径重构（2026-07-06）**：`backend/app/paths/` 包已删除，各路径按能力域拆分为
> `chat/` / `deep/` / `team/` / `subagents/`。
>
> **场景化重构（2026-07-08）**：删除旧的「CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam」四路径分类；
> `agent_mode` 单字段取 `work` / `coding` / `coding_team`；新增 `agents/` 包组织
> Supervisor / Expert / Team 三层执行体；`router/graph.py` 仅按 `agent_mode` 直接分发，
> 不再做消息分类。

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

### Router 场景分发

聊天主入口 `backend/app/router/graph.py::run_router` 仅按 `agent_mode`
**直接分发**到对应场景执行体，不再做 CHAT / SINGLE_TOOL / DEEP_TASK 等消息分类：

| `agent_mode` | 场景 | 执行体 | 文件 |
|---|---|---|---|
| `"work"` | work | `run_work_supervisor`（全能 Supervisor） | [agents/supervisor/work_supervisor.py](file:///d:/java/agentprojects/agentx/backend/app/agents/supervisor/work_supervisor.py) |
| `"coding"` | coding | `run_coding_expert`（基于 `build_deep_agent`） | [agents/expert/coding.py](file:///d:/java/agentprojects/agentx/backend/app/agents/expert/coding.py) |
| `"coding_team"` | coding | `run_coding_team`（Orchestrator + 并行 Expert + Blackboard + Aggregator） | [agents/team/coding_team.py](file:///d:/java/agentprojects/agentx/backend/app/agents/team/coding_team.py) |

`run_router` 公共职责：`@skill:<name>` 标记解析 → workspace 授权同步 →
用户画像加载 → checkpointer 历史消息读取并截断 → 按 `agent_mode` 分发 →
统一收口 assistant token 写回 checkpointer 并 yield `done`。

### Subagent 与委派

Subagent 层（`backend/app/subagents/`）提供基础子代理，被 Supervisor 与
Coding Expert 调用：

- **基础子代理**：`rag` / `web`（只读 / 安全工具，禁用 `FORBIDDEN_SUBAGENT_TOOLS`）
- **自定义子代理**：`custom_agent.py` 提供工厂函数，可由用户在「设置 → 子代理」配置
- **Supervisor 委派能力**：
  - `delegate_to_expert(expert_name, task, context)` — 委派 Coding Expert
  - `delegate_to_subagent(agent_name, task)` — 委派 rag / web / 自定义子代理
- **`@mention` 语法**（`@coding` / `@rag` / `@web`）强制委派，覆盖 LLM 自主决策；
  解析在 [agents/supervisor/mention.py](file:///d:/java/agentprojects/agentx/backend/app/agents/supervisor/mention.py)

Coding Team（`backend/app/team/` + `agents/team/coding_team.py`）使用 7 角色
软件开发专家团：`frontend_dev` / `backend_dev` / `tester` / `architect` /
`devops` / `ui_designer` / `product_manager`。Orchestrator 把任务拆分成
子任务计划（`team_plan` 事件），`scheduler.py` 并行执行（`team_progress`），
结果写入 Blackboard（`team_result`），最后由 `aggregator.py` 综合输出
（`team_done`）。写 / 编辑 / shell 等危险任务必须分配为 `code` 子任务，
由 Coding Expert 执行并走审批。

### 危险工具审批

`DANGEROUS_TOOLS = {"edit_file", "write_file", "shell_exec", "cli_execute",
"git_clone", "git_pull", "git_checkout", "git_stage", "git_commit"}`
在 Supervisor 与 Coding Expert 中均暴露，通过 LangGraph
`interrupt_before=["tools"]` 触发用户审批；基础子代理（rag / web）与
自定义子代理**严禁**直接暴露写工具（子代理层禁止工具集合
`FORBIDDEN_SUBAGENT_TOOLS`，见 [config/subagents.py](file:///d:/java/agentprojects/agentx/backend/app/config/subagents.py)）。

审批类型（`ApprovalKind`）：

- `dangerous_tool`：危险工具调用（默认类型，向后兼容）
- `directory_extension`：路径越界扩展授权（payload 含 `requestedPath` + `writable`）

审批决策 `ApprovalDecision` 取值：`approve` / `once` / `session` / `deny`。

**流控制端点**（[api/chat.py](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)）：

- `POST /api/chat/approve` — 提交审批决策
- `POST /api/chat/abort` — 中止 SSE 流
- `POST /api/chat/pause` — 暂停（流保留，等待 resume）
- `POST /api/chat/resume` — 清除暂停标志（DeepAgent 继续执行）

跨进程状态（审批 / 中止 / 暂停三套 dict）已从 `main.py` 解耦到
[backend/app/approval/](file:///d:/java/agentprojects/agentx/backend/app/approval/)
模块，便于未来迁移到 Redis 时替换实现而不动调用方。

### SSE 事件契约

事件 discriminated union 定义见
[frontend/shared/api-types.ts::ChatEvent](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts#L15-L65)，
后端构造器 [utils/sse_events.py::make_sse_event](file:///d:/java/agentprojects/agentx/backend/app/utils/sse_events.py)，
后端流式分发 [api/chat.py::_event_generator](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)，
前端解析 [hooks/useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts)。

| event | data 类型 | 说明 |
|---|---|---|
| `token` | 纯字符串 | 增量 token（visible text，已剥离 `think` 块） |
| `reasoning` | `{content, source}` | 思考过程 chunk（ThinkFilter retain 模式） |
| `tool_call` | `{id, name, args, source}` | 工具调用开始（id 供前端配对 tool_result） |
| `tool_result` | `{id, name, result, source, error?}` | 工具调用结束 |
| `delegation` | `{target, source, message}` | Supervisor / Router 委派标记 |
| `classification` | `{label, reason}` | Router 决策可视化 |
| `todo_update` | `{todos, task_id?}` | DeepAgent 任务级 todo |
| `plan` / `plan_update` | `{plan}` / `{plan}` | 计划与状态更新 |
| `approval_request` | `{thread_id, tool_name, args, preview, kind?, requestedPath?, writable?}` | 危险工具 / 目录越界审批请求 |
| `paused` | `{}` | 用户暂停，等待 resume |
| `team_plan` | `{plan, reasoning}` | AgentTeam Orchestrator 计划 |
| `team_progress` | `{agent, status, message?}` | AgentTeam 子任务状态 |
| `team_result` | `{agent, summary}` | AgentTeam 结果 |
| `team_done` | `{status?}` | AgentTeam 整体结束（在 `done` 之前发出） |
| `done` | `{}` | 流结束 |
| `error` | 错误字符串 | 错误 |

**`source` 字段标识**（reasoning / tool_call / tool_result / delegation 事件携带）：

| `source` 值 | 来源 |
|---|---|
| `"work"` | Work Supervisor |
| `"coding"` | Coding Expert |
| `"rag"` | RAG 子代理 |
| `"web"` | Web 子代理 |

修改任一事件类型或字段名，**必须**同步更新以下四处：

- [backend/app/api/chat.py](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)（事件 yield）
- [backend/app/utils/sse_events.py](file:///d:/java/agentprojects/agentx/backend/app/utils/sse_events.py)（构造器）
- [frontend/shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts)（类型契约）
- [frontend/renderer/hooks/useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts)（解析器）

## 配置

![设置界面](docs/agentx-config.png)

配置通过前端设置面板实时保存，由 Rust 主进程通过 tauri-plugin-store 持久化
（凭证用 `enc:` / `plain:` 前缀格式）并注入后端进程，无需手动编辑 `.env`：

- **模型**：LLM 服务商（OpenAI / DeepSeek / MiniMax / Kimi / GLM / 自定义）、API Key、激活模型（支持多模型切换）
- **记忆**：系统提示词、上下文窗口（消息数 / token 上限）、技能、画像、会话
- **技能**：Markdown 技能文件管理（消息中 `@skill:<name>` 触发注入）
- **MCP**：MCP server 配置与工具发现（支持 stdio / sse / streamable_http 三种传输）
- **子代理**：基础子代理（rag / web）开关与参数、AgentTeam 7 角色、自定义子代理
- **工具**：14 项内置工具的启用状态（含 cli_execute 与 git_* 系列）
- **知识库**：Milvus 连接配置 + 嵌入服务（TEI BGE-M3）
- **审批与安全**：自动审批倒计时、审批最长等待、沙箱授权目录、权限模式（`standard` / `full_trust`）

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