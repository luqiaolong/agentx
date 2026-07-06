# Proposal: Agent Team 多代理协作模式

## 背景

当前 AgentX 采用 **Router + 单专家委派** 模式：
- Router 把用户消息分类为 `CHAT` / `SINGLE_TOOL` / `DEEP_TASK`
- `SINGLE_TOOL` 路径每次只选一个专家（code / rag / web / custom）
- 复杂任务无法让多个专家并行协作，也无法让 Agent 之间共享中间结果

随着任务复杂度上升，用户需要一个 **Agent Team 模式**：由 Orchestrator 拆任务、多专家并行/串行执行、共享黑板状态、Aggregator 汇总结果。

## 目标

在聊天输入区提供 `Agent` / `AgentTeam` 模式切换，默认 `Agent`；切换到 `AgentTeam` 后启用完整多代理协作链路，不影响现有 `Agent` 模式行为。

## 范围

本次变更包含：
- 前端：输入区左下角模式切换 UI + 全局状态
- 前后端 API：`agent_mode` 字段透传
- 后端：新增 `team_path.py`，实现 Orchestrator 拆任务、并行调度、共享黑板、Aggregator 汇总
- 后端：新增 `team_plan` / `team_progress` / `team_result` SSE 事件
- 安全：危险工具仍走 DeepAgent `interrupt_before` 审批，不通过子代理暴露

## 预期收益

- 复杂查询可被拆成多专家并行子任务，降低单代理 LLM 幻觉
- 文件分析 + 知识库检索 + 联网搜索可同时执行，缩短整体 latency
- Agent 中间结果写入共享黑板，Aggregator 综合多源信息生成最终回复
- UI 上用户可见团队计划与进度，提升可解释性

## 风险

| 风险 | 缓解 |
|---|---|
| 并行 LLM 调用增加 token 成本 | `AgentTeam` 默认关闭，用户主动切换才启用；并发数上限控制 |
| Orchestrator 拆任务错误 | 拆任务结果返回 UI 供用户感知；错误任务可跳过 |
| 子代理暴露危险工具 | Team 路径中写/编辑/shell 仍回归 DeepAgent 子环节走审批 |
| 状态冲突（多个 Agent 同时写黑板）| 黑板采用 `dict[agent_name, result]` 结构，按 key 隔离写 |
