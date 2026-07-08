# AgentX

本地优先的个人助理桌面应用，基于 Tauri 2.x + React + FastAPI + LangGraph 构建。
同时提供 **Tauri 桌面 GUI** 与 **终端 CLI** 两种入口，二者配置完全共享。

![主界面](docs/agentx.png)

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Tauri 2.x + Rust 1.77+（tokio async runtime） |
| 前端 UI | React 18 + TypeScript + Tailwind CSS v4 + zustand |
| 主进程 | Rust（10 个官方插件 + `commands/<domain>.rs`） |
| 后端 API | FastAPI + Uvicorn |
| AI 编排 | LangGraph `StateGraph` + DeepAgents + LangChain |
| 智能体分层 | `agents/{supervisor,expert,team}/` 场景化执行体 + `subagents/` 轻量子代理 |
| 向量存储 | Milvus（TEI BGE-M3） |
| 检查点 | LangGraph `SqliteSaver` / `AsyncSqliteSaver` |
| 观测 | LangSmith + Langfuse + loguru |
| 依赖管理 | 前端 npm + Vite 5，Rust cargo，后端 uv + pyproject.toml |

## 架构与运行模式

### Router 场景分发

聊天主入口 [backend/app/router/graph.py::run_router](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py)
按 `agent_mode` **直接分发**到对应场景执行体（不再做消息分类），CLI 与 GUI **共用此入口**：

| `agent_mode` | 执行体 | 文件 |
|---|---|---|
| `"work"` | `run_work_supervisor`（全能 Supervisor，可委派 Expert / 子代理） | [agents/supervisor/work_supervisor.py](file:///d:/java/agentprojects/agentx/backend/app/agents/supervisor/work_supervisor.py) |
| `"coding"` | `run_coding_expert`（基于 `build_deep_agent` + 审批） | [agents/expert/coding.py](file:///d:/java/agentprojects/agentx/backend/app/agents/expert/coding.py) |
| `"coding_team"` | `run_coding_team`（Orchestrator + 并行 Expert + Blackboard + Aggregator） | [agents/team/coding_team.py](file:///d:/java/agentprojects/agentx/backend/app/agents/team/coding_team.py) |

`run_router` 公共职责：`@skill:<name>` 解析 → workspace 授权同步 →
用户画像加载 → checkpointer 历史读取并截断 → 按 `agent_mode` 分发 →
统一收口 assistant token 写回 checkpointer 并 yield `done`。

### Subagent 与委派

`backend/app/subagents/` 提供基础子代理（被 Supervisor / Coding Expert 调用）：

- **基础子代理**：`rag` / `web`（只读 / 安全工具，禁用 `FORBIDDEN_SUBAGENT_TOOLS`）
- **自定义子代理**：[custom_agent.py](file:///d:/java/agentprojects/agentx/backend/app/subagents/custom_agent.py) 提供工厂函数，可由用户在「设置 → 子代理」配置
- **Supervisor 委派能力**：
  - `delegate_to_expert(expert_name, task, context)` — 委派 Coding Expert
  - `delegate_to_subagent(agent_name, task)` — 委派 rag / web / 自定义子代理
- **`@mention` 语法**（`@coding` / `@rag` / `@web`）强制委派，覆盖 LLM 自主决策；解析在
  [agents/supervisor/mention.py](file:///d:/java/agentprojects/agentx/backend/app/agents/supervisor/mention.py)

Coding Team 角色团：`frontend_dev` / `backend_dev` / `tester` / `architect` / `devops` / `ui_designer` / `product_manager`。
Orchestrator 把任务拆分成子任务计划（`team_plan` 事件），`scheduler.py` 并行执行（`team_progress`），
结果写入 Blackboard（`team_result`），最后由 `aggregator.py` 综合输出（`team_done`）。
写 / 编辑 / shell 等危险任务必须分配为 `code` 子任务，由 Coding Expert 执行并走审批。

### 危险工具审批

`DANGEROUS_TOOLS = {"edit_file", "write_file", "shell_exec", "cli_execute",
"git_clone", "git_pull", "git_checkout", "git_stage", "git_commit"}`
在 Supervisor 与 Coding Expert 中通过 LangGraph `interrupt_before=["tools"]` 触发审批；基础子代理（rag / web）
与自定义子代理**严禁**直接暴露写工具（`FORBIDDEN_SUBAGENT_TOOLS`，见
[config/subagents.py](file:///d:/java/agentprojects/agentx/backend/app/config/subagents.py)）。

审批类型：

- `dangerous_tool`：危险工具调用（默认类型）
- `directory_extension`：路径越界扩展授权（payload 含 `requestedPath` + `writable`）

交互方式（同一份 `app.approval.state` 跨 GUI / CLI）：

- **GUI**：前端 ApprovalRequestModal 弹窗
- **CLI**：终端阻塞输入 `y/n/o/s`，见 [cli.py::_handle_approval](file:///d:/java/agentprojects/agentx/backend/app/cli.py)

流控制端点（[api/chat.py](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)）：
`POST /api/chat/{approve,abort,pause,resume,compact}`。

### SSE 事件契约

事件 discriminated union 定义见
[shared/api-types.ts::ChatEvent](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts)，
后端构造器 [utils/sse_events.py](file:///d:/java/agentprojects/agentx/backend/app/utils/sse_events.py)，
后端流式分发 [api/chat.py::_event_generator](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)，
**CLI 渲染** [cli_render.py](file:///d:/java/agentprojects/agentx/backend/app/cli_render.py)，
前端解析 [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts)。

主要事件：`token` / `reasoning` / `tool_call` / `tool_result` / `delegation` /
`todo_update` / `plan` / `plan_update` / `approval_request` / `paused` /
`team_plan` / `team_progress` / `team_result` / `team_done` / `done` / `error`。

`source` 字段标识：`work` / `coding` / `rag` / `web`。

修改任一事件类型或字段名，**必须**同步更新以下五处：

- [api/chat.py](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)（事件 yield）
- [utils/sse_events.py](file:///d:/java/agentprojects/agentx/backend/app/utils/sse_events.py)（构造器）
- [cli_render.py](file:///d:/java/agentprojects/agentx/backend/app/cli_render.py)（**CLI 渲染**）
- [shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts)（类型契约）
- [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts)（前端解析）
