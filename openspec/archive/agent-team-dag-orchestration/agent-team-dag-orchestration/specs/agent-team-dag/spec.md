# Spec: AgentTeam DAG 依赖编排

## ADDED Requirements

### Requirement: DAG 依赖标注解析

The system MUST parse Orchestrator's `[after:N1,N2]` annotations via `_parse_todos_from_text` into `deps: list[int]`.

#### Scenario: 单依赖解析

- **Given** LLM 输出 `[agent:deep][after:0] 修改 src/main.py 添加日志`
- **When** 调用 `_parse_todos_from_text(text)`
- **Then** 返回 `[{"content": "[agent:deep][after:0] 修改 src/main.py 添加日志", "status": "pending", "deps": [0]}]`

#### Scenario: 多依赖解析

- **Given** LLM 输出 `[agent:rag][after:0, 1] 检索 FastAPI 最佳实践`
- **When** 调用 `_parse_todos_from_text(text)`
- **Then** 返回 `[{"content": "...", "status": "pending", "deps": [0, 1]}]`

#### Scenario: 无依赖解析

- **Given** LLM 输出 `[agent:code] 读取 src/main.py`
- **When** 调用 `_parse_todos_from_text(text)`
- **Then** 返回 `[{"content": "...", "status": "pending", "deps": []}]`

#### Scenario: 容错 - 非数字依赖

- **Given** LLM 输出 `[agent:deep][after:abc] 修改文件`
- **When** 调用 `_parse_todos_from_text(text)`
- **Then** 返回 `[{"content": "...", "status": "pending", "deps": []}]`，并 logger.warning 记录

#### Scenario: 容错 - 越界依赖

- **Given** tasks 长度为 2，某任务 `deps=[99]`
- **When** 调用 `_validate_dag(tasks)`
- **Then** 越界索引 99 被丢弃，logger.warning 记录，任务仍可执行（deps 变为空）

### Requirement: 拓扑排序与循环检测

`_validate_dag` MUST correctly compute topological levels and detect/break cycles.

#### Scenario: 线性链

- **Given** tasks = `[A(deps=[]), B(deps=[0]), C(deps=[1])]`
- **When** 调用 `_validate_dag(tasks)`
- **Then** 返回 `levels = [[0], [1], [2]]`，`dropped_edges = []`

#### Scenario: 菱形依赖

- **Given** tasks = `[A(deps=[]), B(deps=[0]), C(deps=[0]), D(deps=[1, 2])]`
- **When** 调用 `_validate_dag(tasks)`
- **Then** 返回 `levels = [[0], [1, 2], [3]]`，`dropped_edges = []`

#### Scenario: 全并行（无依赖）

- **Given** tasks = `[A(deps=[]), B(deps=[]), C(deps=[])]`
- **When** 调用 `_validate_dag(tasks)`
- **Then** 返回 `levels = [[0, 1, 2]]`，`dropped_edges = []`

#### Scenario: 循环检测与打破

- **Given** tasks = `[A(deps=[1]), B(deps=[0])]`（A 依赖 B，B 依赖 A，循环）
- **When** 调用 `_validate_dag(tasks)`
- **Then** 不抛异常，返回 `levels` 包含所有任务（强制打破循环），`dropped_edges` 非空，logger.error 记录

#### Scenario: 自环检测

- **Given** tasks = `[A(deps=[0])]`（A 依赖自己）
- **When** 调用 `_validate_dag(tasks)`
- **Then** 自环边被丢弃，`levels = [[0]]`，logger.warning 记录

### Requirement: 分批 fan-out

`_dispatch_batch_node` MUST dispatch tasks by topological level — parallel within a level, serial across levels.

#### Scenario: 多层派发

- **Given** `pending_levels = [[0], [1, 2], [3]]`，plan 有 4 个任务
- **When** 执行 `_dispatch_batch_node`
- **Then** 返回 `[Send("subtask", {task_index: 0, config: ...})]`（只派发第一层，复用 Phase 2 单一 subtask 节点）
- **When** 第一层子任务完成，`_check_next_level_node` 检查
- **Then** `pending_levels` 仍有 `[[1, 2], [3]]`，条件边路由到 `dispatch_batch`
- **When** 第二层派发
- **Then** 返回 `[Send("subtask", {task_index: 1, ...}), Send("subtask", {task_index: 2, ...})]`（层内并行）

#### Scenario: 空计划

- **Given** `pending_levels = []`
- **When** 执行 `_dispatch_batch_node`
- **Then** 返回 `[Send("aggregate", {})]`

#### Scenario: 全部完成

- **Given** `pending_levels = []` 且 `replan_count < max_replans`
- **When** 执行 `_check_next_level_node`
- **Then** 条件边路由到 `replan_check`

### Requirement: 子任务间结果传递

The system MUST auto-inject dependency findings into dependent task's input via `_inject_dependency_context`.

#### Scenario: 单依赖注入

- **Given** task = `TeamPlanTask(agent="deep", input="修改文件", deps=[0])`，
  `findings = {"code-0": "src/main.py 内容如下..."}`
- **When** 调用 `_inject_dependency_context(task, findings)`
- **Then** 返回字符串包含 `[依赖任务结果]` + `--- #0 [code-0] ---` + `src/main.py 内容如下...` + `[当前任务]` + `修改文件`

#### Scenario: 多依赖注入

- **Given** task = `TeamPlanTask(agent="deep", input="修改", deps=[0, 1])`，
  `findings = {"code-0": "...", "rag-1": "..."}`
- **When** 调用 `_inject_dependency_context(task, findings)`
- **Then** 返回字符串包含两个依赖段 + 当前任务段

#### Scenario: 依赖未完成

- **Given** task = `TeamPlanTask(agent="deep", input="修改", deps=[0, 99])`，
  `findings = {"code-0": "..."}`（99 不存在）
- **When** 调用 `_inject_dependency_context(task, findings)`
- **Then** 返回字符串包含 `--- #99 (未完成或失败) ---`

#### Scenario: 截断

- **Given** task deps=[0]，`findings["code-0"]` 长度 3000，`agent_team_result_max_chars=2000`
- **When** 调用 `_inject_dependency_context(task, findings)`
- **Then** 依赖段长度不超过 2000 + `[结果已截断]` 标记

#### Scenario: 无依赖

- **Given** task = `TeamPlanTask(agent="code", input="读取", deps=[])`
- **When** 调用 `_inject_dependency_context(task, findings)`
- **Then** 返回原 input 不变

### Requirement: 未知 agent fallback 到 code

Unknown agents via Phase 2's `_DEFAULT_CONFIG` MUST fallback to code runner instead of failing directly.

> **DEPENDS_ON Phase 2 A1**：本要求在 Phase 2 统一的 `_run_subtask_node` + `_DEFAULT_CONFIG` 之上叠加 fallback 语义。

#### Scenario: 未知 agent 兜底

- **Given** task = `TeamPlanTask(agent="unknown_agent", input="...", purpose="...")`，经 `_DEFAULT_CONFIG` 派发
- **When** `_run_subtask_node` 执行未知 agent 分支
- **Then** logger.warning 记录 `agent="unknown_agent"`，实际调用 `_get_runner("code")` 执行
- **And** `TeamSubtaskResult.agent` 为 `"unknown_agent"`（保留原始名）
- **And** 不直接返回 `success=False`

#### Scenario: fallback 中止

- **Given** 未知 agent fallback 执行中 `abort_event.is_set()`
- **When** `_run_subtask_node` 检查到 abort
- **Then** 返回 `TeamSubtaskResult(success=False, payload="用户中止")`

### Requirement: team_role_node 显式失败

`_run_team_role_subtask` MUST fail explicitly when `cfg.system_prompt` is empty, not silently degrade.

#### Scenario: 配置缺失 system_prompt

- **Given** `settings.team_subagents["frontend_dev"].system_prompt = ""`，`enabled=True`
- **When** 执行 `_run_team_role_subtask(task=TeamPlanTask(agent="frontend_dev", ...))`
- **Then** logger.error 记录，返回 `TeamSubtaskResult(success=False, payload="团队角色 frontend_dev 配置缺失 system_prompt")`
- **And** 不调用 code runner

#### Scenario: 启动时校验

- **Given** settings 中某 `team_subagents[key].enabled=True` 且 `system_prompt=""`
- **When** 应用启动调用 `validate_team_subagents(settings)`
- **Then** 抛 `ValueError` 阻止启动

#### Scenario: 正常配置

- **Given** `settings.team_subagents["frontend_dev"].system_prompt` 非空
- **When** 执行 `_run_team_role_subtask`
- **Then** 走原 `build_custom_agent` 逻辑（不触发显式失败）

### Requirement: 迭代式 replan

`_replan_check_node` MUST check whether to append new tasks after all levels complete.

#### Scenario: LLM 判定需要追加

- **Given** 当前 plan 已全部执行，`findings` 非空，`replan_count=0`，`max_replans=2`
- **And** LLM 输出 `[agent:deep][after:0] 补充修改`
- **When** 执行 `_replan_check_node`
- **Then** 解析新任务追加到 plan，`pending_levels` 更新，`replan_count=1`
- **And** 发射 `team_replan` SSE 事件
- **And** 条件边路由到 `dispatch_batch`

#### Scenario: LLM 判定无需追加

- **Given** LLM 输出 `NO_NEW_TASKS`
- **When** 执行 `_replan_check_node`
- **Then** 返回空 dict，条件边路由到 `aggregate`

#### Scenario: replan 次数上限

- **Given** `replan_count=2`，`max_replans=2`
- **When** 执行 `_replan_check_node`
- **Then** 不调用 LLM，直接返回空 dict，条件边路由到 `aggregate`

#### Scenario: replan 新任务依赖已完成任务

- **Given** 原 plan 有 2 个任务（索引 0、1），LLM replan 输出 `[agent:deep][after:0,1] 补充`
- **When** 执行 `_replan_check_node`
- **Then** 新任务 `deps=[0, 1]`（绝对索引），追加到 plan 后索引为 2
- **And** 新任务的 input 通过 `_inject_dependency_context` 注入 #0 和 #1 的 findings

### Requirement: StateGraph 拓扑完整性

The new StateGraph MUST overlay DAG orchestration nodes on top of Phase 2's single `subtask` node.

> **DEPENDS_ON Phase 2 A1**：复用 Phase 2 统一的 `_run_subtask_node`（节点名 `subtask`），不重新引入 6 个分类节点。

#### Scenario: 拓扑节点完整

- **Given** 调用 `_build_team_graph()`
- **When** 检查 graph 节点
- **Then** 包含 `plan` / `dispatch_batch` / `subtask` / `check_next_level` / `replan_check` / `aggregate`
- **And** 不包含 `subtask_deep` / `subtask_code` / `subtask_builtin` / `subtask_team_role` / `subtask_custom` / `subtask_default`（这些属 Phase 2 前的旧结构）

#### Scenario: 条件边路由 - check_next_level

- **Given** `pending_levels` 非空
- **When** `_route_after_level(state)` 被调用
- **Then** 返回 `"dispatch_batch"`
- **Given** `pending_levels` 为空
- **When** `_route_after_level(state)` 被调用
- **Then** 返回 `"replan_check"`

#### Scenario: 条件边路由 - replan_check

- **Given** `_replan_check_node` 返回后 `pending_levels` 非空
- **When** `_route_after_replan(state)` 被调用
- **Then** 返回 `"dispatch_batch"`
- **Given** `pending_levels` 为空
- **When** `_route_after_replan(state)` 被调用
- **Then** 返回 `"aggregate"`

### Requirement: 配置项

New config fields MUST be configurable with sensible defaults.

#### Scenario: agent_team_max_replans 默认值

- **Given** 未显式配置 `agent_team_max_replans`
- **When** 读取 `settings.agent_team_max_replans`
- **Then** 返回 `2`

#### Scenario: agent_team_max_replans 范围校验

- **Given** 配置 `agent_team_max_replans=5`
- **When** 加载 settings
- **Then** 校验通过（范围 0-5）
- **Given** 配置 `agent_team_max_replans=-1`
- **When** 加载 settings
- **Then** 校验失败

### Requirement: 向后兼容

When no `[after:]` annotation is present, behavior MUST be equivalent to existing parallel fan-out.

#### Scenario: 无依赖等价并行

- **Given** LLM 输出 3 个任务，均无 `[after:]` 标注
- **When** 执行 `run_team_path`
- **Then** 3 个任务全部并行执行（单层 fan-out），行为与变更前一致
- **And** `team_done` 事件正常发射

#### Scenario: 降级路径不变

- **Given** 调用 `_should_downgrade_to_single` 返回 `True`
- **When** 执行 `run_team_path`
- **Then** 走降级路径（token + team_done），不进入 DAG 编排
