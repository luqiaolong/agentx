# Design: DeepAgents 子代理体系统一

## 架构决策

### 决策 1: 用 `SubAgentMiddleware` 替代自研委派工具

**问题**: `backend/app/agents/supervisor/delegation.py` 手写 `delegate_to_expert` / `delegate_to_subagent`，`coding.py` 又单独写 `make_expert_delegation_tools`，重复且无法利用 deepagents 的上下文隔离和并行能力。

**方案**:
- 在 `app.deep.harness.create_agent` 中增加 `subagents=` 参数。
- Supervisor 和 Expert 传入声明式 `SubAgent` 列表。
- 调用 `create_deep_agent(..., subagents=subagents)`，自动挂载 `SubAgentMiddleware` 和 `task` 工具。
- 用 `HarnessProfile.general_purpose_subagent=enabled(False)` 禁用默认通用子代理，避免冲突。

**保留项目安全约束**:
- 子 agent 的 `tools=` 只传过滤后的安全工具（不含 `write_file`/`edit_file`/`cli_execute` 等危险工具）。
- 子 agent 仍通过项目自研 `SessionSandbox` 校验文件路径（沙箱在工具层，不依赖 deepagents）。

### 决策 2: 自定义子代理统一使用 `create_deep_agent`

**问题**: `backend/app/subagents/custom_agent.py:build_custom_agent` 仍使用 `langgraph.prebuilt.create_react_agent`，导致子代理缺少 `write_todos`、summarization、offloading、patch tool calls。

**方案**:
- `build_custom_agent` 改为调用 `app.deep.harness.create_agent`。
- 保留 `THINK_PROMPT_SUFFIX` 注入 system prompt。
- 保留 `FORBIDDEN_SUBAGENT_TOOLS` 过滤。
- 工具名映射（`glob_files`→`glob` 等）保持不变。

### 决策 3: 提取公共审批执行层

**问题**: `work_supervisor.py`、`coding.py`、`deep/agent.py` 都包含重复的 `_is_interrupted`、运行时 dangerous 计算、调用 `run_approval_loop` 的逻辑。

**方案**:
- 新建 `backend/app/agents/execution.py`（或 `backend/app/deep/execution.py`）。
- 提供统一函数 `run_agent_with_approval(...)`，封装：
  - 初始流式执行
  - 中断检测
  - 危险工具判定
  - 目录扩展授权
  - 恢复执行
  - 循环保护
- Supervisor/Expert/DeepAgent 只负责构建 agent 和 system prompt，然后调用公共执行层。

### 决策 4: AgentTeam 探索声明式子代理化

**问题**: `team/planner.py` 手写 JSON 计划解析，`team/scheduler.py` 手动串并行调度，维护成本高。

**方案**:
- 将 `frontend_dev/backend_dev/tester/architect/devops/ui_designer/product_manager` 声明为 `SubAgent` 列表。
- `coding_team` 主 agent 使用 `create_deep_agent(subagents=team_subagents, system_prompt=...)`。
- 主 agent 的 system prompt 说明“按需调用团队角色，并行执行独立任务”。
- 保留 `team/blackboard.py` 作为结果聚合的数据结构（子 agent 返回结果写入 blackboard）。
- 如果声明式方案在测试中计划质量不如旧 orchestrator，则保留旧方案作为 fallback。

### 决策 5: 可选引入 `RubricMiddleware` 运行时自纠

**问题**: `RubricMiddleware` 仅在 eval 中使用，生产 agent 没有运行时自纠能力。

**方案**:
- 在 `app.deep.harness.create_agent` 中增加可选 `rubric_middleware=` 参数。
- 当调用方传入 `rubric` 字段时，注入 `RubricMiddleware(model=grader_model, max_iterations=3)`。
- 默认不启用，避免普通对话延迟增加。
- 前端/API 可新增 `rubric` 字段让用户为复杂任务指定完成标准。

### 决策 6: `SessionSandbox` 与 `FilesystemPermission` 暂不做桥接

**问题**: deepagents 提供 `permissions=` 参数，项目自研 `SessionSandbox` 更严格。

**方案**:
- 本次不改 `SessionSandbox`。
- 继续用 `excluded_tools` 隐藏 deepagents 内置 fs 工具，避免安全模型冲突。
- 未来 P2 单独提案研究桥接。

## 包结构变化

```
backend/app/
├── deep/
│   ├── harness.py          # 新增 subagents= / rubric= 参数
│   └── execution.py        # 新增公共审批执行层
├── agents/
│   ├── supervisor/
│   │   ├── work_supervisor.py     # 删除/简化，调用 execution.py
│   │   └── delegation.py          # 删除
│   ├── expert/
│   │   └── coding.py              # 删除 make_expert_delegation_tools，调用 execution.py
│   └── execution.py               # 或放在 deep/execution.py
├── subagents/
│   └── custom_agent.py            # 从 create_react_agent 迁移到 create_deep_agent
└── team/
    ├── planner.py                 # 探索简化/删除
    ├── scheduler.py               # 探索简化/删除
    └── blackboard.py              # 保留
```

## 数据流

### Supervisor 子代理调用

1. 用户输入 → `run_work_supervisor`。
2. `build_work_supervisor` 构建 `SubAgent` 列表（coding Expert、rag、web、custom 等）。
3. `create_deep_agent(..., subagents=...)` 返回 agent。
4. `run_agent_with_approval(agent, ...)` 驱动流式执行。
5. LLM 决定调用 `task` 工具 → `SubAgentMiddleware` 启动子 agent。
6. 子 agent 返回结果 → 主 agent 继续合成回复。

### 自定义子代理

1. `build_custom_agent` 调用 `app.deep.harness.create_agent`。
2. 子 agent 自动获得 `write_todos`、`SummarizationMiddleware`、`PatchToolCallsMiddleware`。
3. 子 agent 的工具集仍经过 `FORBIDDEN_SUBAGENT_TOOLS` 过滤。

## 兼容性

- `task` 工具返回的 `ToolMessage` 会被 `_stream_agent_events` 转换为 `tool_result` SSE 事件，前端无需改动。
- 子 agent 内部的事件（token/tool_call/tool_result）默认不会透传给外层，保持现有 `_collect_agent_result` 行为。
- `@mention` 强制委派可映射为直接调用对应 `SubAgent` 的 runnable，保持现有行为。

## 测试策略

- 单元测试：验证 `SubAgentMiddleware` 正确挂载、子 agent 工具列表安全、自定义子 agent 基于 `create_deep_agent`。
- 集成测试：验证 `task` 工具调用子代理、审批透传、并行子代理执行。
- E2E 测试：Supervisor 委派 coding Expert、rag 子代理、web 子代理的完整流程。
