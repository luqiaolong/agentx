# claude.md — AgentPy 项目 Claude 工作手册

> 本文件每次会话开始时必须强制阅读（用户规则）。
> 通用 AI 代理规范见 [AGENTS.md](file:///d:/java/agentprojects/agent-py/AGENTS.md)，本文档聚焦
> Claude 在本项目工作时所需的**架构定位 / 关键约定 / 易踩坑点**。

---

## 1. 项目一句话定位

本地优先的个人助理桌面应用：Electron 壳 + React 渲染层 + FastAPI/Python 后端 + LangGraph
多智能体编排，支持工具调用、RAG 检索、危险操作审批、技能/画像记忆。

---

## 2. 技术栈速查（与 AGENTS.md §2 同步）

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

## 3. 三层架构与文件地图

```
agent-py/
├── backend/app/                ← Python 后端
│   ├── main.py                 ← FastAPI 入口（lifespan + 全部 REST + SSE）
│   ├── config.py               ← pydantic-settings，AGENT_PY_* 前缀
│   ├── llm.py                  ← ChatModel 单例
│   ├── router/                 ← 消息分类 + StateGraph
│   │   ├── classifier.py       ← 规则前置 + LLM 分类
│   │   ├── graph.py            ← Router 图 + run_router（主入口）
│   │   └── state.py            ← RouterState TypedDict
│   ├── paths/
│   │   ├── chat_path.py        ← 路径 A：LLM 直答
│   │   ├── tool_path.py        ← 路径 B：单工具 subagent
│   │   └── deep_path.py        ← 路径 C：DeepAgent + interrupt_before 审批
│   ├── subagents/              ← code / rag / web 子代理
│   ├── tools/                  ← filesystem + rag_retrieve
│   ├── memory/                 ← skills / profile / checkpointer
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

## 4. Router 三路径（消息分类 → 路径分发）

`backend/app/router/graph.py::run_router` 是聊天主入口，分类后驱动：

| 分类 | 路径 | 文件 | 典型场景 |
|---|---|---|---|
| `CHAT` | A | [chat_path.py](file:///d:/java/agentprojects/agent-py/backend/app/paths/chat_path.py) | 闲聊、问答、翻译 |
| `SINGLE_TOOL` | B | [tool_path.py](file:///d:/java/agentprojects/agent-py/backend/app/paths/tool_path.py) | 单工具调用（读文件 / 搜索 / 联网） |
| `DEEP_TASK` | C | [deep_path.py](file:///d:/java/agentprojects/agent-py/backend/app/paths/deep_path.py) | 多步规划 + 工具，含**危险工具审批** |

**危险工具**（`DANGEROUS_TOOLS = {"edit_file", "write_file", "shell_exec"}`）**只在路径 C
暴露**，配合 LangGraph `interrupt_before=["tools"]` 触发用户审批；路径 B 子代理**严禁**
直接暴露写工具——这是安全设计的硬约束。

---

## 5. SSE 事件契约（前后端必对齐）

`backend/app/main.py::_event_generator` 与 `frontend/preload/index.ts::streamChat`
共同实现。事件类型（`event` 字段）：

| event | data 类型 | 说明 |
|---|---|---|
| `token` | 纯字符串 | 增量 token（已剥离 <think>...</think>） |
| `todo_update` | JSON `{"todos": [{text, done, args?}]}` | DeepAgent 任务进度 |
| `approval_request` | JSON `{"thread_id","tool_name","args","preview"}` | 危险工具审批请求 |
| `done` | `"{}"` | 流结束 |
| `error` | 错误消息字符串 | 错误 |

> 修改任一事件类型或字段名，**必须**同步更新
> [main.py](file:///d:/java/agentprojects/agent-py/backend/app/main.py#L425-L479)、
> [preload/index.ts](file:///d:/java/agentprojects/agent-py/frontend/preload/index.ts#L44-L111)、
> [useChatStream.ts](file:///d:/java/agentprojects/agent-py/frontend/renderer/hooks/useChatStream.ts) 三处。

---

## 6. 关键约定 / 易踩坑

### 6.1 凭证与配置

- 后端 `Settings` 用 `env_prefix="AGENT_PY_"` + `env_file=None`，**禁止**从 `.env` 读凭证。
- 凭证（LLM key / Milvus user/password）由 Electron Main 从 `electron-store`（safeStorage
  解密）→ 通过 `subprocess.Popen(env=...)` 注入进程环境。
- 修改 `.env.example` 仅是文档用途，**运行时不会生效**。

### 6.2 Electron ↔ 后端进程

- 后端 8123 端口由 `frontend/main/python/spawn.ts` 启动（`uv run python -m app.main`，
  uv 缺失则回退 `python -m app.main`）。
- 崩溃退避：指数 1s/2s/4s 最多 3 次 → `giving_up` 状态由前端遮罩兜底。
- 关闭时 Windows 必须 `taskkill /T /F` 杀整棵进程树（uv→python 父子链），否则
  8123 端口被占用导致下次启动 Errno 10048。**完整的重启 SOP（包含端口探测 + 健康
  验证脚本）见 §6.7。**
- `electron-store` 是 ESM-only，**必须**通过 `externalizeDepsPlugin({ exclude: ['electron-store'] })`
  打进产物，见 [electron.vite.config.ts](file:///d:/java/agentprojects/agent-py/electron.vite.config.ts#L10)。

### 6.3 沙箱与安全

- 文件操作走 [app/utils/security.py](file:///d:/java/agentprojects/agent-py/backend/app/utils/security.py) 的 `get_sandbox()`，未授权目录 → `PathNotAuthorized`。
- 沙箱授权目录通过 `POST /api/sandbox/authorize` 显式开启（renderer 直连 HTTP，**不**走 IPC）。
- 系统关键目录黑名单（Windows / Unix）见 [main/index.ts](file:///d:/java/agentprojects/agent-py/frontend/main/index.ts#L181-L186)。
- `RouterState.authorized_dirs` 随 checkpoint 持久化，实现跨会话恢复。

### 6.4 SSE / 审批流

- 审批状态用模块级 `_pending_approvals: dict[str, bool]` 内存 dict 维护（M2 计划迁移
  checkpoint / Redis）。
- 自动批准：`AGENT_PY_AUTO_APPROVE_AFTER_SECONDS > 0` 时倒计时归零自动 approve。
- `auto_approve_after_seconds=0` → 禁用，等用户操作。
- SSE handler 每轮检查 `_abort_flags[thread_id]`，用户中止立即退出循环。

### 6.5 路径导入循环

- `graph.py` 与 `deep_path.py` 有循环导入风险：`graph.py` 顶层
  `from app.paths.deep_path import run_deep_path`。
- `deep_path.py` 用 `TYPE_CHECKING` 延迟导入 `RouterState`，**禁止**改为运行时导入，
  详见 [deep_path.py:30-34](file:///d:/java/agentprojects/agent-py/backend/app/paths/deep_path.py#L30-L34)。

### 6.6 路由别名（前端）

- `@` → `frontend/renderer`
- `@main` → `frontend/main`
- 见 [electron.vite.config.ts](file:///d:/java/agentprojects/agent-py/electron.vite.config.ts#L32-L36)。

### 6.7 重启前后端（踩坑沉淀）

> 这套流程是 2026-07-04 反复实战出来的，**替代** §7 的原始 `npm run dev` 入口脚本。

- **入口：永远 `npm run dev`**，不要直接 `uv run python -m app.main`——
  后端依赖的 `AGENT_PY_*` 凭证 + 配置由 Electron Main 通过
  [spawn.ts::buildEnv](file:///d:/java/agentprojects/agent-py/frontend/main/python/spawn.ts) 注入，
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
- **`/api/health` 不是存活探针**。该端点 [main.py:259-274](file:///d:/java/agentprojects/agent-py/backend/app/main.py#L259-L274)
  同步串行调 TEI（myserver:8093）+ Milvus（myserver:19530），外部不通就耗时 5s+
  看起来像超时，但它**永远 200 兜底**。要做进程存活检测，用下面 4 个**轻量**端点
  任意一个：
  | 端点 | 用法 |
  |---|---|
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

## 7. 常用命令

### 前端

```bash
npm run dev            # electron-vite 开发模式（同时启动 main + preload + renderer + 后端）
npm run build          # 生产构建
npm run typecheck      # tsc 严格模式（node + web 两套配置）
npm test               # vitest（renderer 单测）
npm run dist:win       # Windows NSIS 安装包
```

> **重启前后端**一律走 `npm run dev`（由 [spawn.ts](file:///d:/java/agentprojects/agent-py/frontend/main/python/spawn.ts) 自动注入凭证 + 配置）。
> 重启前的进程清理、8123 端口探测、健康验证脚本等完整 SOP 见 §6.7。

### 后端

```bash
uv run python -m app.main                              # 启动 FastAPI（8123）
uv run pytest tests/python/unit -m "not integration"   # 单元测试
uv run pytest tests/python/integration -m requires_myserver  # 联调测试
uv run ruff check backend/                              # 风格检查
```

> 单元测试不需要 myserver；`-m requires_myserver` 标记的测试需要 TEI/Milvus 可达。

---

## 8. 配置入口（renderer 改 → electron-store → 重启后端生效）

后端启动时从 `AGENT_PY_SUBAGENTS_CONFIG` / `AGENT_PY_TOOLS_CONFIG` / `AGENT_PY_PROFILE_AUTO_EXTRACT`
读取 JSON 配置，由 `frontend/main/python/spawn.ts::buildEnv` 注入。
**修改后必须重启应用**——后端不监听热更新。

---

## 9. 修改前必读清单（按需查阅）

| 任务 | 先读 |
|---|---|
| 新增 REST 端点 | [backend/app/main.py](file:///d:/java/agentprojects/agent-py/backend/app/main.py) 顶部端点总览 + [AGENTS.md §1.1](file:///d:/java/agentprojects/agent-py/AGENTS.md) |
| 新增/修改 SSE 事件 | §5 + [preload/index.ts](file:///d:/java/agentprojects/agent-py/frontend/preload/index.ts) + [useChatStream.ts](file:///d:/java/agentprojects/agent-py/frontend/renderer/hooks/useChatStream.ts) |
| 新增工具 | [backend/app/tools/](file:///d:/java/agentprojects/agent-py/backend/app/tools/) + `subagents/*_agent.py` + [deep_path.py](file:///d:/java/agentprojects/agent-py/backend/app/paths/deep_path.py)（危险工具**仅**路径 C） |
| 调整分类规则 | [classifier.py](file:///d:/java/agentprojects/agent-py/backend/app/router/classifier.py) 关键词表 + §4 路径分发 |
| 改 Electron IPC | [preload/index.ts](file:///d:/java/agentprojects/agent-py/frontend/preload/index.ts) + [shared/api-types.ts](file:///d:/java/agentprojects/agent-py/frontend/shared/api-types.ts)（双份类型同步） |
| 写 ADR / 提案 | [openspec/changes/archive/](file:///d:/java/agentprojects/agent-py/openspec/changes/archive/) 历史格式参考 |
| 重启前后端 | §6.7（清理两棵树 → `npm run dev` → 健康验证脚本） |

---

## 10. 安全红线（违反必拒）

- ❌ **不要**在 `.env` / 代码 / 日志里出现明文 API key / Milvus password。
- ❌ **不要**把 `write_file` / `edit_file` / `shell_exec` 暴露给路径 B（subagent）。
- ❌ **不要**绕过 `interrupt_before` 审批流让 DeepAgent 直接执行危险工具。
- ❌ **不要**改 `Settings.env_file=None`（会从 `.env` 读凭证 → 部署/打包泄漏）。
- ❌ **不要**改 SSE 事件契约而不更新 preload + useChatStream。

---

## 11. 与 OpenSpec / AGENTS.md 的关系

- **AGENTS.md** = 工程文化层规范（AI 代理通用约束 / 反面清单 / 决策流程）。
- **OpenSpec** = 变更提案流程（proposal / design / tasks / specs）。
- **本文件 (claude.md)** = Claude 工作手册（架构定位 / 关键约定 / 易踩坑）。

三者互不替代：Claude 在写代码前应同时检查本文件与 AGENTS.md §1.1「优先用现成框架」。