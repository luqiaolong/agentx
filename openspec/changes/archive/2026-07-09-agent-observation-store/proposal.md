# Proposal: Agent 观测中心 — LangSmith SDK + LangChain Callback 统一可观测层

## Why

AgentX 现状在"记录 prompt / toolcall / state / trace / 用户反馈"五个维度上**严重不均衡**：

| 维度 | 现状 | 缺口 |
|---|---|---|
| trace_id | ✅ 完整（[observability/trace.py](file:///d:/java/agentprojects/agentx/backend/app/observability/trace.py) ContextVar + 16 字符 hex） | 仅链路 ID，不存载荷 |
| LangSmith trace | ⚠️ 占位（[observability/langsmith.py](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py) `logger.debug`） | **未接 SDK**，无远程追溯 |
| prompt 落盘 | ❌ 缺失 | 拼好就丢，无法复现 LLM 输入 |
| tool_call 落盘 | ⚠️ SSE 实时推 | 关闭窗口即丢，无历史 |
| LangGraph state | ✅ checkpointer 持久 messages | 无 start/mid/end 语义化快照 |
| 用户显式反馈 | ❌ 完全缺失 | 无 👍/👎 入口 |
| 用户隐式反馈 | ⚠️ 弱（approval/abort） | 是安全/中断信号，不是质量信号 |
| 评测闭环 | ✅ 离线 EvalRunner + 3 层 Judge | 与线上真实反馈完全断链 |

具体痛点：
- 用户报"刚才那次工具调用卡死了" → 只能 `grep backend.log` 找 trace_id，无法回放整次会话
- 评测 case 全部手工写在 YAML，与真实用户问题脱节 → 优化方向无数据支撑
- prompt 拼接结果（profile + project + skill）从未落盘，LLM 版本回退时无法对比输入差异
- LangSmith 占位 6 个月没接，浪费基础设施

> **本提案的核心立场**：违反 AGENTS.md §3 R1（自研 LLM 路由）、R12（自研评估器）、R14（自研 checkpoint 序列化）、R15（自研 prompt 拼接）**任意一条都足以否决自研方案**。本次必须 100% 复用 LangSmith SDK、LangChain `BaseCallbackHandler`、LangGraph `SqliteSaver`、deepagents `RubricMiddleware` 等现成框架，仅在"串联"层做薄薄的胶水代码。

## What Changes

### 阶段 1：观测基础设施

- **新建** `backend/app/observability/observation.py`：
  - `ObservationSink` 协议 + `SqliteObservationSink` 实现
  - 数据库 `data/agent_observation.db`（WAL + busy_timeout=30000，与 [sandbox/store.py](file:///d:/java/agentprojects/agentx/backend/app/sandbox/store.py) 同款配置）
  - 4 张表：`observation_run` / `observation_event` / `observation_tool_call` / `observation_feedback`
  - `ObservationCallback(BaseCallbackHandler)`：挂到 LangChain `agent.astream(..., config={"callbacks": [...]})`，自动捕获 `on_llm_start` / `on_tool_start` / `on_tool_end`
- **修改** [backend/app/main.py::lifespan](file:///d:/java/agentprojects/agentx/backend/app/main.py#L97)：启动时建表 + 注入 `observation_sink` 单例
- **修改** [backend/app/api/chat.py](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)：在 `_event_generator` 入口写 `run_start`、出口写 `run_end`

### 阶段 2：LangSmith 真实接入（双写）

- **修改** [backend/app/observability/langsmith.py](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py)：把当前受 `settings.langsmith_tracing` 开关控制的 `logger.debug` 占位升级为真实 `langsmith.trace` contextmanager，保留 `redact` 5 字段黑名单
- **新增** `backend/app/observability/langsmith_dual.py`：双写 helper，本地 SQLite + LangSmith remote，凭证缺失时降级为本地 only
- **修改** `pyproject.toml`：新增 `langsmith>=0.1.0` 依赖（langsmith-python SDK）
- **修改** [.env.example](file:///d:/java/agentprojects/agentx/.env.example)：补充 `LANGSMITH_API_KEY` / `LANGSMITH_TRACING_V2` / `LANGSMITH_PROJECT` 文档（运行时不会读，仅文档）

### 阶段 3：五维度串联

- **修改** [backend/app/router/graph.py::run_router](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py#L126)：dispatch 前调 `sink.record_prompt()` 写 system_prompt/user_message/history_preview；yield done 前调 `sink.record_state_snapshot()` 写 3 个时间点快照
- **修改** [backend/app/deep/streaming.py](file:///d:/java/agentprojects/agentx/backend/app/deep/streaming.py)：每个 `yield make_*_event` 前调 `sink.append_event()`（用 `asyncio.to_thread` 避免阻塞事件循环）
- **修改** [backend/app/api/chat.py::chat_approve](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py#L154)：按 trace_id 找到最近 `approval_request` 事件，回填 `approval_decision` / `auto_approved`

### 阶段 4：用户反馈端点 + 前端按钮

- **新增** REST 端点（注册到 [backend/app/api/](file:///d:/java/agentprojects/agentx/backend/app/api/__init__.py)）：
  - `POST /api/observation/feedback` — 写 feedback
  - `GET /api/observation/feedback?run_id=...` — 查 feedback
  - `GET /api/observation/runs?thread_id=...&limit=...` — 列 run
  - `GET /api/observation/runs/{run_id}` — 单 run 详情
  - `GET /api/observation/runs/{run_id}/events` — 事件流
- **新增** `backend/app/observability/feedback.py`：`ImplicitFeedback` 信号埋点（auto_approve 归零 + 成功 → 写 `implicit_ok`；用户 abort → 写 `implicit_bad`；审批 deny → 写 `implicit_bad`）
- **前端**：在 assistant 消息气泡右侧加 👍/👎 两个图标按钮，👎 弹 popover 选分类（fact_error / tone / speed / wrong_tool / other）+ 可选评论

### 阶段 5：反馈 → 评测闭环

- **修改** [backend/app/eval/cli.py](file:///d:/java/agentprojects/agentx/backend/app/eval/cli.py)：新增 `export-feedback` 子命令，把 `kind=thumb_down + comment` 的 feedback 转成 `EvalCase` YAML 写到 `tests/eval/suites/feedback-YYYYMMDD.yaml`
- **复用** [backend/app/eval/runner.py::EvalRunner](file:///d:/java/agentprojects/agentx/backend/app/eval/runner.py) 的 `chat_model` 注入能力 + [MockChatModel](file:///d:/java/agentprojects/agentx/backend/app/eval/mocks/llm.py) 支持 replay 验证

## Capabilities

### New Capabilities

- `agent-observation-store`：四表结构 + ObservationCallback + 双写 LangSmith SDK
- `user-feedback-channel`：显式 👍/👎 端点 + 隐式信号埋点
- `feedback-eval-closure`：feedback → EvalCase 导出 + 回归门禁

### Modified Capabilities

- `sse-event-contract`：SSE 事件 data 字段**增加 `_tid` 字段**携带 trace_id，前端可直接展示（不破坏旧 `event` / `data` 字段）
- `dangerous-operation-approval`：[chat.py::chat_approve](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py#L154) 审批写入后**联动** observation 库，回填 approval 决策到 `observation_event` 关联行
- `langsmith-tracing`：[observability/langsmith.py](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py) 从占位升级为真实 SDK，保留 redact 行为

## Impact

- **后端**（新建 4 文件 / 修改 7 文件 / 新增 1 依赖）：
  - 新建 `backend/app/observability/observation.py`（~300 行：协议 + SQLite sink + Callback）
  - 新建 `backend/app/observability/langsmith_dual.py`（~100 行：双写 helper）
  - 新建 `backend/app/observability/feedback.py`（~150 行：隐式信号）
  - 新建 `backend/app/api/observation.py`（~200 行：5 个端点）
  - 修改 `backend/app/observability/langsmith.py`（占位 → 真实 SDK）
  - 修改 `backend/app/main.py`（lifespan 注入）
  - 修改 `backend/app/api/chat.py`（_event_generator + chat_approve）
  - 修改 `backend/app/api/__init__.py`（注册 observation 路由）
  - 修改 `backend/app/router/graph.py`（prompt + snapshot 写入点）
  - 修改 `backend/app/deep/streaming.py`（事件 append 点）
  - 修改 `backend/app/eval/cli.py`（export-feedback 子命令）
  - 修改 `pyproject.toml`（新增 `langsmith>=0.1.0`）
- **前端**（新建 1 组件 / 修改 2 文件）：
  - 新建 `frontend/renderer/components/chat/MessageFeedback.tsx`（👍/👎 按钮 + popover）
  - 修改 `frontend/renderer/components/chat/AssistantMessageParts.tsx`（集成反馈按钮，项目无 `MessageBubble.tsx`）
  - 修改 `frontend/renderer/lib/api/chat.ts`（feedback API client，该文件未用 zod）
- **API**：5 个新端点（POST/GET 上述），SSE 事件 data 字段新增可选 `_tid`
- **数据**：新增 `data/agent_observation.db`（与 checkpointer 分库）
- **测试**：
  - 新增 `tests/python/unit/test_observation_sink.py`（SQLite 性能、崩溃恢复）
  - 新增 `tests/python/unit/test_observation_callback.py`（Callback 触发条件）
  - 新增 `tests/python/unit/test_langsmith_dual.py`（双写 + 降级）
  - 新增 `tests/python/unit/test_observation_api.py`（5 端点契约）
  - 新增 `tests/python/unit/test_feedback_implicit.py`（隐式信号）
  - 新增 `tests/python/unit/test_eval_feedback_export.py`（导出 → EvalCase）
  - 新增 `tests/renderer/MessageFeedback.test.tsx`（按钮 + popover）
- **文档**：更新 [AGENTS.md](file:///d:/java/agentprojects/agentx/AGENTS.md) §3 反面清单的"R1 自研 trace 协议"加例外说明

## Compliance with AGENTS.md §3 (反面清单)

| 反面清单条款 | 本提案如何合规 |
|---|---|
| R1（自研 trace 协议）| ✅ 100% 复用 LangSmith SDK + LangChain Callback + LangGraph checkpointer |
| R2（自研 ReAct 循环）| 不涉及 |
| R3（自研 RAG）| 不涉及 |
| R4（自研 checkpoint）| ✅ 复用 `SqliteSaver`（仅在 observation 库做轻量追加，不替换 checkpoint） |
| R5（自研 SSE）| ✅ 复用现有 `make_sse_event`（[utils/sse_events.py](file:///d:/java/agentprojects/agentx/backend/app/utils/sse_events.py)） |
| R11（自研消息截断）| ✅ 复用 LangChain `trim_messages`（后续 P2） |
| R12（自研 LLM 输出解析）| ✅ 复用 deepagents `GraderResponse`（feedback→eval 闭环） |
| R14（自研 checkpoint 序列化）| ✅ 复用 `SqliteSaver` |
| R15（自研 prompt 拼接）| ✅ 复用 `ChatPromptTemplate` 渲染结果作为"组装后 prompt"输入 |
| R16（自研 embedding）| 不涉及 |

## Future Extensibility

- P2: OpenTelemetry exporter（与 LangSmith 并存，向 Jaeger / Tempo 推送）
- P2: feedback → 自动化 prompt tuning（用 LLM-as-judge 反向优化 system_prompt）
- P3: 观测数据接入 BI 工具（导出 Parquet 到 `data/observation/exports/`，供 Metabase / DuckDB 分析）
- P3: 反向 replay 工具：根据 observation_run 重新执行 run_router，对比 LLM 升级前后的输出差异
- P3: 评测数据集市场：把"用户 👍 反馈 + 评测通过"的 case 沉淀为长期回归基线

## Open Questions

详见 [design.md](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-09-agent-observation-store/design.md) §Open Questions（5 项）。
