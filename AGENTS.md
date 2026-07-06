# AGENTS.md — AI 代理协作规范

> 适用对象：在本项目（`d:\java\agentprojects\agentx`）上工作的所有 AI 代理
> （Qoder、Claude、Codex、Cursor 等）
> 维护者：项目所有者
> 适用范围：本仓库全项目，跨后端 Python、前端 Electron+React、AI 编排三层

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
- 桌面壳：Electron
- UI：React 18 + TypeScript
- 构建：electron-vite
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
| R6 | 自己写桌面应用框架 | Electron + electron-vite + preload IPC | 跨平台、签名、自动更新都已就绪 |
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
桌面壳                       │ Electron + preload + IPC
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
│  LangGraph / DeepAgents / Electron 官方文档   │
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

本地优先的个人助理桌面应用：Electron 壳 + React 渲染层 + FastAPI/Python 后端 + LangGraph
多智能体编排，支持工具调用、RAG 检索、危险操作审批、技能/画像记忆。

---

## 9.5 前端界面概念定义

主界面采用**单窗口会话模式**，左右分栏布局：

| 区域 | 术语 | 说明 |
|---|---|---|
| **左侧** | **会话列表**（Session List / Chat List）| 展示历史会话，以用户首条消息内容作为主标题，UUID 短码弱化展示 |
| **右侧** | **工作区**（Workspace）| 当前选中会话的聊天内容区域，包含消息流、输入框、工具栏 |

> 所有 AI 代理在讨论前端 UI 时，**必须使用上述术语**，避免"左边""右边"等模糊描述。

---

## 10. 技术栈速查（与 §2 同步）

| 层 | 选型 |
|---|---|
| 桌面壳 | Electron 32 + electron-vite 2 |
| 渲染层 | React 18 + TypeScript + Tailwind v4 + zustand |
| 主进程 | TypeScript（Node 22） |
| 后端 | Python ≥ 3.11 + FastAPI + uvicorn |
| AI 编排 | LangGraph `StateGraph` + DeepAgents (`create_react_agent`) |
| 嵌入 | TEI（BGE-M3，部署在 myserver:8093） |
| 向量库 | Milvus（部署在 myserver:19530） |
| 检查点 | LangGraph `SqliteSaver` / `AsyncSqliteSaver` |
| 观测 | LangSmith + Langfuse + loguru |
| 依赖管理 | 前端 npm，后端 uv + pyproject.toml |

---

## 11. 三层架构与文件地图

```
agentx/
├── backend/app/                ← Python 后端
│   ├── main.py                 ← FastAPI 入口（lifespan + 全部 REST + SSE）
│   ├── config.py               ← pydantic-settings，AGENTX_* 前缀
│   ├── llm.py                  ← ChatModel 单例
│   ├── router/                 ← 消息分类 + StateGraph
│   │   ├── classifier.py       ← 规则前置 + LLM 分类
│   │   ├── graph.py            ← Router 图 + run_router（主入口，仅编排）
│   │   └── state.py            ← RouterState TypedDict
│   ├── chat/                   ← 路径 A：LLM 直答
│   │   ├── __init__.py
│   │   └── run.py              ← run_chat_path（ThinkFilter 流式 token）
│   ├── deep/                   ← 路径 C：DeepAgent + interrupt_before 审批
│   │   ├── __init__.py
│   │   └── agent.py            ← run_deep_path / build_deep_agent / wait_for_approval
│   ├── team/                   ← 路径 D：AgentTeam 多代理协作
│   │   ├── __init__.py
│   │   └── orchestrator.py     ← run_team_path（Orchestrator + 并行子代理 + Blackboard）
│   ├── subagents/              ← code / rag / web 子代理 + 路径 B 分发
│   │   ├── code_agent.py       ← code 子代理（ReAct）
│   │   ├── rag_agent.py        ← rag 子代理（ReAct）
│   │   ├── web_agent.py        ← web 子代理（ReAct）
│   │   ├── custom_agent.py     ← 自定义子代理工厂
│   │   └── dispatch.py         ← run_tool_path（路径 B）+ select_subagent + 事件转换
│   ├── tools/                  ← filesystem + rag_retrieve
│   ├── memory/                 ← skills / profile / checkpointer
│   │   ├── profile_extractor.py ← LLM 画像抽取（extract_profile_via_llm）
│   │   ├── profile_store.py    ← 画像存储（upsert_from_llm / build_profile_prompt）
│   │   ├── skills_loader.py    ← 技能加载
│   │   ├── skills_store.py     ← 技能存储
│   │   ├── checkpointer.py     ← LangGraph checkpointer
│   │   ├── context.py          ← 消息截断（trim_messages_with_budget）
│   │   └── sandbox_store.py    ← 授权目录存储
│   ├── vectorstore/            ← Milvus 客户端
│   ├── embedding/              ← TEI 客户端
│   ├── observability/          ← LangSmith + logger
│   └── utils/                  ← security(沙箱) + text(ThinkFilter) + chunks
├── frontend/
│   ├── main/                   ← Electron 主进程（spawn 后端 + IPC）
│   │   ├── index.ts            ← BrowserWindow + startPython + registerIpc
│   │   ├── store.ts            ← electron-store（safeStorage 凭证）
│   │   ├── python/spawn.ts     ← 后端 spawn + 健康握手 + 崩溃退避重试
│   │   └── logger.ts           ← 日志落盘（logs/）
│   ├── preload/index.ts        ← contextBridge 暴露 window.api
│   ├── renderer/               ← React UI（chat/settings/workspace 组件）
│   └── shared/api-types.ts     ← preload/renderer 共享类型
├── tests/python/{unit,integration}/  ← pytest（asyncio_mode=auto）
├── tests/renderer/             ← vitest
├── openspec/changes/           ← OpenSpec 提案存档（archive/）
├── docs/superpowers/specs/     ← 项目设计文档
├── .env.example                ← 配置项文档（后端 MUST NOT 读取，仅文档）
└── AGENTS.md                   ← 全 AI 代理通用规范
```

---

## 12. Router 三路径（消息分类 → 路径分发）

`backend/app/router/graph.py::run_router` 是聊天主入口，分类后驱动：

| 分类 | 路径 | 文件 | 典型场景 |
|---|---|---|---|
| `CHAT` | A | [chat/run.py](file:///d:/java/agentprojects/agentx/backend/app/chat/run.py) | 闲聊、问答、翻译 |
| `SINGLE_TOOL` | B | [subagents/dispatch.py](file:///d:/java/agentprojects/agentx/backend/app/subagents/dispatch.py) | 单工具调用（读文件 / 搜索 / 联网） |
| `DEEP_TASK` | C | [deep/agent.py](file:///d:/java/agentprojects/agentx/backend/app/deep/agent.py) | 多步规划 + 工具，含**危险工具审批** |
| `agent_team` 模式 | D | [team/orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py) | 多代理协作（Orchestrator + 并行子代理 + Blackboard） |

**危险工具**（`DANGEROUS_TOOLS = {"edit_file", "write_file", "shell_exec"}`）**只在路径 C
暴露**，配合 LangGraph `interrupt_before=["tools"]` 触发用户审批；路径 B 子代理**严禁**
直接暴露写工具——这是安全设计的硬约束。

---

## 13. SSE 事件契约（前后端必对齐）

`backend/app/main.py::_event_generator` 与 `frontend/preload/index.ts::streamChat`
共同实现。事件类型（`event` 字段）：

| event | data 类型 | 说明 |
|---|---|---|
| `token` | 纯字符串 | 增量 token（visible text，已剥离 `<think>` 块） |
| `reasoning` | JSON `{"content": str, "source": str}` | 思考过程 chunk（chat-rendering-trace-v2：由 ThinkFilter retain_think 模式从 token 流分离） |
| `tool_call` | JSON `{"id","name","args","source"}` | 工具调用开始（id 供前端配对 tool_result；subagent 用 astream_events v2 run_id） |
| `tool_result` | JSON `{"id","name","result","source","error?"}` | 工具调用结束 |
| `delegation` | JSON `{"target","source","message"}` | 子代理委派标记（路径 B 入口下发） |
| `todo_update` | JSON `{"todos": [{text, done, args?}]}` | DeepAgent 任务进度 |
| `approval_request` | JSON `{"thread_id","tool_name","args","preview"}` | 危险工具审批请求 |
| `team_plan` | JSON `{"plan": [{agent, input, purpose}], "reasoning": str}` | AgentTeam Orchestrator 生成的子任务计划 |
| `team_progress` | JSON `{"agent": str, "status": "running"|"done"|"error", "message?": str}` | AgentTeam 子任务状态变化 |
| `team_result` | JSON `{"agent": str, "summary": str}` | AgentTeam 子任务结果摘要 |
| `team_done` | JSON `{"status": "done"|"error"}` | AgentTeam 整体执行结束（在 `done` 之前发出） |
| `done` | `"{}"` | 流结束 |
| `error` | 错误消息字符串 | 错误 |

> 修改任一事件类型或字段名，**必须**同步更新
> [main.py](file:///d:/java/agentprojects/agentx/backend/app/main.py#L515-L530)、
> [preload/index.ts](file:///d:/java/agentprojects/agentx/frontend/preload/index.ts#L44-L111)、
> [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) 三处。

---

## 14. 关键约定 / 易踩坑

### 14.1 凭证与配置

- 后端 `Settings` 用 `env_prefix="AGENTX_"` + `env_file=None`，**禁止**从 `.env` 读凭证。
- 凭证（LLM key / Milvus user/password）由 Electron Main 从 `electron-store`（safeStorage
  解密）→ 通过 `subprocess.Popen(env=...)` 注入进程环境。
- 修改 `.env.example` 仅是文档用途，**运行时不会生效**。

### 14.2 Electron ↔ 后端进程

- 后端 8123 端口由 `frontend/main/python/spawn.ts` 启动（`uv run python -m app.main`，
  uv 缺失则回退 `python -m app.main`）。
- 崩溃退避：指数 1s/2s/4s 最多 3 次 → `giving_up` 状态由前端遮罩兜底。
- 关闭时 Windows 必须 `taskkill /T /F` 杀整棵进程树（uv→python 父子链），否则
  8123 端口被占用导致下次启动 Errno 10048。**完整的重启 SOP（包含端口探测 + 健康
  验证脚本）见 §14.7。**
- `electron-store` 是 ESM-only，**必须**通过 `externalizeDepsPlugin({ exclude: ['electron-store'] })`
  打进产物，见 [electron.vite.config.ts](file:///d:/java/agentprojects/agentx/electron.vite.config.ts#L10)。

### 14.3 沙箱与安全

- 文件操作走 [app/utils/security.py](file:///d:/java/agentprojects/agentx/backend/app/utils/security.py) 的 `get_sandbox()`，未授权目录 → `PathNotAuthorized`。
- 沙箱授权目录通过 `POST /api/sandbox/authorize` 显式开启（renderer 直连 HTTP，**不**走 IPC）。
- 系统关键目录黑名单（Windows / Unix）见 [main/index.ts](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L181-L186)。
- `RouterState.authorized_dirs` 随 checkpoint 持久化，实现跨会话恢复。

### 14.4 SSE / 审批流

- 审批状态用模块级 `_pending_approvals: dict[str, bool]` 内存 dict 维护（M2 计划迁移
  checkpoint / Redis）。
- 自动批准：`AGENTX_AUTO_APPROVE_AFTER_SECONDS > 0` 时倒计时归零自动 approve。
- `auto_approve_after_seconds=0` → 禁用，等用户操作。
- SSE handler 每轮检查 `_abort_flags[thread_id]`，用户中止立即退出循环。

### 14.5 路径导入循环（已消除）

- 路径重构后 `graph.py` 与路径模块**无循环导入**：
  - `graph.py` 顶层单向 import `app.chat.run` / `app.deep.agent` / `app.subagents.dispatch` / `app.team.orchestrator`。
  - `deep/agent.py` 用 `TYPE_CHECKING` 延迟导入 `RouterState`，**禁止**改为运行时导入。
  - `team/orchestrator.py` 回退路径 A 时在函数内延迟 import `run_chat_path`（保持 lazy）。
- `app.paths` 包已删除，**禁止**重新创建 `backend/app/paths/` 目录。

### 14.6 路由别名（前端）

- `@` → `frontend/renderer`
- `@main` → `frontend/main`
- 见 [electron.vite.config.ts](file:///d:/java/agentprojects/agentx/electron.vite.config.ts#L32-L36)。

### 14.7 重启前后端（踩坑沉淀）

> 这套流程是 2026-07-04 反复实战出来的，**替代** §15 的原始 `npm run dev` 入口脚本。

- **入口：永远 `npm run dev`**，不要直接 `uv run python -m app.main`——
  后端依赖的 `AGENTX_*` 凭证 + 配置由 Electron Main 通过
  [spawn.ts::buildEnv](file:///d:/java/agentprojects/agentx/frontend/main/python/spawn.ts) 注入，
  直接起 uvicorn 会缺 key、缺 Milvus 密码、缺 tools/subagents config。
- **重启前必须两棵树一起端**。常见误区：以为只有 Electron 进程在占端口，结果
  `electron-vite` 退出后**前端 watcher + uv + python** 仍残留。两棵树并行使用：
  ```powershell
  taskkill /T /F /IM electron.exe          # 包含 renderer/preload/main 全家
  # 杀掉所有还活着的 uv / python.exe 后端进程
  Get-Process -Name python,uv -ErrorAction SilentlyContinue | Stop-Process -Force
  # 确认 8123 释放
  netstat -ano | findstr ':8123 '          # 返回空串才算彻底清干净
  ```
  只 `taskkill /F /PID xxxx` 单 PID 不够——uv→python 的父子链不杀干净就 Errno 10048。
- **`/api/health` 不是存活探针**。该端点 [main.py:259-274](file:///d:/java/agentprojects/agentx/backend/app/main.py#L259-L274)
  同步串行调 TEI（myserver:8093）+ Milvus（myserver:19530），外部不通就耗时 5s+
  看起来像超时，但它**永远 200 兜底**。要做进程存活检测，用下面 4 个**轻量**端点
  任意一个：
  | 端点 | 用法 |
  |---|
  | `GET /` | 返回 `{app, version, status}`，零依赖，< 50ms |
  | `GET /api/skills` | 验证技能文件加载链路 |
  | `GET /api/memory/checkpointer` | 验证 SQLite checkpoint |
  | `POST /api/sandbox/authorize` | 顺手验证沙箱授权链路 |
- **时序**：`npm run dev` 后看到 `start electron app...` 后**再等 8-10s** 再探测
  8123，否则会误判。launch 顺序：predev patch-electron-icon → electron-vite SSR
  → main / preload / renderer 构建 → Electron start → spawn.ts 拉 uv → uvicorn 监听。
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
- **重启后看到 `electron-store` 报错 / 多份 Electron 残留**，大概率上一次没
  `taskkill /T /F` 干净的副作用，先按上方"两棵树一起端"重置再启。
- **dev 是长进程**，启动后用 `CheckCommandStatus` 轮询日志观察 `start electron app`
  + uvicorn 监听即可，**不要等进程结束**。

---

## 15. 常用命令

### 前端

```bash
npm run dev            # electron-vite 开发模式（同时启动 main + preload + renderer + 后端）
npm run build          # 生产构建
npm run typecheck      # tsc 严格模式（node + web 两套配置）
npm test               # vitest（renderer 单测）
npm run dist:win       # Windows NSIS 安装包
```

> **重启前后端**一律走 `npm run dev`（由 [spawn.ts](file:///d:/java/agentprojects/agentx/frontend/main/python/spawn.ts) 自动注入凭证 + 配置）。
> 重启前的进程清理、8123 端口探测、健康验证脚本等完整 SOP 见 §14.7。

### 后端

```bash
uv run python -m app.main                              # 启动 FastAPI（8123）
uv run pytest tests/python/unit -m "not integration"   # 单元测试
uv run pytest tests/python/integration -m requires_myserver  # 联调测试
uv run ruff check backend/                              # 风格检查
```

> 单元测试不需要 myserver；`-m requires_myserver` 标记的测试需要 TEI/Milvus 可达。

---

## 16. 配置入口（renderer 改 → electron-store → 热更新即时生效）

后端启动时从 `AGENTX_SUBAGENTS_CONFIG` / `AGENTX_TOOLS_CONFIG` / `AGENTX_PROFILE_AUTO_EXTRACT`
等 `AGENTX_*` 环境变量读取配置，由 `frontend/main/python/spawn.ts::buildEnv` 注入。

**配置变更即时生效**（无需重启后端）：
- Renderer 保存配置 → electron-store → `window.api.app.reloadBackendConfig()` IPC
- Main 进程从 electron-store 读最新配置 → `POST /api/config/reload`
- 后端 `reload_settings()` 清除 `get_settings` 的 `lru_cache` → 后续 `get_chat_model` /
  子代理 / 工具 / 用户画像等运行时立即读取新配置
- MCP 配置变更额外触发 `get_mcp_manager().refresh()` 重连

如遇异常可手动「重启后端」（`window.api.app.restartBackend()`，仅重启 Python 进程，
不重启 Electron 窗口）。全量重启 Electron（`app:restart`）仅用于 ErrorBoundary 渲染错误恢复。

---

## 17. 修改前必读清单（按需查阅）

| 任务 | 先读 |
|---|---|
| 新增 REST 端点 | [backend/app/main.py](file:///d:/java/agentprojects/agentx/backend/app/main.py) 顶部端点总览 + §1.1「优先用现成框架」 |
| 新增/修改 SSE 事件 | §13 + [preload/index.ts](file:///d:/java/agentprojects/agentx/frontend/preload/index.ts) + [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) |
| 新增工具 | [backend/app/tools/](file:///d:/java/agentprojects/agentx/backend/app/tools/) + `subagents/*_agent.py` + [deep/agent.py](file:///d:/java/agentprojects/agentx/backend/app/deep/agent.py)（危险工具**仅**路径 C） |
| 调整分类规则 | [classifier.py](file:///d:/java/agentprojects/agentx/backend/app/router/classifier.py) 关键词表 + §12 路径分发 |
| 改 Electron IPC | [preload/index.ts](file:///d:/java/agentprojects/agentx/frontend/preload/index.ts) + [shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts)（双份类型同步） |
| 写 ADR / 提案 | [openspec/changes/archive/](file:///d:/java/agentprojects/agentx/openspec/changes/archive/) 历史格式参考 |
| 重启前后端 | §14.7（清理两棵树 → `npm run dev` → 健康验证脚本） |

---

## 18. 安全红线（违反必拒）

- ❌ **不要**在 `.env` / 代码 / 日志里出现明文 API key / Milvus password。
- ❌ **不要**把 `write_file` / `edit_file` / `shell_exec` 暴露给路径 B（subagent）。
- ❌ **不要**绕过 `interrupt_before` 审批流让 DeepAgent 直接执行危险工具。
- ❌ **不要**改 `Settings.env_file=None`（会从 `.env` 读凭证 → 部署/打包泄漏）。
- ❌ **不要**改 SSE 事件契约而不更新 preload + useChatStream。

---

## 19. 与 OpenSpec / claude.md 的关系

- **AGENTS.md §1–§8** = 工程文化层规范（AI 代理通用约束 / 反面清单 / 决策流程）。
- **AGENTS.md §9–§18（本节）** = Claude 工作手册（架构定位 / 关键约定 / 易踩坑）。
- **OpenSpec** = 变更提案流程（proposal / design / tasks / specs）。
- **claude.md** = 引用本文件的指针（保留以满足"每次会话强制阅读"的项目规则）。

三者互不替代：Claude 在写代码前应同时检查本节与 §1.1「优先用现成框架」。
