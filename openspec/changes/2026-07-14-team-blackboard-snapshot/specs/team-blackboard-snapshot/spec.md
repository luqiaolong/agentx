# Spec: Team Blackboard Snapshot

## Purpose

将后端 `TeamState.findings / errors` 一次性快照通过 SSE `team_done` 推给前端，前端
`BlackboardPanel` 升级消费快照（task_id / wave_index / error / retries），让用户
能看到「谁写了什么 finding、什么 task 失败、是否重试」。

## Requirements

### REQ-BB-1: 后端 team_done payload 必须含 blackboard 字段（所有发射点）

`aggregate_node` 有 **3 个 `team_done` 发射点**，每个都必须在 payload 中包含 `blackboard` 字段，
结构为 `{ findings: Finding[], errors: string[] }`。

- **line 530**（findings 为空，status=error）：`blackboard = { findings: [], errors: [...] }`
- **line 544**（abort，status=error）：`blackboard = { findings: [...], errors: [...] }`（含已完成的部分 findings）
- **line 612**（正常完成，status=done/error/replanning）：`blackboard = { findings: [...], errors: [...] }`

序列化规则：
- `findings`：按 key 遍历 `state["findings"]`，value 若是 `list[Finding]` 则展平为多个 Finding 对象
- `errors`：直接拷贝 `state["errors"]`（`list[str]`）
- `findings / errors` 为空时写入空数组 `[]`

#### Scenario

- **Given** `state["findings"] = {key1: Finding(agent="code", task_id="t1", wave_index=0, content="...", success=true, retries=0)}`
- **When** `aggregate_node` 在正常完成路径（line 612）发射 `team_done`
- **Then** `payload.blackboard.findings == [{agent: "code", task_id: "t1", wave_index: 0, content: "...", success: true, retries: 0}]`

#### Scenario（findings 为空路径）

- **Given** `state["findings"] = {}` 且 `state["errors"] = ["no agents available"]`
- **When** `aggregate_node` 在空 findings 路径（line 530）发射 `team_done`
- **Then** `payload.blackboard.findings == []` 且 `payload.blackboard.errors == ["no agents available"]`

### REQ-BB-2: Finding 序列化结构固定

后端序列化的每个 Finding 对象必须含以下字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent` | string | ✓ | 子代理角色名 |
| `task_id` | string | ✓ | TeamTask.id，用于回链 |
| `wave_index` | number | ✓ | 当前 wave 索引（0-based） |
| `content` | string | ✓ | finding 内容摘要 |
| `success` | boolean | ✓ | 子任务是否成功 |
| `error` | string \| undefined | ✗ | 失败原因（success=false 时通常存在） |
| `retries` | number | ✓ | 重试次数（0 表示未重试） |

#### Scenario

- **Given** 一个失败的 Finding：`Finding(agent="code", task_id="t1", success=False, error="timeout", retries=2)`
- **When** 序列化到 SSE payload
- **Then** `payload.blackboard.findings[0]` 含 `error: "timeout"` 且 `retries: 2`

### REQ-BB-3: 前端 MessagePart.team 支持 blackboard 字段

`MessagePart` 的 `type: "team"` variant 必须支持可选 `blackboard?: BlackboardSnapshot`
字段。**该字段为增量扩展，旧客户端忽略即可**。

#### Scenario

- **Given** 一个旧客户端（无 blackboard 字段支持）
- **When** 收到 `team_done` 事件 payload 含 blackboard
- **Then** TypeScript 编译通过（blackboard 字段可选）

### REQ-BB-4: upsertTeamNode 接收 blackboard 参数

`upsertTeamNode` action 必须支持可选 `blackboard?: BlackboardSnapshot` updater：

- 创建路径（team part 不存在）：若传入 blackboard 则写入 `MessagePart.team.blackboard`
- 更新路径（team part 已存在）：若传入 blackboard 则覆盖 `MessagePart.team.blackboard`
- 不传 blackboard 时：`MessagePart.team.blackboard` 保持不变（不动现有字段）

#### Scenario

- **Given** team part 已存在且 `blackboard` 字段已有值
- **When** 调用 `upsertTeamNode(messageId, { blackboard: { findings: [...], errors: [] } })`
- **Then** `MessagePart.team.blackboard` 被覆盖为新值（其他字段如 agents / reasoning 不变）

### REQ-BB-5: useChatStream 处理 team_done.blackboard

`useChatStream` 在 `team_done` 事件处理分支中必须：

- 读取 `payload.blackboard`
- 若存在则调用 `upsertTeamNode(messageId, { blackboard })`

#### Scenario

- **Given** SSE 事件：`team_done` payload `{ status: "done", agents: [...], blackboard: { findings: [...], errors: [...] } }`
- **When** `useChatStream` 收到该事件
- **Then** store 中对应 message 的 team part 的 `blackboard` 字段被填充

### REQ-BB-6: BlackboardPanel 数据源优先级

`BlackboardPanel` 的数据源优先级：

1. **优先**消费 `blackboardSnapshot.findings / errors`（后端 SSE 推送）
2. **Fallback**消费 `agents: TeamAgentState[]` 聚合（档位 A 行为，用于旧数据）

当 `blackboardSnapshot` 存在时：

- `findings` 显示为 finding 段（包括 success=false 的 finding）
- `errors` 显示为 errors 段
- 不再使用 `agents` 聚合

当 `blackboardSnapshot` 不存在时：

- 按档位 A 行为聚合 `agents`（done → finding, error → error）

#### Scenario

- **Given** team part 同时含 `blackboard` 字段（3 findings / 1 error）和 `agents` 数组（5 entries）
- **When** `BlackboardPanel` 渲染
- **Then** 面板显示 3 findings + 1 errors（**不**显示 agents 聚合）

### REQ-BB-7: BlackboardPanel 渲染增强

`BlackboardPanel` 消费 `blackboardSnapshot` 时必须展示：

| Finding 字段 | UI 表现 |
|---|---|
| `task_id` | 行首全文 task_id + `·` + agent label |
| `wave_index` | 行尾灰色标签 `wave N` |
| `success=true` | 绿色 finding 行 |
| `success=false` | 红色 finding 行 + 底部 `error` 描述（红色） |
| `retries > 0` | 行尾 `↻ N 次` 角标 |

#### Scenario

- **Given** `blackboardSnapshot.findings = [{task_id: "task-abc", agent: "code", wave_index: 1, content: "result", success: true, retries: 2}]`
- **When** 渲染 finding 行
- **Then** 行首显示 `task-abc` + `code`，行尾显示 `wave 1` + `↻ 2 次`

### REQ-BB-8: SSE 事件契约向后兼容

`team_done` payload 新增 `blackboard` 字段是**可选扩展**：

- 旧客户端：忽略 `blackboard` 字段，按 `agents` 渲染（向后兼容）
- 新客户端：消费 `blackboard` 字段优先（更丰富）
- 后端回滚：删除 `blackboard` 序列化即可，前端自动 fallback 到 `agents` 聚合

#### Scenario

- **Given** 旧客户端（无 blackboard 字段类型）
- **When** 收到 `{status, agents, blackboard}` payload
- **Then** 客户端解析不报错，渲染 agents 列表
