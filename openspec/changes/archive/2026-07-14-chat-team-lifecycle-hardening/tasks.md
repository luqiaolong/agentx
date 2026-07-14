# 任务追踪 — chat-team-lifecycle-hardening

## 预期产物

- [x] `openspec/changes/2026-07-14-chat-team-lifecycle-hardening/proposal.md`
- [x] `openspec/changes/2026-07-14-chat-team-lifecycle-hardening/design.md`
- [x] `openspec/changes/2026-07-14-chat-team-lifecycle-hardening/tasks.md`
- [x] `openspec/changes/2026-07-14-chat-team-lifecycle-hardening/specs/approval-request-lifecycle/spec.md`
- [x] `openspec/changes/2026-07-14-chat-team-lifecycle-hardening/specs/chat-run-lifecycle/spec.md`
- [x] `openspec/changes/2026-07-14-chat-team-lifecycle-hardening/specs/agent-team-v2/spec.md`
- [x] `openspec/changes/2026-07-14-chat-team-lifecycle-hardening/specs/sse-event-contract/spec.md`

## OpenSpec Tasks

| ID | 任务描述 | 涉及区域 | 验收标准 | 状态 |
|---|---|---|---|---|
| T1 | 建立审批生命周期 proposal/design | `backend/app/security/approval/`, `backend/app/api/chat.py` | artifacts 明确 `approval_id` / `run_id` / consume-once 模型 | [x] |
| T2 | 建立聊天运行生命周期 proposal/design | `backend/app/api/chat.py`, `backend/app/router/graph.py`, `frontend/renderer/lib/api/chat.ts`, `frontend/renderer/hooks/useChatStream.ts` | artifacts 明确 run session、cleanup gating、compact exclusion、多 session 隔离 | [x] |
| T3 | 建立 Team outcome 语义 proposal/design | `backend/app/team/`, `frontend/renderer/components/chat/parts/TeamNodeCard.tsx` | artifacts 明确 `TeamOutcome` 与 partial/error/aborted 规则 | [x] |
| T4 | 建立 SSE 契约对齐 proposal/design | `frontend/shared/api-types.ts`, `docs/agents/02-sse-event-contract.md` | artifacts 明确 `task_id`、`outcome`、`done.reason`、`sandbox_escalation` 保真 | [x] |
| T5 | 输出 approval lifecycle delta spec | `specs/approval-request-lifecycle/spec.md` | spec 覆盖 stale/preapproval/full-trust/pending-recovery | [x] |
| T6 | 输出 chat run lifecycle delta spec | `specs/chat-run-lifecycle/spec.md` | spec 覆盖 stream lock、abort terminal、compact race、多 session、system_prompt 透传 | [x] |
| T7 | 输出 agent-team-v2 delta spec | `specs/agent-team-v2/spec.md` | spec 覆盖 typed outcome、task correlation、classifier、role prompt/model、unique task id | [x] |
| T8 | 输出 sse-event-contract delta spec | `specs/sse-event-contract/spec.md` | spec 覆盖 typed events、team_done/error handling、warning/replan、sandbox_escalation | [x] |

## 建议实施顺序

### Phase 1: P0 安全与生命周期一致性

- [x] I1.1 在 `backend/app/security/approval/state.py` 及相关 API 引入 `approval_id`、活跃请求注册表、TTL、consume-once
- [x] I1.2 在 `backend/app/api/chat.py` / `backend/app/router/graph.py` 引入每线程 `run_id` 与 live run lifecycle 对象
- [x] I1.3 修复 stream lock cleanup 顺序，确保 cleanup 完成后才允许新 run
- [x] I1.4 修复 abort / cancel / approval-timeout 终态发射，确保前端总能收到唯一 terminal event
- [x] I1.5 阻止 `compact` 与活跃流并发操作 checkpoint
- [x] I1.6 为以上改动补后端单测：stale approval 拒绝、run generation 隔离、abort terminal、compact exclusion

### Phase 2: 前端流状态与 SSE reducer 收敛

- [x] I2.1 将 `frontend/renderer/lib/api/chat.ts` 和 `frontend/renderer/hooks/useChatStream.ts` 的连接/回调/ref 全部按 `thread_id` 管理
- [x] I2.2 修复后台 session 事件丢失、singleton ref 覆盖、`team_done:error` 被 `done` 覆盖的问题
- [x] I2.3 保留并正确解析 `sandbox_escalation`，不再强制映射为 `dangerous_tool`
- [x] I2.4 补共享类型：`warning`、`replan`、`team_done.blackboard`、`done.reason`
- [x] I2.5 为以上改动补前端单测：multi-session、terminal reducer、event parser、typed payload

### Phase 3: Team outcome 与关联键

- [x] I3.1 在 `backend/app/team/aggregator.py`、`nodes.py`、`runner.py` 引入 `TeamOutcome`
- [x] I3.2 修复“全失败仍 done”“聚合异常仍 done”“partial history 误记为 success”
- [x] I3.3 为 delegation / summary / final payload 补齐稳定 `task_id`
- [x] I3.4 让前端 Team 卡片和黑板以 `outcome + task_id` 为主键渲染
- [x] I3.5 为以上改动补 Team 单测：all-failed、partial、aggregate-error、repeat-role、replan correlation

### Phase 4: Prompt / Safety / Planner 补线

- [x] I4.1 打通 `system_prompt` 从请求到 router / executor 的透传
- [x] I4.2 让 Team role 继承 chat model、project prompt、profile prompt
- [x] I4.3 将 `DangerousTaskClassifier` 接入 Team 执行主链并补回归测试
- [x] I4.4 保证 planner 初始 task id 唯一，replan 追加时也不冲突
- [x] I4.5 更新 `docs/agents/02-sse-event-contract.md` 与相关说明文档

## 验证建议

- [x] V1. 后端：`uv run --project backend pytest tests/python/unit -q`
- [x] V2. 前端：`pnpm test`
- [x] V3. 类型：`pnpm typecheck`
- [x] V4. Team focused：验证 repeated role / replan / partial failure / aggregate failure / abort
- [x] V5. Chat focused：验证 approval refresh、abort、compact、background session streaming、system_prompt propagation

## 规模判定

- 涉及文件数: 10+ (backend approval/api/router/team/observation + frontend chat/stream/components/shared + docs + tests)
- 涉及模块数: 5 (security, api, router, team, frontend-renderer)
- 实施任务数: 21 (I1.1-I1.6 + I2.1-I2.5 + I3.1-I3.5 + I4.1-I4.5)
- 规模: **L（大改）** → 全流程：Worktree + TDD + 双轨 Review + 部署 + 归档
- 依赖关系: Phase 1 (approval/run lifecycle) 为 Phase 2/3/4 提供基础设施；Phase 2 (frontend) 与 Phase 3 (team) 可并行；Phase 4 依赖 Phase 1 的 system_prompt 透传链路

### 并行化策略

- **Wave A（串行基础）**: I1.1 + I1.2 (approval lifecycle + run lifecycle 核心数据结构)
- **Wave B（并行）**: I1.3/I1.4/I1.5 (cleanup/abort/compact) ∥ I3.1 (TeamOutcome 类型) ∥ I4.1 (system_prompt 透传链路)
- **Wave C（并行）**: I2.1-I2.4 (前端流) ∥ I3.2-I3.4 (team outcome 应用) ∥ I4.2-I4.4 (role prompt/classifier/task id)
- **Wave D（测试）**: I1.6 ∥ I2.5 ∥ I3.5 ∥ I4.5 (docs)

