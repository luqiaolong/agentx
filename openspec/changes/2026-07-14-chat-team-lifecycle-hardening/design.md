# Design: 聊天链路与 AgentTeam 链路生命周期硬化

## Context

当前系统已经具备聊天、审批、Team 编排、SSE 流式渲染的基本能力，但实现上的状态所有权仍然是分散的：

- 审批层有“请求”和“决定”两个概念，但实际只存“决定”
- 聊天层有 stream lock，但没有“run cleanup 完成”这个状态
- Team 层有 findings / errors / warnings / blackboard，但没有稳定的结果类型
- SSE 层同时承担 transport 结束与业务结束两个语义，前后端没有严格对齐

因此系统在正常路径上通常可用，但在跨请求竞争、用户刷新恢复、abort、partial failure、replan、多 session 并发时会暴露出一致性问题。

## Goals

- 让审批决定只能作用于一个真实存在的活跃审批请求
- 让同一线程上的 run 具备唯一身份和完整 cleanup 生命周期
- 让任意 run 都有明确、唯一、可恢复的终态
- 让 Team 最终结果从“推断”改为“显式 typed outcome”
- 让 SSE 事件重新成为前后端共享契约，而不是双方各自猜测
- 让 `system_prompt` / model / role prompt / classifier 这些现有配置真正进入执行主链

## Non-Goals

- 不重做 Team DAG 结构
- 不替换 DeepAgents / LangGraph 为自研编排
- 不在本提案中引入新的 UI 交互模式
- 不把所有遗留问题一次性打包实现；本设计优先解决状态与契约一致性

## Decisions

### D1. 审批必须区分“活跃请求”和“已提交决定”

当前把审批决定按 `thread_id` 存储，等价于“线程里只允许有一个抽象审批状态”。这不足以表达：

- 一个线程里先后出现的多个审批请求
- 页面刷新后对“当前活跃请求”的恢复
- 用户提交的是不是正在等待的那个请求
- `full_trust` 是不是因为当前工具审批被放开的

因此本设计引入两个独立实体：

- `ApprovalRequest`: 活跃请求，字段含 `approval_id`、`thread_id`、`run_id`、`tool_call_id`、`request_kind`、`created_at`、`expires_at`、`consumed_at`
- `ApprovalDecision`: 用户决定，字段含 `approval_id`、`decision`、`submitted_at`

`submit_approval` 的语义改为“compare-and-consume active request”，而不是“给线程写一个决定”。

### D2. 用每线程 live run lifecycle 对象替代分散 dict + lock

当前系统把 stream lock、abort flag、pending approval、checkpoint write、frontend connection ref 分散在不同位置管理，导致“锁已经释放，但清理还没做完”的竞态。

本设计为每个线程建立统一的 runtime lifecycle：

```text
ThreadRunSession
- thread_id
- run_id
- generation
- state: starting | streaming | waiting_approval | cleaning_up | finished
- owner_connection_id
- active_approval_id?
- abort_requested
- pause_requested
- cleanup_complete
```

约束如下：

1. 同一线程任何时刻最多一个 live run
2. 新 run 启动前必须确认旧 run `cleanup_complete=true`
3. `compact` / `reset` / `abort` / 新 `chat` 请求都通过同一个 session 协调
4. cleanup 完成前不得释放该线程对外可见的“可重入”状态

### D3. 终态事件分为“业务 outcome”和“transport completion”两层

当前 `done` 既被当成 SSE transport 结束，也被当成业务成功结束；`team_done` 又承担 Team 业务终态。这导致：

- `team_done:error` 后又收到 transport `done`，前端容易覆写成完成
- abort/cancel 时如果 `CancelledError` 逃逸，前端一个终态都收不到
- 恢复路径只能拼接文本，不知道业务是否已经以 error/partial 结束

本设计将终态拆为两层：

- 业务终态：如 `team_done`、`approval_request` resolved、普通聊天 run 的 `done.reason`
- transport 终态：SSE 必须以一个明确 terminal event 收尾

兼容策略：

- `done` 事件新增可选 `reason` 字段：`completed | aborted | recovered | error`
- `team_done` 新增规范化 `outcome` 字段：`success | partial | error | aborted`
- 旧字段如 `status` 保留一个迁移周期，但前端 reducer 必须优先消费新字段

### D4. Team 成功判定必须基于 typed outcome，不基于 findings 非空

当前 Team 逻辑存在“所有子任务都失败，但仍然产出 findings，于是被判成 done”的问题。根因是质量门把“有内容”误认为“成功完成”。

本设计要求：

1. `Finding.success=false` 的结果不能计入成功质量门
2. 聚合器异常、planner 异常、上游 abort、全失败、部分失败都要映射到显式 `TeamOutcome`
3. Team 历史持久化与 `team_done` 必须写入同一个 outcome
4. 前端 Team 卡片和任务流只认 outcome，不再根据 `done` 与否自行推断

建议的 outcome 规则：

- `success`: 全部必需子任务成功，聚合成功
- `partial`: 存在成功结果，也存在失败/跳过，但系统仍可给出带风险标记的总结
- `error`: 无法给出有效团队结果，或聚合失败
- `aborted`: 用户中止或上游 run 被取消

### D5. 相关 SSE 事件必须带稳定关联键

当前 Team 前端经常靠“角色名 + 索引”去关联一行 UI，这在重复角色、多波次 replan、重试后必然出错。

本设计要求以下事件补齐稳定键：

- `delegation`
- Team 子任务完成/失败摘要
- `team_done.agents[]`
- 黑板快照中的 finding / error 归属

最小关联集合：

```text
task_id
run_id
wave_index?（需要时）
attempt?（需要时）
role
```

其中 `task_id` 是 UI 和聚合逻辑的主键，role 只是展示标签。

### D6. Prompt / model / classifier 视为执行契约的一部分

这批问题里有几项看似是“配置没透传”，本质上仍然属于生命周期一致性问题：

- `system_prompt` 被 API 接收却未进入执行器
- Team role 未继承 chat model / project prompt / profile prompt
- `DangerousTaskClassifier` 已存在但未进入 `execute_node`
- 初始 planner 生成的 task id 不唯一，导致后续 event correlation 不稳定

因此本设计不把它们当作“后续优化”，而是纳入主提案的可验证 contract：

1. 请求层传入的 `system_prompt` 必须进入 router -> executor
2. Team role 的 prompt/context/model 继承链必须可测试
3. Dangerous classifier 必须在危险任务真正执行前参与路由
4. `task_id` 生成必须稳定唯一

## Delivery Sequence

### Phase 1: Approval + Run Lifecycle

- 落地 `approval_id` / `run_id` / consume-once
- 落地每线程 live run lifecycle
- 修复 stream lock cleanup race、abort terminal event、compact race、多 session 串线

### Phase 2: Team Outcome + SSE Contract

- 落地 `TeamOutcome`
- 修复失败仍 done、聚合异常仍 done、前端 `team_done:error` 被覆盖
- 补齐 `task_id` / event typing / `sandbox_escalation`

### Phase 3: Prompt / Safety / Planner Gap Closure

- 打通 `system_prompt`
- 打通 Team role model/prompt/profile 继承
- 接入 `DangerousTaskClassifier`
- 兜住 unique task id

## Risks & Mitigations

### Risk 1: 引入 `run_id` / `approval_id` 后需要前后端同步升级

缓解：

- 采用向后兼容字段扩展
- 新接口先接受旧字段但仅用于兼容日志，不参与授权判断

### Risk 2: ThreadRunSession 串行化可能降低同线程吞吐

缓解：

- 仅串行化“同一线程的互斥操作”
- 不影响不同线程并发
- 若体验退化，优先保留 correctness，再做细粒度拆分

### Risk 3: Team outcome 引入 partial 后前端文案与历史兼容复杂

缓解：

- `team_done` 先同时输出 `status` + `outcome`
- UI 迁移到 `outcome` 后再考虑移除 legacy `status`

### Risk 4: prompt/model 透传修复可能暴露更多已有配置不一致

缓解：

- 在测试里显式验证 role prompt 与 model 来源
- 对缺失 prompt 的 Team role 走 fail-fast，而不是静默降级
