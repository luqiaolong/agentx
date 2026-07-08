# AGENTS.md — AI 代理协作规范

> 适用对象：在本项目（`d:\java\agentprojects\agentx`）上工作的所有 AI 代理
> （Qoder、Claude、Codex、Cursor 等）
> 维护者：项目所有者
> 适用范围：本仓库全项目，跨后端 Python、前端 Tauri+React、AI 编排三层
> 入会话指针：[claude.md](file:///d:/java/agentprojects/agentx/claude.md)（每次会话开始必读）

---

## 1. 核心原则

### 1.1 优先使用成熟框架，避免自己造轮子（最重要）

当一个能力**已经存在成熟、社区维护活跃的框架/库**时，**必须优先使用现成方案**。
只有在确认现有方案确实无法满足需求时，才考虑自研或扩展。

**判断"成熟"的标准**（任一不满足都视为"不成熟"）：
- GitHub Stars ≥ 1k，或被大厂生产环境使用
- 12 个月内仍有发版（非僵尸项目）
- 官方文档完整、有可运行的快速开始

### 1.2 衍生原则

| 编号 | 原则 | 一句话解释 |
|---|---|---|
| P1 | 组合优于重写 | 在现成框架上加 middleware/callback/子类，而不是另起炉灶 |
| P2 | 单一职责 | 一个文件/一个函数只做一件事 |
| P3 | 可观测 | 关键路径必须可被 LangSmith / Langfuse 追踪 |
| P4 | 类型安全 | Python 用 type hints；TypeScript 严格模式 + 路径别名 |
| P5 | 配置外置 | 业务参数走 `.env` / `config.py`，不写死在代码里 |
| P6 | 可测试 | 业务逻辑与 IO 解耦，核心函数可纯函数化测试 |

---

## 2. 已确定的技术栈（禁止随意替换）

### 后端（Python）
- 框架：FastAPI
- AI 编排：LangGraph
- 智能体框架：DeepAgents
- 嵌入服务：TEI（Hugging Face Text Embeddings Inference）
- 检查点：LangGraph SqliteSaver
- 观测：LangSmith + Langfuse
- 依赖管理：uv + pyproject.toml

### 前端
- 桌面壳：Tauri 2.x + Rust 1.77+
- UI：React 18 + TypeScript
- 构建：Vite 5（仅 renderer 单入口）
- 状态管理：zustand

---

## 3. 反面清单（绝对不要做的事）

| # | ❌ 不要做的 | ✅ 应该用的 | 理由 |
|---|---|---|---|
| R1 | 手写 LLM 路由 / 智能体编排循环 | LangGraph `StateGraph` / DeepAgents `create_deep_agent` | 框架已提供检查点、人在回路、断点恢复 |
| R2 | 手写工具调用（ReAct/CoT）循环 | LangGraph `ToolNode` + `@tool` 装饰器 | 错误重试、token 计数、tool_choice 控制很容易错 |
| R3 | 自己写 RAG 检索（chunk + embed + retrieve） | LangChain Retriever + TEI 嵌入服务 | 分块策略、rerank、元数据过滤是工程化重灾区 |
| R4 | 自己写检查点 / 会话持久化 | LangGraph `SqliteSaver` | 序列化、thread_id 隔离、断点恢复由框架处理 |
| R5 | 自己实现 SSE/流式分块协议 | LangChain `astream_events` + FastAPI `StreamingResponse` | 协议细节（heartbeat、reconnect）容易出错 |
| R6 | 自己写桌面应用框架 | Tauri 2.x + `tauri::command` + `invoke()` | 跨平台、签名、自动更新都已就绪 |
| R7 | 自己造状态管理（store）| zustand（已用）| 引入 Redux/MobX 会与现有架构冲突 |
| R8 | 自己写 CSV/JSON 解析 | 标准库 `csv` / `json` / `pathlib` | 处理边界情况（编码、转义）成本高 |
| R9 | 自己写 HTTP 客户端 | `httpx`（已用）| 重试、超时、连接池都现成 |
| R10 | 自己实现 OpenAPI 文档 | FastAPI 的 `pydantic` 模型 + 自动生成 `/docs` | 手动维护文档必然过时 |

---

## 4. 正面例子（项目里"对"的做法）

```text
需求                        │ 用现成框架
────────────────────────────┼──────────────────────────────
智能体对话编排               │ DeepAgents + LangGraph
工具调用                     │ @tool 装饰器 + ToolNode
长期记忆 / 会话恢复          │ LangGraph checkpointer + SQLite
嵌入向量                     │ TEI 客户端（已有 HTTP 模块）
向量检索                     │ LangChain VectorStore
Web API                      │ FastAPI + pydantic
桌面壳                       │ Tauri 2.x + Rust + invoke/listen
前端状态                     │ zustand
文件 IO                      │ pathlib + with 块
HTTP 客户端                  │ httpx
CSV/JSON                     │ 标准库 csv / json
配置                         │ pydantic-settings + .env
观测                         │ LangSmith + Langfuse
测试                         │ pytest + httpx.AsyncClient
```

---

## 5. 决策流程（写新代码前必走 5 步）

```
┌─ Step 1: 问题归类 ───────────────────────────┐
│  这个需求属于"已有框架能解决"还是"框架外"？   │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 2: 查官方文档 ─────────────────────────┐
│  LangGraph / DeepAgents / Tauri 官方文档       │
│  找现成 API / 官方示例 / cookbook              │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 3: 搜项目内现成代码 ────────────────────┐
│  Grep 看看同事/历史 PR 怎么实现的              │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 4: 确认缺口 ───────────────────────────┐
│  仍不满足 → 评估"扩展现成" vs "自研"           │
│  优先扩展（middleware/callback/子类）          │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 5: 记录原因 ───────────────────────────┐
│  在 commit message / PR 描述里写明             │
│  "为什么不用现成 / 为什么必须自研"             │
└──────────────────────────────────────────────┘
```

---

## 6. 例外情况（什么时候可以"自己造"）

### ✅ 允许自研的场景

1. **`backend/pythonlearning/` 下的示例文件**（教学目的，演示原理）
2. **性能瓶颈已被 profiling 明确证实**的微型工具（须有数据支撑）
3. **框架 bug 无 workaround**，且已提交 issue
4. **业务规则极特殊**（如自定义 DSL、特定行业协议），且无法用配置表达

### ❌ 禁止自研的借口

- "我觉得现有方案不够优雅" → 用 1.2 P1，**组合优于重写**
- "框架学习成本高" → 这是必须投入的成本
- "网上有类似代码可以参考" → 网络代码质量不可控
- "想练手 / 想加深理解" → 去做 `pythonlearning/`，不要污染主项目

---

## 7. 违反本规范的处置

- AI 代理在生成代码前**应主动说明** "为什么没用现成框架"
- 评审人**应优先质疑**任何自研部分
- 突破例外清单时，须在 `docs/decisions/ADR-xxxx.md` 写 ADR
  （Architecture Decision Record），并在 PR 链接 ADR

---

## 8. 维护

- 本文件随项目技术栈变化更新，修改需在 commit message 中说明
- 任何 AI 代理如有"应加入反面清单"的新发现，可在 PR 中提出
- 与 OpenSpec 的关系：本规范是 OpenSpec 之外的"工程文化层"约束，
  与 OpenSpec 的 proposal/design/tasks 互不替代

---

# Claude 工作手册

> 本节是 Claude 在本项目工作所需的**架构定位 / 关键约定 / 易踩坑**。
> 工程文化层规范见上文 §1–§8；变更提案走 OpenSpec。
> 通用 AI 代理规范见本文件整体，与 OpenSpec 三者互不替代。

---

## 9. 项目一句话定位

本地优先的个人助理桌面应用：Tauri 2.x 壳 + React 渲染层 + FastAPI/Python 后端 +
LangGraph 多智能体编排，支持工具调用、RAG 检索、危险操作审批、技能/画像记忆、
AgentTeam 多代理协作（Orchestrator + 并行子代理 + Blackboard + Aggregator）。

---

## 9.5 前端界面概念定义

主界面采用**单窗口会话模式**，左右分栏布局；顶部为自定义 title bar。

| 区域 | 术语 | 说明 |
|---|---|---|
| **顶部 title bar（左侧）** | **场景 tab**（Scene Tab） | 位于 Bot icon 旁、`v0.1` badge 之后。**场景**为 UI 维度，取值 `work` / `coding`；点 tab 触发联动修改 `agent_mode`（`work` 强制 mode=work；`coding` 保留原 mode，work→coding 升级为 `coding`）|
| **顶部 title bar（左侧）** | **agent 类型选择器**（位于输入框旁，不在 title bar） | 输入框左下角的 ModeToggle，**agent 类型**为 UI 维度，仅展示当前场景下的选项：work 场景下为 `Work`；coding 场景下为 `Coding Agent` / `Coding Team`（`coding_team_enabled=false` 时隐藏 Team）|
| **左侧** | **会话列表**（Session List / Chat List）| 展示历史会话，以用户首条消息内容作为主标题，UUID 短码弱化展示 |
| **右侧** | **工作区**（Workspace）| 当前选中会话的聊天内容区域，包含消息流、输入框、工具栏 |

> 所有 AI 代理在讨论前端 UI 时，**必须使用上述术语**，避免"左边""右边"等模糊描述。

**场景 vs agent 类型的派生关系**：
- 后端 `agent_mode` 仍为单字段 `work` / `coding` / `coding_team`（见 [shared/api-types.ts::AgentMode](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts#L89)），后端契约零改动。
- 场景 **从 mode 派生**：`scene = mode === "work" ? "work" : "coding"`（见 [stores/scene.ts::getSceneFromMode](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/scene.ts)）。
- 场景 tab 写回 mode 的逻辑见 [stores/scene.ts::applySceneChange](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/scene.ts)。

---

## 10. 技术栈速查（与 §2 同步）

| 层 | 选型 |
|---|---|
| 桌面壳 | Tauri 2.x + Rust 1.77+ |
| 渲染层 | React 18 + TypeScript + Tailwind v4 + zustand |
| 主进程 | Rust（tokio async runtime） |
| 后端 | Python ≥ 3.11 + FastAPI + uvicorn |
| AI 编排 | LangGraph `StateGraph` + DeepAgents 0.6.12 (`create_deep_agent`) |
| 嵌入 | TEI（BGE-M3，部署在 myserver:8093） |
| 向量库 | Milvus（部署在 myserver:19530） |
| 检查点 | LangGraph `SqliteSaver` / `AsyncSqliteSaver` |
| 观测 | LangSmith + Langfuse + loguru |
| 依赖管理 | 前端 npm，Rust cargo，后端 uv + pyproject.toml |

---

## 11. 三层架构与文件地图

```
agentx/
├── backend/app/                ← Python 后端
│   ├── main.py                 ← FastAPI 入口（lifespan + app + 中间件 + register_routes）
│   ├── llm.py                  ← ChatModel 单例
│   ├── api/                    ← REST + SSE 端点（按职责拆分，register_*_routes 注册）
│   │   ├── schemas.py          ← 15 个 Pydantic 请求/响应模型
│   │   ├── health.py           ← / + /api/health
│   │   ├── sandbox.py          ← 沙箱授权 CRUD
│   │   ├── chat.py             ← /api/chat + approve + abort + compact + _event_generator
│   │   ├── memory.py           ← skills/profile/checkpointer CRUD
│   │   ├── mcp.py              ← MCP servers/tools/test/refresh
│   │   ├── skills.py           ← skills list/reload
│   │   ├── workspace.py        ← workspace list
│   │   ├── config_reload.py    ← 配置热重载
│   │   ├── models_test.py      ← 模型连通性测试
│   │   ├── project_config.py   ← /api/project-config/init + /api/project-config（.agentx/ 项目级配置）
│   │   └── __init__.py         ← register_routes(app) 聚合
│   ├── approval/               ← 审批状态解耦（消除 deep → main 反射）
│   │   ├── decision.py         ← ApprovalDecision dataclass
│   │   ├── state.py            ← submit/pop_approval + set/is/clear_abort
│   │   └── __init__.py         ← 聚合导出
│   ├── config/                 ← pydantic-settings 包（替代单文件 config.py）
│   │   ├── settings.py         ← Settings + get_settings + 路径常量
│   │   ├── subagents.py        ← SubagentSettings + _default_subagents + _parse_custom_subagents
│   │   └── prompts/            ← 内置 system prompt + trigger 描述 + tools 常量
│   │       ├── builtin.py      ← code/rag/web 子代理默认值
│   │       └── team.py         ← 7 个团队专家默认值
│   ├── router/                 ← 消息分类 + StateGraph（仅编排，不嵌路径实现）
│   │   ├── classifier.py       ← 规则前置 + LLM 分类
│   │   ├── graph.py            ← Router 图 + run_router（主入口）+ _parse_skill_tag
│   │   └── state.py            ← RouterState TypedDict
│   ├── chat/                   ← 路径 A：LLM 直答
│   │   ├── __init__.py
│   │   └── run.py              ← run_chat_path（ThinkFilter 流式 token）
│   ├── deep/                   ← 路径 C：DeepAgent + interrupt_on 审批（deepagents 0.6+）
│   │   ├── __init__.py
│   │   ├── agent.py            ← run_deep_path / build_deep_agent（主入口，~200 行）
│   │   ├── harness.py          ← deepagents 集成层（create_agent + excluded_tools + interrupt_on + memory= + skills= + backend=）
│   │   ├── tools.py            ← _make_deep_tools + _load_mcp_tools + DANGEROUS_TOOLS
│   │   ├── streaming.py        ← _stream_agent_events
│   │   ├── approval.py         ← _await_approval + wait_for_approval + _make_approval_event
│   │   └── recovery.py         ← _collect_unpaired_tool_call_ids + _to_serializable（消息修复由 PatchToolCallsMiddleware 接管）
│   ├── team/                   ← 路径 D：AgentTeam 多代理协作
│   │   ├── __init__.py
│   │   ├── orchestrator.py     ← run_team_path（主入口，~200 行）
│   │   ├── planner.py          ← _build_orchestrator_prompt + _parse_plan + _validate_task
│   │   ├── scheduler.py        ← _run_subtask + 队列驱动
│   │   ├── blackboard.py       ← Blackboard + TeamPlanTask + TeamSubtaskResult
│   │   └── aggregator.py       ← _run_aggregator + _quality_gate + _should_downgrade_to_single
│   ├── subagents/              ← code / rag / web 子代理 + 路径 B 分发
│   │   ├── base.py             ← make_fs_tools / make_rag_tools / make_web_tools + extract_text
│   │   ├── code_agent.py       ← code 子代理（ReAct）
│   │   ├── rag_agent.py        ← rag 子代理（ReAct）
│   │   ├── web_agent.py        ← web 子代理（ReAct）
│   │   ├── custom_agent.py     ← 自定义子代理工厂
│   │   └── dispatch.py         ← run_tool_path（路径 B）+ select_subagent + 事件转换
│   ├── tools/                  ← filesystem + rag_retrieve
│   ├── memory/                 ← skills / profile / checkpointer / sandbox
│   │   ├── profile_extractor.py ← LLM 画像抽取（extract_profile_via_llm）
│   │   ├── profile_store.py    ← 画像存储（upsert_from_llm / build_profile_prompt）
│   │   ├── skills_loader.py    ← 技能加载（@skill: 标签解析 + /api/skills 端点）
│   │   ├── skills_store.py     ← 技能存储
│   │   ├── checkpointer.py     ← LangGraph checkpointer
│   │   ├── summarizer.py       ← 消息摘要（/compact 手动触发；自动摘要由 SummarizationMiddleware 接管）
│   │   └── sandbox_store.py    ← 授权目录存储
│   ├── project_config/         ← .agentx/ 项目级配置（generator/loader/merger/templates）
│   │   ├── __init__.py         ← 包导出
│   │   ├── templates.py        ← 6 个文件模板（AGENTS.md/mcp.json/subagents.json/tools.json/system_prompt.md/rules/README.md）
│   │   ├── generator.py        ← generate_agentx_dir 幂等生成
│   │   ├── loader.py           ← load_project_config 容错加载
│   │   └── merger.py           ← merge_configs 合并到 Settings 之上
│   ├── vectorstore/            ← Milvus 客户端
│   ├── embedding/              ← TEI 客户端
│   ├── mcp/                    ← MCP 客户端 + 配置
│   ├── observability/          ← LangSmith + logger
│   └── utils/                  ← security(沙箱) + text(ThinkFilter) + chunks + sse_events + prompts + paths
├── frontend/
│   ├── renderer/               ← React UI（chat/settings/workspace 组件）
│   │   ├── lib/
│   │   │   ├── api/            ← Tauri invoke + fetch 模块 + request.ts (apiGet/apiPost/apiPut/apiDelete)
│   │   │   ├── schemas/        ← zod schemas (approval/mcp-server/model-entry/subagent/system-prompt/tools/sandbox/milvus)
│   │   │   ├── format.ts       ← formatTime/formatDate/formatSize
│   │   │   ├── validators.ts   ← KEY_RE/NAME_RE/validateKey/validateName
│   │   │   ├── logger.ts       ← logger.warn/error
│   │   │   ├── errors.ts       ← ApiError/humanizeError
│   │   │   └── subagentConstants.ts ← ALL_TOOLS/emptyToolsConfig
│   │   ├── hooks/
│   │   │   ├── useChatStream.ts  ← SSE 流解析（直连 fetch 8123）
│   │   │   ├── useCrudList.ts    ← 通用 CRUD 列表 hook
│   │   │   ├── useConfigSave.ts  ← 配置保存 hook
│   │   │   ├── usePopover.ts     ← Popover 状态 + 外部点击
│   │   │   └── useModalDialog.ts ← Modal a11y (ESC/焦点/Tab 陷阱)
│   │   ├── components/
│   │   │   ├── ui/             ← 公共组件 (ErrorBanner/ConfirmButton + hooks/)
│   │   │   ├── settings/       ← 拆分为子目录 (model-provider/subagents/mcp/memory)
│   │   │   └── ...
│   │   └── stores/
│   │       ├── chat/           ← chat store 拆分 (index/migrations/quotaStorage/messageOps)
│   │       └── ...             ← zustand 状态 (agentMode/scene/skills/settings)
│   └── shared/api-types.ts     ← renderer/shared 共享类型
├── src-tauri/                  ← Rust 主进程（替代 Electron main + preload）
│   ├── src/
│   │   ├── lib.rs              ← run() 入口 + setup() 启动 Python + 注册命令
│   │   ├── backend/            ← PythonHandle（spawn + 健康握手 + 崩溃退避）+ env 注入
│   │   ├── commands/           ← settings / dialog / shell / window / clipboard / notify / logs / app / git
│   │   ├── store/              ← tauri-plugin-store 封装（enc:/plain: 凭证）+ 自定义子代理存储
│   │   ├── migration/          ← electron-store → tauri-plugin-store 数据迁移
│   │   ├── git/                ← git2 crate 封装
│   │   └── logger/             ← log 落盘
│   ├── Cargo.toml
│   └── tauri.conf.json         ← 窗口/打包/updater 骨架
├── tests/python/{unit,integration}/  ← pytest（asyncio_mode=auto）
├── tests/renderer/             ← vitest
├── scripts/smoke-tauri.ps1     ← Tauri 迁移冒烟脚本
├── openspec/changes/           ← OpenSpec 提案（archive/ 历史）
├── docs/superpowers/specs/     ← 项目设计文档
├── .env.example                ← 配置项文档（后端 MUST NOT 读取，仅文档）
└── AGENTS.md                   ← 全 AI 代理通用规范
```

> **关键重构**：`backend/app/paths/` 包已删除（见
> [openspec/2026-07-06-paths-refactor](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-paths-refactor/proposal.md)），
> 各路径按能力域拆分为 `chat/` / `deep/` / `team/` / `subagents/`。**禁止**重新创建 `backend/app/paths/` 目录。

---

## 12. Router 场景+模式分发

`backend/app/router/graph.py::run_router` 是聊天主入口，按 `agent_mode` 单字段直接分发到对应场景 agent（场景化架构：Supervisor + Expert）：

| `agent_mode` | 场景 | 执行器 | 文件 | 典型场景 |
|---|---|---|---|---|
| `"work"` | Work | `run_work_supervisor()` | [agents/supervisor/work_supervisor.py](file:///d:/java/agentprojects/agentx/backend/app/agents/supervisor/work_supervisor.py) | 全能 Supervisor：自主决策执行或委派 Expert/子代理，含危险工具审批 |
| `"coding"` | Coding | `run_coding_expert()` | [agents/expert/coding.py](file:///d:/java/agentprojects/agentx/backend/app/agents/expert/coding.py) | 单一 Coding Expert：代码任务专家，含 interrupt 审批流 |
| `"coding_team"` | Coding | `run_coding_team()` | [agents/team/coding_team.py](file:///d:/java/agentprojects/agentx/backend/app/agents/team/coding_team.py) | 多代理协作（Orchestrator + 并行 Expert + Blackboard + Aggregator） |

`run_router` 关键步骤（与 [graph.py](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py) 对齐）：

1. 解析 `@skill:<name>` 标记 → 提取 skill content
2. 读取用户画像（`build_profile_prompt`）
3. 从 checkpointer 加载历史 messages
4. 按预算截断历史（`context_max_messages` / `context_max_tokens`）
5. 按 `agent_mode` 直接分发到 `run_work_supervisor` / `run_coding_expert` / `run_coding_team`

> 旧的 CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam 四路径分类已删除（推倒重来，无兼容层）。
> 旧值 `"agent"` / `"agent_team"` 已废弃，前端 store migrate 时重置为 `"work"`。

**前端 UI 场景/模式双层结构**（后端 agent_mode 单字段不变，前端双层展示）：

- **L1 场景**（顶部 title bar tab，[App.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/App.tsx)）：场景为 UI 维度，取值 `work` / `coding`，从 `agent_mode.mode` 派生。点 tab 触发 [applySceneChange](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/scene.ts) 联动修改 mode。
- **L2 agent 类型**（输入框旁的 [ModeToggle](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ModeToggle.tsx)）：仅展示当前场景下的选项（work → `Work`；coding → `Coding Agent` / `Coding Team`），由 [getSceneFromMode](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/scene.ts) 联动 mode。
- 后端 `agent_mode` 契约不变；Renderer 透传该字段给后端（见 [lib/api/chat.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts)）。

> 详细 UI 概念与术语见 §9.5；场景/模式双层交互的动机见
> [openspec/2026-07-08-restore-scenario-mode-separation](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-08-restore-scenario-mode-separation/proposal.md)。

**危险工具审批流**：`FORBIDDEN_SUBAGENT_TOOLS`（write_file / edit_file / cli_execute / git_write 等）
在 Supervisor 和 Coding Expert 中通过 LangGraph `interrupt_before=["tools"]` 触发用户审批；
子代理（rag / web）与自定义子代理**严禁**直接暴露写工具——这是安全设计的硬约束。

**Work Supervisor 委派能力**：
- `delegate_to_expert(expert_name, task, context)` — 委派 Coding Expert
- `delegate_to_subagent(agent_name, task)` — 委派 rag / web 子代理
- `@mention` 语法（`@coding` / `@rag` / `@web`）强制委派，覆盖 LLM 自主决策

**Coding Team 安全约束**：

- 基础子代理：`rag` / `web`（只读 / 安全工具）
- 软件开发专家团：`frontend_dev` / `backend_dev` / `tester` / `architect` / `devops` /
  `ui_designer` / `product_manager`
- `code` 角色映射到 `run_coding_expert`（不依赖 subagents 配置，由 scheduler 直接分发）
- 写/编辑/shell 等危险任务由 Coding Expert 执行并走审批
- 若 Orchestrator 把危险任务误分配给普通子代理，后端会强制改写为 `code` 子任务（映射到 Coding Expert）

---

## 13. SSE 事件契约（前后端必对齐）

`backend/app/api/chat.py::_event_generator` 与
[frontend/renderer/lib/api/chat.ts::send](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts#L36-L111)
+ [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) 共同实现。

聊天相关 REST 端点（除 SSE 外）：

- `POST /api/chat` — SSE 流式聊天，请求体 `ChatRequest`。
- `POST /api/chat/approve` — 提交审批决定。
- `POST /api/chat/abort` — 设置中止标志。
- `POST /api/chat/pause` — 设置暂停标志（请求体同 `AbortRequest`，仅 `thread_id`），返回 `{"ok": True}`。
- `POST /api/chat/resume` — 清除暂停标志（请求体同 `AbortRequest`，仅 `thread_id`），返回 `{"ok": True}`。
- `POST /api/chat/compact` — 压缩会话历史。

`ChatRequest` 新增 `workspace_path` 字段（当前会话绑定的 workspace 绝对路径）。前端不再在消息正文中拼接 `<workspace>` 标签，后端也不再解析该标签；workspace 授权由该字段驱动。

| event | data 类型 | 说明 |
|---|---|---|
| `token` | 纯字符串 | 增量 token（visible text，已剥离 `<think>` 块） |
| `reasoning` | JSON `{"content": str, "source": str}` | 思考过程 chunk（由 ThinkFilter retain_think 模式从 token 流分离） |
| `tool_call` | JSON `{"id","name","args","source"}` | 工具调用开始（id 供前端配对 tool_result；subagent 用 astream_events v2 run_id） |
| `tool_result` | JSON `{"id","name","result","source","error?"}` | 工具调用结束 |
| `delegation` | JSON `{"target","source","message"}` | 子代理委派标记（路径 B 入口下发） |
| `todo_update` | JSON `{"task_id": str, "title": str, "done": bool}` | DeepAgent 任务进度（按 `task_id` 分组） |
| `approval_request` | JSON `{"thread_id","tool_name","args","preview","kind?","requestedPath?","writable?"}` | 危险工具 / 目录越界审批请求 |
| `plan` | JSON `{"plan": [{"id","title","status"}]}` | DeepAgent 结构化任务计划 |
| `plan_update` | JSON `{"id": str, "status": str}` | 计划项状态更新 |
| `paused` | `"{}"` | 用户暂停，SSE 流在下一轮迭代退出并保留状态，等待 `resume` |
| `team_plan` | JSON `{"plan": [{agent, input, purpose}], "reasoning": str}` | AgentTeam Orchestrator 生成的子任务计划 |
| `team_progress` | JSON `{"agent": str, "status": "running"|"done"|"error", "message?": str}` | AgentTeam 子任务状态变化 |
| `team_result` | JSON `{"agent": str, "summary": str}` | AgentTeam 子任务结果摘要 |
| `team_done` | JSON `{"status": "done"|"error"}` | AgentTeam 整体执行结束（在 `done` 之前发出） |
| `done` | `"{}"` | 流结束 |
| `error` | 错误消息字符串 | 错误 |

**`source` 字段标识**（reasoning / tool_call / tool_result / delegation 事件携带）：

| `source` 值 | 来源 | 说明 |
|---|---|---|
| `"work"` | Work Supervisor | 场景化架构下的全能 agent |
| `"coding"` | Coding Expert | 代码任务专家 |
| `"rag"` | RAG 子代理 | 知识库检索子代理 |
| `"web"` | Web 子代理 | 联网搜索子代理 |

> 旧值 `"code"` / `"deep"` / `"agent"` 已删除（推倒重来，无兼容层）。

> 修改任一事件类型或字段名，**必须**同步更新
> [chat.py](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)、
> [lib/api/chat.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts)、
> [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) 三处。

---

## 14. 关键约定 / 易踩坑

### 14.1 凭证与配置

- 后端 `Settings` 用 `env_prefix="AGENTX_"` + `env_file=None`，**禁止**从 `.env` 读凭证。
- 凭证（LLM key / Milvus user/password）由 Rust 主进程从 `tauri-plugin-store`
  （`enc:` / `plain:` 前缀格式）→ 通过 `tokio::process::Command::env()` 注入进程环境。
  注入点在 [src-tauri/src/backend/env.rs::build_env](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs)。
- 修改 `.env.example` 仅是文档用途，**运行时不会生效**。
- 旧 Electron 用户首次启动 Tauri 时，`migration::migrate_electron_store()` 自动迁移
  `%APPDATA%/agentx/config.json` → tauri-plugin-store；`enc:` 加密值无法跨进程解密，
  记录到 `MigrationReport.requires_reinput` 由前端提示用户重新输入。
- Milvus `auth_enabled=False` 时跳过凭证校验（myserver Milvus authorizationEnabled=false），
  见 [config.py::milvus_credentials_configured](file:///d:/java/agentprojects/agentx/backend/app/config.py#L550-L555)。

### 14.2 Tauri ↔ 后端进程

- 后端 8123 端口由 [src-tauri/src/backend/handle.rs::PythonHandle::start](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/handle.rs)
  启动（`uv run python -m app.main`，uv 缺失则回退 `python -m app.main`）。
- 崩溃退避：指数 1s/2s/4s 最多 3 次 → `giving_up` 状态由前端遮罩兜底。
- **dev_mode 持久化 + 切换即重启**：切换 dev_mode 开关时前端先 `setDevMode()` 写 store，
  再 `restartBackend()` 立即以新值 spawn。`wait_for_ready` 保证 mask 不卡（emit Ready / GivingUp）。
  选项自动持久化，下次应用启动也按此值 spawn。dev_mode=true 时跨平台 console 拉起：
  | 平台 | 命令 |
  |---|---|
  | Windows | `powershell -NoExit -Command "Set-Location -LiteralPath <cwd>; uv run python -m app.main"` |
  | macOS   | `osascript -e 'tell application "Terminal" to do script "cd <cwd> && uv run python -m app.main; exec /bin/bash"'` |
  | Linux   | `x-terminal-emulator -e bash -lc "cd <cwd> && uv run python -m app.main; exec bash"`（缺失则回退 gnome-terminal / konsole，全缺失降级 tokio） |
- 关闭时 Windows 必须 `taskkill /T /F` 杀整棵进程树（uv→python 父子链），否则
  8123 端口被占用导致下次启动 Errno 10048。**完整的重启 SOP 见 §14.7**。
- 配置存储统一走 `tauri-plugin-store`（文件 `config.json`），凭证用 `enc:` / `plain:`
  前缀格式，与 electron-store 旧格式兼容以便迁移。

### 14.3 沙箱与安全

- 文件操作走 [app/utils/security.py::get_sandbox](file:///d:/java/agentprojects/agentx/backend/app/utils/security.py)，
  未授权目录 → `PathNotAuthorized`。
- 沙箱授权目录通过 `POST /api/sandbox/authorize` 显式开启（renderer 直连 HTTP，
  **不**走 Tauri invoke）。
- 系统关键目录黑名单（Windows / Unix）在 [src-tauri/src/commands/dialog.rs::save_dropped_file](file:///d:/java/agentprojects/agentx/src-tauri/src/commands/dialog.rs)。
- `RouterState.authorized_dirs` 随 checkpoint 持久化，实现跨会话恢复。
- `sandbox_persistence_enabled=False` 时所有双写降级为内存-only（故障注入 / 调试用）。
- `POST /api/sandbox/revoke` 撤销授权；`GET /api/sandbox/authorized/{thread_id}` 列出已授权目录。

### 14.4 SSE / 审批流

- 审批状态用模块级 `_pending_approvals: dict[str, ApprovalDecision]` 内存 dict 维护
  （M2 计划迁移 checkpoint / Redis）。
- 自动批准：`AGENTX_AUTO_APPROVE_AFTER_SECONDS > 0` 时倒计时归零自动 approve；
  `= 0` 禁用，等用户操作。
- `AGENTX_APPROVAL_MAX_WAIT`（默认 300s）控制单次审批最长等待；`0` = 无限等待。
- SSE handler 每轮检查 `_abort_flags[thread_id]`，用户中止立即退出循环。
- 审批类型 `kind`：`dangerous_tool`（写/编辑/shell）| `directory_extension`
  （路径越界扩展授权，含 `requestedPath` + `writable`）。

### 14.5 路径导入循环（已消除）

`2026-07-06-paths-refactor` 重构后 `graph.py` 与路径模块**无循环导入**：

- `graph.py` 顶层单向 import `app.chat.run` / `app.deep.agent` /
  `app.subagents.dispatch` / `app.team.orchestrator`。
- `deep/agent.py` 用 `TYPE_CHECKING` 延迟导入 `RouterState`，**禁止**改为运行时导入。
- `team/orchestrator.py` 回退路径 A 时在函数内延迟 import `run_chat_path`（保持 lazy）。
- `app.paths` 包已删除，**禁止**重新创建 `backend/app/paths/` 目录。

### 14.6 路由别名（前端）

- `@` → `frontend/renderer`
- 见 [vite.config.ts](file:///d:/java/agentprojects/agentx/vite.config.ts) +
  [tsconfig.web.json](file:///d:/java/agentprojects/agentx/tsconfig.web.json)。

### 14.7 重启前后端（踩坑沉淀）

> 这套流程是 2026-07-04 反复实战出来的。

- **入口：永远 `npm run dev`**（即 `tauri dev`），不要直接 `uv run python -m app.main`——
  后端依赖的 `AGENTX_*` 凭证 + 配置由 Rust 主进程通过
  [backend/env.rs::build_env](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs) 注入，
  直接起 uvicorn 会缺 key、缺 Milvus 密码、缺 tools / subagents config。
- **dev_mode 切换即重启**：在 UI 切换开发模式后，前端自动 `setDevMode()` + `restartBackend()` 立即以新值 spawn。`wait_for_ready` 保证 mask 不卡。详见 §14.2。
- **重启前必须两棵树一起端**。常见误区：以为只有 Tauri 进程在占端口，结果
  `tauri dev` 退出后 **vite watcher + uv + python** 仍残留。两棵树并行使用：
  ```powershell
  taskkill /T /F /IM agentx.exe        # Tauri 主进程 + WebView2 子进程
  Get-Process -Name python,uv -ErrorAction SilentlyContinue | Stop-Process -Force
  netstat -ano | findstr ':8123 '          # 返回空串才算彻底清干净
  ```
  只 `taskkill /F /PID xxxx` 单 PID 不够——uv→python 的父子链不杀干净就 Errno 10048。
- **`/api/health` 不是存活探针**。该端点同步串行调 TEI（myserver:8093）+ Milvus
  （myserver:19530），外部不通就耗时 5s+ 看起来像超时，但它**永远 200 兜底**。
  要做进程存活检测，用下面 4 个**轻量**端点任意一个：
  | 端点 | 用法 |
  |---|
  | `GET /` | 返回 `{app, version, status}`，零依赖，< 50ms |
  | `GET /api/skills` | 验证技能文件加载链路 |
  | `GET /api/memory/checkpointer` | 验证 SQLite checkpoint |
  | `POST /api/sandbox/authorize` | 顺手验证沙箱授权链路 |
- **时序**：`npm run dev` 后看到 `AgentX Tauri shell started` 日志后**再等 8-10s** 再探测
  8123，否则会误判。launch 顺序：vite renderer 构建 → Tauri 主进程启动 →
  `setup()` hook → migration → `PythonHandle::start` 拉 uv → uvicorn 监听。
- **PowerShell inline -Command 的坑**：`$_` 在 `-Command` 字符串里会被序列化替换导致
  解析失败。**探测脚本写 `.ps1` 文件用 `-File` 调用**，不要 inline `Invoke-RestMethod`。
- **健康探测推荐脚本**（一次性，detached 启动后跑一次即可）：
  ```powershell
  # probe.ps1
  $ErrorActionPreference = 'Continue'
  $endpoints = @(
      @{ method='GET';  url='http://127.0.0.1:8123/';                          label='root' },
      @{ method='GET';  url='http://127.0.0.1:8123/api/skills';                label='skills' },
      @{ method='GET';  url='http://127.0.0.1:8123/api/memory/checkpointer';   label='checkpointer' }
  )
  foreach ($e in $endpoints) {
      try { Invoke-RestMethod -Method $e.method -Uri $e.url -TimeoutSec 5 | Out-Null
           Write-Host ("OK  {0}" -f $e.label) }
      catch { Write-Host ("ERR {0}: {1}" -f $e.label, $_.Exception.Message) }
  }
  # powershell -ExecutionPolicy Bypass -File .\probe.ps1  # 用完后删除
  ```
- **重启后看到 8123 端口占用 / 多份 Tauri 残留**，大概率上一次没
  `taskkill /T /F` 干净的副作用，先按上方"两棵树一起端"重置再启。
- **dev 是长进程**，启动后用 `CheckCommandStatus` 轮询日志观察 `AgentX Tauri shell started`
  + uvicorn 监听即可，**不要等进程结束**。

---

## 15. 常用命令

### 前端 / Tauri

```bash
npm run dev            # tauri dev（同时启动 vite renderer + Rust 主进程 + Python 后端）
npm run build          # tauri build（生产构建，生成 NSIS 安装包）
npm run typecheck      # tsc 严格模式（node + web 两套配置）
npm test               # vitest（renderer 单测）
npm run dist:win       # Windows NSIS 安装包（等价于 npm run build）
```

> **重启前后端**一律走 `npm run dev`（由 [backend/env.rs::build_env](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs) 自动注入凭证 + 配置）。
> 重启前的进程清理、8123 端口探测、健康验证脚本等完整 SOP 见 §14.7。
> Rust 单测：`cd src-tauri && cargo test --lib`；冒烟脚本：`pwsh scripts/smoke-tauri.ps1`。

### 后端

```bash
uv run python -m app.main                              # 启动 FastAPI（8123）
uv run pytest tests/python/unit -m "not integration"   # 单元测试
uv run pytest tests/python/integration -m requires_myserver  # 联调测试
uv run ruff check backend/                              # 风格检查
```

> 单元测试不需要 myserver；`-m requires_myserver` 标记的测试需要 TEI / Milvus 可达。

---

## 16. 配置入口（renderer 改 → tauri-plugin-store → 热更新即时生效）

后端启动时从 `AGENTX_SUBAGENTS_CONFIG` / `AGENTX_TOOLS_CONFIG` /
`AGENTX_PROFILE_AUTO_EXTRACT` / `AGENTX_TEAM_SUBAGENTS_CONFIG` /
`AGENTX_CUSTOM_SUBAGENTS_CONFIG` / `AGENTX_MCP_SERVERS_CONFIG` 等 `AGENTX_*` 环境变量读取配置，
由 [src-tauri/src/backend/env.rs::build_env](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs) 注入。

**配置变更即时生效**（无需重启后端）：
- Renderer 保存配置 → `invoke("settings_set_*")` → tauri-plugin-store
  → `invoke("app_reload_backend_config")`
- Rust 主进程从 tauri-plugin-store 读最新配置 → `POST /api/config/reload`
- 后端 `reload_settings()` 清除 `get_settings` 的 `lru_cache` → 后续 `get_chat_model` /
  子代理 / 工具 / 用户画像等运行时立即读取新配置
- MCP 配置变更额外触发 `get_mcp_manager().refresh()` 重连
- 自定义子代理配置变更触发 `app_init_agents_md` 重新写入 AGENTS.md

如遇异常可手动「重启后端」（`invoke("app_restart_backend")`，仅重启 Python 进程，
不重启 Tauri 窗口）。全量重启 Tauri（`invoke("app_restart")`）仅用于
ErrorBoundary 渲染错误恢复。

### 16.1 `.agentx/` 项目级配置（工作区根目录）

类似 Cursor 的 `.cursor/`，AgentX 支持在工作区根目录放置 `.agentx/` 目录承载
**项目级 AI 规则与配置覆盖**。用户在前端选择工作区时，后端通过
`POST /api/project-config/init` **幂等生成**该目录（仅创建缺失文件，已存在的
文件保持不动，保护用户编辑）。读取配置状态走
`GET /api/project-config?path=<ws>&thread_id=<tid>`。

目录结构（由 [backend/app/project_config/templates.py](file:///d:/java/agentprojects/agentx/backend/app/project_config/templates.py) 生成）：

| 文件 | 作用 |
|---|---|
| `.agentx/AGENTS.md` | 项目级 AI 规则（注入到 profile_prompt） |
| `.agentx/mcp.json` | 项目级 MCP servers |
| `.agentx/subagents.json` | 项目级子代理配置覆盖 |
| `.agentx/tools.json` | 项目级工具开关覆盖 |
| `.agentx/system_prompt.md` | 项目级系统提示词（前置到默认提示词之前） |
| `.agentx/rules/*.md` | 项目级规则文件（最多 10 个，每个最大 4KB，注入到 profile_prompt） |

合并策略（[backend/app/project_config/merger.py](file:///d:/java/agentprojects/agentx/backend/app/project_config/merger.py) `merge_configs`，在全局 `Settings` 之上叠加）：

- **MCP servers**：追加去重（项目优先，按 name 去重）
- **subagents**：深合并（项目字段覆盖全局同名字代理）
- **tools**：覆盖模式（项目配置整体覆盖全局工具开关）
- **system_prompt**：前置模式（项目提示词拼接到默认提示词之前）
- **AGENTS.md + rules**：注入到 profile_prompt
- **凭证**：全局独占（安全红线，**不**进项目配置、**不**合并）

配置变更**下次发送消息即生效**（无需重启后端）：每次发起会话时 `load_project_config`
容错读取最新文件内容，合并后注入该会话的运行时配置。

> **当前限制**：由于下游 agent 内部硬编码 `get_settings()`，MCP / subagents / tools
> 的合并在 router 层暂未接入下游，**仅 `system_prompt` + `AGENTS.md` + `rules`
> 注入生效**。MCP / subagents / tools 合并代码已就绪，待后续下游 agent 支持
> `merge_configs` 结果后即可启用。

---

## 17. 修改前必读清单（按需查阅）

| 任务 | 先读 |
|---|---|
| 新增 REST 端点 | [backend/app/main.py](file:///d:/java/agentprojects/agentx/backend/app/main.py) 顶部端点总览 + §1.1「优先用现成框架」 |
| 新增 / 修改 SSE 事件 | §13 + [lib/api/chat.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts) + [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) |
| 新增工具 | [backend/app/tools/](file:///d:/java/agentprojects/agentx/backend/app/tools/) + `subagents/*_agent.py` + [deep/agent.py](file:///d:/java/agentprojects/agentx/backend/app/deep/agent.py)（危险工具**仅**路径 C） |
| 调整分类规则 | [classifier.py](file:///d:/java/agentprojects/agentx/backend/app/router/classifier.py) 关键词表 + §12 路径分发 |
| 新增 Tauri command | [src-tauri/src/commands/](file:///d:/java/agentprojects/agentx/src-tauri/src/commands/) + [lib.rs](file:///d:/java/agentprojects/agentx/src-tauri/src/lib.rs) `invoke_handler!` 注册 + [shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts) 类型同步 |
| 调整路径实现 | [openspec/2026-07-06-paths-refactor](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-paths-refactor/proposal.md) + §14.5（不要重新引入 paths/ 包） |
| 调整 AgentTeam | [team/orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py) + [openspec/2026-07-06-agent-team](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-agent-team/proposal.md) |
| 写 ADR / 提案 | [openspec/changes/archive/](file:///d:/java/agentprojects/agentx/openspec/changes/archive/) 历史格式参考 |
| 重启前后端 | §14.7（清理两棵树 → `npm run dev` → 健康验证脚本） |
| 修改项目配置 | [backend/app/project_config/](file:///d:/java/agentprojects/agentx/backend/app/project_config/) + §16.1 `.agentx/` 项目级配置 |

---

## 18. 安全红线（违反必拒）

- ❌ **不要**在 `.env` / 代码 / 日志里出现明文 API key / Milvus password。
- ❌ **不要**把 `write_file` / `edit_file` / `shell_exec` 暴露给路径 B（subagent）或自定义子代理。
- ❌ **不要**绕过 `interrupt_before` 审批流让 DeepAgent 直接执行危险工具。
- ❌ **不要**改 `Settings.env_file=None`（会从 `.env` 读凭证 → 部署 / 打包泄漏）。
- ❌ **不要**改 SSE 事件契约而不更新 lib/api/chat.ts + useChatStream。
- ❌ **不要**让 AgentTeam 普通子代理执行危险任务；必须强制改写为 `deep` 子任务。

---

## 19. 与 OpenSpec / claude.md 的关系

- **AGENTS.md §1–§8** = 工程文化层规范（AI 代理通用约束 / 反面清单 / 决策流程）。
- **AGENTS.md §9–§18（本节）** = Claude 工作手册（架构定位 / 关键约定 / 易踩坑）。
- **OpenSpec** = 变更提案流程（proposal / design / tasks / specs）。
- **claude.md** = 引用本文件的指针（保留以满足"每次会话强制阅读"的项目规则）。

三者互不替代：Claude 在写代码前应同时检查本节与 §1.1「优先用现成框架」。