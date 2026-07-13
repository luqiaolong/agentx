# AGENTS.md — AI 代理协作规范

> 适用对象：在本项目（`d:\java\agentprojects\agentx`）上工作的所有 AI 代理
> （Qoder、Claude、Codex、Cursor 等）
> 维护者：项目所有者
> 适用范围：本仓库全项目，跨后端 Python、前端 Tauri+React、AI 编排三层
> 入会话指针：[claude.md](file:///d:/java/agentprojects/agentx/claude.md)（每次会话开始必读）

> **2026-07-09 拆分**：本文档原 681 行 / 64KB 体积过大，按主题拆分：
> - **强约束**（agent 上下文必读）→ [`.agentx/rules/`](file:///d:/java/agentprojects/agentx/.agentx/rules/)（deepagents memory= 自动加载；`.agentx/` 目录不进 git，由 `workspace/config/generator.py` 动态生成）
> - **查阅型文档** → [`docs/agents/`](file:///d:/java/agentprojects/agentx/docs/agents/)（人工查阅，随 git 同步）
> - 本文件保留**核心原则 + 工作手册索引**，预计压缩至 ~350 行。

---

## 1. 核心原则

### 1.1 优先使用成熟框架，避免自己造轮子（最重要）

当一个能力**已经存在成熟、社区维护活跃的框架/库**时，**必须优先使用现成方案**。
只有在确认现有方案确实无法满足需求时，才考虑自研或扩展。

**核心原则：DeepAgents 优先**——能用 DeepAgents 现成 API 解决的，绝不绕过它直接调用
LangGraph/LangChain 底层；DeepAgents 不覆盖的能力，才退化到 LangGraph / LangChain；这三层都
覆盖不到的，才允许扩展或自研。

**本项目核心框架优先序**（按优先级降序，写代码前必须逐层检查）：

| 优先级 | 框架/库 | 适用场景 | 必须使用的最新特性 |
|---|---|---|---|
| **P0** | **DeepAgents** | 高层智能体封装（**首选入口**） | `create_deep_agent`、`create_react_agent`、内置 `TodoListMiddleware` / `FilesystemMiddleware` / `SubAgentMiddleware`、内置工具绑定、子代理委派、规划与回写 |
| P0 | **LangGraph** | DeepAgents 未覆盖的复杂编排 | `StateGraph`、`interrupt_before`/`interrupt_after`、人在回路、`SqliteSaver`/`AsyncSqliteSaver` 检查点、流式 `astream_events` |
| P0 | **LangChain** | LLM 调用、链式组合、RAG、工具定义、消息处理 | `@tool` 装饰器、`ToolNode`、`BaseTool`、`Runnable` 接口、`ChatPromptTemplate`、`trim_messages` / `filter_messages`、`with_structured_output` |
| P1 | **FastAPI** | Web API、SSE 流式响应 | `StreamingResponse`、`Depends`、自动 OpenAPI 生成 |
| P1 | **Pydantic** | 数据校验、配置管理、API 模型 | `BaseModel`、`pydantic-settings`、`Field` 校验 |
| P1 | **Tauri 2.x** | 桌面壳、进程管理、安全通信 | `tauri::command`、`invoke()`/`listen()`、`tauri-plugin-store` |
| P2 | **React 18 + zustand** | 前端 UI、状态管理 | 函数组件、hooks、zustand 原子化状态 |

> **关键原则**：LangChain 生态（DeepAgents + LangGraph + LangChain Core）已覆盖 90%+ 的
> AI 编排需求，**DeepAgents 是首选入口**，LangGraph / LangChain 仅在 DeepAgents 显式不支持时
> 退化使用。**写任何 agent 相关代码前必须先查阅 DeepAgents 官方文档确认是否有现成 API**；
> 确认不支持后才退化到 LangGraph / LangChain。禁止因"学习成本高"或"觉得不够优雅"
> 而绕过 DeepAgents 直接调底层，更禁止自研。

**判断"成熟"的标准**（任一不满足都视为"不成熟"）：

- GitHub Stars ≥ 1k，或被大厂生产环境使用
- 12 个月内仍有发版（非僵尸项目）
- 官方文档完整、有可运行的快速开始

**LangChain 生态官方文档必查入口**（写新代码前按此顺序查阅，**DeepAgents 第一**）：

1. [DeepAgents 文档](https://deepagents.readthedocs.io/) — **首选**：高层 agent 封装、ReAct、内置 middleware、子代理委派、规划与回写
2. [LangGraph 文档](https://langchain-ai.github.io/langgraph/) — DeepAgents 不支持时退化：状态图、检查点、流式、人在回路
3. [LangChain 文档](https://python.langchain.com/) — 模型、提示词、工具、RAG、检索器、消息处理
4. [LangChain API Reference](https://api.python.langchain.com/) — 精确类/方法签名

### 1.2 衍生原则

| 编号 | 原则 | 一句话解释 |
|---|---|---|
| P1 | 组合优于重写 | 在现成框架上加 middleware/callback/子类，而不是另起炉灶 |
| P2 | 单一职责 | 一个文件/一个函数只做一件事 |
| P3 | 可观测 | 关键路径必须可被 LangSmith / Langfuse 追踪 |
| P4 | 类型安全 | Python 用 type hints；TypeScript 严格模式 + 路径别名 |
| P5 | 配置外置 | 业务参数走 `.env` / `config.py`，不写死在代码里 |
| P6 | 可测试 | 业务逻辑与 IO 解耦，核心函数可纯函数化测试 |
| P7 | DeepAgents 优先 | 优先使用 DeepAgents 最新稳定 API；DeepAgents 不支持时退化到 LangGraph / LangChain；禁止兼容旧版本或自研替代 |
| P8 | 需求澄清优先 | 当用户意图存在歧义、需求模糊或关键信息缺失时，AI 代理应主动暂停执行，通过弹窗/对话框向用户澄清，而非擅自猜测或按默认假设继续 | 避免

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
| **R0** | **DeepAgents 能直接解决却绕过，自行调 LangGraph / LangChain 底层** | **先看 DeepAgents `create_deep_agent` / `create_react_agent` / 内置 middleware 能否直接覆盖；不支持才退化** | **DeepAgents 是 P0 首选入口；绕过它直调底层等于绕过项目最高优先级的统一封装** |
| R1 | 手写 LLM 路由 / 智能体编排循环 | DeepAgents `create_deep_agent` / `create_react_agent`（首选）→ LangGraph `StateGraph`（退化）| 框架已提供检查点、人在回路、断点恢复 |
| R2 | 手写工具调用（ReAct/CoT）循环 | DeepAgents `create_react_agent`（首选）→ LangGraph `ToolNode` + `@tool` 装饰器（退化）| 错误重试、token 计数、tool_choice 控制很容易错 |
| R3 | 自己写 RAG 检索（chunk + embed + retrieve） | DeepAgents 内置 RAG 能力（首选）→ LangChain Retriever + TEI 嵌入服务（退化）| 分块策略、rerank、元数据过滤是工程化重灾区 |
| R4 | 自己写检查点 / 会话持久化 | DeepAgents 自带 checkpoint 集成（首选）→ LangGraph `SqliteSaver`（退化）| 序列化、thread_id 隔离、断点恢复由框架处理 |
| R5 | 自己实现 SSE/流式分块协议 | LangChain `astream_events` + FastAPI `StreamingResponse` | 协议细节（heartbeat、reconnect）容易出错 |
| R6 | 自己写桌面应用框架 | Tauri 2.x + `tauri::command` + `invoke()` | 跨平台、签名、自动更新都已就绪 |
| R7 | 自己造状态管理（store）| zustand（已用）| 引入 Redux/MobX 会与现有架构冲突 |
| R8 | 自己写 CSV/JSON 解析 | 标准库 `csv` / `json` / `pathlib` | 处理边界情况（编码、转义）成本高 |
| R9 | 自己写 HTTP 客户端 | `httpx`（已用）| 重试、超时、连接池都现成 |
| R10 | 自己实现 OpenAPI 文档 | FastAPI 的 `pydantic` 模型 + 自动生成 `/docs` | 手动维护文档必然过时 |
| R11 | 手写消息历史管理 / 上下文截断 | LangChain `trim_messages` / `filter_messages` + `ChatPromptTemplate` | 消息截断、token 计数、角色过滤极易出错 |
| R12 | 手写 LLM 输出解析（JSON/结构化）| LangChain `with_structured_output` / `PydanticOutputParser` / `JsonOutputParser` | 模型输出格式不稳定，解析容错需大量 edge case 处理 |
| R13 | 手写并行工具调用编排 | DeepAgents 内置并行（首选）→ LangGraph `StateGraph` 并行分支 + `ToolNode` 批量执行（退化）| 并发控制、错误隔离、结果聚合由框架处理 |
| R14 | 手写记忆 / 画像存储层 | DeepAgents 内置 memory / LangGraph `checkpointer`（首选）→ `SqliteSaver` / `PostgresSaver`（退化）| 序列化、thread 隔离、时间旅行已内置 |
| R15 | 手写 prompt 模板拼接 | LangChain `ChatPromptTemplate` / `MessagesPlaceholder` / `PipelinePromptTemplate` | 变量注入、消息角色、条件渲染有成熟方案 |
| R16 | 手写 embedding / 向量检索客户端 | LangChain `Embeddings` 接口 + `VectorStore` 抽象（Milvus/TEI）| 批量嵌入、索引管理、查询参数由驱动处理 |
| R17 | 手写 LangGraph 旧版兼容代码 | 使用最新稳定版 API（如 `astream_events` v2、`interrupt` 语义）| 旧版 API 已废弃，维护成本极高 |
| R18 | 自研 trace 协议 / 自研 checkpoint 序列化 | LangChain `BaseCallbackHandler` + LangSmith SDK（`from langsmith import trace`） + LangGraph `SqliteSaver` | 已提供完整的钩子、remotability、序列化、断点恢复；自研协议会与 LangChain 生态脱节 |
| R19 | 子代理委派不走 DeepAgents `SubAgentMiddleware`，自实现调度 | DeepAgents 内置 `SubAgentMiddleware` + 子代理声明 | 子代理隔离、上下文传递、错误传播由 DeepAgents 处理 |

---

## 4. 正面例子（项目里"对"的做法）

```text
需求                              │ 首选（DeepAgents）                │ 退化（LangGraph / LangChain）
──────────────────────────────────┼──────────────────────────────────┼────────────────────────────────────────────
智能体对话编排                     │ DeepAgents `create_deep_agent`   │ LangGraph `StateGraph`
ReAct 循环                         │ DeepAgents `create_react_agent`  │ LangGraph `ToolNode` + `@tool`
工具调用                           │ DeepAgents 内置工具绑定          │ `@tool` 装饰器 + `ToolNode` + `BindToolsMixin`
子代理委派                         │ DeepAgents `SubAgentMiddleware`  │ LangGraph `StateGraph` 子图 + `Send` 语法
规划与回写（TodoList）             │ DeepAgents `TodoListMiddleware`  │ LangGraph 状态字段 + 工具实现
文件系统隔离（虚拟 FS）            │ DeepAgents `FilesystemMiddleware`│ LangChain `ReadFile`/`WriteFile` 自封装
状态图工作流                       │ DeepAgents 嵌套编排              │ LangGraph `StateGraph` + `add_node` / `add_edge` / `add_conditional_edges`
人在回路 / 审批中断                │ DeepAgents 内置 interrupt hook   │ LangGraph `interrupt_before=["tools"]` + `Command(resume=...)`
检查点 / 会话持久化                │ DeepAgents 自带 checkpointer 集成│ LangGraph `SqliteSaver` / `AsyncSqliteSaver` + `checkpointer` 参数
长期记忆 / 画像存储                │ DeepAgents 内置 memory           │ LangGraph checkpointer + `InMemorySaver` / `PostgresSaver`
并行子任务编排                     │ DeepAgents 内置并行              │ LangGraph `StateGraph` 并行分支 + `Send` 语法
RAG 检索                           │ DeepAgents 内置 RAG              │ LangChain `Retriever` + `VectorStore`（Milvus）+ TEI 嵌入
嵌入向量                           │ DeepAgents 内置 Embeddings 集成  │ LangChain `Embeddings` 接口 + TEI 客户端
流式事件                           │ DeepAgents `astream`            │ LangChain `astream_events` (v2) + FastAPI `StreamingResponse`
消息历史截断                       │ DeepAgents 上下文管理            │ LangChain `trim_messages` / `filter_messages` + `MessagesPlaceholder`
结构化输出                         │ DeepAgents `response_format`     │ LangChain `with_structured_output` / `PydanticOutputParser`
Prompt 模板                        │ DeepAgents `system_prompt` 参数  │ LangChain `ChatPromptTemplate` / `MessagesPlaceholder` / `HumanMessagePromptTemplate`
Web API                            │ —                                │ FastAPI + pydantic
桌面壳                             │ —                                │ Tauri 2.x + Rust + `invoke()` / `listen()`
前端状态                           │ —                                │ zustand
文件 IO                            │ —                                │ pathlib + with 块
HTTP 客户端                        │ —                                │ httpx
CSV/JSON                           │ —                                │ 标准库 csv / json
配置                               │ —                                │ pydantic-settings + .env
观测                               │ —                                │ LangSmith + Langfuse + loguru
测试                               │ —                                │ pytest + httpx.AsyncClient
```

---

## 5. 决策流程（写新代码前必走 5 步）

```
┌─ Step 1: 问题归类（按 DeepAgents 优先序定位）─────────┐
│  这个需求属于"已有框架能解决"还是"框架外"？           │
│  优先级：DeepAgents → LangGraph → LangChain → 自研    │
│  先对照 §1.1 核心框架优先序表确认归属层级。            │
└────────────────────┬───────────────────────────────────┘
                     ▼
┌─ Step 2: 查官方文档（最新稳定版，按优先序）────────────┐
│  ① DeepAgents 文档 → ② LangGraph 文档 → ③ LangChain  │
│  找现成 API / 官方示例 / cookbook / migration          │
│  特别注意：是否已有新版 API 替代旧实现？               │
│  （默认动作：DeepAgents 不支持才退化到下一层）         │
└────────────────────┬───────────────────────────────────┘
                     ▼
┌─ Step 3: 搜项目内现成代码 ──────────────────────────────┐
│  Grep 看看同事/历史 PR 怎么实现的                        │
│  优先复用已验证的模式，避免重复造轮子。                  │
└────────────────────┬─────────────────────────────────────┘
                     ▼
┌─ Step 4: 确认缺口 ──────────────────────────────────────┐
│  三层都覆盖不到 → 评估"扩展现成" vs "自研"               │
│  优先扩展（middleware / callback / 子类 / 适配器）       │
│  禁止因"学习成本高"而绕过 DeepAgents。                   │
└────────────────────┬─────────────────────────────────────┘
                     ▼
┌─ Step 5: 记录原因 ──────────────────────────────────────┐
│  在 commit message / PR 描述里写明                       │
│  "为什么 DeepAgents 不行 / 为什么退化到 LangGraph /       │
│   为什么必须自研"                                        │
│  若突破 §3 反面清单，须写 ADR 文档。                     │
└─────────────────────────────────────────────────────────┘
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

- AI 代理在生成代码前**应主动说明** "为什么没用 DeepAgents、退化到 LangGraph / LangChain 的原因"
- 评审人**应优先质疑**任何自研部分，以及**绕过 DeepAgents 直调底层**的写法（违反 §3 R0）
- 突破例外清单时，须在 `docs/decisions/ADR-xxxx.md` 写 ADR
  （Architecture Decision Record），并在 PR 链接 ADR

---

## 8. 维护

### 8.1 拆分结构（2026-07-09）

| 拆分章节 | 拆到哪里 | 加载方式 |
|---|---|---|
| §9.5 前端命名规范 | [`docs/agents/05-frontend-naming.md`](file:///d:/java/agentprojects/agentx/docs/agents/05-frontend-naming.md) | 人工查阅（强约束） |
| §11 三层架构文件地图 | [`docs/agents/01-architecture-file-map.md`](file:///d:/java/agentprojects/agentx/docs/agents/01-architecture-file-map.md) | 人工查阅 |
| §13 SSE 事件契约 | [`docs/agents/02-sse-event-contract.md`](file:///d:/java/agentprojects/agentx/docs/agents/02-sse-event-contract.md) | 人工查阅 |
| §14.1–§14.6 关键约定 | [`docs/agents/03-key-conventions.md`](file:///d:/java/agentprojects/agentx/docs/agents/03-key-conventions.md) | 人工查阅 |
| §14.7 启动 / 重启 SOP | [`docs/agents/04-restart-sop.md`](file:///d:/java/agentprojects/agentx/docs/agents/04-restart-sop.md) | 人工查阅 |

完整索引见 [`docs/agents/README.md`](file:///d:/java/agentprojects/agentx/docs/agents/README.md)。

### 8.2 维护规则

- 本文件随项目技术栈变化更新，修改需在 commit message 中说明
- 拆分出去的子文件变更**同步更新本节索引表**
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

## 9.5 前端主界面模块化命名规范

**强约束**：所有 AI 代理讨论 / 修改 / 评审前端 UI 时**必须**使用规范术语。

**完整内容已拆分至** [`docs/agents/05-frontend-naming.md`](file:///d:/java/agentprojects/agentx/docs/agents/05-frontend-naming.md)。
开发者本地可按需把内容拷贝到 `.agentx/rules/01-frontend-naming.md` 让 deepagents memory=
自动加载到 agent 上下文（`.agentx/` 目录不进 git，由 `workspace/config/generator.py`
动态生成）。

**速查要点**：

- 8 个标准视觉区域：**标题栏 / 侧栏 / 主区域 / 工作面板 / 设置面板 / 浮层 / 日志弹窗 / 通用 UI**
  （禁止使用"左/右/上/下/中"等模糊描述）
- 主区域内部 4 子区：**消息流 / 输入区 / 任务进度 / 消息导航**
- `components/` 顶层目录名 = 视觉区域英文术语（小写、单数）：`titlebar/` `sidebar/` `main/` `workspace/` `settings/` `overlay/` `log/` `ui/`
- 消息片段命名用 `<Role>Part.tsx`（如 `TextPart` / `ToolCallPart`），禁止 `*Card.tsx`
- 场景 tab（`work`/`coding`）与 agent 类型选择器（`Work`/`Coding Agent`/`Coding Team`）是**两层独立 UI 维度**

---

## 10. 技术栈速查（与 §2 同步）

| 层 | 选型 |
|---|---|
| 桌面壳 | Tauri 2.x + Rust 1.77+ |
| 渲染层 | React 18 + TypeScript + Tailwind v4 + zustand |
| 主进程 | Rust（tokio async runtime） |
| 后端 | Python ≥ 3.11 + FastAPI + uvicorn |
| AI 编排 | DeepAgents（首选入口）→ LangGraph `StateGraph`（退化）→ LangChain Core（退化）|
| 嵌入 | TEI（BGE-M3，部署在 myserver:8093） |
| 向量库 | Milvus（部署在 myserver:19530） |
| 检查点 | LangGraph `SqliteSaver` / `AsyncSqliteSaver` |
| 观测 | LangSmith + Langfuse + loguru |
| 依赖管理 | 前端 npm，Rust cargo，后端 uv + pyproject.toml |

---

## 11. 三层架构与文件地图

**完整文件地图已拆分至** [`docs/agents/01-architecture-file-map.md`](file:///d:/java/agentprojects/agentx/docs/agents/01-architecture-file-map.md)。

**关键约束**（写代码前必读）：

- `backend/app/paths/` 包已删除，按能力域拆为各路径（chat / deep / team / subagents 等），**禁止**重新创建 `paths/`
- `sandbox/` + `security/` 是顶级包，与 `deep/` / `team/` / `tools/` 平行
- 前端 `components/` 顶层目录 = 视觉区域英文术语（详见 §9.5）

---

## 12. Router 场景+模式分发

`backend/app/router/graph.py::run_router` 是聊天主入口，按 `agent_mode` 单字段直接分发到对应场景 agent（场景化架构：Supervisor + Expert）：

| `agent_mode` | 场景 | 执行器 | 典型场景 |
|---|---|---|---|
| `"work"` | Work | `run_work_supervisor()` | 全能 Supervisor：自主决策执行或委派 Expert/子代理，含危险工具审批 |
| `"coding"` | Coding | `run_coding_expert()` | 单一 Coding Expert：代码任务专家，含 interrupt 审批流 |
| `"coding_team"` | Coding | `run_coding_team()` | 多代理协作（Orchestrator + 并行 Expert + Blackboard + Aggregator） |

`run_router` 关键步骤：

1. 解析 `@skill:<name>` 标记 → 提取 skill content
2. 读取用户画像（`build_profile_prompt`）
3. 从 checkpointer 加载历史 messages
4. 按预算截断历史（`context_max_messages` / `context_max_tokens`）
5. 按 `agent_mode` 直接分发到 `run_work_supervisor` / `run_coding_expert` / `run_coding_team`

> 旧的 CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam 四路径分类已删除（推倒重来，无兼容层）。
> 旧值 `"agent"` / `"agent_team"` 已废弃，前端 store migrate 时重置为 `"work"`。

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

**完整事件表已拆分至** [`docs/agents/02-sse-event-contract.md`](file:///d:/java/agentprojects/agentx/docs/agents/02-sse-event-contract.md)。

**约束**：修改任一事件类型或字段名，**必须**同步更新
[chat.py](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)、
[lib/api/chat.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts)、
[useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) 三处。

**核心事件速查**：`token` / `reasoning` / `tool_call` / `tool_result` / `delegation` /
`todo_update` / `approval_request` / `paused` / `team_done` / `done` / `error`。
`todo_update` 采用 deepagents 原生 `{content, status}` 三态 schema（pending/in_progress/completed），
payload 可选字段：`task_id`（任务分组标识）、`source`（任务来源 `work`/`coding`/`rag`/`web` 或 Team 子任务角色 `frontend_dev`/`backend_dev` 等）、`parent_task_id`（Team 子任务的父 thread_id，前端据此嵌套渲染）。
`source` 取值：`work` / `coding` / `rag` / `web`（旧值 `code`/`deep`/`agent` 已废弃）。

---

## 14. 关键约定 / 易踩坑

**完整内容已拆分至** [`docs/agents/03-key-conventions.md`](file:///d:/java/agentprojects/agentx/docs/agents/03-key-conventions.md)（§14.1–§14.6）
与 [`docs/agents/04-restart-sop.md`](file:///d:/java/agentprojects/agentx/docs/agents/04-restart-sop.md)（§14.7）。

**核心要点速查**：

- **凭证**：后端 `Settings` 用 `env_prefix="AGENTX_"` + `env_file=None`，**禁止**从 `.env` 读；凭证由 Rust 主进程从 `tauri-plugin-store` 注入
- **Tauri 进程**：崩溃退避 1s/2s/4s → `giving_up`；dev_mode 持久化 + 切换即重启
- **沙箱授权**：文件操作走 `SessionSandbox`，未授权 → `PathNotAuthorized`；`cli_execute` 始终需审批
- **审批流**：`_pending_approvals` 模块级 dict + TTL reaper；`AGENTX_AUTO_APPROVE_AFTER_SECONDS` / `AGENTX_APPROVAL_MAX_WAIT`
- **重启**：必须清理 uv→python 父子链（PowerShell `Get-Process` 按 CommandLine 精准筛选，禁止 `taskkill`）
- **健康探测**：`/api/health` 不是存活探针（永远 200 兜底）；用 `/` 或 `/api/skills` 做轻量检测

---

## 15. 常用命令

### 启停脚本（强约束：以后都直接使用脚本启停）

> ✅ **强约束**：本项目所有 AI 代理 / 开发者 / 自动化脚本 **必须**通过
> [`scripts/`](file:///d:/java/agentprojects/agentx/scripts/) 下的启停脚本操作 AgentX dev session，
> **禁止**手动 `pnpm tauri dev` / `Stop-Process` / `netstat` / `taskkill` / Ctrl+C 直关。
>
> 设计原因（详见 [docs/agents/04-restart-sop.md](file:///d:/java/agentprojects/agentx/docs/agents/04-restart-sop.md) §14.7.8）：
> - 进程精准筛选必须按 CommandLine（裸 `Stop-Process -Name python` 会误杀同机 Hermes 等其他项目）
> - uv→python 父子链不杀干净会引发 Errno 10048，必须多轮清理 + 端口复检
> - 启动入口 `pnpm tauri dev` 必须由脚本封装，禁止直接拼命令

**快捷命令（项目根目录下）**：

| 操作 | 命令 | 说明 |
|---|---|---|
| 启动（前台） | `agentx-start` | 阻塞终端，Ctrl+C 中断；自动等待 8123 listen |
| 启动（后台） | `agentx-start -NoWait` | 立即返回，日志写到 `data/logs/tauri-dev.{log,err}` |
| 启动（含清理） | `agentx-start -Clean` | 先调 stop.ps1 清残留再启动 |
| 停止 | `agentx-stop` | 按白名单 + 黑名单精准清理三棵树（Tauri/Vite/Python） |
| 强制停止 | `agentx-stop -Force` | 跳过优雅等待，立即强杀 |
| 重启 | `agentx-restart` | 等价于 stop + start（前台） |
| 后台重启 | `agentx-restart -NoWait` | 等价于 stop + 后台 start |
| 健康探测 | `agentx-health` | 端口 + 关键端点 + 进程家族探测 |
| 健康探测（延迟） | `agentx-health -Wait 30` | 启动 30s 后再探测 |

**PowerShell 原生调用**（不带 .cmd 封装，等价功能）：

```powershell
pwsh scripts/start.ps1             # 启动
pwsh scripts/start.ps1 -NoWait     # 后台启动
pwsh scripts/stop.ps1              # 停止
pwsh scripts/stop.ps1 -Force       # 强制停止
pwsh scripts/restart.ps1           # 重启
pwsh scripts/health-check.ps1      # 健康探测
```

> 所有脚本支持 `pwsh scripts/<name>.ps1 -?` 查看完整参数；所有参数透传到下层。
>
> `.cmd` 入口是薄封装，直接调对应 `.ps1`，等价功能 + Windows 友好（无需手敲 `pwsh` 前缀）。
> 若 shell alias 冲突想卸载，删除对应 `scripts/agentx-*.cmd` 即可，不影响 .ps1。

### 前端 / Tauri（仅调试场景）

```bash
pnpm tauri dev          # 仅在排查脚本化启动问题时手动验证；生产场景请用 agentx-start
pnpm exec vite dev      # 仅启动前端 Vite（端口 5173，被占用自动递增）
pnpm tauri build        # 生产构建，生成 NSIS 安装包
pnpm typecheck          # tsc 严格模式（node + web 两套配置）
pnpm test               # vitest（renderer 单测）
pnpm dist:win           # Windows NSIS 安装包（等价于 tauri build）
```

> 单独启动场景（仅调试）见 [docs/agents/04-restart-sop.md](file:///d:/java/agentprojects/agentx/docs/agents/04-restart-sop.md) §14.7.2。

### 后端

```bash
uv run python -m app.main                              # 仅调试后端（需自注入 AGENTX_* 凭证）
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

目录结构（由 [backend/app/workspace/config/templates.py](file:///d:/java/agentprojects/agentx/backend/app/workspace/config/templates.py) 生成）：

| 文件 | 作用 |
|---|---|
| `.agentx/AGENTS.md` | 项目级 AI 规则（注入到 profile_prompt） |
| `.agentx/mcp.json` | 项目级 MCP servers |
| `.agentx/subagents.json` | 项目级子代理配置覆盖 |
| `.agentx/tools.json` | 项目级工具开关覆盖 |
| `.agentx/system_prompt.md` | 项目级系统提示词（前置到默认提示词之前） |
| `.agentx/rules/*.md` | 项目级规则文件（最多 10 个，每个最大 4KB，注入到 profile_prompt） |

合并策略（[backend/app/workspace/config/merger.py](file:///d:/java/agentprojects/agentx/backend/app/workspace/config/merger.py) `merge_configs`，在全局 `Settings` 之上叠加）：

- **MCP servers**：追加去重（项目优先，按 name 去重）
- **subagents**：深合并（项目字段覆盖全局同名字代理）
- **tools**：覆盖模式（项目配置整体覆盖全局工具开关）
- **system_prompt**：前置模式（项目提示词拼接到默认提示词之前）
- **AGENTS.md + rules**：注入到 profile_prompt
- **凭证**：全局独占（安全红线，**不**进项目配置、**不**合并）

配置变更**下次发送消息即生效**（无需重启后端）：每次发起会话时 `load_project_config`
容错读取最新文件内容，合并后注入该会话的运行时配置。

---

## 17. 修改前必读清单（按需查阅）

| 任务 | 先读 |
|---|---|
| 新增 REST 端点 | [backend/app/main.py](file:///d:/java/agentprojects/agentx/backend/app/main.py) 顶部端点总览 + §1.1「优先用现成框架」 |
| 新增 / 修改 SSE 事件 | §13 + [`docs/agents/02-sse-event-contract.md`](file:///d:/java/agentprojects/agentx/docs/agents/02-sse-event-contract.md) + [lib/api/chat.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts) + [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) |
| 新增工具 | [backend/app/tools/](file:///d:/java/agentprojects/agentx/backend/app/tools/) + `subagents/*_agent.py` + [deepagent/agent.py](file:///d:/java/agentprojects/agentx/backend/app/deepagent/agent.py)（危险工具**仅**路径 C） |
| 调整分类规则 | [classifier.py](file:///d:/java/agentprojects/agentx/backend/app/router/classifier.py) 关键词表 + §12 路径分发 |
| 新增 Tauri command | [src-tauri/src/commands/](file:///d:/java/agentprojects/agentx/src-tauri/src/commands/) + [lib.rs](file:///d:/java/agentprojects/agentx/src-tauri/src/lib.rs) `invoke_handler!` 注册 + [shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts) 类型同步 |
| 调整路径实现 | `docs/agents/03-key-conventions.md` §14.5（不要重新引入 paths/ 包） |
| 调整 AgentTeam | [team/orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py) + 相关 OpenSpec 提案 |
| 调整沙箱/授权 | [backend/app/sandbox/](file:///d:/java/agentprojects/agentx/backend/app/sandbox/) + [`docs/agents/03-key-conventions.md`](file:///d:/java/agentprojects/agentx/docs/agents/03-key-conventions.md) §14.3 |
| 调整审批/安全策略 | [backend/app/security/](file:///d:/java/agentprojects/agentx/backend/app/security/) + §14.3 + §14.4 |
| 写 ADR / 提案 | [openspec/changes/archive/](file:///d:/java/agentprojects/agentx/openspec/changes/archive/) 历史格式参考 |
| 重启前后端 | [`docs/agents/04-restart-sop.md`](file:///d:/java/agentprojects/agentx/docs/agents/04-restart-sop.md)（清理两棵树 → `pnpm tauri dev` → 健康验证脚本） |
| 修改项目配置 | [backend/app/workspace/](file:///d:/java/agentprojects/agentx/backend/app/workspace/) + §16.1 `.agentx/` 项目级配置 |
| 讨论 / 评审前端 UI | [`docs/agents/05-frontend-naming.md`](file:///d:/java/agentprojects/agentx/docs/agents/05-frontend-naming.md)（强约束术语表） |

---

## 18. 安全红线（违反必拒）

- ❌ **不要**在 `.env` / 代码 / 日志里出现明文 API key / Milvus password。
- ❌ **不要**把 `write_file` / `edit_file` / `cli_execute` 暴露给路径 B（subagent）或自定义子代理。
- ❌ **不要**绕过 `interrupt_before` 审批流让 DeepAgent 直接执行危险工具。
- ❌ **不要**改 `Settings.env_file=None`（会从 `.env` 读凭证 → 部署 / 打包泄漏）。
- ❌ **不要**改 SSE 事件契约而不更新 lib/api/chat.ts + useChatStream。
- ❌ **不要**让 AgentTeam 普通子代理执行危险任务；必须强制改写为 `deep` 子任务。

---

## 19. 与 OpenSpec / claude.md 的关系

- **AGENTS.md §1–§8** = 工程文化层规范（AI 代理通用约束 / 反面清单 / 决策流程）。
- **AGENTS.md §9–§18（本节）** = Claude 工作手册（架构定位 / 关键约定 / 易踩坑）。
- **[`docs/agents/`](file:///d:/java/agentprojects/agentx/docs/agents/)** = 工作手册的离线查阅文档（架构地图 / 契约 / SOP / 前端命名规范）。
- **[`.agentx/rules/`](file:///d:/java/agentprojects/agentx/.agentx/rules/)** = 项目级 AI 上下文目录（**不进 git**，由 `workspace/config/generator.py` 动态生成；开发者可按需从 `docs/agents/` 拷贝内容到此启用 deepagents memory= 自动加载）。
- **OpenSpec** = 变更提案流程（proposal / design / tasks / specs）。
- **claude.md** = 引用本文件的指针（保留以满足"每次会话强制阅读"的项目规则）。

三者互不替代：Claude 在写代码前应同时检查本节与 §1.1「优先用现成框架」。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **agentx** (15128 symbols, 24320 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/agentx/context` | Codebase overview, check index freshness |
| `gitnexus://repo/agentx/clusters` | All functional areas |
| `gitnexus://repo/agentx/processes` | All execution flows |
| `gitnexus://repo/agentx/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
