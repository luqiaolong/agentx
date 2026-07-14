# SSE 事件契约（前后端必对齐）

> 原 `AGENTS.md` §13 拆分。阅读时机：新增/修改 SSE 事件、调试聊天流、联调前后端 SSE handler。

---

`backend/app/api/chat.py::_event_generator` 与
[frontend/renderer/lib/api/chat.ts::send](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts#L36-L111)
+ [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) 共同实现。

主 agent 路径（work / coding）已开启 `stream_mode="messages"`，`<think>` 块以 `reasoning_delta` 事件实时增量推送；`reasoning` 事件保留给 observation / team 等需要一次性推送完整 thinking 内容的路径。

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
| `token` | 纯字符串 | 增量 token（visible text，已剥离 think 块；messages 模式下实时推送） |
| `token_rollback` | JSON `{}` | 撤回当前 text part（模型把计划文本误推为 token 后撤回，随后发 reasoning + tool_call） |
| `reasoning_delta` | JSON `{"delta": str, "source": str}` | 思考过程实时增量 token（主 agent 路径 `<think>` 块实时推送） |
| `reasoning` | JSON `{"content": str, "source": str}` | 思考过程完整 chunk（observation / team 等一次性推送路径；主 agent 路径仅推送非 think 的计划文本） |
| `tool_call` | JSON `{"id","name","args","source"}` | 工具调用开始（id 供前端配对 tool_result；subagent 用 astream_events v2 run_id） |
| `tool_result` | JSON `{"id","name","result","source","error?"}` | 工具调用结束 |
| `delegation` | JSON `{"target","source","message"}` | 子代理委派标记（路径 B 入口下发） |
| `todo_update` | JSON `{"todos": [{"content": str, "status": "pending"\|"in_progress"\|"completed"}], "task_id?": str, "source?": str, "parent_task_id?": str}` | DeepAgent/Team 任务列表更新（deepagents 原生 TodoListMiddleware 维护）。`task_id` 区分 Team 子任务；`source` 标识来源（`work`/`coding`/`rag`/`web` 或 Team 子任务角色 `frontend_dev`/`backend_dev` 等），前端按角色分组；`parent_task_id` 为 Team 子任务的父 thread_id，前端据此把子任务 todo 嵌套到父任务卡片下。主路径（单 agent）不传 `parent_task_id`。 |
| `approval_request` | JSON `{"thread_id","tool_name","args","preview","kind?","requestedPath?","writable?"}` | 危险工具 / 目录越界 / 沙箱权限升级审批请求。`kind` 取值：`"dangerous_tool"`（危险工具）、`"directory_extension"`（目录越界）、`"sandbox_escalation"`（沙箱权限升级）。`sandbox_escalation` 额外字段：`command`、`exit_code`、`reason`、`suggested_action`、`suggested_path`。 |
| `paused` | `"{}"` | 用户暂停，SSE 流在下一轮迭代退出并保留状态，等待 `resume` |
| `team_init` | JSON `{"plan","agents","reasoning"}` | AgentTeam 计划生成完成，前端据此在消息顶部创建 TeamNodeCard（在 `team_done` 之前发出） |
| `team_done` | JSON `{"status": "done"|"error"|"replanning", "agents"?, "blackboard"?}` | AgentTeam 整体执行结束或过渡态。`status` 枚举：`"done"` 终态（团队完成）；`"error"` 终态（团队失败）；`"replanning"` 过渡态（质量门失败，正在重规划，前端不应 finalize）。`agents` 为可选的子代理摘要列表（终态时携带）。`blackboard` 为可选的黑板快照（终态时携带），结构 `{"findings": Finding[], "errors": string[]}`，其中 `Finding` 含 `{agent, task_id, wave_index, content, success, error?, retries}`。前端 `BlackboardPanel` 优先消费 `blackboard`，fallback 到 `agents` 聚合。在 `done` 之前发出。 |
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
| `"team"` | AgentTeam 编排器 | Team 路径的 `delegation` 事件专用，前端据此同步更新 TeamNodeCard 中对应 agent 的状态为 running |

> 旧值 `"code"` / `"deep"` / `"agent"` 已删除（推倒重来，无兼容层）。

## 同步约束

> 修改任一事件类型或字段名，**必须**同步更新
> [backend/app/sse/events.py](file:///d:/java/agentprojects/agentx/backend/app/sse/events.py)、
> [frontend/shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts)、
> [useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts)、
> 以及本事件契约文档。