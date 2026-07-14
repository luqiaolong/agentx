# Proposal: AgentTeam 黑板快照（SSE 一次性）

## Why

档位 A 已落地：前端 `BlackboardPanel` 纯前端聚合 `TeamAgentState[]`（done/error）展示 findings/errors，
但**只能展示「最终态」，无法体现后端黑板的完整信息**：

1. **丢失 task_id / wave_index**：当前 `team_done.agents[].agent` 只带角色名 + summary，前端无法把 finding 回链到具体 task
2. **error 与 finding 分桶丢失**：档位 A 用 `agent.status==='error'` 推断 error 桶，但**任何带 summary 但 success=false 的 finding** 都被算成 error，与 `TeamState.errors: list[str]` 桶不等价
3. **看不到「错误描述」**：当前前端 `ExpandableAgentRow` 展示 `summary || message`，但 `Finding.error` 字段（后端已有）从未推到前端
4. **retries 不可见**：`Finding.retries` 是后端 v2 已有字段，前端 UI 完全未消费

本次（档位 B）补齐"后端黑板→前端"的最后一公里：**在 `aggregate_node` 一次性把 `findings / errors`
完整快照推给前端**，前端 `BlackboardPanel` 升级消费这个快照，让"谁写了什么、什么 task 失败了、重试了几次"
全部可见。

## What Changes

### B1. 后端 `team_done` payload 增加 `blackboard` 字段

- 修改 [nodes.py](../../../backend/app/team/nodes.py) `aggregate_node`：
  - **3 个 `team_done` 发射点都追加 `blackboard` 字段**：
    - line 530（findings 为空，status=error）：`blackboard = { findings: [], errors: [...] }`
    - line 544（abort，status=error）：`blackboard = { findings: [...], errors: [...] }`（含已完成的部分 findings）
    - line 612（正常完成，status=done/error/replanning）：`blackboard = { findings: [...], errors: [...] }`
  - `blackboard = { findings: [...], errors: [...] }`
  - `findings` 序列化为 list（按 key 遍历 `state["findings"]`，value 若是 list 则展平）
    - 每项：`{ agent, task_id, wave_index, content, success, error?, retries }`
  - `errors` 序列化为 list（直接拷贝 `state["errors"]`）
- 序列化函数抽到 [blackboard.py](../../../backend/app/team/blackboard.py) 作 `_serialize_findings_for_sse(findings) -> list[dict]`（避免污染 nodes.py；已有 `_serialize_blackboard` 是给 aggregator prompt 用的文本格式，不复用）
- 不动 `state["findings"]` / `state["errors"]` 本身（reducer 行为不变）
- 不动 `team_done.agents` 字段（保留向后兼容，前端 `agentMessages` reducer 不变）

### B2. 前端 `MessagePart.team` 类型扩展

- 修改 [stores/chat/index.ts:106-117](../../../frontend/renderer/stores/chat/index.ts#L106-L117)
  - 新增可选 `blackboard?: { findings: Finding[]; errors: string[] }` 字段
  - `Finding` 类型定义新文件 `frontend/renderer/lib/api/blackboard.ts`（避免污染 chat store）

### B3. 前端 `useChatStream` 处理 `team_done.blackboard`

- 修改 `useChatStream.ts` 中 `team_done` 事件处理分支（约 line 575 附近）：
  - 从 `payload.blackboard` 读取并写入对应 team part
  - 通过 `upsertTeamNode({ blackboard })` 触发 store 更新（**先扩 store action，再扩 SSE 处理**）

### B4. 前端 store action 扩展

- 修改 `upsertTeamNode` updaters 类型（[stores/chat/index.ts:306-326](../../../frontend/renderer/stores/chat/index.ts#L306-L326)）：
  - 新增可选 `blackboard?: { findings: Finding[]; errors: string[] }` 参数
  - 实现路径：传入时覆盖 team part 的 `blackboard` 字段

### B5. 前端 `BlackboardPanel` 升级消费快照

- 修改 [TeamNodeCard.tsx BlackboardPanel](../../../frontend/renderer/components/chat/parts/TeamNodeCard.tsx)：
  - **优先**消费 `teamPart.blackboard.findings / errors`（后端快照）
  - **fallback**到档位 A 的 `agents` 聚合（旧数据兼容）
  - 新增展示字段：
    - 每行 finding 显示 `task_id`（全文）与 `wave_index`
    - 失败 finding 显示 `error` 描述（红色）
    - finding 显示 `retries` 角标（"↻ 2 次"）

### B6. 文档与测试

- 更新 [docs/agents/02-sse-event-contract.md](../../../docs/agents/02-sse-event-contract.md) 中 `team_done` 章节
- 后端单测：`aggregate_node` 在 `state.findings` 含 Finding 时，team_done SSE payload 含 blackboard 字段
- 前端单测：`useChatStream` 处理 `team_done.blackboard` 时调用 `upsertTeamNode({ blackboard })`
- 前端单测：`BlackboardPanel` 优先使用 `teamPart.blackboard` 而非 agents 聚合

## Capabilities

### Modified Capabilities

- `agent-team-v2`：Team 路径的 `team_done` SSE 事件从「agents 摘要」扩展为「agents 摘要 + 完整黑板快照」；
  前端 `BlackboardPanel` 从「纯前端聚合」升级为「优先消费后端快照，fallback 聚合」。

### New Capabilities

- 无

## Impact

- **后端**：修改 2 个文件
  - [nodes.py](../../../backend/app/team/nodes.py)（aggregate_node 的 3 个 team_done 发射点）
  - [blackboard.py](../../../backend/app/team/blackboard.py)（新增 `_serialize_findings_for_sse` 函数）
- **前端**：修改 4 个文件
  - [stores/chat/index.ts](../../../frontend/renderer/stores/chat/index.ts)（MessagePart 类型 + upsertTeamNode updater）
  - 新增 [lib/api/blackboard.ts](../../../frontend/renderer/lib/api/blackboard.ts)（Finding / BlackboardSnapshot 类型）
  - [hooks/useChatStream.ts](../../../frontend/renderer/hooks/useChatStream.ts)（team_done.blackboard 处理）
  - [components/chat/parts/TeamNodeCard.tsx](../../../frontend/renderer/components/chat/parts/TeamNodeCard.tsx)（BlackboardPanel 升级）
- **SSE 事件契约**：向后兼容扩展（新增字段，旧前端忽略即可）
- **数据**：team_done 事件 payload 体积约增加 `findings × 200B + errors × 100B`（典型 5-10 个 finding / 0-2 个 error）
- **风险**：LOW（纯增量字段，旧客户端兼容）

## Rollback Plan

1. **代码层**：所有变更集中于单分支，回滚即 `git revert` 单个 merge commit；无数据迁移、无 schema 变更
2. **SSE 兼容**：旧客户端收到含 `blackboard` 字段的 team_done 会忽略（MessagePart 字段可选），回滚仅需删除后端 payload 构造即可
3. **前端 fallback**：`BlackboardPanel` 双路径（blackboard / agents），回滚后端后前端自动 fallback 到 agents 聚合，无视觉回归
4. **store 兼容**：upsertTeamNode 不传 blackboard 时不动 MessagePart.blackboard 字段
5. **验证门禁**：合并前必须通过 `pytest tests/python/unit -m "not integration"` + vitest 全量 + Team 路径冒烟（team 任务完成 → BlackboardPanel 可见）

## Out of Scope

- 不增量推送 blackboard 变化（用户决策"一次性快照"，不做 SSE `blackboard_write` 新事件）
- 不展示 `warnings / pending_waves / completed_task_ids`（用户决策"只 findings / errors"）
- 不动 `agents` 字段（保留 `agentMessages` 回填逻辑）
- 不做档位 C（消息流视图）
- 不做 plan / DAG 可视化
- 不重构 `aggregate_node` 内部逻辑（仅扩 SSE payload）
