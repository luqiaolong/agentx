# 任务追踪 — DeepAgents 子代理体系统一

## 预期修改文件

### 后端新建文件
- `backend/app/deep/execution.py` — 公共审批执行层
- `backend/app/agents/execution.py` — 或放在 agents 包下的公共执行层（待架构决定）
- `tests/python/unit/test_harness_subagents.py` — harness subagents 参数测试
- `tests/python/unit/test_custom_agent_deep.py` — 自定义子代理迁移测试
- `tests/python/unit/test_execution_layer.py` — 公共执行层测试
- `tests/python/integration/test_supervisor_subagent_task.py` — Supervisor task 调用集成测试
- `tests/python/integration/test_coding_expert_subagent_task.py` — Expert task 调用集成测试

### 后端修改文件
- `backend/app/deep/harness.py` — 新增 `subagents=`、`rubric=`、`grader_model=` 参数
- `backend/app/deep/agent.py` — 调用公共执行层，删除重复审批循环
- `backend/app/agents/supervisor/work_supervisor.py` — 接入 SubAgentMiddleware，调用公共执行层
- `backend/app/agents/supervisor/delegation.py` — 删除或大幅简化
- `backend/app/agents/expert/coding.py` — 删除 `make_expert_delegation_tools`，接入 SubAgentMiddleware
- `backend/app/subagents/custom_agent.py` — 从 `create_react_agent` 迁移到 `create_deep_agent`
- `backend/app/subagents/base.py` — 清理 `_make_*_tools` 兼容别名（可选）
- `backend/app/security/approval_flow.py` — 提取公共循环到 execution.py，保留审批状态 API
- `backend/app/team/planner.py` — 探索简化/删除（如果声明式子代理化成功）
- `backend/app/team/scheduler.py` — 探索简化/删除
- `backend/app/team/coding_team.py` — 接入声明式 subagents
- `backend/app/router/graph.py` — 可能需要调整 `parent_thread_id` 传递
- `backend/app/config/settings.py` — 可能新增团队角色 subagent 配置

### 保留不修改的文件
- `backend/app/sandbox/session_sandbox.py` — 安全模型不变
- `backend/app/tools/filesystem.py` — 自研 fs 工具不变
- `backend/app/tools/cli.py` — 自研 cli 工具不变
- `backend/app/workspace/config/loader.py` — 项目配置加载不变

## 规模判定

- 涉及文件数: ~15（3 新建 + 12 修改）
- 涉及模块数: 6（deep/ + agents/supervisor/ + agents/expert/ + subagents/ + team/ + security/）
- 规模: **L（大改）** — 5+ 文件且跨模块，需全流程执行

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | harness.py 新增 `subagents=` / `rubric=` / `grader_model=` 参数 | `backend/app/deep/harness.py` | `create_deep_agent` 正确接收 subagents 和 rubric middleware | ⬜ |
| T2 | 新建公共审批执行层 `run_agent_with_approval` | `backend/app/deep/execution.py` | Supervisor/Expert/DeepAgent 共享同一执行入口 | ⬜ |
| T3 | DeepAgent 迁移到公共执行层 | `backend/app/deep/agent.py` | 删除重复审批循环，调用 `run_agent_with_approval` | ⬜ |
| T4 | Supervisor 接入 SubAgentMiddleware，删除自研委派工具 | `backend/app/agents/supervisor/work_supervisor.py`, `backend/app/agents/supervisor/delegation.py` | `delegate_to_expert`/`delegate_to_subagent` 消失，task 工具可用 | ⬜ |
| T5 | coding Expert 接入 SubAgentMiddleware | `backend/app/agents/expert/coding.py` | 删除 `make_expert_delegation_tools` | ⬜ |
| T6 | 自定义子代理从 `create_react_agent` 迁移到 `create_deep_agent` | `backend/app/subagents/custom_agent.py` | 子代理自动获得 write_todos/summarization/offloading | ⬜ |
| T7 | 安全过滤子代理工具集 | `backend/app/deep/harness.py`, `backend/app/subagents/custom_agent.py` | 子代理工具列表不含 FORBIDDEN_SUBAGENT_TOOLS | ⬜ |
| T8 | AgentTeam 声明式子代理化 | `backend/app/team/coding_team.py`, `backend/app/team/planner.py`, `backend/app/team/scheduler.py` | 团队角色声明为 SubAgent，主 agent 通过 task 调用 | ⬜ |
| T9 | 可选：RubricMiddleware 运行时自纠 | `backend/app/deep/harness.py` | 传入 rubric 时启用自纠，默认关闭 | ⬜ |
| T10 | 新增/更新单元测试 | `tests/python/unit/` | 全部通过 | ⬜ |
| T11 | 新增集成测试 | `tests/python/integration/` | 子代理 task 调用 + 审批透传通过 | ⬜ |
| T12 | E2E 验证 | 完整 SSE 流程 | Supervisor 委派 coding Expert / rag / web 流程正常 | ⬜ |
| T13 | 更新 AGENTS.md 文件地图 | `AGENTS.md` | 反映新包结构和删除的模块 | ⬜ |

## 子任务优先级

### P0（必须完成）
- T1 harness 参数扩展
- T2 公共执行层
- T3 DeepAgent 迁移
- T4 Supervisor 子代理化
- T5 coding Expert 子代理化
- T6 自定义子代理统一
- T7 安全过滤
- T10 单元测试

### P1（推荐完成）
- T8 AgentTeam 声明式子代理化
- T11 集成测试
- T12 E2E 验证
- T13 文档更新

### P2（可选）
- T9 RubricMiddleware 运行时自纠

## 依赖关系

```
T1 → T2 → T3
T1 → T4, T5, T6
T2 → T4, T5
T6 → T7
T4 + T5 → T8
T3 + T4 + T5 + T6 → T10
T10 → T11 → T12
```
