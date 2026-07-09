# SSE 事件契约（前后端必对齐）

> 原 `AGENTS.md` §13 拆分。阅读时机：新增/修改 SSE 事件、调试聊天流、联调前后端 SSE handler。

---

`backend/app/api/chat.py::_event_generator` 与
[frontend/renderer/lib/api/chat.ts::send](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts#L36-L111)
+ [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) 共同实现。

## 聊天相关 REST 端点（除 SSE 外）

- `POST /api/chat` — SSE 流式聊天，请求体 `ChatRequest`。
- `POST /api/chat/approve` — 提交审批决定。
- `POST /api/chat/abort` — 设置中止标志。
- `POST /api/chat/pause` — 设置暂停标志（请求体同 `AbortRequest`，仅 `thread_id`），返回 `{"ok": True}`。
- `POST /api/chat/resume` — 清除暂停标志（请求体同 `AbortRequest`，仅 `thread_id`），返回 `{"ok": True}`。
- `POST /api/chat/compact` — 压缩会话历史。

`ChatRequest` 新增 `workspace_path` 字段（当前会话绑定的 workspace 绝对路径）。前端不再在消息正文中拼接 `<workspace>` 标签，后端也不再解析该标签；workspace 授权由该字段驱动。

## 事件类型总表

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

## `source` 字段标识

`reasoning` / `tool_call` / `tool_result` / `delegation` 事件携带 `source` 字段：

| `source` 值 | 来源 | 说明 |
|---|---|---|
| `"work"` | Work Supervisor | 场景化架构下的全能 agent |
| `"coding"` | Coding Expert | 代码任务专家 |
| `"rag"` | RAG 子代理 | 知识库检索子代理 |
| `"web"` | Web 子代理 | 联网搜索子代理 |

> 旧值 `"code"` / `"deep"` / `"agent"` 已删除（推倒重来，无兼容层）。

## 同步约束

> 修改任一事件类型或字段名，**必须**同步更新
> [chat.py](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)、
> [lib/api/chat.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts)、
> [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) 三处。