## Why

`backend/app/deepagent` 已成为 Work、Coding、Team 与子代理共用的 DeepAgents 运行时，
但工具装配、`interrupt_on`、运行期危险集合和审批恢复分别维护，已经出现非可信 MCP
工具未进入建图时 HITL、内置文件工具开关不生效、resume 观测覆盖等契约漂移。
同时，审批与流式入口分别增长到约 760 行和 400 行，隐式闭包状态和跨包私有依赖
使安全修复难以独立验证，因此需要在继续扩展 Agent 能力前先收敛运行时边界。

## What Changes

- 建立一次性装配的 DeepAgent 工具描述，统一显式工具、禁用的内置工具和实际
  `interrupt_on`，让建图期 HITL 与运行期审批使用同一事实源。
- 使 `tools_enabled` 对 DeepAgents 自动注入的文件系统工具生效，并确保非可信 MCP
  工具在执行前触发审批。
- 为 thread、parent thread、trace 和临时授权定义有界执行上下文，保证完成、异常、取消
  和生成器提前关闭时均恢复 ContextVar 并清理一次性授权。
- 引入跨 interrupt/resume 共享的流式运行状态，保证消息、todo 与观测事件幂等且序号单调；
  修复相同内容的并行 `ToolMessage` 被误去重问题。
- 修正只读工具循环检测、混合 `request_permission` 调用、最大审批迭代边界和 shell fallback
  工作目录等已确认的不合理行为。
- 将 LangGraph HITL 机制、审批策略和 SSE 转换从巨型函数中拆为可单测的内部组件，
  保留现有公共入口作为兼容 facade。
- 不新增依赖，不改变前端 SSE 事件类型/字段，不删除 `app.deepagent` 当前公开符号。

## Capabilities

### New Capabilities

- `deepagent-runtime`: 规定 AgentX 的 DeepAgents 工具装配、HITL 审批、执行上下文、
  interrupt/resume 流式幂等和运行时终止语义。

### Modified Capabilities

无。当前 `openspec/specs/` 中没有已归档的 DeepAgent 运行时 capability；本变更以新规格
建立当前实现应满足的正式契约。

## Impact

- **核心代码**：`backend/app/deepagent/`，重点涉及 `factory.py`、`tool_assembly.py`、
  `context.py`、`streaming.py`、`approval_runner.py`、`middleware.py` 和 shell backend。
- **调用方**：`backend/app/scenarios/work/agent.py`、`backend/app/scenarios/coding/agent.py`、
  `backend/app/scenarios/coding_team/agent.py`，仅调整内部装配与上下文绑定，不改变入口签名。
- **安全边界**：复用 `app.security.dangerous_tools` 和 `app.security.approval`，消除重复危险
  工具计算；SessionSandbox 授权模型不变。
- **协议**：现有 SSE `token`、`reasoning`、`tool_call`、`tool_result`、`todo_update`、
  `approval_request`、`paused`、`error` 契约保持不变。
- **兼容性**：保留 `app.deepagent.__all__` 中现有 12 个公开符号；继续使用 DeepAgents 0.6.12
  的 `create_deep_agent`、`HumanInTheLoopMiddleware`、内置 filesystem/memory/skills 能力。
- **测试**：先新增失效回归测试，再分阶段迁移；最终运行全量 Python 单测、Ruff 和
  GitNexus 变更影响检查。
