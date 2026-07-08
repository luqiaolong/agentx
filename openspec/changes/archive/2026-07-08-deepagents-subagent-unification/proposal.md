# Proposal: DeepAgents 子代理体系统一

## 背景

AgentX 已完成 DeepAgents 0.6.12 基础能力迁移（`create_deep_agent`、`interrupt_on`、`memory=`、`skills=`、`backend=`、`SummarizationMiddleware`、`PatchToolCallsMiddleware`）。但当前 Supervisor/Expert 仍使用自研委派工具 `delegate_to_expert` / `delegate_to_subagent`，自定义子代理仍基于 `langgraph.prebuilt.create_react_agent`，AgentTeam 仍采用手写 Orchestrator/Scheduler/Aggregator 模式。

这导致：
1. **重复造轮子**：`delegation.py`、`coding.py:make_expert_delegation_tools`、`team/planner.py`、`team/scheduler.py` 中大量硬编码分发逻辑。
2. **能力不一致**：自定义子代理无法复用 DeepAgent 的 `write_todos`、自动摘要、Context Offloading、Prompt Caching。
3. **缺乏运行时自纠**：`RubricMiddleware` 仅在 eval 框架中使用，未进入生产 agent。

## 目标

1. 用 `deepagents.SubAgentMiddleware` 的 `task` 工具替代自研 `delegate_to_expert` / `delegate_to_subagent`。
2. 将所有子代理（rag/web/custom/team 角色）统一构建在 `create_deep_agent` 之上。
3. 提取 Supervisor/Expert/DeepAgent 公共审批执行层，消除重复代码。
4. 探索 AgentTeam 从手动 orchestration 转向声明式子代理化。
5. 在生产 agent 中引入 `RubricMiddleware` 运行时自纠能力（可选增强）。

## 非目标

- 不替换 `SessionSandbox` 安全模型（deepagents `FilesystemPermission` 仅覆盖内置 fs 工具，不足以替代项目级沙箱）。
- 不替换自研 `cli_execute`（项目有黑名单、元字符过滤等额外安全层）。
- 不改动 SSE 事件契约（`tool_call` / `tool_result` / `approval_request` 等格式不变）。
- 不改动前端聊天组件的核心渲染逻辑。

## 涉及范围

- `backend/app/agents/supervisor/delegation.py` — 删除或大幅简化
- `backend/app/agents/supervisor/work_supervisor.py` — 接入 `SubAgentMiddleware`
- `backend/app/agents/expert/coding.py` — 接入 `SubAgentMiddleware`，删除 `make_expert_delegation_tools`
- `backend/app/subagents/custom_agent.py` — 从 `create_react_agent` 迁移到 `create_deep_agent`
- `backend/app/team/planner.py`、`team/scheduler.py` — 探索用声明式 subagents 替代
- `backend/app/deep/` — 新增/扩展公共执行层
- `backend/app/security/approval_flow.py` — 提取公共循环，适配 subagent 审批透传

## 验收标准

1. `delegate_to_expert` / `delegate_to_subagent` 工具消失，Supervisor/Expert 通过 `task` 工具调用子代理。
2. 自定义子代理基于 `create_deep_agent`，自动获得 `write_todos`、summarization、offloading。
3. Supervisor/Expert/DeepAgent 共享同一审批执行层代码。
4. 现有测试全部通过，新增集成测试覆盖 `task` 工具子代理调用和审批透传。
5. （可选）AgentTeam 主 agent 使用声明式 subagents，删除手动 JSON 计划解析。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| `task` 工具的安全模型与项目沙箱不一致 | 子 agent 只传入过滤后的安全工具集合，保留 `SessionSandbox` 校验 |
| 子代理并行调用导致审批状态混乱 | 公共执行层统一处理 `thread_id` 和 `parent_thread_id` |
| 团队角色迁移后计划质量下降 | 保留 `team/blackboard.py` 作为结果聚合，A/B 对比旧 orchestrator |
| RubricMiddleware 引入后延迟增加 | 默认关闭，仅对显式传入 rubric 的会话启用 |

## 回滚策略

- 每个子任务独立提交，可单独 revert。
- 保留旧 `delegate_to_*` 工具的实现分支在 git 历史中。
- 部署前在 staging 环境跑完整 E2E。
