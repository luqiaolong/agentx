# 任务追踪 — Agent 观测中心

## 阶段一：观测基础设施（SqliteObservationSink + Callback）

- [ ] T1.1 新建 `backend/app/observability/observation.py`：
  - `ObservationSink` 协议（5 个方法：append_event / record_prompt / record_state_snapshot / write_feedback / close）
  - `SqliteObservationSink` 实现：4 张表 + WAL + busy_timeout=30000
  - `ObservationCallback(BaseCallbackHandler)`：on_llm_start / on_llm_end / on_tool_start / on_tool_end
  - 数据库初始化 helper（首次启动建表）
- [ ] T1.2 修改 `backend/app/main.py` lifespan：
  - 启动时调 `get_observation_sink()` 单例
  - shutdown 时 `sink.close()`
- [ ] T1.3 新建 `tests/python/unit/test_observation_sink.py`：
  - 性能测试：append 1000 event < 200ms
  - 崩溃恢复：异常退出后重连数据不丢
  - 字段类型校验（4 表 schema 与 spec 一致）
  - redact 行为（`*_KEY` 字段被替换为 `<redacted>`）

## 阶段二：LangSmith 真实 SDK 接入

- [ ] T2.1 修改 `backend/app/observability/langsmith.py`：
  - 保留 `redact()` 与 `trace_span()` 公开签名
  - 内部切换为 `from langsmith import trace` 真实调用
  - `trace_span` 增加 `run_id` / `thread_id` 入参（透传 trace_id 锚点）
- [ ] T2.2 新建 `backend/app/observability/langsmith_dual.py`：
  - `dual_trace()` contextmanager：本地 SQLite（必写）+ LangSmith remote（凭证存在时）
  - 凭证缺失 / 网络失败降级为本地 only，记 warning
- [ ] T2.3 修改 `pyproject.toml`：
  - 新增 `langsmith>=0.1.0` 依赖
- [ ] T2.4 修改 `.env.example`：
  - 补充 `LANGSMITH_API_KEY` / `LANGSMITH_TRACING_V2` / `LANGSMITH_PROJECT` 文档
- [ ] T2.5 新建 `tests/python/unit/test_langsmith_dual.py`：
  - 双写路径（凭证存在 → 本地 + remote 各 1 次）
  - 降级路径（凭证缺失 → 仅本地 + 1 条 warning）
  - 凭证注入走 tauri-plugin-store（monkeypatch env vars）

## 阶段三：五维度串联

- [ ] T3.1 修改 `backend/app/router/graph.py::run_router`：
  - dispatch 前调 `sink.record_prompt(run_id, system_prompt=profile_prompt, user_message=cleaned_message, history_preview=...)`
  - yield done 前调 `sink.record_state_snapshot(run_id, kind="end", state=checkpointer.aget(...))`
  - 3 个时间点：start（dispatch 前）/ mid（approve resume 后）/ end（done 前）
- [ ] T3.2 修改 `backend/app/deep/streaming.py`：
  - 每个 `yield make_*_event` 前调 `sink.append_event(run_id, seq, event_type, payload)` （asyncio.to_thread 包装）
- [ ] T3.3 修改 `backend/app/api/chat.py::_event_generator`：
  - 入口生成 `run_id` = `trace_id`（沿用 `bind_trace`），写 `observation_run.start`
  - 出口写 `observation_run.end`（含 duration_ms / result_text / result_token_count）
  - 异常分支写 `observation_run.end` + `error_type` / `error_message`
- [ ] T3.4 修改 `backend/app/api/chat.py::chat_approve`（函数定义在 L155，L154 是装饰器）：
  - 按 `thread_id` 找到最近 `approval_request` 事件，回填 `approval_decision` / `auto_approved`
- [ ] T3.5 新建 `tests/python/unit/test_observation_callback.py`：
  - Callback 触发条件（on_llm_start / on_tool_start / on_chain_end）
  - args redact 行为
  - 异常隔离（callback 抛错不影响 agent 主流程）

## 阶段四：用户反馈端点 + 前端按钮

- [ ] T4.1 新建 `backend/app/api/observation.py`：
  - `POST /api/observation/feedback` — 写 feedback（含 redact）
  - `GET /api/observation/feedback?run_id=...` — 查 feedback
  - `GET /api/observation/runs?thread_id=...&limit=...` — 列 run
  - `GET /api/observation/runs/{run_id}` — 单 run 详情（含 prompt / state snapshot）
  - `GET /api/observation/runs/{run_id}/events` — 事件流
- [ ] T4.2 修改 `backend/app/api/__init__.py`：
  - `register_observation_routes(app)` 注册 5 个端点
- [ ] T4.3 新建 `backend/app/observability/feedback.py`：
  - `record_implicit_ok(run_id, reason)` — auto_approve 倒计时归零 + 工具成功
  - `record_implicit_bad(run_id, reason)` — 用户 abort
  - `record_implicit_bad(run_id, reason="rejected_dangerous_tool")` — 审批 deny
- [ ] T4.4 新建 `frontend/renderer/components/chat/MessageFeedback.tsx`：
  - 👍 按钮（点击直接写 thumb_up）
  - 👎 按钮（点击展开 popover：分类下拉 + 评论输入框）
  - 复用 [usePopover.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/components/ui/hooks/usePopover.ts) 状态管理（注意：实际路径在 `components/ui/hooks/`，非 `hooks/`）
- [ ] T4.5 修改 `frontend/renderer/components/chat/AssistantMessageParts.tsx`：
  - 在 assistant 消息气泡**右侧**（参考 [UserMessageBubble.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/UserMessageBubble.tsx) L118-126 编辑按钮的"右侧布局"）集成 `<MessageFeedback />`
  - hover 态浮现，默认不占空间（项目无 `MessageBubble.tsx`）
- [ ] T4.6 修改 `frontend/renderer/lib/api/chat.ts`：
  - 新增 `apiPost('/api/observation/feedback', { run_id, kind, score?, comment?, categories? })` 客户端
- [ ] T4.7 修改 `frontend/shared/api-types.ts`：
  - 补充 `FeedbackKind` / `FeedbackRequest` / `ObservationRun` / `ObservationEvent` 类型
  - SSE 事件 data 字段类型扩展 `& { _tid?: string }`
- [ ] T4.8 新建 `tests/python/unit/test_observation_api.py`：
  - 5 端点契约（请求/响应 schema）
  - 权限：feedback 写入限同 thread_id 用户
- [ ] T4.9 新建 `tests/python/unit/test_feedback_implicit.py`：
  - 隐式信号 3 种类型
- [ ] T4.10 新建 `tests/renderer/MessageFeedback.test.tsx`：
  - 👍 按钮点击 → API 调用
  - 👎 按钮 → popover 展开 + 分类下拉 + 评论提交
  - hover 态浮现 / 默认隐藏

## 阶段五：反馈 → 评测闭环

- [ ] T5.1 修改 `backend/app/eval/cli.py`：
  - 新增 `export-feedback` 子命令
  - 入参：`--days=30`（默认）/ `--output-dir=tests/eval/suites/`
  - 行为：查 `observation_feedback` 中 `kind=thumb_down` 行 → 关联 `observation_run.user_message` → 转 `EvalCase` YAML
  - YAML schema 兼容 [eval/models.py::EvalCase](file:///d:/java/agentprojects/agentx/backend/app/eval/models.py#L54)
- [ ] T5.2 新建 `tests/python/unit/test_eval_feedback_export.py`：
  - 10 条 thumb_down → 1 个 YAML 文件，case 数 = 10
  - 字段映射正确（user_message / agent_mode / comment → rubric）
- [ ] T5.3 集成验证：
  - 手工 👎 3 条
  - `agentx eval export-feedback` → YAML 出现
  - `agentx eval run --suite feedback-YYYYMMDD --mock` → L1 通过

## 阶段六：文档 + AGENTS.md 更新

- [ ] T6.1 更新 `AGENTS.md` §3 反面清单：
  - 明确"自研 trace 协议"的例外（已用 LangSmith SDK + LangChain Callback）
  - 明确"自研评估器"的例外（已用 deepagents `RubricMiddleware`）
- [ ] T6.2 更新 `.env.example`：
  - 补全 LangSmith 凭证注入流程（运行时不会读，仅文档）
  - 补全 observation TTL 配置 `AGENTX_OBSERVATION_TTL_DAYS`
- [ ] T6.3 更新 `README.md`：
  - 增加"观测 / 反馈 / 复盘"章节
  - 说明 SSE 事件 data 字段新增 `_tid` 的兼容性

## 预期修改文件

### 新建文件（10 个）
- `backend/app/observability/observation.py` — SqliteObservationSink + Callback
- `backend/app/observability/langsmith_dual.py` — 双写 helper
- `backend/app/observability/feedback.py` — 隐式信号
- `backend/app/api/observation.py` — 5 端点
- `frontend/renderer/components/chat/MessageFeedback.tsx` — 反馈按钮
- `tests/python/unit/test_observation_sink.py` — sink 单元测试
- `tests/python/unit/test_observation_callback.py` — callback 单元测试
- `tests/python/unit/test_langsmith_dual.py` — 双写 + 降级
- `tests/python/unit/test_observation_api.py` — 5 端点契约
- `tests/python/unit/test_feedback_implicit.py` — 隐式信号
- `tests/python/unit/test_eval_feedback_export.py` — 导出
- `tests/renderer/MessageFeedback.test.tsx` — 前端组件

### 修改文件（10 个）
- `backend/app/observability/langsmith.py` — 占位 → 真实 SDK
- `backend/app/main.py` — lifespan 注入
- `backend/app/api/chat.py` — _event_generator + chat_approve
- `backend/app/api/__init__.py` — 注册路由
- `backend/app/router/graph.py` — prompt + snapshot 写入点
- `backend/app/deep/streaming.py` — 事件 append 点
- `backend/app/eval/cli.py` — export-feedback 子命令
- `pyproject.toml` — 新增 `langsmith>=0.1.0`（位于项目根目录，非 `backend/`）
- `frontend/renderer/components/chat/AssistantMessageParts.tsx` — 集成反馈按钮
- `frontend/renderer/lib/api/chat.ts` — feedback API client
- `frontend/shared/api-types.ts` — FeedbackKind / ObservationRun / _tid 类型
- `AGENTS.md` — §3 反面清单例外说明
- `.env.example` — LangSmith + observation TTL 文档
- `README.md` — 观测 / 反馈 / 复盘章节

## 规模判定

- 涉及文件数: 24（12 新建 + 12 修改）
- 涉及模块数: 5（backend/observability + backend/api + backend/router + backend/deep + backend/eval + frontend）
- 跨语言: Python + TypeScript
- 跨层: 后端 / 前端 / 数据 / 依赖
- 规模: **XL（大改）** — 24 文件 + 5 模块 + 3 新能力 + 3 修订能力，需全流程执行 + 多次评审

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | 观测基础设施（sink + callback）| observation.py / main.py / test_observation_sink.py | 4 表建表 + 1000 event < 200ms + 崩溃恢复 | ☐ |
| T2 | LangSmith 真实 SDK 接入 | langsmith.py / langsmith_dual.py / pyproject.toml / .env.example | 双写 + 降级 + `langsmith>=0.1.0` 声明 | ☐ |
| T3 | 五维度串联 | router/graph.py / deep/streaming.py / api/chat.py / test_observation_callback.py | prompt 落盘 + state snapshot 3 点 + event append | ☐ |
| T4 | 用户反馈端点 + 前端 | api/observation.py / feedback.py / MessageFeedback.tsx / MessageBubble.tsx / 共享类型 / 4 个测试 | 5 端点契约 + 👍/👎 UI + 隐式 3 信号 | ☐ |
| T5 | 反馈 → 评测闭环 | eval/cli.py / test_eval_feedback_export.py | export-feedback 子命令 + YAML 转换 + replay 跑通 | ☐ |
| T6 | 文档 + AGENTS 更新 | AGENTS.md / .env.example / README.md | 反面清单例外说明 + LangSmith 文档 + 观测章节 | ☐ |
