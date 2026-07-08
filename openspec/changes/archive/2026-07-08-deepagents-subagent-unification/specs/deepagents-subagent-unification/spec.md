# Spec: DeepAgents 子代理体系统一

## 概述

将 AgentX 的 Supervisor/Expert 自研委派工具替换为 deepagents `SubAgentMiddleware` 的 `task` 工具；将自定义子代理从 `create_react_agent` 迁移到 `create_deep_agent`；提取公共审批执行层；探索 AgentTeam 声明式子代理化；可选引入 `RubricMiddleware` 运行时自纠。

## 公共 API 变更

### `app.deep.harness.create_agent`

```python
def create_agent(
    model: Any,
    tools: list,
    *,
    checkpointer: Any | None = None,
    system_prompt: str | None = None,
    thread_id: str | None = None,
    workspace_path: str | None = None,
    name: str | None = None,
    subagents: list[SubAgent | CompiledSubAgent] | None = None,   # 新增
    rubric: str | None = None,                                      # 新增
    grader_model: Any | None = None,                                # 新增
) -> Any:
```

- `subagents`: 声明式子代理列表，透传给 `create_deep_agent(subagents=...)`。
- `rubric`: 非空时注入 `RubricMiddleware(model=grader_model or model, max_iterations=3)`。
- `grader_model`: rubric 评分用的 grader 模型，默认回退到主 model。

### `app.deep.execution.run_agent_with_approval`（新增）

```python
async def run_agent_with_approval(
    agent: Any,
    config: dict,
    *,
    thread_id: str,
    workspace_path: str | None,
    permission_mode: str,
    runtime_dangerous: set[str],
    source: str,
    inputs: dict,
    readonly_streak_threshold: int = 0,
) -> AsyncIterator[dict[str, str]]:
```

统一封装：初始流式执行 → 中断检测 → 危险工具/目录扩展审批 → 恢复执行 → 循环保护。

### `app.subagents.custom_agent.build_custom_agent`

- 内部从 `create_react_agent` 改为 `app.deep.harness.create_agent`。
- 保留参数签名不变。

## 行为规格

### 子代理工具安全过滤

子 agent 的 `tools` 参数必须过滤：
- `FORBIDDEN_SUBAGENT_TOOLS` 中的工具名（`write_file`, `edit_file`, `shell_exec`, `cli_execute` 等）。
- 只保留 `read_file`, `list_dir`, `glob_files`, `grep_files`, `rag_retrieve`, `web_search` 等安全工具。

### `task` 工具命名

- Supervisor 子代理：`coding`, `rag`, `web`, `custom-<key>`。
- Expert 子代理：`rag`, `web`, `custom-<key>`（不含 `coding`）。
- Team 角色：`frontend_dev`, `backend_dev`, `tester`, `architect`, `devops`, `ui_designer`, `product_manager`。

### 审批透传

- 主 agent 的 `interrupt_on` 会继承到通过 `SubAgent` 声明的子 agent（deepagents 默认行为）。
- 子 agent 的 `interrupt_on` 在 `SubAgent` spec 中显式覆盖为只包含安全工具（即无危险工具，因为子代理本身不暴露危险工具）。
- 如果子代理需要访问未授权目录，仍走主 agent 的 `directory_extension` 审批流程。

### Rubric 运行时自纠

- 仅当 `rubric` 参数非空时启用。
- 最大迭代次数 3（与 eval 一致）。
- 自纠完成后主 agent 输出最终答案。
- 如果 grader 失败或模型不可用，降级为普通执行（不抛错）。

## 工具冲突管理

| deepagents 工具 | 处理方式 | 理由 |
|---|---|---|
| `task` | **启用** | 替代自研委派工具 |
| `ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep` | `excluded_tools` | 继续用项目自研 fs 工具 |
| `execute` | 不存在（被排除） | 项目自研 `cli_execute` |

## SSE 事件契约

- `tool_call`：包含 `task` 工具调用（`subagent_type` 参数表示目标子代理）。
- `tool_result`：子代理返回结果。
- 子代理内部工具调用不单独产生 SSE，保持与现有 `_collect_agent_result` 一致。
- `approval_request`：当 `task` 调用的子代理需要访问未授权目录时触发。

## 测试规格

### 单元测试

1. `test_harness_subagents.py`: 验证 `create_agent(subagents=...)` 正确调用 `create_deep_agent`，`task` 工具存在。
2. `test_custom_agent_deep.py`: 验证 `build_custom_agent` 返回的 agent 是基于 `create_deep_agent`。
3. `test_execution_layer.py`: 验证 `run_agent_with_approval` 正确封装审批循环。

### 集成测试

1. `test_supervisor_subagent_task.py`: Supervisor 通过 `task` 调用 `rag` 子代理。
2. `test_coding_expert_subagent_task.py`: coding Expert 通过 `task` 调用 `web` 子代理。
3. `test_subagent_parallel.py`: 一次 LLM 调用并行触发多个 `task`。

### E2E 测试

1. 完整对话：Supervisor → `task` coding Expert → 返回结果 → 合成回复。
2. 带审批：子代理访问未授权目录触发 `directory_extension` 审批。

## 回滚规格

- 每个子任务独立 git commit。
- `run_work_supervisor` 和 `run_coding_expert` 保留旧 import 路径作为 fallback 注释（不实际启用）。
- 部署验证失败时，优先 revert 子 agent 迁移 commit，保留公共执行层。
