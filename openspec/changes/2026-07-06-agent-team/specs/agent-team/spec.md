## ADDED Requirements

### Requirement: 输入区 Agent / AgentTeam 模式切换

Renderer SHALL 在 [ChatComposer.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ChatComposer.tsx) 底部 toolbar 左侧新增模式切换按钮，支持 `Agent`（默认）与 `AgentTeam` 两种模式。

#### Scenario: 默认显示 Agent 模式

- **WHEN** 用户首次打开应用或重置状态
- **THEN** 模式切换按钮显示 `Agent`，后续聊天走路径 A/B/C 的单代理逻辑

#### Scenario: 切换到 AgentTeam 模式

- **WHEN** 用户点击模式按钮并选择 `AgentTeam`
- **THEN** 按钮变为紫色高亮，显示 `Team`
- **AND** 下一条发送的消息走 `agent_team` 模式的多代理协作链路
- **AND** 当前会话历史保持不变

#### Scenario: 模式持久化

- **WHEN** 用户切换到 `AgentTeam` 后关闭应用再打开
- **THEN** 模式切换按钮仍显示 `Team`（写入 `localStorage`）

---

### Requirement: AgentTeam 前后端 API 透传

系统 SHALL 把 `agent_mode` 字段从 renderer 经 preload 透传到后端 `/api/chat`，默认值为 `"agent"`。

#### Scenario: Agent 模式默认透传

- **WHEN** 用户以默认 `Agent` 模式发送消息
- **THEN** `POST /api/chat` body 中 `agent_mode` 为 `"agent"`
- **AND** 后端 `run_router` 走路径 A/B/C 单代理逻辑

#### Scenario: AgentTeam 模式透传

- **WHEN** 用户以 `AgentTeam` 模式发送消息
- **THEN** `POST /api/chat` body 中 `agent_mode` 为 `"agent_team"`
- **AND** 后端 `run_router` 调用 `run_team_path`

---

### Requirement: Orchestrator 拆任务

AgentTeam 模式下，系统 SHALL 调用 Orchestrator LLM 把用户消息拆成若干子任务，每个子任务包含 `agent`（执行者）、`input`（输入）、`purpose`（目的）。

#### Scenario: 复杂查询自动拆分

- **GIVEN** 用户问"分析 src/main.py 的入口逻辑，并检索 AgentX 的 Router 设计文档"
- **WHEN** 进入 AgentTeam 模式
- **THEN** Orchestrator 输出 JSON 计划，例如：
  ```json
  {
    "plan": [
      {"agent": "code", "input": "读取 src/main.py 并总结入口逻辑", "purpose": "获取代码入口结构"},
      {"agent": "rag", "input": "AgentX Router 三路径设计", "purpose": "检索设计文档"}
    ],
    "reasoning": "需要同时查看代码和内部文档"
  }
  ```
- **AND** 后端向前端发送 `team_plan` SSE 事件

#### Scenario: 子任务数上限

- **WHEN** Orchestrator 返回超过 5 个子任务
- **THEN** 后端截断到前 5 个，并记录 warning 日志

---

### Requirement: 多专家并行执行与共享黑板

系统 SHALL 并行调度子任务到对应专家；每个专家完成后把结果摘要写入共享黑板（blackboard）。

#### Scenario: code 与 rag 专家并行

- **GIVEN** Team 计划包含 code 和 rag 两个子任务
- **WHEN** 调度器执行
- **THEN** code 和 rag 子任务并行启动（受 `max_parallel=3` 限制）
- **AND** 每个专家完成后发送 `team_progress` 事件（`status: done`）
- **AND** 每个专家完成后发送 `team_result` 事件，包含结果摘要

#### Scenario: 共享黑板汇总

- **GIVEN** code 专家返回"main.py 启动 FastAPI 8123 端口"，rag 专家返回"Router 分 CHAT/SINGLE_TOOL/DEEP_TASK 三条路径"
- **WHEN** 所有子任务完成
- **THEN** 黑板 `findings` 为：
  ```json
  {"code": "main.py 启动 FastAPI 8123 端口...", "rag": "Router 分三条路径..."}
  ```

#### Scenario: 某个专家失败不影响整体

- **GIVEN** code 子任务成功，rag 子任务失败
- **WHEN** 调度完成
- **THEN** 黑板 `findings` 包含 code 结果，`errors` 记录 rag 错误
- **AND** Aggregator 仍继续执行，并在 prompt 中收到失败说明

---

### Requirement: Aggregator 汇总生成最终回复

所有子任务完成后，系统 SHALL 调用 Aggregator LLM 综合黑板内容生成最终回复，并以流式 SSE 输出。

#### Scenario: 汇总多源结果

- **GIVEN** 黑板已有 code 和 rag 结果
- **WHEN** Aggregator 执行
- **THEN** 向后端发送 `token` / `reasoning` 事件
- **AND** 最终回复综合代码结构和设计文档内容

#### Scenario: 全部失败则报错

- **GIVEN** 所有子任务均失败
- **WHEN** 调度完成
- **THEN** 后端发送 `error` SSE 事件，内容为"所有专家任务均失败"

---

### Requirement: Team 模式安全红线

AgentTeam 模式下，写文件、编辑文件、shell 执行等危险操作 SHALL 只能作为 `deep` 子任务执行，并继承 DeepAgent 的 `interrupt_before` 审批流。

#### Scenario: Orchestrator 错误分配写任务

- **GIVEN** Orchestrator 把写文件任务分配给 `code` agent
- **WHEN** 后端校验计划
- **THEN** 强制把该子任务改为 `agent: "deep"`

#### Scenario: deep 子任务走审批

- **GIVEN** Team 计划包含 deep 子任务"写入 config.py"
- **WHEN** 执行到该子任务
- **THEN** 调用 `run_deep_path`
- **AND** 危险工具触发前端 `approval_request` 弹窗
- **AND** 用户拒绝后该子任务失败并记录到黑板 `errors`

---

### Requirement: Team 配置项

后端 SHALL 支持通过 `AGENTX_AGENT_TEAM_*` 环境变量配置 Team 行为。

#### Scenario: 关闭 AgentTeam

- **WHEN** `AGENTX_AGENT_TEAM_ENABLED=false`
- **THEN** 即使前端发送 `agent_mode=agent_team`，后端也按 `agent` 模式处理
- **AND** 后端记录 warning 日志

#### Scenario: 调整并行度与摘要长度

- **WHEN** `AGENTX_AGENT_TEAM_MAX_PARALLEL=2` 且 `AGENTX_AGENT_TEAM_RESULT_MAX_CHARS=1500`
- **THEN** 调度器最多并发 2 个子任务，每个结果摘要上限 1500 字符

---

### Requirement: SSE 事件契约扩展

系统 SHALL 新增 `team_plan` / `team_progress` / `team_result` 三类 SSE 事件，前后端契约对齐。

| event | payload | 触发时机 |
|---|---|---|
| `team_plan` | `{plan: [{agent, input, purpose}], reasoning: string}` | Orchestrator 拆任务完成后 |
| `team_progress` | `{agent: string, status: "running" \| "done" \| "error", message?: string}` | 子任务状态变化时 |
| `team_result` | `{agent: string, summary: string}` | 子任务成功完成后 |

#### Scenario: 前端正确解析 Team 事件

- **WHEN** 后端发送 `team_plan` / `team_progress` / `team_result`
- **THEN** preload 解析为 `ChatEvent` 并分发给 renderer
- **AND** `useChatStream.ts` 能识别这些事件类型

---

### Requirement: 向后兼容

本次变更 SHALL 不影响 `Agent` 模式行为；未传 `agent_mode` 时默认按 `"agent"` 处理。

#### Scenario: 旧客户端兼容

- **WHEN** 旧版 renderer 发送 `POST /api/chat` 且 body 不含 `agent_mode`
- **THEN** 后端默认 `agent_mode="agent"`，行为与变更前一致
