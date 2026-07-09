# AgentX 工程文档集（docs/agents/）

> 本目录是 [`AGENTS.md`](file:///d:/java/agentprojects/agentx/AGENTS.md) 的**离线查阅文档**，
> 不进 deepagents 自动加载上下文（[`.agentx/rules/`](file:///d:/java/agentprojects/agentx/.agentx/rules/) 才是自动加载入口）。
> 阅读时机：排查具体问题、写新代码前查阅约定、或新人 Onboarding。

---

## 文档列表

| # | 文档 | 内容 | 原 AGENTS.md 章节 |
|---|---|---|---|
| 01 | [01-architecture-file-map.md](file:///d:/java/agentprojects/agentx/docs/agents/01-architecture-file-map.md) | 三层架构与文件地图（145 行树状） | §11 |
| 02 | [02-sse-event-contract.md](file:///d:/java/agentprojects/agentx/docs/agents/02-sse-event-contract.md) | SSE 事件契约（event/data 类型/source 标识） | §13 |
| 03 | [03-key-conventions.md](file:///d:/java/agentprojects/agentx/docs/agents/03-key-conventions.md) | 关键约定 / 易踩坑（凭证 / Tauri 进程 / 沙箱 / 审批流 / 路径导入 / 路由别名） | §14.1–§14.6 |
| 04 | [04-restart-sop.md](file:///d:/java/agentprojects/agentx/docs/agents/04-restart-sop.md) | 启动 / 重启前后端 SOP（清理两棵树 / 端口诊断 / 健康探测 / 错误表） | §14.7 |
| 05 | [05-frontend-naming.md](file:///d:/java/agentprojects/agentx/docs/agents/05-frontend-naming.md) | 前端主界面模块化命名规范（强约束术语表，可选拷贝到 `.agentx/rules/`） | §9.5 |

---

## 与 `.agentx/rules/` 的边界

| 位置 | 用途 | 加载机制 |
|---|---|---|
| `AGENTS.md`（根） | 核心规范 + 工作手册索引（≈ 300 行） | 人工阅读（AI 不自动加载） |
| `.agentx/AGENTS.md` | 项目级 AI 规则（用户编辑） | deepagents memory= 自动加载 |
| `.agentx/rules/*.md` | **强约束**类自动加载上下文（如前端命名规范） | deepagents memory= 自动加载（按文件名排序，最多 10 个文件） |
| `docs/agents/*.md` | **查阅型**文档（架构地图、契约、SOP） | 人工查阅，不进 agent 上下文 |

---

## 维护约定

- 新增文档用 `NN-<topic>.md` 前缀编号（N2 起），保持排序稳定
- 每个文档顶部注明**原 AGENTS.md 章节号**，便于双向追溯
- 跨文档引用使用相对路径或 `file:///` 绝对路径
- 内容变更同步更新主 `AGENTS.md` 索引表