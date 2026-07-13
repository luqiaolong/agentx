# Proposal: AgentTeam v2 全量重构

## Why

AgentTeam 路径自 2026-07-06 引入以来，经历了 deepagents 迁移、Phase 1 稳定性硬化。
**本 change 基于「feature/agent-team-dag-orchestration 分支（含 Phase 1+2+DAG 完整实现）
已合并到 master 后的代码状态」**——但合并后的实现是中间态，不是 v2 设计的终态。

### 合并后的代码状态（事实依据）

- **模块仍是 5 文件结构**：`backend/app/team/` 仍为 `orchestrator.py` / `planner.py` /
  `scheduler.py` / `aggregator.py` / `blackboard.py`，未拆分为 v2 设计的 11 文件
  （未创建 `state.py` / `dispatcher.py` / `nodes.py` / `graph_builder.py` / `runner.py` / `classifier.py`）
- **`orchestrator.py` 从约 780 行膨胀至 1250 行**：Phase 1+2+DAG 的实现全部堆叠在单文件内
  （plan / dispatch / 6 类节点派发 / DAG 多波次 / replan / 入口流 / SSE 发射）
- **13 项改动（D1-D13）落地状态**：
  - 3 项已实现但有命名/细节偏差（D3 DAG 命名不一致 / D5 配置项前缀 `agent_team_*`
    而非 `team_*` / D11 缺 SSE warning）
  - 5 项部分实现（D4 结果传递单函数而非双函数 / D6 仅 ASCII 词边界缺 CJK + LLM 分类器 /
    D7 fallback 已有但缺 warning / D8 abort 用轮询式而非 registry + cancel /
    D10 Replanner 触发位置错误——在 `check_next_level` 无层时触发而非质量门失败时触发）
  - 5 项完全未实现（D1 模块拆分 / D2 结构化输出 / D9 失败重试 /
    D12 Token 一致性 / D13 Findings Key 复合格式）

### 三个核心遗留问题

#### 问题 1：`state.warnings` channel 完全缺失

[blackboard.py:148-188](file:///d:/java/agentprojects/agentx/backend/app/team/blackboard.py#L148-L188)
`TeamState` 无 `warnings` 字段。当前 D6/D7/D11 的可观测性（危险任务改写、fallback 触发、
team_role 缺配置等）均无统一的 warning 写入与 SSE 发射通道，运行时排障困难。

#### 问题 2：配置项命名系统性偏差

当前用 `agent_team_*` 前缀（[settings.py:187](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L187)
`agent_team_max_parallel` / [settings.py:193](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L193)
`agent_team_max_replans` / `agent_team_subtask_timeout`），spec 要求 `team_*` 前缀
（`team_max_concurrency` / `team_max_replan_attempts` / `team_max_retries` / `team_classifier_timeout`）。
命名不一致导致配置项与 spec 契约脱钩。

#### 问题 3：D10 Replanner 触发位置错误

[orchestrator.py:898-1026](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L898-L1026)
`_replan_check_node` 在 [orchestrator.py:874-883](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L874-L883)
`_check_next_level_node` 检测到「无下一层」时触发。spec 要求 replan 在 `_aggregate_node`
质量门失败时触发——当前 `_aggregate_node` 后直接 `END`，**质量门失败路径完全缺失**。
此外 abort 用轮询式（[scheduler.py:253-287](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L253-L287)
`asyncio.wait(timeout=5)`）而非 spec 要求的 `_running_tasks` registry + 主动 `task.cancel()`。

### v2 的核心价值（重新定位）

v2 的核心价值从「全量重构 + 推倒重来」**调整为「模块拆分 + 修复偏差 + 补齐未实现项」**：

1. **模块拆分（D1）**：把 `orchestrator.py` 1250 行的混合职责拆分为 11 文件，
   消除「改一处要查 3 个文件、异常被静默吞掉」的维护性问题
2. **修复偏差（D3/D5/D11/D10/D8 等）**：把已实现但有命名/细节偏差的代码对齐到 spec 契约
3. **补齐未实现项（D2/D9/D12/D13/D14 等）**：补齐结构化输出、失败重试、
   token 一致性、findings key 复合格式、`state.warnings` channel 等缺失能力

### 模块膨胀问题（合并后状态）

[orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py) 单文件承载：

- DAG 多波次派发（`_dispatch_batch_node` / `_dispatch_batch_router` /
  `_check_next_level_node` / `_route_after_level` / `_replan_check_node`）
- 6 类分类节点（`_NODE_DISPATCH` 派发表 + default fallback）
- StateGraph 构建（`_build_team_graph`）
- 入口流处理（`run_team_path`）
- abort 检查、状态更新 helper、SSE 事件发射
- 依赖上下文注入（`_inject_dependency_context`）
- `team_semaphore` 限流

每次行为修复要改 5 处分类节点（漂移风险），删字段要查 3 个文件，异常被静默吞掉导致排查链路断裂。

## What Changes

本 change 共 **13 项改动（D1-D13）** + 3 项新增设计决策（D14/D15/D16，详见
[design.md](design.md)）。每项标注当前实现状态：

- `[已实现-需对齐]`：已实现但有命名/细节偏差，仅需小范围调整
- `[部分实现-需补齐]`：已实现核心逻辑但缺失关键子能力，需补齐
- `[未实现-需新建]`：完全缺失，需新建文件/函数

### D1. 模块拆分（11 文件，单一职责）`[未实现-需新建]`

将 [orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py)
（当前 1250 行）拆为 11 个模块，每个文件单一职责：

```
backend/app/team/
├── __init__.py
├── state.py           # TeamState/SubtaskState/TaskDAG TypedDict + Pydantic schema
├── planner.py         # Orchestrator LLM + with_structured_output(TeamPlan)
├── dispatcher.py      # DAG 拓扑排序(Kahn) + wave 解析 + Send 生成
├── nodes.py           # 纯节点函数: plan_node/execute_node/barrier_node/aggregate_node/replan_node
├── graph_builder.py   # _build_team_graph (StateGraph 构建)
├── runner.py          # run_team_path 入口 + astream 事件消费
├── scheduler.py       # 子任务执行helper: Semaphore限流 + retry + abort cancel
├── aggregator.py      # Aggregator LLM + 质量门 + 结构化findings
├── blackboard.py      # Reducers (findings/errors/warnings/subtask_results)
├── classifier.py      # 危险任务LLM分类器(替代子串匹配)
```

**当前状态**：[backend/app/team/](file:///d:/java/agentprojects/agentx/backend/app/team/) 仍是
5 文件，未创建 `state.py` / `dispatcher.py` / `nodes.py` / `graph_builder.py` / `runner.py` / `classifier.py`。

### D2. 结构化任务通信 `[未实现-需新建]`

[planner.py](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py) Orchestrator LLM 改用
`with_structured_output(TeamPlan)`，替代正则解析：

```python
class TeamTask(BaseModel):
    id: str                              # "t1", "t2"...
    agent: str                           # code/deep/rag/web/frontend_dev...
    description: str
    depends_on: list[str] = []           # 依赖的 task IDs
    expected_output: str
    is_dangerous_hint: bool = False

class TeamPlan(BaseModel):
    tasks: list[TeamTask]
    summary: str
    needs_iterative: bool = False
```

保留正则解析作为 LLM 不支持结构化输出时的 fallback。

**当前状态**：[planner.py:115-119](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L115-L119)
仍用 `_AGENT_PREFIX_RE` 正则解析 `[agent:xxx]` 行，无 `with_structured_output`。

### D3. DAG 多波次派发（P0 核心） `[已实现-需对齐]`

用 Kahn 算法 `resolve_waves(tasks) -> list[list[TeamTask]]`，每 wave 内并行 Send fan-out，
wave 间串行通过 `barrier_node` 控制流转。

**当前状态**：[planner.py:344-417](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L344-L417)
已实现 `_validate_dag`（Kahn 算法分层 + 循环检测），但命名为 `_validate_dag` 而非 spec 要求的
`resolve_waves`，且仍位于 `planner.py` 而非 `dispatcher.py`。
[orchestrator.py:336-411](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L336-L411)
已实现 `_dispatch_batch_node` + `_dispatch_batch_router`（单 wave 派发），需迁移到 `dispatcher.py`。

### D4. 子任务结果传递（P0） `[部分实现-需补齐]`

`SubtaskState` 新增 `upstream_findings: dict[str, Finding]`。生成 Send 时根据
`task.depends_on` 从 `state.findings` 注入上游结果。子任务 runner 把 `upstream_findings`
拼入 agent context（`[依赖任务结果]` 段 + 当前任务 input）。

**当前状态**：[orchestrator.py:140-180](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L140-L180)
已实现 `_inject_dependency_context`（**单函数**），spec 要求拆分为双函数
（`_inject_upstream_findings` + `_compose_input_with_upstream`）。
`blackboard.py:179` 的 `findings: Annotated[dict[str, str], _merge_dict]` 仍是裸字符串 dict
（应为 `dict[str, Finding]`）。

### D5. 并发限流（P1） `[已实现-需对齐]`

[scheduler.py](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py) 新增全局
`asyncio.Semaphore(settings.team_max_concurrency)`（默认 5）。每个子任务执行
`async with semaphore` 后再调用 runner。

**当前状态**：[orchestrator.py:1139](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L1139)
已实现 `team_semaphore = asyncio.Semaphore(max_parallel)`，但配置项命名为
[settings.py:187](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py#L187)
`agent_team_max_parallel: int = Field(default=3, ge=1, le=5)`（默认值与范围均与 spec 不符）。
spec 要求改名为 `team_max_concurrency: int = Field(default=5, ge=1, le=20)`。

### D6. 危险任务 LLM 分类器（P0） `[部分实现-需补齐]`

[classifier.py](file:///d:/java/agentprojects/agentx/backend/app/team/classifier.py) 的
`DangerousTaskClassifier`：LLM `with_structured_output(ClassificationResult)`。
输入 `task.description` + `agent` + 可用工具；输出 `is_dangerous` + `reason` + `suggested_agent`。
缓存按 `task.description` hash。LLM 不可用时降级到改进的关键词匹配（带词边界 `\b` 防止
「修改」误命中「查看修改历史」）。

**当前状态**：[utils/text.py:274-294](file:///d:/java/agentprojects/agentx/backend/app/utils/text.py#L274-L294)
已实现 `compile_keyword_patterns`（ASCII `\b` 词边界 + CJK 子串匹配），但仅作为 keyword 匹配工具，
未封装为 `DangerousTaskClassifier` 类，缺 LLM 分类器路径与 SSE warning 发射。
`classifier.py` 文件未创建。

### D7. 未知 Agent Fallback（P1） `[部分实现-需补齐]`

`_default_node` 改名 `_fallback_node`（或并入统一 `execute_node` 的 default 配置分支）：
尝试用 `code` agent 执行，发射 `warning` SSE 事件，写入 `state.warnings`。

**当前状态**：[orchestrator.py:582-588](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L582-L588)
已实现 `_NODE_DISPATCH["default"]` fallback 到 `code`，但缺 SSE warning 发射与
`state.warnings` 写入（受 D14 阻塞）。

### D8. Abort 响应 LLM 长调用（P1） `[部分实现-需补齐]`

[scheduler.py](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py) 把子任务 runner
包装为 `asyncio.Task`，注册到 `_running_tasks[thread_id]`。abort handler 调用 `task.cancel()`
中断 LLM 长调用。`CancelledError` 被捕获返回 `TeamSubtaskResult(success=False, payload="用户中止")`。

**当前状态**：[scheduler.py:253-287](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L253-L287)
`_run_subtask_stream._iterate` 用 `asyncio.wait(FIRST_COMPLETED, timeout=5)` **轮询式竞速 abort**，
而非 spec 要求的 `_running_tasks` registry + 主动 `task.cancel()`。轮询式延迟 5s，
且无法中断 LLM 长调用本身。

### D9. 失败重试（P2） `[未实现-需新建]`

[scheduler.py](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py) 新增
`max_retries=2`，指数退避 `1s/2s/4s`。仅重试瞬态失败（`TimeoutError`、网络异常），逻辑错误不重试。
记录 `retries` 字段到 `subtask_results`。

**当前状态**：完全未实现。无 `max_retries` / 指数退避 / `TRANSIENT_ERRORS` 分类。

### D10. 迭代式拆解 / Replanner（P2） `[部分实现-需补齐]`

质量门失败时 `replan_node` 把原任务 + findings + errors 重新喂给 planner。
`max_replan_attempts=1`（可配置）。发射 `replan` SSE 事件。

**当前状态**：[orchestrator.py:898-1026](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L898-L1026)
已实现 `_replan_check_node`，但**触发位置错误**——当前在
[orchestrator.py:874-883](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L874-L883)
`_check_next_level_node` 检测到「无下一层 wave」时触发（`check_next_level → replan_check`），
spec 要求在 `_aggregate_node` 质量门失败时触发（`_aggregate_node → _route_after_aggregate → replan`）。
当前 `_aggregate_node` 后直接 `END`，**质量门失败路径完全缺失**。需新增
`_route_after_aggregate` 条件边（详见 [design.md D16](design.md)）。

### D11. 静默降级消除（P1） `[已实现-需对齐]`

[orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py)
`_run_team_role_subtask` 配置缺失时发射 `warning` 事件 + 写入 `state.warnings`，不再静默 fallback。
配置层新增启动校验 `validate_team_subagents`：所有 `enabled=True` 的 `team_subagents`
必须有非空 `system_prompt`，否则启动失败。

**当前状态**：[scheduler.py:356-369](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L356-L369)
已实现 `_run_team_role_subtask` 显式失败（不再静默降级到 coding Expert）；
[config/subagents.py:294-327](file:///d:/java/agentprojects/agentx/backend/app/config/subagents.py#L294-L327)
已实现 `validate_team_subagents`；[main.py:113-115](file:///d:/java/agentprojects/agentx/backend/app/main.py#L113-L115)
lifespan 已调用。**仅缺 SSE warning 发射**（受 D14 阻塞）。

### D12. Token 透传一致性（P2） `[未实现-需新建]`

所有 `execute` 节点统一 token 事件 schema：`{agent: str, content: str}`。
aggregator token 事件：`{agent: "aggregator", content: str}`。前端无需特殊处理。
消除当前 6 个分类节点 token 事件字段不一致问题。

**当前状态**：[sse/events.py:98-99](file:///d:/java/agentprojects/agentx/backend/app/sse/events.py#L98-L99)
token 事件 payload 为**纯字符串**，未结构化为 `{agent, content}`。

### D13. Findings Key 区分（P2） `[未实现-需新建]`

Key 格式改为 `{agent}:{task_id}:{wave_index}`（如 `code:t1:0`）。
`_make_subtask_state_update` 使用复合 key。同类型多任务不再互相覆盖。
替代当前 `{agent}-{task_index}` 格式（同类型多任务在多波次场景下会覆盖）。

**当前状态**：[orchestrator.py:433](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L433)
仍用 `key = f"{result.agent}-{task_index}"` 旧格式，未改为复合 key。

## Dependencies

**自包含，无外部 DEPENDS_ON**。

**基于已合并的 Phase 1+2+DAG 实现增量改进**——两个 prior change
（`agent-team-dag-orchestration` 与 `2026-07-12-agent-team-architecture-cleanup`）
均已合并到 master 并 archive。本 change 不再 `supersedes` 它们（
`.openspec.yaml` 中的 supersedes 关系保留以记录 capability 演进链路），
而是在合并后代码基础上做增量改进：

- **保留**：合并后已正确实现的 DAG 分层（`_validate_dag`）、`team_semaphore` 限流、
  `_NODE_DISPATCH["default"]` fallback、`validate_team_subagents` 启动校验等
- **重命名/对齐**：配置项前缀 `agent_team_*` → `team_*`（D5/D15）、
  `_validate_dag` → `resolve_waves` 并迁移到 `dispatcher.py`（D3）
- **补齐**：`state.warnings` channel（D14）、`with_structured_output`（D2）、
  失败重试（D9）、token schema 统一（D12）、findings 复合 key（D13）
- **修复**：D10 Replanner 路由（`_aggregate_node` 质量门失败 → replan，详见 D16）、
  D8 abort 改为 `_running_tasks` registry + 主动 cancel

## Capabilities

### New Capabilities

- `agent-team-v2`：AgentTeam v2 全量重构后的能力契约。涵盖 DAG 多波次派发、结构化任务通信、
  并发限流、危险任务 LLM 分类、未知 agent fallback、abort 响应 LLM 长调用、失败重试、
  迭代式 replan、静默降级消除、token 透传一致性、findings key 区分、`state.warnings` channel。
  详见 [specs/agent-team-v2/spec.md](specs/agent-team-v2/spec.md)。

### Modified Capabilities

- `agent-team`（[archive/2026-07-06-agent-team/spec.md](file:///d:/java/agentprojects/agentx/openspec/changes/archive/2026-07-06-agent-team/specs/agent-team/spec.md)）：
  本 change 在合并后代码基础上推倒重写 Team 路径的核心控制流。`TeamState` / `TeamPlanTask` schema 变更，
  `_dispatch_node` 改为 `dispatcher.resolve_waves` + 多波次 Send，6 个分类节点合并为单一
  `execute_node`，`_default_node` 改为 fallback 分支。项目约定无需向后兼容，直接删除旧代码。

## Impact

### 后端文件清单

**新增 6 文件**（[backend/app/team/](file:///d:/java/agentprojects/agentx/backend/app/team/)）：

- `backend/app/team/state.py`（从 `orchestrator.py` / `blackboard.py` 提取 `TeamState` / `TeamPlanTask`）
- `backend/app/team/dispatcher.py`（从 `orchestrator.py` 提取 `_dispatch_batch_node` / `_inject_dependency_context` / `_check_next_level_node` + 从 `planner.py` 提取 `_validate_dag` 并改名 `resolve_waves`）
- `backend/app/team/nodes.py`（从 `orchestrator.py` 提取 `_NODE_DISPATCH` + 节点函数）
- `backend/app/team/graph_builder.py`（从 `orchestrator.py` 提取 `_build_team_graph` + 新增 `_route_after_aggregate`）
- `backend/app/team/runner.py`（从 `orchestrator.py` 提取 `run_team_path` + astream 消费）
- `backend/app/team/classifier.py`（新建，`DangerousTaskClassifier`）

**重写 4 文件**：

- `backend/app/team/planner.py`（保留 `_validate_dag` / `_parse_todos_from_text`，新增 `Planner` 类 + `with_structured_output`）
- `backend/app/team/scheduler.py`（新增 `_running_tasks` registry + `cancel_running_tasks` + `run_with_retry` + `TRANSIENT_ERRORS`）
- `backend/app/team/aggregator.py`（新增结构化 findings + 质量门）
- `backend/app/team/blackboard.py`（新增 `_merge_warnings` reducer + findings 改为 `dict[str, Finding]`）

**删除 1 文件**：

- `backend/app/team/orchestrator.py`（约 1250 行，职责拆分到上述文件后删除）

**修改文件**：

- [backend/app/config/settings.py](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py)：
  重命名 `agent_team_max_parallel` → `team_max_concurrency: int = Field(default=5, ge=1, le=20)`、
  `agent_team_max_replans` → `team_max_replan_attempts: int = Field(default=1, ge=0, le=5)`、
  新增 `team_max_retries: int = Field(default=2, ge=0, le=5)`、
  `team_classifier_timeout: float = Field(default=2.0, ge=0.1, le=10.0)`
- [backend/app/config/subagents.py](file:///d:/java/agentprojects/agentx/backend/app/config/subagents.py)：
  `validate_team_subagents` 已实现，仅需验证与 `team_*` 命名对齐
- [backend/app/main.py](file:///d:/java/agentprojects/agentx/backend/app/main.py)：
  lifespan 已调用 `validate_team_subagents`，无需新增
- [backend/app/sse/events.py](file:///d:/java/agentprojects/agentx/backend/app/sse/events.py)：
  token 事件 payload 改为 `{agent: str, content: str}` 结构

### 前端

无改动。SSE 事件契约不新增类型，仅 token 事件 schema 统一为 `{agent, content}`（前端无需特殊处理）。

### 测试

**已存在（合并后）**：

- [tests/python/unit/test_team_dag.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_team_dag.py)（681 行）：DAG 分层 / dispatch / barrier 路由
- [tests/python/unit/test_team_fallback.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_team_fallback.py)（333 行）：fallback / team_role 显式失败
- [tests/python/unit/test_team_stability.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_team_stability.py)（450 行）：稳定性
- [tests/python/unit/test_keyword_patterns.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_keyword_patterns.py)（109 行）：关键词词边界
- [tests/python/unit/test_quality_gate.py](file:///d:/java/agentprojects/agentx/tests/python/unit/test_quality_gate.py)（105 行）：质量门

**需新增 / 补齐**：

- `tests/python/unit/test_team_v2.py`：模块拆分后的核心流程（plan / dispatch / execute / aggregate）
- `tests/python/unit/test_team_v2_concurrency.py`：Semaphore 限流、abort cancel、retry 策略
- `tests/python/unit/test_team_v2_classifier.py`：DangerousTaskClassifier LLM 路径 + 关键词降级（CJK 词边界补齐）
- `tests/python/unit/test_team_v2_replan.py`：replan_node、`_route_after_aggregate` 质量门失败路由、findings key 复合格式

测试运行命令：
- `uv run pytest tests/python/unit -m "not integration" -k "team"`
- `uv run ruff check backend/app/team/`

### 依赖

无新增外部依赖。Kahn 算法手写（不引入 networkx），`asyncio.Semaphore` / `asyncio.Task` 是标准库。

## Rollback Plan

1. **代码层**：所有变更集中于单分支，回滚即 `git revert` 单个 merge commit；无数据迁移、无 schema 变更
2. **模块拆分（D1）**：若拆分后引入循环依赖或导入错误，回退为单文件 `orchestrator.py`（保留 git 历史中的旧版本）
3. **结构化输出（D2）**：若 `with_structured_output` 在某些 LLM provider 上不支持，回退为正则解析 fallback（保留旧 `_AGENT_PREFIX_RE`）
4. **DAG 多波次（D3）**：DAG 已合并实现，回退保留现有 `_dispatch_batch_node` 单 wave 派发
5. **结果传递（D4）**：若 findings 注入导致 input 过长触发 LLM context 限制，回退为「只传 summary 不传 full findings」（截断到 `team_result_max_chars`）
6. **并发限流（D5）**：若 Semaphore 引发死锁（如子任务内部又 acquire 同一 semaphore），回退为无限制并行
7. **危险分类器（D6）**：若 LLM 分类延迟过高（>2s），回退为关键词匹配（保留词边界 `\b` 改进）
8. **Fallback（D7）**：若 code runner 兜底引发新异常，回退为原 `_default_node` 失败行为
9. **Abort cancel（D8）**：若 `task.cancel()` 引发未捕获的 `CancelledError` 级联，回退为事件边界检查方案
10. **重试（D9）**：若重试导致雪崩（上游限流加重），回退为 `max_retries=0` = 不重试
11. **Replan（D10）**：若 LLM 陷入「无限追加任务」循环，回退为 `max_replan_attempts=0` = 一次性 plan
12. **静默降级消除（D11）**：若 `validate_team_subagents` 启动校验过严阻塞启动，回退为 warn 不 error
13. **Token 一致性（D12）**：若统一 schema 破坏前端渲染，回退为各节点独立 schema
14. **Findings key（D13）**：若复合 key 破坏 aggregator 解析，回退为 `{agent}-{task_index}` 格式
15. **state.warnings channel（D14）**：若 reducer 冲突，回退为不写入 state，仅 SSE 发射
16. **配置项重命名（D15）**：若引用遗漏导致启动失败，通过 `git grep agent_team_` 全量回退
17. **Replanner 路由（D16）**：若 `_route_after_aggregate` 条件边引发死循环，回退为 `_aggregate_node → END`（不触发 replan）
18. **验证门禁**：合并前必须通过 `uv run pytest tests/python/unit -m "not integration" -k "team"` +
    `uv run ruff check backend/app/team/` + Team 路径端到端冒烟（顺序任务 / 并行任务 / 混合任务 / replan 任务 / abort / retry）

## Out of Scope

- 不新增 SSE 事件类型（`team_node_done` / `team_node_error` 等留待未来 UX 增强）
- 不做条件分支编排（DAG 是确定性依赖图，不支持 `if-else` 分支）
- 不做跨会话的 plan 持久化（plan 仅在单次 team 运行内有效）
- 不做子任务失败后的自动扩 plan（失败任务的结果进 errors，依赖任务可读取 errors 决定是否继续）
- 不做前端渲染改动（token 事件 schema 统一后前端无需适配）
- 不引入灰度开关 / 版本兼容层 / `@Deprecated`（项目约定直接推倒重来）
- 不回滚已合并的 Phase 1+2+DAG 实现（在合并基础上增量改进）
