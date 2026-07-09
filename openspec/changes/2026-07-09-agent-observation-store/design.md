# Design: Agent 观测中心 — LangSmith SDK + LangChain Callback 统一可观测层

## Context

AgentX 是一个本地优先的 AI 智能体桌面应用（FastAPI + LangGraph + DeepAgents + Tauri/React），其可观测能力目前**碎片化**在多个模块中：

- **链路追踪**：[observability/trace.py](file:///d:/java/agentprojects/agentx/backend/app/observability/trace.py) 的 16 字符 hex trace_id + `bind_trace` ContextVar
- **结构化日志**：[observability/logger.py](file:///d:/java/agentprojects/agentx/backend/app/observability/logger.py) loguru 双 sink（stderr + `data/logs/backend.log`）
- **LangSmith 占位**：[observability/langsmith.py](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py) `trace_span` 上下文管理器（M1 阶段以 `logger.debug` 占位，M2 待接入）
- **会话状态**：[memory/checkpointer.py](file:///d:/java/agentprojects/agentx/backend/app/memory/checkpointer.py) `SqliteSaver`（存 `messages` channel） + [memory/checkpointer_view.py](file:///d:/java/agentprojects/agentx/backend/app/memory/checkpointer_view.py) 只读视图
- **离线评测**：[eval/](file:///d:/java/agentprojects/agentx/backend/app/eval/) 完整 EvalRunner + 3 层 Judge + Reporter + Mock

**问题**：
1. 上述 5 块**互不串联**——trace_id 只在日志里，LangSmith 占位不接，checkpointer 只存 messages，prompt 拼好就丢，tool_call 关闭窗口即丢
2. 用户报问题时只能 `grep backend.log`，无法回放整次会话
3. 评测 case 全部手工 YAML，**与线上真实用户问题完全断链**
4. 缺一个"用户对回复质量的反馈通道"，agent 优化无数据支撑

**机会**：LangChain 生态已提供完整链路能力，本提案**100% 复用现成框架**，仅在"串联"层做薄薄胶水：
- `langsmith` Python SDK（trace 上传 + remote dashboard）
- LangChain `BaseCallbackHandler`（`on_llm_start` / `on_tool_start` / `on_chain_end` 等钩子）
- LangGraph `SqliteSaver`（状态快照）
- deepagents `RubricMiddleware`（feedback → eval 闭环）
- LangChain `ChatPromptTemplate`（prompt 渲染结果落盘）

## Goals / Non-Goals

**Goals:**
- 把 5 个维度的可观测数据**收敛到一张表**（4 张 SQLite 表 + 1 个 SQLite 文件）
- trace_id 全链路贯穿（前端 → run_id → SSE → LangSmith remote → 后端日志）
- LangSmith 占位升级为真实 SDK，本地 + remote 双写，凭证缺失时降级为本地 only
- 用户显式反馈（👍/👎 + 分类 + 评论）+ 隐式信号（approval/abort/auto_approve）
- 反馈数据可一键导出为 EvalCase 接入 [eval/runner.py](file:///d:/java/agentprojects/agentx/backend/app/eval/runner.py) 回归门禁
- 观测 5 维度全部**不破坏现有 SSE 事件契约**（仅在 `data` 字段新增可选 `_tid`）

**Non-Goals:**
- 不修改 LangGraph checkpointer 行为（[memory/checkpointer.py](file:///d:/java/agentprojects/agentx/backend/app/memory/checkpointer.py) 已满足需求）
- 不修改 Router 三路径分发逻辑（[router/graph.py::run_router](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py#L126) 只增加 hook 点）
- 不替换现有 loguru 日志（stderr + file sink 继续工作）
- 不做 OpenTelemetry 接入（P2 扩展）
- 不做 BI 工具集成 / Parquet 导出（P3 扩展）
- 不改 LangSmith 协议本身（langsmith-python SDK 是事实标准）

## Decisions

### Decision 1: SQLite 独立分库 vs 复用 checkpointer 同库

**选择**: 独立文件 `data/agent_observation.db`（与 [sandbox/store.py](file:///d:/java/agentprojects/agentx/backend/app/sandbox/store.py) 同款 SQLite + WAL + busy_timeout=30000 配置）。

**理由**:
- observation 库是**高频 append**（单次 chat 可能 1000+ 事件），与 checkpointer 的低频 checkpoint 写做**写负载隔离**，避免高频事件 append 阻塞 router
- 现有 `data/agentx.db` 已被 checkpointer + sandbox **共用**（[memory/checkpointer.py](file:///d:/java/agentprojects/agentx/backend/app/memory/checkpointer.py) 与 [sandbox/store.py](file:///d:/java/agentprojects/agentx/backend/app/sandbox/store.py) 均用 `_DB_FILENAME = "agentx.db"`），再叠加高频 observation 写会加剧锁竞争
- 备份 / 清理策略可独立配置（observation 库可设 TTL 30 天自动清理，checkpointer 永久保留）
- 数据库结构变更不影响 checkpointer 的 langgraph schema 升级

**替代方案**:
- 复用 `data/agentx.db` 多 schema → 拒绝：observation 高频 append 与 checkpointer/sandbox 共用库会放大写竞争 + 备份粒度粗 + schema 演进冲突
- JSONL append-only 文件 → 拒绝：缺乏索引和事务保证，查询性能差

### Decision 2: LangSmith 真实 SDK 接入 + 本地双写

**选择**: 在 [observability/langsmith.py](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py) 现有 `trace_span` 基础上接入 `langsmith.trace` 真实 SDK，新建 [observability/langsmith_dual.py](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith_dual.py) 做双写 helper：
- 本地：必写 `data/agent_observation.db`（保证本地可查）
- 远程：凭证存在且 `LANGSMITH_TRACING_V2=true` 时调 `langsmith.trace()` 上传
- 凭证缺失 / 网络失败：降级为本地 only，记 warning 日志

**理由**:
- LangSmith remote dashboard 提供跨 trace 的"调用链时间线" + LLM 成本分析
- 双写避免单点：remote 不可用不影响本地调试
- `LANGSMITH_TRACING_V2` 是 langsmith-python 官方开关，零侵入
- 凭证注入走 tauri-plugin-store（[AGENTS.md §18](file:///d:/java/agentprojects/agentx/AGENTS.md) 安全红线），**不**改 `Settings.env_file=None`

**替代方案**:
- 仅本地不上传 → 拒绝：失去跨会话 / 跨用户 trace 关联能力
- OpenTelemetry 替代 → P2 扩展（与 LangSmith 并存）

### Decision 3: LangChain `BaseCallbackHandler` 而非手写事件订阅

**选择**: 新建 `ObservationCallback(BaseCallbackHandler)`，挂到 LangChain `agent.astream(..., config={"callbacks": [cb]})`。自动捕获：
- `on_llm_start` / `on_llm_end` → 写入 LLM 输入输出（含 token 数）
- `on_tool_start` / `on_tool_end` → 写入 tool_call 完整生命周期（含 args redact + result 截断）
- `on_chain_start` / `on_chain_end` → 写入 LangGraph 节点级别 span

**理由**:
- 框架已提供完整事件订阅 + parent-child 关系维护，**不重写**
- 钩子触发时机与 LangGraph `astream` 一致，避免漏事件
- 跨子代理 / 跨 LangGraph node 都能捕获（work/coding/team 三个场景都覆盖）

**替代方案**:
- 手写 `astream_events` 解析 → 拒绝：违反 AGENTS.md §3 R1/R5
- monkeypatch `make_sse_event` 写 sink → 拒绝：仅捕获 SSE 输出，丢失 LLM 中间态

### Decision 4: 4 张表 + `trace_id` 锚点

**选择**:
```sql
-- 表 1：一次完整 chat 请求 = 一行
CREATE TABLE observation_run (
  run_id TEXT PRIMARY KEY,        -- UUID4 hex[:16]，与 trace_id 一致
  trace_id TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  agent_mode TEXT NOT NULL,        -- work/coding/coding_team
  permission_mode TEXT,            -- standard/full_trust
  user_message TEXT NOT NULL,
  workspace_path TEXT,
  final_prompt TEXT,               -- 组装后 system_prompt
  history_preview TEXT,            -- 前 4 条消息截断
  result_text TEXT,                -- 完整 assistant 回复
  result_token_count INTEGER,
  duration_ms INTEGER,
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP,
  error_type TEXT,
  error_message TEXT
);

-- 表 2：每条 SSE 事件 = 一行，append-only
CREATE TABLE observation_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,            -- 同 run 内自增
  ts TIMESTAMP NOT NULL,
  event_type TEXT NOT NULL,        -- token/reasoning/tool_call/tool_result/approval_request/delegation/...
  payload_json TEXT NOT NULL,      -- data 字段 JSON 序列化（敏感字段 redact）
  UNIQUE(run_id, seq)
);

-- 表 3：tool_call 聚合行（便于分析）
CREATE TABLE observation_tool_call (
  tool_call_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  call_seq INTEGER,
  args_json TEXT,                  -- redact 后
  result_preview TEXT,             -- 截断 2000 字符
  result_token_count INTEGER,
  duration_ms INTEGER,
  approval_decision TEXT,          -- approve/deny/once/session/NULL
  approved INTEGER,                -- 0/1
  error_message TEXT,
  started_at TIMESTAMP,
  ended_at TIMESTAMP
);

-- 表 4：用户反馈
CREATE TABLE observation_feedback (
  feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,               -- thumb_up/thumb_down/rating/note/implicit_ok/implicit_bad
  score REAL,                       -- 1-5 (rating 类型)
  comment TEXT,
  categories_json TEXT,            -- JSON 数组：["fact_error","tone",...]
  created_at TIMESTAMP NOT NULL
);
```

**理由**:
- `run_id` = `trace_id`，单一锚点关联 4 张表
- `observation_event` append-only，不修改 / 不删除（保证审计完整）
- `observation_tool_call` 聚合行便于 SQL 分析（"哪个 tool 失败率最高"）
- `observation_feedback` 独立表便于未来多维分类

### Decision 5: prompt 落盘用 ChatPromptTemplate 渲染结果

**选择**: 在 [router/graph.py::run_router](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py#L230-L275) 拼好 `profile_prompt` 后，调 `sink.record_prompt(run_id, system_prompt=profile_prompt, user_message=cleaned_message, history_preview=...)` 一次性落盘。

**理由**:
- **不**重新渲染或二次拼装（直接用已拼好的字符串 + 模板版本号）
- profile + project + skill 三段拼接结果保留 100% 真实 LLM 输入
- 模板版本号 `prompt_template_version` 关联配置变更，便于对比 LLM 升级前后的输入差异

**替代方案**:
- 存原始变量（profile_content / project_prompt / skill_content）后查时拼 → 拒绝：增加重建复杂度，违反"所见即所得"

### Decision 6: 状态快照 3 个时间点（start / mid / end）

**选择**: 在 router 关键节点调 `checkpointer.aget(config)` 拿 channel_values，写轻量 JSON 快照到 `observation_run.state_snapshots_json`：
- **start**：dispatch 前（含初始 messages + authorized_dirs）
- **mid**：approve resume 后（含 approval 写入后的新 messages）
- **end**：yield done 前（含最终 messages + 任何 L3 `_rubric_status`）

**理由**:
- 复用 LangGraph `SqliteSaver`（[memory/checkpointer.py](file:///d:/java/agentprojects/agentx/backend/app/memory/checkpointer.py)）的 `aget` 接口
- 不破坏 checkpoint 内部 schema，仅在 observation 库做镜像
- 3 个时间点覆盖 95% 调试场景，避免 N 个节点的存储爆炸

**替代方案**:
- 每次 state 变化都快照 → 拒绝：N 倍存储，N 倍写延迟
- 完全不存 state → 拒绝：失去"为什么 agent 在某步做了 X"的诊断能力

### Decision 7: 显式反馈 UI 在 assistant 消息气泡右侧

**选择**: 在 frontend/renderer/components/chat/AssistantMessageParts.tsx 的 assistant 消息气泡**右侧**加 👍/👎 两个图标按钮（参考 [UserMessageBubble.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/UserMessageBubble.tsx) L118-126 的"右侧按钮"模式：`Pencil` 编辑按钮在 `flex justify-end` 容器 hover 态浮现）：
- 👍：点击直接写 `feedback(kind=thumb_up)`
- 👎：点击展开 popover 选分类（fact_error / tone / speed / wrong_tool / other）+ 可选评论

**理由**:
- 与现有 `UserMessageBubble.tsx` 编辑按钮位置一致，对称性好（注意：项目无 `MessageBubble.tsx`，assistant 消息渲染走 `AssistantMessageParts.tsx`）
- 不打扰对话流（hover 触发 / 默认显示待确认）
- 分类下拉避免"👎 不知道为什么"的不可分析反馈

**替代方案**:
- 工具栏的"复盘中心"入口 → 拒绝：外置能力，与单条反馈粒度不符
- 消息底部 hover 浮现 → 拒绝：保持聊天流纯净，但录入路径长

### Decision 8: 隐式信号 3 种类型

**选择**: 在 [observability/feedback.py](file:///d:/java/agentprojects/agentx/backend/app/observability/feedback.py) 统一埋点：
- `implicit_ok`：auto_approve 倒计时归零 + 工具执行成功 → 隐式好评
- `implicit_bad`（reason=aborted）：用户主动 abort
- `implicit_bad`（reason=rejected_dangerous_tool）：审批 deny

**理由**:
- 不打扰用户的前提下积累反馈数据
- 与显式 👍/👎 区分（kind 字段标识），不污染显式反馈分析
- 隐式信号可视为"行为 proxy"，与显式信号可对照分析

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| [Risk] SQLite 写延迟在 1k+ 事件/秒时阻塞 router | [Mitigation] 全部写入走 `asyncio.to_thread`；SQLite 用 WAL 模式 + 单独连接；吞吐测试 [test_observation_sink.py] 验证 1000 事件 < 200ms |
| [Risk] LangSmith SDK 升级破坏 trace_span 签名 | [Mitigation] 现有 `trace_span` 上下文管理器签名保持不变，langsmith 真实 SDK 套在外面做适配（[observability/langsmith_dual.py](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith_dual.py)） |
| [Risk] 用户反馈可能含敏感信息（API key / 密码） | [Mitigation] **复用 [observability/langsmith.py::redact](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py#L25-L41) 的 5 字段黑名单** + 反馈写入前先 `redact(comment)` |
| [Risk] Observation 库无限增长导致磁盘爆满 | [Mitigation] 后台 `cleanup_old_observations()` 协程（lifespan 启动），默认 TTL 30 天，可配；不删 run_id 关联的 feedback（feedback 永久保留） |
| [Risk] 前端 SSE `_tid` 字段破坏旧前端解析 | [Mitigation] `_tid` 仅添加不删除；前端 [lib/api/chat.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/lib/api/chat.ts) 未用 zod，SSE 解析用 `as unknown as ChatEvent` 类型断言（结构宽松，自动忽略未知字段）；正式版在 [shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts) 补 `_tid?: string` 类型 |
| [Risk] 显式反馈按钮在 mobile / 小窗口被遮挡 | [Mitigation] 按钮在 hover 态浮现，默认不占空间；移动端 fallback 到长按菜单 |
| [Risk] SQLite 跨进程锁（CLI 直连 + Tauri 后端同时打开）| [Mitigation] 复用 [sandbox/store.py](file:///d:/java/agentprojects/agentx/backend/app/sandbox/store.py) 的 `busy_timeout=30000` + `PRAGMA journal_mode=WAL`；CLI 模式下走 `app observation list` 端点（不直连） |
| [Risk] LangSmith API key 注入走环境变量破坏 `Settings.env_file=None` 安全红线 | [Mitigation] 走 [src-tauri/src/backend/env.rs::build_env](file:///d:/java/agentprojects/agentx/src-tauri/src/backend/env.rs) 注入，**不**改 Settings；`LANGSMITH_API_KEY` 注入已存在（env.rs L47-49），本次仅需补 `LANGSMITH_TRACING_V2` + `LANGSMITH_PROJECT`；凭证在 tauri-plugin-store 中 `enc:` 加密 |

## Migration Plan

### Phase 1: 观测基础设施（3 天）

1. 新建 [backend/app/observability/observation.py](file:///d:/java/agentprojects/agentx/backend/app/observability/observation.py)：
   - `ObservationSink` 协议（`append_event` / `record_prompt` / `record_state_snapshot` / `write_feedback` / `close`）
   - `SqliteObservationSink` 实现（含 WAL 配置）
   - `ObservationCallback(BaseCallbackHandler)` 实现
2. 修改 [backend/app/main.py::lifespan](file:///d:/java/agentprojects/agentx/backend/app/main.py#L97)：启动时建表 + 注入 `observation_sink` 单例
3. 新建 `tests/python/unit/test_observation_sink.py`：性能 + 崩溃恢复 + 字段类型
4. 集成验证：手工触发一次 chat，确认 `data/agent_observation.db` 有 1 行 `observation_run` + N 行 `observation_event`

### Phase 2: LangSmith 真实接入（2 天）

1. 修改 [backend/app/observability/langsmith.py](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith.py)：保留 `redact` + `trace_span` 签名，内部切换为 `langsmith.trace` 真实调用
2. 新建 [backend/app/observability/langsmith_dual.py](file:///d:/java/agentprojects/agentx/backend/app/observability/langsmith_dual.py)：双写 helper，本地 SQLite + LangSmith remote
3. 修改 `pyproject.toml`：新增 `langsmith>=0.1.0`
4. 修改 [.env.example](file:///d:/java/agentprojects/agentx/.env.example)：补充 LangSmith 配置文档
5. 新建 `tests/python/unit/test_langsmith_dual.py`：双写路径 + 降级路径
6. 集成验证：本地 dev 模式下不设 LangSmith 凭证，确认仅本地写入；测试环境设 `LANGSMITH_TRACING_V2=true` 确认上传

### Phase 3: 五维度串联（3 天）

1. 修改 [backend/app/router/graph.py::run_router](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py#L126)：
   - dispatch 前调 `sink.record_prompt(...)`
   - yield done 前调 `sink.record_state_snapshot(...)`（3 个时间点）
2. 修改 [backend/app/deep/streaming.py](file:///d:/java/agentprojects/agentx/backend/app/deep/streaming.py)：每个 `yield make_*_event` 前调 `sink.append_event(...)`
3. 修改 [backend/app/api/chat.py](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py)：
   - `_event_generator` 入口写 `run_start`（含 trace_id 锚定）
   - `chat_approve` 按 trace_id 回填 `approval_decision`
4. 新建 `tests/python/unit/test_observation_callback.py`：Callback 触发条件（LLM start/end、tool start/end）
5. 集成验证：触发一次含审批的 chat，确认 `observation_tool_call` 中 `approval_decision` 字段被正确回填

### Phase 4: 用户反馈端点 + 前端按钮（3 天）

1. 新建 [backend/app/api/observation.py](file:///d:/java/agentprojects/agentx/backend/app/api/observation.py)：5 个端点
2. 修改 [backend/app/api/__init__.py](file:///d:/java/agentprojects/agentx/backend/app/api/__init__.py)：注册路由
3. 新建 [backend/app/observability/feedback.py](file:///d:/java/agentprojects/agentx/backend/app/observability/feedback.py)：隐式信号埋点
4. 新建 `frontend/renderer/components/chat/MessageFeedback.tsx`：👍/👎 按钮 + popover
5. 修改 `frontend/renderer/components/chat/MessageBubble.tsx`：集成反馈按钮（按"右侧按钮"规范）
6. 修改 `frontend/renderer/lib/api/chat.ts`：feedback API client
7. 新建 `tests/python/unit/test_observation_api.py`：5 端点契约
8. 新建 `tests/python/unit/test_feedback_implicit.py`：隐式信号 3 种类型
9. 新建 `tests/renderer/MessageFeedback.test.tsx`：按钮 + popover 交互

### Phase 5: 反馈 → 评测闭环（2 天）

1. 修改 [backend/app/eval/cli.py](file:///d:/java/agentprojects/agentx/backend/app/eval/cli.py)：新增 `export-feedback` 子命令
2. 新建 `tests/python/unit/test_eval_feedback_export.py`：导出 → EvalCase 转换
3. 集成验证：手工 👎 几条 → `agentx eval export-feedback` → `tests/eval/suites/feedback-YYYYMMDD.yaml` 出现 → `agentx eval run --suite feedback-XXX` 跑通

### Phase 6: 文档 + AGENTS.md 更新（1 天）

1. 更新 [AGENTS.md](file:///d:/java/agentprojects/agentx/AGENTS.md) §3 反面清单：明确"自研 trace 协议"的例外（已用 LangSmith SDK + LangChain Callback）
2. 更新 [AGENTS.md](file:///d:/java/agentprojects/agentx/AGENTS.md) §3 反面清单：明确"自研评估器"的例外（已用 deepagents `RubricMiddleware`）
3. 更新 [.env.example](file:///d:/java/agentprojects/agentx/.env.example)：补全 LangSmith 凭证注入流程
4. 更新 README：增加"观测 / 反馈 / 复盘"章节

## Open Questions

1. **LangSmith 上传是否需要按 trace_id 分桶？** → 当前 langsmith-python SDK 默认按 `LANGSMITH_PROJECT` 聚合，**不**做额外分桶。若未来需要"按用户分桶"再扩展。
2. **observation 库 TTL 30 天是否合理？** → 默认 30 天，可配 `AGENTX_OBSERVATION_TTL_DAYS`。feedback 行**不**走 TTL（永久保留，与 run 关联）。
3. **是否需要 observation 库的"导出 / 导入"工具？** → P3 暂不做。CLI 暴露 `agentx observation list` + `agentx observation export` 两个最小端点即可。
4. **SSE 事件 `data` 字段新增 `_tid` 是否需要前端 schema 同步？** → 需要。`frontend/renderer/lib/api/chat.ts` 未用 zod，SSE 解析走 `as unknown as ChatEvent` 类型断言（结构宽松，自动忽略未知字段，旧前端不受影响）；正式版要在 `frontend/shared/api-types.ts` 的 `ChatEvent` 各变体补充 `_tid?: string` 类型（`trace_id?: string` 字段已存在，`_tid` 为同语义别名便于前端直接展示）。
5. **EvalRunner 的 `chat_model` 注入是否支持 replay 时用原 LLM（而非 Mock）？** → 当前已支持 `chat_model=None`（走 `get_chat_model` 真实 LLM）。`export-feedback` 子命令可选 `--mock` / `--live` 模式。

## ADR 候选

若本提案对 R1 / R14 / R15 反面清单有"扩展现成"的解释，需在 `docs/decisions/ADR-XXXX-observation-store.md` 写明。本提案判定**无** ADR 需要，因为：
- 观测 4 表是"串联层"，不是"协议层"
- 唯一可能的"自研"是 `SqliteObservationSink`，但 SQLite 仅是存储介质，schema 是 LangChain Callback 标准事件类型，**不是协议**
- 复用 LangChain `BaseCallbackHandler` + LangGraph `aget` + LangSmith `trace` 三个现成 API，仅做 1 个 `ObservationSink` 抽象

**结论**：所有 4 张表都是"现成 API 的存储后端"，无 ADR 必要。
