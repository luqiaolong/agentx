# Spec: Agent 观测中心（agent-observation-store）

## 概述

AgentX 项目需建设统一的"Agent Observation Store"可观测能力，把分散在 `observability/`、`memory/`、`api/`、`eval/` 的可观测数据**收敛到一张表**（4 张 SQLite 表 + 1 个 SQLite 文件），围绕 `trace_id`（16 字符 hex）串起 prompt 组装结果、tool_call 全生命周期事件（含脱敏 args/result/审批/时延）、LangGraph state 快照（start / mid / end 3 个时间点）、全链路 trace 注入（SSE 事件 + LangSmith SDK 双写）、显式用户反馈（👍/👎 + 分类评论）。

**核心立场**：100% 复用 LangSmith SDK、LangChain `BaseCallbackHandler`、LangGraph `SqliteSaver`、deepagents `RubricMiddleware` 等现成框架，**禁止**自研 trace 协议、评估器或 checkpoint 序列化。本 spec 全部 FR / NFR 围绕"复用现成"展开。

---

## 功能需求

### FR-1：观测数据存储（SqliteObservationSink）

**FR-1.1** `backend/app/observability/observation.py` 必须实现 `ObservationSink` 协议，5 个方法：
- `async append_event(run_id, seq, event_type, payload)`
- `async record_prompt(run_id, system_prompt, user_message, history_preview)`
- `async record_state_snapshot(run_id, kind, state)`
- `async write_feedback(run_id, kind, score, comment, categories)`
- `async close()`

**FR-1.2** `SqliteObservationSink` 必须用 `data/agent_observation.db` 独立文件，配置 `PRAGMA journal_mode=WAL` + `busy_timeout=30000`（与 [sandbox/store.py](file:///d:/java/agentprojects/agentx/backend/app/sandbox/store.py) 同款）。

**FR-1.3** 4 张表 schema：
- `observation_run` — run_id (PK) / trace_id / thread_id / agent_mode / permission_mode / user_message / workspace_path / final_prompt / history_preview / result_text / result_token_count / duration_ms / started_at / ended_at / error_type / error_message
- `observation_event` — event_id (PK auto) / run_id / seq / ts / event_type / payload_json
- `observation_tool_call` — tool_call_id (PK) / run_id / tool_name / call_seq / args_json / result_preview / result_token_count / duration_ms / approval_decision / approved / error_message / started_at / ended_at
- `observation_feedback` — feedback_id (PK auto) / run_id / kind / score / comment / categories_json / created_at

**FR-1.4** `run_id` 必须等于 `trace_id`（16 字符 hex），**不**用独立 UUID。保证 4 张表单一锚点关联。

**FR-1.5** `append_event` 必须**复用** [observability/langsmith.py::redact](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py#L25-L41) 做 args 脱敏（`*_PASSWORD/*_KEY/*_SECRET/*_TOKEN/*_CREDENTIAL` → `<redacted>`）。

**FR-1.6** `record_prompt` 写入的 `system_prompt` 必须是 router 已拼好的 `profile_prompt` 完整字符串（profile + project + skill），**不**重新渲染或二次拼装。

**FR-1.7** `record_state_snapshot` 的 3 个 `kind`：`start`（dispatch 前）/ `mid`（approve resume 后）/ `end`（yield done 前），只存白名单字段（`messages` 截断到最近 N 条 + `authorized_dirs` + `_rubric_status` + `remaining_steps`）。

### FR-2：LangChain Callback 自动捕获

**FR-2.1** `ObservationCallback` 必须继承 LangChain `BaseCallbackHandler`，挂在 `agent.astream(..., config={"callbacks": [cb]})`。

**FR-2.2** 必须实现以下钩子：
- `on_llm_start` / `on_llm_end` → 写 LLM 输入（含 messages 截断 4000 字符）+ 输出 + token 数
- `on_tool_start` / `on_tool_end` → 写 tool_call 完整生命周期（含 args redact + result 截断 2000 字符 + duration_ms）
- `on_chain_start` / `on_chain_end` → 写 LangGraph 节点级 span

**FR-2.3** Callback 异常**必须**隔离（try/except 包裹 `sink.append_event` 调用），不影响 agent 主流程。

**FR-2.4** 跨子代理 / 跨 LangGraph node（work / coding / team 三场景）都必须捕获。

### FR-3：LangSmith 真实 SDK 接入（双写）

**FR-3.1** `backend/app/observability/langsmith.py` 的 `trace_span()` 必须**保留**公开签名（`name`, `**metadata`），内部从当前受 `settings.langsmith_tracing` 开关控制的 `logger.debug` 占位升级为 `from langsmith import trace` 真实调用。

**FR-3.2** `trace_span()` 必须接受新增参数 `run_id` / `thread_id`（透传 trace_id 锚点），元数据 redact 后上传。

**FR-3.3** `backend/app/observability/langsmith_dual.py` 必须提供 `dual_trace()` contextmanager：
- 本地 `data/agent_observation.db`：**必写**（即使 LangSmith 失败）
- LangSmith remote：仅当 `LANGSMITH_TRACING_V2=true` 且 `LANGSMITH_API_KEY` 非空时上传

**FR-3.4** LangSmith 凭证缺失 / 网络失败时**必须**降级为本地 only，写 1 条 `loguru warning`（含 `trace_id`），**不**中断业务。

**FR-3.5** `pyproject.toml` 必须新增 `langsmith>=0.1.0` 依赖。

**FR-3.6** LangSmith 凭证注入走 tauri-plugin-store（[src-tauri/src/backend/env.rs::build_env](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs)），**不**改 `Settings.env_file=None`（[AGENTS.md §18](file:///d:/java/agentprojects/agentx/AGENTS.md) 红线）。`LANGSMITH_API_KEY` 注入已存在（env.rs L47-49），本次仅需补 `LANGSMITH_TRACING_V2` + `LANGSMITH_PROJECT` 两个环境变量注入。

### FR-4：SSE 事件 trace_id 注入

**FR-4.1** [api/chat.py::_event_generator](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py#L50) 入口必须**沿用**现有 [observability/trace.py::bind_trace](file:///d:/java/agentprojects/agentx/backend/app/observability/trace.py) 机制生成/接受 `trace_id`（16 字符 hex，前端可传 1-32 字符）。

**FR-4.2** 所有 SSE 事件 `data` 字段必须**新增可选** `_tid` 字段携带 trace_id（不破坏旧 `event` / `data` 字段，zod schema `passthrough` 模式兼容）。

**FR-4.3** `_event_generator` 入口必须写 1 条 `observation_run.start`（含 `run_id` = `trace_id` / `thread_id` / `agent_mode` / `permission_mode` / `user_message` / `workspace_path`）。

**FR-4.4** `_event_generator` 正常出口（yield done 前）必须写 1 条 `observation_run.end`（含 `duration_ms` / `result_text` / `result_token_count`）。

**FR-4.5** `_event_generator` 异常分支（except 兜底）必须写 1 条 `observation_run.end` + `error_type` / `error_message`。

### FR-5：tool_call 事件落盘

**FR-5.1** [deep/streaming.py](file:///d:/java/agentprojects/agentx/backend/app/deep/streaming.py) 每个 `yield make_tool_call_event` / `make_tool_result_event` / `make_sse_event` 前必须调 `sink.append_event(run_id, seq, event_type, payload)`，用 `asyncio.to_thread` 包装避免阻塞事件循环。

**FR-5.2** `seq` 同 `run_id` 内自增（INTEGER，从 1 开始），保证事件流顺序。

**FR-5.3** `payload_json` 必须是 `make_*_event` 第二个参数的 JSON 序列化（dict 模式）或原样字符串（str 模式）。

### FR-6：审批回填

**FR-6.1** [api/chat.py::chat_approve](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py#L154) 收到用户审批决定后，必须按 `thread_id` + `trace_id` 找到最近 1 条 `approval_request` 事件，**回填**到 `observation_tool_call` 表的 `approval_decision` / `approved` 字段。

**FR-6.2** 审批回填**必须**区分：
- `auto_approved=true`（`auto_approve_after_seconds > 0` 倒计时归零）
- `user_approved=true`（用户主动 approve）
- `user_rejected=true`（用户 deny）

### FR-7：用户反馈端点（5 个 REST）

**FR-7.1** `POST /api/observation/feedback`
- 入参：`{run_id, kind, score?, comment?, categories?}`（`kind`: `thumb_up` / `thumb_down` / `rating` / `note`）
- 出参：`{ok: true, feedback_id: int}`
- 副作用：写 1 行 `observation_feedback` + redact comment

**FR-7.2** `GET /api/observation/feedback?run_id=...`
- 出参：`list[ObservationFeedback]`

**FR-7.3** `GET /api/observation/runs?thread_id=...&limit=...`
- 出参：`list[ObservationRun]`，按 `started_at` 降序

**FR-7.4** `GET /api/observation/runs/{run_id}`
- 出参：单条 `ObservationRun` + `state_snapshots_json` 数组

**FR-7.5** `GET /api/observation/runs/{run_id}/events`
- 出参：按 `seq` 升序的 `list[ObservationEvent]`

**FR-7.6** 5 个端点全部注册到 `backend/app/api/__init__.py::register_observation_routes(app)`。

### FR-8：用户显式反馈 UI

**FR-8.1** `frontend/renderer/components/chat/MessageFeedback.tsx` 必须实现：
- 👍 按钮（点击直接 `POST /api/observation/feedback`，`kind=thumb_up`）
- 👎 按钮（点击展开 popover：分类下拉 `fact_error` / `tone` / `speed` / `wrong_tool` / `other` + 评论输入框 + 提交按钮）

**FR-8.2** 按钮位置必须**集成在 `AssistantMessageParts.tsx` 的 assistant 消息气泡右侧**（项目无 `MessageBubble.tsx`；参考 [UserMessageBubble.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/UserMessageBubble.tsx) L118-126 的"右侧布局"模式：`Pencil` 编辑按钮在 `flex justify-end` 容器 hover 态浮现）。

**FR-8.3** 按钮**默认**不显示，**hover 态**浮现（避免聊天流被反馈 UI 污染）。

**FR-8.4** 复用 [usePopover.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/components/ui/hooks/usePopover.ts) 状态管理（外部点击关闭、ESC 关闭；注意实际路径在 `components/ui/hooks/`，非 `hooks/`）。

**FR-8.5** API client 必须新增 `apiPost('/api/observation/feedback', ...)`，封装在 [lib/api/chat.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts)。

### FR-9：用户隐式反馈信号

**FR-9.1** `backend/app/observability/feedback.py` 必须实现 3 种隐式信号：
- `record_implicit_ok(run_id, reason="auto_approved+success")` — auto_approve 倒计时归零 + 工具执行成功
- `record_implicit_bad(run_id, reason="aborted")` — 用户主动 `POST /api/chat/abort`
- `record_implicit_bad(run_id, reason="rejected_dangerous_tool")` — 审批 deny

**FR-9.2** 隐式信号**必须**用独立 `kind` 值（`implicit_ok` / `implicit_bad`）与显式反馈区分，便于分析时过滤。

**FR-9.3** 隐式信号不写入 user 通知 / 不弹窗（静默采集）。

### FR-10：反馈 → 评测闭环

**FR-10.1** [backend/app/eval/cli.py](file:///d:/java/agentprojects/agentx/backend/app/eval/cli.py) 必须新增 `export-feedback` 子命令。

**FR-10.2** `export-feedback` 入参：`--days=30`（默认）/ `--output-dir=tests/eval/suites/`。

**FR-10.3** `export-feedback` 行为：查 `observation_feedback` 中 `kind=thumb_down` 行 → 关联 `observation_run.user_message` 与 `agent_mode` → 转 [eval/models.py::EvalCase](file:///d:/java/agentprojects/agentx/backend/app/eval/models.py#L54) YAML → 写到 `feedback-YYYYMMDD.yaml`。

**FR-10.4** 导出 YAML 必须**复用**现有 [tests/eval/suites/*.yaml](file:///d:/java/agentprojects/agentx/tests/eval/suites/) 的 schema（`id` / `user_message` / `agent_mode` / `expect.rubric`），可直接被 `agentx eval run` 消费。

**FR-10.5** 导出的 `expect.rubric` 字段填 feedback 的 `comment`（用户在 👎 时填的评论），无 comment 时填默认 rubric（"回复应满足用户期望"）。

**FR-10.6** 复用 [eval/runner.py::EvalRunner](file:///d:/java/agentprojects/agentx/backend/app/eval/runner.py) 的 `chat_model` 注入能力，replay 时支持 `--mock` / `--live` 模式。

### FR-11：观测 TTL 自动清理

**FR-11.1** lifespan 启动时必须启动 1 个后台协程 `cleanup_old_observations()`，默认每 6 小时清理 1 次。

**FR-11.2** 清理策略：删除 `started_at < now() - AGENTX_OBSERVATION_TTL_DAYS` 的 `observation_run` 与 `observation_event` / `observation_tool_call` 行。

**FR-11.3** `observation_feedback` 表**不**走 TTL（永久保留，与 run 关联，即使 run 被清理）。

**FR-11.4** `AGENTX_OBSERVATION_TTL_DAYS` 默认 30，可配。

---

## 非功能需求

### NFR-1：性能

- `sink.append_event` 同步路径耗时 < 5ms（SQLite WAL + 短连接）
- 1000 连续 event append 总耗时 < 200ms（[test_observation_sink.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_observation_sink.py) 性能测试）
- SSE 事件流**不**因 sink 写入而延迟（用 `asyncio.to_thread` 异步化）
- LangSmith remote 写入**不**阻塞本地（双写降级为 fire-and-forget）

### NFR-2：离线可跑

- 无 `LANGSMITH_API_KEY` / 无网络时，观测 100% 走本地 SQLite
- 无 API key 时，feedback 写入仍正常工作
- 无 myserver / 无 TEI 时，observation 写入仍正常工作

### NFR-3：模块隔离

- `app.observability.observation` 不导入 `app.api`（FastAPI 层），不导入 `app.cli`（CLI 层）
- `app.api.observation` 仅依赖 `app.observability.observation`（Sink 协议）
- `app.eval.cli.export_feedback` 复用 `app.observability.observation` 读 feedback 数据

### NFR-4：LangSmith SDK 真实消费

- 本次改造必须实际调用 `from langsmith import trace` 真实 SDK，**禁止**仅 import 不使用
- `langsmith>=0.1.0` 必须在 `pyproject.toml` 声明
- `LANGSMITH_TRACING_V2=true` 时必须真的有 remote 写入（用 `langsmith.Client` mock 验证）

### NFR-5：LangChain Callback 真实消费

- `ObservationCallback` 必须继承 `langchain_core.callbacks.base.BaseCallbackHandler`
- 必须真实实现 `on_llm_start` / `on_tool_start` 等钩子（不仅 stub）
- 钩子触发条件由 LangChain 框架保证（无需手写触发逻辑）

### NFR-6：LangGraph checkpointer 复用

- `record_state_snapshot` 必须通过 `checkpointer.aget(config)` 读 channel_values
- **不**自研状态序列化（违反 AGENTS.md §3 R14）
- snapshot 字段白名单**必须**与 [memory/checkpointer.py::get_checkpointer](file:///d:/java/agentprojects/agentx/backend/app/memory/checkpointer.py) 返回的 schema 兼容

### NFR-7：可测试性

- 4 张表 + 5 个端点 + 3 种隐式信号 + Callback 钩子必须全部有单元测试
- 性能测试：1000 event < 200ms（CI 必跑）
- 崩溃恢复测试：异常退出后重连数据不丢
- 端到端测试：触发一次含审批的 chat，确认 4 张表都有数据

### NFR-8：SSE 向后兼容

- 新增 `_tid` 字段**必须**是可选的，旧前端忽略未知字段
- `_tid` 仅添加不删除，旧事件流不破坏
- 前端 [lib/api/chat.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts) 未用 zod，SSE 解析走 `as unknown as ChatEvent` 类型断言（结构宽松，自动接受/忽略未知字段）；正式版在 [shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts) 补 `_tid?: string` 类型

### NFR-9：安全合规

- 5 字段黑名单（`*_PASSWORD/*_KEY/*_SECRET/*_TOKEN/*_CREDENTIAL`）必须复用 [observability/langsmith.py::redact](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py#L25-L41)
- 反馈 `comment` 写入前必须 redact
- 工具 `args` 写入前必须 redact
- LLM 输入 messages 写入前必须 redact
- 凭证注入走 tauri-plugin-store，**不**改 `Settings.env_file=None`（[AGENTS.md §18](file:///d:/java/agentprojects/agentx/AGENTS.md) 红线）

---

## 约束

### CON-1：不修改 LangGraph checkpointer 行为

[memory/checkpointer.py::get_checkpointer](file:///d:/java/agentprojects/agentx/backend/app/memory/checkpointer.py) 现有实现满足本提案需求，**不修改**。

### CON-2：不修改 Router 三路径分发逻辑

[router/graph.py::run_router](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py#L126) 仅**增加** hook 点（dispatch 前写 prompt、done 前写 snapshot），不修改路径分发、消息截断、profile 注入等核心逻辑。

### CON-3：不破坏现有 SSE 事件契约

[AGENTS.md §13](file:///d:/java/agentprojects/agentx/AGENTS.md) 定义的 14 种事件类型（token / reasoning / tool_call / tool_result / delegation / todo_update / approval_request / plan / plan_update / paused / team_plan / team_progress / team_result / team_done / done / error）**不修改**。仅在 `data` 字段新增可选 `_tid`。

### CON-4：不修改 LangSmith redact 行为

[observability/langsmith.py::redact](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py#L25-L41) 的 5 字段黑名单**不修改**。本提案**复用**此函数，不重复实现。

### CON-5：不引入新的全局状态

observation sink、feedback writer、callback 实例化时**必须**接受配置，**禁止**模块级可变全局状态（与 [eval/runner.py::EvalRunner](file:///d:/java/agentprojects/agentx/backend/app/eval/runner.py) 模式一致）。

### CON-6：deepagents 依赖消费

`RubricMiddleware` / `GraderResponse` / `GRADER_SYSTEM_PROMPT`（用于 feedback → eval 闭环的 L2/L3 Judge）必须实际消费，**禁止**仅 import 不使用。

### CON-7：不修改现有评测 Runner 行为

[eval/runner.py::EvalRunner](file:///d:/java/agentprojects/agentx/backend/app/eval/runner.py) 现有实现满足本提案需求，**不修改**。`export-feedback` 子命令仅**新增** YAML 生成逻辑。

### CON-8：不创建数据库迁移文件

SQLite 4 张表通过 `_ensure_table()` 在 lifespan 启动时建表（与 [memory/checkpointer.py::setup()](file:///d:/java/agentprojects/agentx/backend/app/memory/checkpointer.py) 同款模式），**不**用 alembic 等迁移工具（避免引入新依赖）。

---

## 依赖变更

- 新增：`langsmith>=0.1.0`（langsmith-python SDK，trace 远程上传）
- 新增（仅运行时反射，无安装）：`pyproject.toml` 已声明的 `deepagents>=0.6.12`（用于 feedback → eval 闭环的 `RubricMiddleware`）
- 无移除依赖

## 兼容性

### SSE 事件 data 字段新增 `_tid`（向后兼容）

**Before**:
```json
{"event": "token", "data": "{\"content\": \"你好\"}"}
```

**After**:
```json
{"event": "token", "data": "{\"content\": \"你好\", \"_tid\": \"a1b2c3d4e5f60718\"}"}
```

旧前端忽略 `_tid` 字段（zod `passthrough`），行为不变。

### Observation 数据库与 checkpointer 隔离

- `data/agent_observation.db`（新）与 `data/agentx.db`（checkpointer）分库
- 两库**不**共享连接 / 不共享事务
- 两库**不**互相同步（observation 库是 checkpointer 的"镜像"层，不是"取代"）

---

## 边界

- 不做 OpenTelemetry 接入（P2 扩展）
- 不做 BI 工具集成 / Parquet 导出（P3 扩展）
- 不做反向 replay 工具（P3 扩展）
- 不做自动化 prompt tuning（P3 扩展）
- 不修改 LangSmith redact 行为（CON-4）
- 不修改 Router / LangGraph checkpointer 行为（CON-1 / CON-2）
- 不做向后兼容的 LangSmith 关闭开关（`LANGSMITH_TRACING_V2=false` 即关闭，不需兼容层）
