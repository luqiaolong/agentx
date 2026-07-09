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

**本项目核心框架优先序**（按优先级降序，写代码前必须逐层检查）：

| 优先级 | 框架/库 | 适用场景 | 必须使用的最新特性 |
|---|---|---|---|
| P0 | **LangGraph** | 智能体编排、状态图、工作流 | `StateGraph`、`interrupt_before`/`interrupt_after`、人在回路、`SqliteSaver`/`AsyncSqliteSaver` 检查点、流式 `astream_events` |
| P0 | **DeepAgents** | 高层智能体封装 | `create_react_agent`、`create_deep_agent`、内置工具绑定、审批流集成 |
| P0 | **LangChain** | LLM 调用、链式组合、RAG、工具定义 | `@tool` 装饰器、`ToolNode`、`BaseTool`、`Runnable` 接口、ChatPromptTemplate |
| P1 | **FastAPI** | Web API、SSE 流式响应 | `StreamingResponse`、`Depends`、自动 OpenAPI 生成 |
| P1 | **Pydantic** | 数据校验、配置管理、API 模型 | `BaseModel`、`pydantic-settings`、`Field` 校验 |
| P1 | **Tauri 2.x** | 桌面壳、进程管理、安全通信 | `tauri::command`、`invoke()`/`listen()`、`tauri-plugin-store` |
| P2 | **React 18 + zustand** | 前端 UI、状态管理 | 函数组件、hooks、zustand 原子化状态 |

> **关键原则**：LangChain 生态（LangGraph + DeepAgents + LangChain Core）已覆盖 90%+ 的 AI 编排需求，**写任何 agent 相关代码前必须先查阅官方文档确认是否有现成 API**。禁止因"学习成本高"或"觉得不够优雅"而绕过框架自研。

**判断"成熟"的标准**（任一不满足都视为"不成熟"）：
- GitHub Stars ≥ 1k，或被大厂生产环境使用
- 12 个月内仍有发版（非僵尸项目）
- 官方文档完整、有可运行的快速开始

**LangChain 生态官方文档必查入口**（写新代码前按此顺序查阅）：
1. [LangGraph 文档](https://langchain-ai.github.io/langgraph/) — 状态图、检查点、流式、人在回路
2. [DeepAgents 文档](https://deepagents.readthedocs.io/) — 高层 agent 封装、ReAct、工具绑定
3. [LangChain 文档](https://python.langchain.com/) — 模型、提示词、工具、RAG、检索器
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
| P7 | 框架优先 | 优先使用 LangChain/LangGraph/DeepAgents 最新稳定 API，而非兼容旧版本或自研替代 |

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
| R11 | 手写消息历史管理 / 上下文截断 | LangChain `trim_messages` / `filter_messages` + `ChatPromptTemplate` | 消息截断、token 计数、角色过滤极易出错 |
| R12 | 手写 LLM 输出解析（JSON/结构化）| LangChain `with_structured_output` / `PydanticOutputParser` / `JsonOutputParser` | 模型输出格式不稳定，解析容错需大量 edge case 处理 |
| R13 | 手写并行工具调用编排 | LangGraph `StateGraph` 并行分支 + `ToolNode` 批量执行 | 并发控制、错误隔离、结果聚合由框架处理 |
| R14 | 手写记忆 / 画像存储层 | LangGraph `checkpointer` + `SqliteSaver` / `PostgresSaver` | 序列化、thread 隔离、时间旅行已内置 |
| R15 | 手写 prompt 模板拼接 | LangChain `ChatPromptTemplate` / `MessagesPlaceholder` / `PipelinePromptTemplate` | 变量注入、消息角色、条件渲染有成熟方案 |
| R16 | 手写 embedding / 向量检索客户端 | LangChain `Embeddings` 接口 + `VectorStore` 抽象（Milvus/TEI）| 批量嵌入、索引管理、查询参数由驱动处理 |
| R17 | 手写 LangGraph 旧版兼容代码 | 使用最新稳定版 API（如 `astream_events` v2、`interrupt` 语义）| 旧版 API 已废弃，维护成本极高 |
| R18 | 自研 trace 协议 / 自研 checkpoint 序列化 | LangChain `BaseCallbackHandler` + LangSmith SDK（`from langsmith import trace`） + LangGraph `SqliteSaver` | 已提供完整的钩子、remotability、序列化、断点恢复；自研协议会与 LangChain 生态脱节 |

---

## 4. 正面例子（项目里"对"的做法）

```text
需求                              │ 用现成框架 / API
──────────────────────────────────┼────────────────────────────────────────────
智能体对话编排                     │ DeepAgents `create_react_agent` / `create_deep_agent` + LangGraph `StateGraph`
工具调用                           │ `@tool` 装饰器 + LangGraph `ToolNode` + `BindToolsMixin`
ReAct 循环                         │ DeepAgents `create_react_agent`（内置 ReAct）
状态图工作流                       │ LangGraph `StateGraph` + `add_node` / `add_edge` / `add_conditional_edges`
人在回路 / 审批中断                │ LangGraph `interrupt_before=["tools"]` + `Command(resume=...)`
检查点 / 会话持久化                │ LangGraph `SqliteSaver` / `AsyncSqliteSaver` + `checkpointer` 参数
流式事件                           │ LangChain `astream_events` (v2) + FastAPI `StreamingResponse`
消息历史截断                       │ LangChain `trim_messages` / `filter_messages` + `MessagesPlaceholder`
结构化输出                         │ LangChain `with_structured_output` / `PydanticOutputParser`
Prompt 模板                        │ LangChain `ChatPromptTemplate` / `MessagesPlaceholder` / `HumanMessagePromptTemplate`
RAG 检索                           │ LangChain `Retriever` + `VectorStore`（Milvus）+ TEI 嵌入
嵌入向量                           │ LangChain `Embeddings` 接口 + TEI 客户端
并行子任务编排                     │ LangGraph `StateGraph` 并行分支 + `Send` 语法
长期记忆 / 画像存储                │ LangGraph checkpointer + `InMemorySaver` / `PostgresSaver`
Web API                            │ FastAPI + pydantic
桌面壳                             │ Tauri 2.x + Rust + `invoke()` / `listen()`
前端状态                           │ zustand
文件 IO                            │ pathlib + with 块
HTTP 客户端                        │ httpx
CSV/JSON                           │ 标准库 csv / json
配置                               │ pydantic-settings + .env
观测                               │ LangSmith + Langfuse + loguru
测试                               │ pytest + httpx.AsyncClient
```

---

## 5. 决策流程（写新代码前必走 5 步）

```
┌─ Step 1: 问题归类 ───────────────────────────┐
│  这个需求属于"已有框架能解决"还是"框架外"？   │
│  先对照 §1.1 核心框架优先序表确认归属层级。    │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 2: 查官方文档（最新稳定版）──────────────┐
│  LangGraph / DeepAgents / LangChain 官方文档   │
│  找现成 API / 官方示例 / cookbook / migration  │
│  特别注意：是否已有新版 API 替代旧实现？       │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 3: 搜项目内现成代码 ────────────────────┐
│  Grep 看看同事/历史 PR 怎么实现的              │
│  优先复用已验证的模式，避免重复造轮子。        │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 4: 确认缺口 ───────────────────────────┐
│  仍不满足 → 评估"扩展现成" vs "自研"           │
│  优先扩展（middleware/callback/子类/适配器）   │
│  禁止因"学习成本高"而绕过框架。                │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 5: 记录原因 ───────────────────────────┐
│  在 commit message / PR 描述里写明             │
│  "为什么不用现成 / 为什么必须自研"             │
│  若突破 §3 反面清单，须写 ADR 文档。           │
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

## 9.5 前端主界面模块化命名规范

主界面采用**单窗口会话模式**：顶部自定义标题栏 + 左侧栏 + 中央主区域 + 右侧工作面板四列布局，
另设设置面板、浮层、日志弹窗三类瞬时或独立窗口。本节定义主界面的**视觉区域术语**、
**目录命名约定**、**组件 / 文件命名约定**，所有 AI 代理讨论前端 UI 时**必须使用**。

### 9.5.1 视觉区域术语表（强制）

主界面划分为 8 个标准视觉区域，**所有 AI 代理在讨论前端 UI 时必须使用下表中的中文术语**，
禁止使用"左边/右边/上面/下面/中间"等模糊描述。

| # | 中文术语 | 英文术语 | 位置 | 典型内容 | 备注 |
|---|---|---|---|---|---|
| 1 | **标题栏** | Title Bar | 顶部 40px | 品牌区、场景切换、窗口控制 | 自定义无边框窗口的标题栏，含场景 tab |
| 2 | **侧栏** | Side Bar | 左侧 240px | 会话列表 + 底部入口 | Home / 工作区分组 |
| 3 | **主区域** | Main Area | 中央 flex-1 | 消息流、输入区、任务进度、消息导航 | 也常被叫"聊天区"，但术语规范用"主区域" |
| 4 | **工作面板** | Work Panel | 右侧 288px | 任务 / 文件 / Git 三标签页 | 与 session.workspacePath 无关，仅是 UI 区域名 |
| 5 | **设置面板** | Settings Panel | 全屏浮层（z-50） | 10 个子域设置 tab | 仅打开时存在，左侧 tab 导航 + 右侧内容区 |
| 6 | **浮层** | Overlay | 全屏遮罩（z-40/50） | 审批弹窗、代码查看器、启动 / 失败遮罩 | 与设置面板并列，但属瞬时反馈层 |
| 7 | **日志弹窗** | Log Window | 独立 Tauri 窗口 | 日志控制台 + 工具栏 | `log-window.tsx` 入口，渲染层独立 |
| 8 | **通用 UI** | Common UI | 跨区域复用 | ErrorBoundary、ConfirmDialog、Modal / Popover hooks | 无业务语义，仅做交互与视觉基础 |

主区域（Main Area）内部进一步划分为 4 个**子区域**，同样必须用规范中文术语：

- **消息流**（Message Stream）— 历史消息列表
- **输入区**（Composer）— 文本输入框 + 底部工具栏
- **任务进度**（Task Progress）— TodoProgress 卡片
- **消息导航**（Message Navigator）— 消息流右侧分段导航条

### 9.5.2 顶层目录命名约定

`components/` 下的顶层目录名 = 视觉区域英文术语（小写、英文、单数），与 §9.5.1 一一对应：

```
components/
├── titlebar/        # 标题栏（场景切换、窗口控制）
├── sidebar/         # 侧栏（会话列表、底部入口）
├── main/            # 主区域（含 parts/ 消息片段）
├── workspace/       # 工作面板（任务/文件/Git 标签）
├── settings/        # 设置面板（按子域拆子目录）
├── overlay/         # 浮层（审批、代码查看、启动遮罩）
├── log/             # 日志弹窗
└── ui/              # 通用 UI（无业务语义）
```

**反例（禁止的目录名）**：

- `chat/` — 视觉上不存在"chat 区"；统一用 `main/`
- `code/` — 仅是浮层的一个组件；用 `overlay/CodeViewer.tsx`
- `Workspace` / `Main` / `Sidebar` — PascalCase 不能用作目录名
- 在 `components/` 根目录散落独立组件（`ErrorBoundary.tsx` / `StatusIndicator.tsx`）— 应入 `ui/`

### 9.5.3 组件命名约定

1. **视觉角色作前缀**：`SidebarHeader` 而非 `Header`，`WorkPanelTabs` 而非 `Tabs`
2. **业务对象作后缀**：`MessageItem`、`SessionGroup`、`ComposerToolbar`
3. **文件名 = 默认导出组件名**（PascalCase，一一对应）
4. **一文件一组件**：私有子组件可同文件但**不导出**；非平凡子组件 > 50 行 → 拆文件
5. **避免通用名**：禁止直接命名 `Header` / `Footer` / `Tabs` / `Modal` 等，必须带视觉区域前缀

### 9.5.4 消息片段命名（`main/parts/`）

`main/parts/` 是消息流的"片段渲染层"，命名规则：

- 使用 `<Role>Part.tsx` 形式：`TextPart`、`ToolCallPart`、`ReasoningPart`、`DelegationPart`、
  `ClassificationPart`、`TeamNodePart`
- 共享标题行用 `TraceCardHeader.tsx`（所有片段复用同一视觉规范）
- 禁止用 `<X>Card.tsx` 命名 — 卡片是视觉外观，不是角色；**角色名才是术语**

### 9.5.5 Store / Hook 命名补强

- **域 store**（zustand）：`useXxxStore`（`useChatStore` / `useTasksStore` / `useSettingsStore`），
  文件名同 store 名，存放域状态（消息、会话、任务等）
- **UI 临时态**（picker / popover / 模态）：仍用 `useXxxStore`，文件名以业务对象命名
  （`commands.ts` / `mention.ts`）
- **业务 hook**：`useXxx`，放 `hooks/`
- **UI 内部 hook**（a11y / 焦点陷阱 / 外部点击）：放 `components/ui/hooks/`

### 9.5.6 现状目录偏离清单（迁移参考，不强制）

| 现状目录 / 文件 | 偏离点 | 建议（未来 PR 渐进迁移） |
|---|---|---|
| `components/chat/` | `chat` 不是视觉区域术语 | 重命名为 `components/main/` |
| `components/code/` | `code` 是文件类型不是区域 | 合并到 `components/overlay/CodeViewer.tsx` |
| `components/ErrorBoundary.tsx`、`StatusIndicator.tsx` | 散落根目录 | 移入 `components/ui/` |
| `components/chat/parts/*Card.tsx` | 用 Card 命名片段 | 改用 `*Part.tsx` 命名 |
| `SettingsModal.tsx` 命名 | "弹窗"在术语表里改用"面板" | 文件名逐步重命名为 `SettingsPanel.tsx` |

### 9.5.7 反例术语表

禁止使用以下说法：

- "左边 / 右边 / 上面 / 下面 / 中间"（模糊，违反 §9.5.1）
- "聊天区 / 聊天窗口"（应改为"主区域"）
- "设置弹窗"（应改为"设置面板"，强调是工作区而非提示）
- "右侧工作区"（应改为"工作面板"，"工作区" 在 §10 已被 session.workspacePath 占用）
- 混合 `chat` / `main` 指代同一区域（必须统一为 `main`）
- 把视觉区域目录命名为业务后缀（`agents/` / `tasks/` / `files/` 等）

### 9.5.8 场景 vs agent 类型的派生关系

标题栏（Title Bar）中的**场景 tab** 与主区域输入区（Composer）旁的 **agent 类型选择器**
是两层独立的 UI 维度，必须分别命名：

- **场景**（UI 维度，取值 `work` / `coding`）：位于标题栏，Bot icon 与 `v0.1` badge 之间。
  点击 tab 触发联动修改 `agent_mode`：`work` 强制 mode=work；`coding` 保留原 mode，
  work→coding 升级为 `coding`。
- **agent 类型**（UI 维度）：位于主区域输入区（Composer）左下角 ModeToggle，
  仅展示当前场景下的选项：work 场景下为 `Work`；coding 场景下为 `Coding Agent` / `Coding Team`
  （`coding_team_enabled=false` 时隐藏 Team）。

后端 `agent_mode` 仍为单字段 `work` / `coding` / `coding_team`
（见 [shared/api-types.ts::AgentMode](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts#L89)），
后端契约零改动。
场景从 mode 派生：`scene = mode === "work" ? "work" : "coding"`
（见 [stores/scene.ts::getSceneFromMode](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/scene.ts)）。
场景 tab 写回 mode 的逻辑见
[stores/scene.ts::applySceneChange](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/scene.ts)。

---

## 10. 技术栈速查（与 §2 同步）

| 层 | 选型 |
|---|---|
| 桌面壳 | Tauri 2.x + Rust 1.77+ |
| 渲染层 | React 18 + TypeScript + Tailwind v4 + zustand |
| 主进程 | Rust（tokio async runtime） |
| 后端 | Python ≥ 3.11 + FastAPI + uvicorn |
| AI 编排 | LangGraph `StateGraph` + DeepAgents (`create_react_agent`) |
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
│   │   ├── chat.py             ← /api/chat + approve + abort + compact + _event_generator
│   │   ├── memory.py           ← skills/profile/checkpointer CRUD
│   │   ├── mcp.py              ← MCP servers/tools/test/refresh
│   │   ├── skills.py           ← skills list/reload
│   │   ├── config_reload.py    ← 配置热重载
│   │   ├── models_test.py      ← 模型连通性测试
│   │   └── __init__.py         ← register_routes(app) 聚合
│   ├── sandbox/                ← 沙箱路径授权（与 security/ 平行，独立包）
│   │   ├── path_guard.py       ← 路径归一化 + 关键目录保护（Linux Path('/') bug 已修复）
│   │   ├── store.py            ← SQLite WAL + busy_timeout 持久化
│   │   ├── session_sandbox.py  ← SessionSandbox (async + asyncio.Lock + DB-first + parent_thread_id)
│   │   ├── schemas.py          ← AuthorizeRequest / RevokeRequest
│   │   ├── api.py              ← /api/sandbox/* 路由 + 审计日志
│   │   └── __init__.py         ← 聚合导出
│   ├── security/               ← 安全策略与审批（与 sandbox/ 平行，独立包）
│   │   ├── approval/           ← 审批决策与状态
│   │   │   ├── decision.py     ← ApprovalDecision(str, Enum) + ApprovalResult.approved property
│   │   │   ├── state.py        ← TTL reaper + wait_for_resume/wait_for_abort 原子原语
│   │   │   └── __init__.py     ← 聚合导出
│   │   ├── dangerous_tools.py  ← DANGEROUS_TOOLS + FORBIDDEN_SUBAGENT_TOOLS (frozenset)
│   │   ├── command_filter.py   ← DEFAULT_BLOCKLIST + redact_args (cli_execute 脱敏)
│   │   ├── approval_flow.py    ← run_approval_loop 公共审批循环 (work/coding 统一)
│   │   └── __init__.py         ← 聚合导出
│   ├── config/                 ← pydantic-settings 包（替代单文件 config.py）
│   │   ├── settings.py         ← Settings + get_settings + 路径常量
│   │   ├── subagents.py        ← SubagentSettings + _default_subagents + _parse_custom_subagents
│   │   └── prompts/            ← 内置 system prompt + trigger 描述 + tools 常量
│   │       ├── builtin.py      ← code/rag/web 子代理默认值
│   │       └── team.py         ← 7 个团队专家默认值
│   │                           ⚠️ 与 ``app.workspace.config``（workspace 内的 .agentx/ 目录配置）含义不同，
│   │                              ``app.config`` 管 pydantic Settings + 子代理 + 专家 prompt。两者职责独立。
│   ├── router/                 ← 消息分类 + StateGraph（仅编排，不嵌路径实现）
│   │   ├── classifier.py       ← 规则前置 + LLM 分类
│   │   ├── graph.py            ← Router 图 + run_router（主入口）+ _parse_skill_tag
│   │   └── state.py            ← RouterState TypedDict
│   ├── chat/                   ← 路径 A：LLM 直答
│   │   ├── __init__.py
│   │   └── run.py              ← run_chat_path（ThinkFilter 流式 token）
│   ├── deep/                   ← 路径 C：DeepAgent + interrupt_before 审批
│   │   ├── __init__.py
│   │   ├── agent.py            ← run_deep_path / build_deep_agent（主入口，~200 行）
│   │   ├── tools.py            ← _make_deep_tools + _load_mcp_tools
│   │   ├── streaming.py        ← _stream_agent_events
│   │   └── recovery.py         ← _inject_tool_error_messages + _sanitize_message_history
│   ├── team/                   ← 路径 D：AgentTeam 多代理协作
│   │   ├── __init__.py
│   │   ├── orchestrator.py     ← run_team_path（主入口，~200 行）
│   │   ├── planner.py          ← _build_orchestrator_prompt + _parse_plan + _validate_task
│   │   ├── scheduler.py        ← _run_subtask + 队列驱动
│   │   ├── blackboard.py       ← Blackboard + TeamPlanTask + TeamSubtaskResult
│   │   └── aggregator.py       ← _run_aggregator + _quality_gate + _should_downgrade_to_single
│   ├── cli/                    ← CLI 终端交互（REPL + One-shot + config 子命令）
│   │   ├── __init__.py        ← 包导出 main
│   │   ├── app.py             ← main() + argparse + 模式分发 + config 子命令
│   │   ├── repl.py            ← run_repl + consume_events
│   │   ├── one_shot.py        ← run_one_shot
│   │   ├── approval.py        ← handle_approval 终端审批交互
│   │   ├── commands.py        ← CommandResult + handle_command + _cmd_*（全部 await）
│   │   ├── renderer.py        ← EventRenderer SSE 事件终端渲染
│   │   └── store.py           ← Tauri store 配置读取 + 凭证解密（DPAPI/AES-GCM）
│   ├── subagents/              ← code / rag / web 子代理 + 路径 B 分发
│   │   ├── base.py             ← make_fs_tools / make_rag_tools / make_web_tools + extract_text
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
│   │   └── context.py          ← 消息截断（trim_messages_with_budget）
│   ├── workspace/              ← workspace 业务包（与 deep/team/subagents 平行；config 子包 + api 路由）
│   │   ├── __init__.py         ← register_routes 聚合
│   │   ├── api.py              ← /api/workspace/* + /api/project-config/* 路由（合并自原 api/workspace.py + api/project_config.py）
│   │   └── config/             ← .agentx/ 项目级配置（generator/loader/merger/templates）
│   │       ├── __init__.py     ← 包导出
│   │       ├── templates.py    ← 6 个文件模板（AGENTS.md/mcp.json/subagents.json/tools.json/system_prompt.md/rules/README.md）
│   │       ├── generator.py    ← generate_agentx_dir 幂等生成
│   │       ├── loader.py       ← load_project_config 容错加载
│   │       └── merger.py       ← merge_configs 合并到 Settings 之上
│   ├── vectorstore/            ← Milvus 客户端
│   ├── embedding/              ← TEI 客户端
│   ├── mcp/                    ← MCP 客户端 + 配置
│   ├── observability/          ← LangSmith SDK + ObservationStore + logger
│   │   ├── observation.py      ← SqliteObservationSink（4 表 + WAL）+ ObservationCallback（FR-1/2）
│   │   ├── langsmith.py        ← LangSmith SDK trace_span + redact（FR-3）
│   │   ├── langsmith_dual.py   ← dual_trace contextmanager（本地+remote 双写+降级，FR-3.3）
│   │   ├── trace.py            ← bind_trace ContextVar（trace_id 透传 0-intrusion）
│   │   └── feedback.py         ← 隐式信号 record_implicit_ok/bad（FR-9）
│   └── utils/                  ← text(ThinkFilter) + chunks + sse_events + prompts + paths
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
│   │   │   ├── ui/             ← 通用 UI（无业务语义）
│   │   │   ├── settings/       ← 设置面板（按子域拆分子目录 model-provider/subagents/mcp/memory）
│   │   │   └── ...             ← 其他顶层目录命名见 §9.5 视觉区域术语表（titlebar/sidebar/main/workspace/overlay/log）
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
| `token` | 纯字符串 | 增量 token（visible text，已剥离 ` 块） |
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

> 2026-07-08 重构：沙箱与安全代码从 `utils/security.py` / `approval/` / `memory/sandbox_store.py` / `deep/approval.py` / `api/sandbox.py` 抽取为独立的 [sandbox/](file:///d:/java/agentprojects/agentx/backend/app/sandbox/) + [security/](file:///d:/java/agentprojects/agentx/backend/app/security/) 两个顶级包，与 `deep/` / `team/` / `tools/` 平行。

- **沙箱授权**：文件操作走 [app.sandbox.get_sandbox](file:///d:/java/agentprojects/agentx/backend/app/sandbox/session_sandbox.py)（`SessionSandbox` async + `asyncio.Lock`），未授权目录 → `PathNotAuthorized`。
- **持久化**：[app.sandbox.store](file:///d:/java/agentprojects/agentx/backend/app/sandbox/store.py) SQLite WAL + `busy_timeout=30000`，并发写不锁。
- **路径保护**：[app.sandbox.path_guard](file:///d:/java/agentprojects/agentx/backend/app/sandbox/path_guard.py) 归一化 + 关键目录黑名单（修复 Linux `Path('/')` 误判 bug）。
- **parent_thread_id 继承**：Team 模式子任务继承父 thread 授权（`run_coding_expert(parent_thread_id=thread_id)`）。
- **审批决策**：[app.security.approval.ApprovalDecision](file:///d:/java/agentprojects/agentx/backend/app/security/approval/decision.py)（`str, Enum`：`approve/once/session/deny`），`ApprovalResult.approved` 为 property。
- **审批状态**：[app.security.approval.state](file:///d:/java/agentprojects/agentx/backend/app/security/approval/state.py) 模块级 dict + `asyncio.Lock`，5 个 dict value 为 `tuple[T, float]`（TTL timestamp）。
- **TTL reaper**：`start_reaper()` 后台协程每 5 分钟清理 30 分钟无活动的 thread_id（`main.py` lifespan 启动）。
- **原子原语**：`wait_for_resume(thread_id, timeout)` / `wait_for_abort(thread_id, timeout)` 消除 "check 后、await 前 clear 已 set event" 竞态。
- **公共审批循环**：[app.security.approval_flow.run_approval_loop](file:///d:/java/agentprojects/agentx/backend/app/security/approval_flow.py) 统一 work/coding 两场景审批逻辑。
- **危险工具**：[app.security.dangerous_tools](file:///d:/java/agentprojects/agentx/backend/app/security/dangerous_tools.py) `DANGEROUS_TOOLS` + `FORBIDDEN_SUBAGENT_TOOLS`（`frozenset`，移除已废弃的 `shell_exec`）。
- **命令过滤**：[app.security.command_filter](file:///d:/java/agentprojects/agentx/backend/app/security/command_filter.py) `DEFAULT_BLOCKLIST` + `redact_args`（`cli_execute` 的 `command`/`arguments` 脱敏）。
- 沙箱授权目录通过 `POST /api/sandbox/authorize` 显式开启（renderer 直连 HTTP，**不**走 Tauri invoke）。
- 系统关键目录黑名单（Windows / Unix）在 [src-tauri/src/commands/dialog.rs::save_dropped_file](file:///d:/java/agentprojects/agentx/src-tauri/src/commands/dialog.rs)。
- `POST /api/sandbox/revoke` 撤销授权；`GET /api/sandbox/authorized/{thread_id}` 列出已授权目录。

### 14.4 SSE / 审批流

- 审批状态用模块级 `_pending_approvals: dict[str, tuple[ApprovalResult, float]]` 内存 dict 维护
  （带 TTL timestamp，reaper 自动清理）。
- 自动批准：`AGENTX_AUTO_APPROVE_AFTER_SECONDS > 0` 时倒计时归零自动 approve；
  `= 0` 禁用，等用户操作。
- `AGENTX_APPROVAL_MAX_WAIT`（默认 300s）控制单次审批最长等待；`0` = 上限 3600s（bug 已修复）。
- SSE handler 每轮检查 `_abort_flags[thread_id]`，用户中止立即退出循环。
- 审批类型 `kind`：`dangerous_tool`（写/编辑/cli_execute）| `directory_extension`
  （路径越界扩展授权，含 `requestedPath` + `writable`）。
- `full_trust` 模式跳过 `directory_extension` 预检查；`cli_execute` 始终需审批（workspace 授权仅放行 fs 工具）。

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

### 14.7 启动、重启前后端（踩坑沉淀）

> 这套流程是 2026-07-04 / 2026-07-09 反复实战出来的。

#### 14.7.1 启动入口

- **入口：永远 `pnpm tauri dev`**（即 `npm run tauri dev`），不要直接 `uv run python -m app.main`——
  后端依赖的 `AGENTX_*` 凭证 + 配置由 Rust 主进程通过
  [src-tauri/src/backend/env.rs](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs) 注入，
  直接起 uvicorn 会缺 key、缺 Milvus 密码、缺 tools / subagents config。
- `pnpm tauri dev` 启动顺序：vite renderer 构建 → Tauri 主进程编译启动 →
  `setup()` hook → migration → `PythonHandle::start` 拉 uv → uvicorn 监听 8123 →
  Tauri 桌面窗口出现。
- **首次启动 Rust 编译**约 1-3 分钟（增量编译约 5-15s），看到
  `Finished dev profile target(s) in ...` 表示 Rust 编译完成。
- 看到 `AgentX Tauri shell started` 日志后再等 **8-10s** 再探测 8123。

#### 14.7.2 单独启动场景（仅调试用）

| 场景 | 命令 | 用途 |
|---|---|---|
| 仅调试前端 | `pnpm dev` 或 `npx vite --config vite.config.mjs --host 127.0.0.1` | 浏览器调试 UI（绕过 Tauri） |
| 仅调试后端 | `.venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8123 --reload` | 跳过 Tauri 直接调试 Python |
| 仅重启后端 | Ctrl+C 当前后端 → 重启上述 uvicorn | 不影响 Tauri 桌面窗口 |

> ⚠️ 单独启动的后端需要自己注入环境变量（`AGENTX_*` 密钥），推荐还是用 `pnpm tauri dev`。

#### 14.7.3 重启流程（标准 SOP）

**步骤 1：清理两棵进程树**

只 `Stop-Process -Id <pid>` 不够——uv→python 的父子链不杀干净会导致 Errno 10048。
**必须两棵树并行端**（PowerShell 原生命令，禁止用 `taskkill`、`netstat`）：

```powershell
# Tauri 主进程 + WebView2 子进程（按 CommandLine 精准筛选，避免误杀其他项目的 python/node）
Get-Process -Name python,node -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*agentx*' -or $_.CommandLine -like '*tauri*' -or $_.CommandLine -like '*vite*' } |
    Stop-Process -Force

# 也可按项目名/包名筛选（如 Hermes 等其他项目并行时）
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -in @('agentx','AgentX') } | Stop-Process -Force

# 验证端口已释放（注意 TimeWait 状态需等待 1-2 分钟）
Get-NetTCPConnection -LocalPort 8123,5173,5174 -ErrorAction SilentlyContinue |
    Where-Object { $_.State -ne 'TimeWait' }
# 返回空才算彻底清干净
```

**步骤 2：重新启动**

```powershell
cd d:/java/agentprojects/agentx
pnpm tauri dev          # 完整启动（Tauri + Vite + Python）
```

或者分步启动（仅排查时）：

```powershell
# 1. 后端（端口 8123）
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8123 --reload

# 2. 前端（默认端口 5173，被占用时自动找下一个空闲端口 5174+）
npx vite --config vite.config.mjs --host 127.0.0.1
```

**步骤 3：健康探测**

```powershell
# 探测 8123 后端
Invoke-RestMethod -Method GET -Uri 'http://127.0.0.1:8123/' -TimeoutSec 5
Invoke-RestMethod -Method GET -Uri 'http://127.0.0.1:8123/api/health' -TimeoutSec 5

# 探测前端端口
Get-NetTCPConnection -LocalPort 5173,5174 -ErrorAction SilentlyContinue | Select-Object LocalPort, State
```

#### 14.7.4 Windows 端口占用诊断与解决

**症状 1：`[WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试`**

- 原因：端口 8123 / 5173 被其他进程占用
- 诊断：
  ```powershell
  Get-NetTCPConnection -LocalPort 8123 -ErrorAction SilentlyContinue |
      Select-Object LocalPort, OwningProcess, State
  ```
- 解决：
  ```powershell
  # 方法 A：精准杀掉占用进程（推荐）
  Stop-Process -Id <OwningProcess> -Force

  # 方法 B：等待 TimeWait 释放（1-2 分钟）
  # 方法 C：换端口启动（仅限临时调试）
  ```

**症状 2：端口 5173 启动后 `Port 5173 is already in use`**

- 原因：上次 `tauri dev` 残留 Vite watcher 进程
- 解决：按 §14.7.3 步骤 1 清理所有相关 node 进程，或换端口 `npx vite --port 5174`

**症状 3：Tauri 自动启动 Python 但端口冲突**

- 现象：Tauri 主进程拉起 Python 时打印 `port 8123 被 PID xxx 占用，先行 kill`
- 处理：Tauri 已自动 kill 占用进程，无需手动干预；如持续冲突，先按 §14.7.3 完全清理

#### 14.7.5 健康探测规范

- **`/api/health` 不是存活探针**。该端点同步串行调 TEI（myserver:8093）+ Milvus
  （myserver:19530），外部不通就耗时 5s+ 看起来像超时，但它**永远 200 兜底**。
  要做进程存活检测，用下面 4 个**轻量**端点任意一个：
  | 端点 | 用法 |
  |---|---|
  | `GET /` | 返回 `{app, version, status}`，零依赖，< 50ms |
  | `GET /api/skills` | 验证技能文件加载链路 |
  | `GET /api/memory/checkpointer` | 验证 SQLite checkpoint |
  | `POST /api/sandbox/authorize` | 顺手验证沙箱授权链路 |

#### 14.7.6 重启常见错误

| 错误 | 原因 | 解决 |
|---|---|---|
| `Errno 10048` | 上次端口未释放（uv→python 父子链残留） | 按 §14.7.3 步骤 1 完整清理 |
| `[WinError 10013]` | 端口被其他应用占用 | `Get-NetTCPConnection` 诊断，`Stop-Process` |
| `[WinError 10048]` | Tauri 内部 Socket 复用冲突 | 完全重启 Tauri |
| Vite `@/` 路径解析失败 | 在 `frontend/renderer` 子目录启动而非项目根目录 | `cd d:/java/agentprojects/agentx` 后启动 |
| Tauri 桌面窗口不出现 | Rust 首次编译未完成 / WebView2 缺失 | 等编译完成 / 安装 WebView2 Runtime |
| 后端 `agent stuck in repeating tool-call loop` | LLM 陷入重复工具调用循环 | 已修复：见 `backend/app/deep/execution.py` 重复检测 + `asyncio.sleep(0.05)` |

#### 14.7.7 dev 进程长存规范

- **dev 是长进程**，启动后用 `CheckCommandStatus` / `GetTerminalOutput` 轮询日志观察
  `AgentX Tauri shell started` + uvicorn 监听即可，**不要等进程结束**。
- 重启前必须先关闭上一次 dev 进程（Ctrl+C 或上文的 Stop-Process），否则会端口冲突。

#### 14.7.8 进程筛选规范（精准而非全杀）

⚠️ **禁止** `Get-Process -Name python | Stop-Process -Force`——会误杀同机的其他项目（如 Hermes）。

**推荐做法**（按 CommandLine 精准筛选）：

```powershell
# agentx 相关 python 进程
Get-Process -Name python -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*agentx*' } |
    Select-Object Id, ProcessName, CommandLine

# agentx 相关 node 进程（Vite）
Get-Process -Name node -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*vite*' -or $_.CommandLine -like '*agentx*' } |
    Select-Object Id, ProcessName, CommandLine
```

> 此规范可沉淀为 [learned_skill_experience] "Windows下精准筛选并重启指定项目进程技能"。



## 15. 常用命令

### 前端 / Tauri

```bash
pnpm tauri dev          # 推荐：同时启动 vite + Rust 主进程 + Python 后端（含凭证注入）
pnpm exec vite dev      # 仅启动前端 Vite（端口 5173，被占用自动递增）
pnpm tauri build        # 生产构建，生成 NSIS 安装包
pnpm typecheck          # tsc 严格模式（node + web 两套配置）
pnpm test               # vitest（renderer 单测）
pnpm dist:win           # Windows NSIS 安装包（等价于 tauri build）
```

> **启动/重启前后端**一律走 `pnpm tauri dev`（由 [src-tauri/src/backend/env.rs::build_env](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs) 自动注入凭证 + 配置）。
> 重启前的进程清理、8123 端口探测、健康验证脚本等完整 SOP 见 §14.7。
> Rust 单测：`cd src-tauri && cargo test --lib`；冒烟脚本：`pwsh scripts/smoke-tauri.ps1`。
> 单独调试某一端（仅前端或仅后端）时的命令与陷阱见 §14.7.2。

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
| 调整沙箱/授权 | [backend/app/sandbox/](file:///d:/java/agentprojects/agentx/backend/app/sandbox/) + §14.3 |
| 调整审批/安全策略 | [backend/app/security/](file:///d:/java/agentprojects/agentx/backend/app/security/) + §14.3 + §14.4 |
| 写 ADR / 提案 | [openspec/changes/archive/](file:///d:/java/agentprojects/agentx/openspec/changes/archive/) 历史格式参考 |
| 重启前后端 | §14.7（清理两棵树 → `pnpm tauri dev` → 健康验证脚本） |
| 修改项目配置 | [backend/app/workspace/](file:///d:/java/agentprojects/agentx/backend/app/workspace/) + §16.1 `.agentx/` 项目级配置 |

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
- **OpenSpec** = 变更提案流程（proposal / design / tasks / specs）。
- **claude.md** = 引用本文件的指针（保留以满足"每次会话强制阅读"的项目规则）。

三者互不替代：Claude 在写代码前应同时检查本节与 §1.1「优先用现成框架」。
