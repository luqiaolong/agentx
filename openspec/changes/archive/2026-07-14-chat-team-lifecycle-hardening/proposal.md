# Proposal: 聊天链路与 AgentTeam 链路生命周期硬化

## Why

本次深度审计确认，当前问题不是几个孤立 bug，而是四条核心链路缺少统一的生命周期约束：

1. 审批链路把决策仅绑定到 `thread_id`，没有和活跃审批请求、运行实例、工具调用建立强关联，存在 stale approval、预提交 approval、错误放开 `full_trust` 的风险。
2. 聊天运行链路缺少统一的 run/session 生命周期对象，导致 SSE 锁释放早于清理、abort 没有稳定终态、`/api/chat/compact` 与活跃流写 checkpoint 竞争、多会话前端互相污染。
3. AgentTeam 把“有 findings”误当作“执行成功”，失败子任务、聚合异常、部分结果持久化都可能最终被渲染成 `team_done: done`。
4. SSE 契约前后端已出现漂移：事件类型/字段不一致、`task_id` 缺失、`sandbox_escalation` 被错误降级、Team 错误终态会被后续 transport `done` 覆盖。
5. 还有一批已经暴露但未打通的能力缺口：`system_prompt` 字段未真正生效，Team role 未继承 chat model / project prompt / profile prompt，`DangerousTaskClassifier` 已存在但未接入执行主链，初始 Team task id 也不保证唯一。

这些问题叠加后，会在真实运行中形成“错误授权、状态串线、结果误报成功、刷新后状态丢失、团队 trace 难以追踪”的组合故障。

## What Changes

### A1. 审批请求生命周期显式化

- 新增独立的活跃审批请求注册表，审批以 `approval_id` 为主键，而不是只看 `thread_id`
- 每个审批请求显式绑定 `run_id`、`tool_call_id`、`request_kind`、创建时间、TTL
- `submit_approval` 必须命中一个仍然活跃且未消费的请求；stale / pre-approved / mismatched run 一律拒绝
- `full_trust` 只能由匹配当前活跃审批请求的决定开启
- 刷新恢复、轮询恢复、前端 pending-approval 回显都改为基于“活跃请求”，不再基于“已提交决定”

### A2. 聊天运行生命周期统一

- 为每个 `thread_id` 引入统一的 live run/session lifecycle 对象，持有 `run_id`、generation、stream ownership、cleanup state、abort/pause state
- 串行化 `chat` / `reset` / `compact` / `abort` 对同一线程的操作，旧 run 清理完成前不得放行新 run
- 保证 abort / cancel / approval-timeout 路径也会发出唯一终态事件，前端不再出现“流断了但没有 `done`/`error`”
- `compact` 不得与活跃流并发写 checkpoint；要么拒绝，要么在稳定快照上执行
- 前端流管理按 `thread_id` 隔离，后台会话事件不再被前台 singleton ref 吞掉
- `system_prompt` 从请求层进入 router / executor / team role，不再“传进来了但没用”

### A3. AgentTeam 结果语义收敛

- 引入规范化 `TeamOutcome = success | partial | error | aborted`
- 聚合器、Team 持久化、`team_done` 事件、前端 Team 卡片全部以 `outcome` 为准，不再用“是否有 findings”推断成功
- failed finding 不能满足成功质量门；聚合器异常、上游 abort、部分完成都要得到对应 outcome
- Team 历史记录和最终事件必须显式标记 partial / error / aborted，而不是落成 `done`

### A4. SSE 契约对齐与版本化

- 为共享事件补齐统一类型：`approval_request`、`team_done`、`done`、`sandbox_escalation`、`warning`、`replan`
- `delegation`、子任务总结、Team 最终汇总都补齐稳定 `task_id` / correlation 字段，支持重复角色、多次 replan、重试波次下的正确关联
- `sandbox_escalation` 必须端到端保真，不能被前端解析器错误映射成 `dangerous_tool`
- `team_done:error` 不能再被后续 transport `done` 覆盖成“成功完成”

### A5. Prompt / Safety / Planner 补线

- Team role 执行必须继承 chat model、project prompt、profile prompt、system prompt
- `DangerousTaskClassifier` 必须接入 Team 执行主链，对危险任务进行改写、阻断或升级
- 初始 Team 规划阶段必须保证 task id 唯一，后续 replan 也不能复用冲突 id

## Audit Coverage

本提案覆盖此前审计结论中的全部 P0/P1 项，以及需顺手补齐的 P2 实现缺口：

| 结论分组 | 本提案收口方式 |
|---|---|
| 审批 request/decision 绑定错误、pending recovery 错误 | A1 |
| SSE 锁清理 race、abort 无终态、compact race、多会话串线 | A2 |
| Team 全失败仍 done、聚合异常仍 done、重复角色关联失败 | A3 + A4 |
| `team_done:error` 被 `done` 覆盖、契约漂移、`sandbox_escalation` 失真 | A4 |
| `system_prompt` 无效、role prompt/model 未透传、classifier 未接线、task id 不唯一 | A5 |

## Capabilities

### New Capabilities

- `approval-request-lifecycle`
- `chat-run-lifecycle`

### Modified Capabilities

- `agent-team-v2`
- `sse-event-contract`

## Impact

- **后端**：`backend/app/api/chat.py`、`backend/app/security/approval/`、`backend/app/router/graph.py`、`backend/app/team/`、`backend/app/observation/`
- **前端**：`frontend/renderer/lib/api/chat.ts`、`frontend/renderer/hooks/useChatStream.ts`、`frontend/renderer/components/chat/`、`frontend/shared/api-types.ts`
- **文档**：`docs/agents/02-sse-event-contract.md`
- **测试**：新增 approval lifecycle、run lifecycle、team outcome、frontend stream reducer 回归测试

## Rollback Plan

1. 按 workstream 分阶段提交：A1、A2、A3/A4、A5 独立可回滚。
2. 契约变更优先采用向后兼容字段扩展：新字段上线后，旧字段保留一个迁移周期。
3. 若 `TeamOutcome` 改造影响现网 UI，可先保留旧 `status` 字段并由前端优先消费 `outcome`。
4. 若 run lifecycle 串行化导致明显吞吐下降，可先只保留“清理完成后放锁”与“compact 拒绝活跃流”，延后更细的 generation 管理。

## Out of Scope

- 本变更只生成 OpenSpec artifacts，不直接实现代码
- 不重新设计整个 Team 架构，不推翻 DeepAgents / LangGraph 既有使用边界
- 不新增外部依赖
