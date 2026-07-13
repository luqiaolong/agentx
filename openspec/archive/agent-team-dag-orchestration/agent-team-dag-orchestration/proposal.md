# Proposal: AgentTeam DAG 依赖编排

## Why

AgentTeam 当前采用「plan-once + parallel fan-out + aggregate-once」架构（[orchestrator.py:631-650](../../../backend/app/team/orchestrator.py#L631-L650) `_build_team_graph`）。
该架构对「独立可并行的子任务」是高效的，但深度分析（见会话 2026-07-13 agentteam 分析）暴露出三个**正确性缺陷**，
导致任何「顺序敏感」或「结果依赖」的团队任务出错或退化为单 agent：

### 缺陷 1：无依赖编排能力，并行 fan-out 破坏顺序语义

[orchestrator.py:192-233](../../../backend/app/team/orchestrator.py#L192-L233) `_dispatch_node` 一次性把所有子任务并行发出，
[planner.py:82-102](../../../backend/app/team/planner.py#L82-L102) Orchestrator prompt 也无法表达「先 A 后 B」。

**实际危害**：用户问「读 src/main.py 并添加日志」，LLM 拆成 `[code] 读取 src/main.py` + `[deep] 修改 src/main.py 添加日志`
→ 两个并行执行 → deep 任务在没有 code 读取结果的情况下盲改文件。这类「读→改」「检索→基于检索结果操作」
是软件开发团队的最常见协作模式，当前架构无法支持。

### 缺陷 2：子任务之间无结果传递

子任务 input 只来自 Orchestrator 一次拆解（[planner.py:233](../../../backend/app/team/planner.py#L233) `input_text`），
无法引用其他子任务的中间产物。`rag` 检索到的文档无法传给 `deep` 去基于文档修改代码；
`architect` 的方案无法传给 `backend_dev` 去实现。子任务之间是「信息孤岛」，
Orchestrator 拆解时的天然依赖关系被并行 fan-out 强行抹平。

### 缺陷 3：未知 agent 类型直接失败，无 fallback

[orchestrator.py:556-564](../../../backend/app/team/orchestrator.py#L556-L564) `_default_node` 返回 `success=False`。
LLM 偶尔会输出 `"frontend"`（少 `_dev`）或 `"researcher"`（不在 [BUILTIN_TEAM_KEYS](../../../backend/app/config/subagents.py#L99)），
整个子任务直接报废。团队协作中单个 agent 失败不应导致整体产出缺失，应有 code 兜底。

### 缺陷 4：team_role_node 静默降级掩盖配置错误

[scheduler.py:262-281](../../../backend/app/team/scheduler.py#L262-L281) `cfg.system_prompt` 为空就降级到 coding Expert，
不报错不日志。配置层若误删 system_prompt，用户看不到错误，团队角色「看起来在跑」但实际是通用 coder，
产出质量不可控且难以排查。

### 缺陷 5：一次性 plan，不支持迭代式拆解

`_plan_node` 拆完就结束，aggregate 完直接 `team_done`。若第一个子任务结果表明需要追加新任务
（如 code 读取后发现需要 rag 检索、architect 评估后需要 devops 评估部署成本），无法动态扩 plan。
真实软件开发是迭代式的，plan-once 模型与现实不匹配。

## What Changes

### D1. 完整 DAG 依赖图：Orchestrator 输出依赖标注，planner 解析为 DAG

扩展 Orchestrator prompt（[planner.py:82-102](../../../backend/app/team/planner.py#L82-L102)）支持依赖标注：

```
[agent:code] 读取 src/main.py 分析入口逻辑
[agent:rag][after:0] 基于 #0 的分析结果检索 FastAPI 最佳实践
[agent:deep][after:0,1] 基于 #0 读取的代码和 #1 检索的文档修改 src/main.py 添加日志
[agent:tester][after:2] 验证 #2 的修改是否通过测试
```

- `[after:N]` 单依赖，`[after:N1,N2]` 多依赖，无 `[after]` 标注的为根节点（首批并行）
- `_parse_todos_from_text`（[planner.py:138](../../../backend/app/team/planner.py#L138)）扩展：解析 `[after:...]` 标注，
  每个 todo 携带 `deps: list[int]` 字段
- `_todos_to_team_tasks`（[planner.py:200](../../../backend/app/team/planner.py#L200)）扩展：`TeamPlanTask` 新增 `deps: list[int]` 字段
- 新增 `_validate_dag(tasks)`：拓扑排序 + 循环检测；发现循环则 logger.error 并丢弃循环边（保留可执行部分）

### D2. 分批 fan-out：按拓扑层级执行，层内并行，层间串行

替换 [orchestrator.py:192-233](../../../backend/app/team/orchestrator.py#L192-L233) `_dispatch_node` 的一次性 fan-out：

- 新增 `_topological_levels(tasks)`：Kahn 算法分层，返回 `list[list[int]]`（每层一组任务索引）
- 新增 `_dispatch_batch_node`：从 `state.pending_levels` 取下一层，`Send` fan-out 该层所有任务
- StateGraph 拓扑调整：`plan → dispatch_batch → subtask_nodes → check_next_level → dispatch_batch | aggregate`
- `check_next_level` 条件边：还有未执行层 → 回到 `dispatch_batch`；全部执行完 → 进 `aggregate`
- 每层内部子任务并行（LangGraph Send API），层间串行（条件边回流）

### D3. 子任务间结果传递：依赖任务 input 自动拼接前置 findings

- 依赖任务（`deps` 非空）执行前，从 `state.findings` 提取 `deps` 对应的子任务结果
- 新增 `_inject_dependency_context(task, findings)`：把 `#N` 引用替换为 `findings[f"{agent}-{N}"]` 的内容
- 拼接格式注入 task.input 头部：
  ```
  [依赖任务结果]
  --- #0 [code] 读取 src/main.py 分析入口逻辑 ---
  {findings["code-0"]}

  --- #1 [rag] 检索 FastAPI 最佳实践 ---
  {findings["rag-1"]}

  [当前任务]
  基于 #0 读取的代码和 #1 检索的文档修改 src/main.py 添加日志
  ```
- `_dispatch_batch_node` 在 `Send` 时调用 `_inject_dependency_context` 构造最终 input

### D4. 未知 agent fallback 到 code（扩展 Phase 2 `_DEFAULT_CONFIG`）

**DEPENDS_ON Phase 2 A1**：本项在 Phase 2 统一的 `_run_subtask_node` + `_DEFAULT_CONFIG` 之上叠加 fallback 语义。

- Phase 2 的 `_DEFAULT_CONFIG`（[architecture-cleanup/design.md D1](../2026-07-12-agent-team-architecture-cleanup/design.md)）
  原行为：未知 agent emit delegation 后返回 `success=False`
- 本 change 扩展 `_DEFAULT_CONFIG` 行为：未知 agent emit delegation 后，调用 `_get_runner("code")` 兜底执行
- `logger.warning` 记录原始 agent 名便于排查：`"unknown agent '{agent}', fallback to code runner"`
- `TeamSubtaskResult.agent` 保留原始 agent 名（前端展示真实意图），但实际执行走 code runner

### D5. team_role_node 显式失败（不静默降级）

- [scheduler.py:262-281](../../../backend/app/team/scheduler.py#L262-L281) `cfg.system_prompt` 为空时：
  - `logger.error("team_role {agent} missing system_prompt, failing explicitly")`
  - 返回 `TeamSubtaskResult(success=False, payload="团队角色 {agent} 配置缺失 system_prompt")`
  - 不再静默降级到 coding Expert
- 配置层（[config/subagents.py](../../../backend/app/config/subagents.py)）新增启动时校验：
  所有 `enabled=True` 的 team_subagents 必须有非空 `system_prompt`，否则启动失败

### D6. 迭代式 replan：aggregate 前检查是否需要追加任务（复用 Phase 2 单一 subtask 节点）

**DEPENDS_ON Phase 2 A1**：复用 Phase 2 统一的 `_run_subtask_node`（节点名 `subtask`），不重新引入 6 个分类节点。

- `_aggregate_node` 前新增 `_replan_check_node`：
  - 把当前 findings 喂回 Orchestrator LLM，prompt 追加「已有任务结果如下，是否需要追加新任务？若需要，输出新的 `[agent:xxx][after:N]` 行」
  - LLM 输出新任务行 → 解析为 `TeamPlanTask`（`after` 引用已完成任务索引）→ 追加到 `state.plan` + `state.pending_levels`
  - LLM 输出 `NO_NEW_TASKS` 或空 → 直接进 `aggregate`
- StateGraph 拓扑：`... → check_next_level → dispatch_batch | _replan_check_node → dispatch_batch | aggregate`
  - `dispatch_batch` 通过 `Send("subtask", {task, config})` 复用 Phase 2 单一节点
- 防止无限循环：`state.replan_count` 限上限 2 次（可配置 `agent_team_max_replans: int = 2`）

## Dependencies

**本 change 显式 DEPENDS_ON**：`2026-07-12-agent-team-architecture-cleanup`（Phase 2 架构清理）

理由：本 change 的 DAG 编排建立在 Phase 2 的「统一 `_run_subtask_node` + 单一 `subtask` 节点 + `_NODE_DISPATCH` 派发表」之上。
若 Phase 2 未落地，本 change 的 T9（扩展 `_DEFAULT_CONFIG`）、T13（StateGraph 拓扑）、T15（子任务节点适配）
将改造的是 Phase 2 要删除的旧结构（6 个分类节点 + `_default_node`），引发硬冲突。

执行顺序：Phase 1（已落地）→ **Phase 2** → **本 change（DAG 编排）** → Phase 3（UX）

## Capabilities

### New Capabilities

- `agent-team-dag`：AgentTeam 支持 DAG 依赖编排 —— Orchestrator 输出 `[after:N]` 依赖标注，
  planner 构建完整 DAG（拓扑排序 + 循环检测），分批 fan-out（层内并行、层间串行），
  依赖任务 input 自动拼接前置 findings，迭代式 replan 支持动态扩 plan。

### Modified Capabilities

- `agent-team`（archive）：`TeamPlanTask` 新增 `deps: list[int]` 字段；`_dispatch_node` 改为
  `_dispatch_batch_node` 分批派发；StateGraph 拓扑增加 `check_next_level` 与 `_replan_check_node` 条件边。
- 无：SSE 事件契约不变（`team_init` / `delegation` / `todo_update` / `team_done` 语义不变，
  仅 `team_init.plan` 的每个 task 新增可选 `deps` 字段供前端展示依赖关系）。

## Impact

- **后端**：
  - 修改 [backend/app/team/planner.py](../../../backend/app/team/planner.py)（prompt 扩展 + `[after:]` 解析 + DAG 校验）
  - 修改 [backend/app/team/blackboard.py](../../../backend/app/team/blackboard.py)（`TeamPlanTask.deps` 字段 + `TeamState.pending_levels`/`completed_tasks`/`replan_count` 字段）
  - 修改 [backend/app/team/orchestrator.py](../../../backend/app/team/orchestrator.py)（`_dispatch_batch_node` + `check_next_level` + `_replan_check_node` + `_default_node` fallback + StateGraph 拓扑）
  - 修改 [backend/app/team/scheduler.py](../../../backend/app/team/scheduler.py)（`_run_team_role_subtask` 显式失败 + `_inject_dependency_context` helper）
  - 修改 [backend/app/config/settings.py](../../../backend/app/config/settings.py)（新增 `agent_team_max_replans: int = 2`）
  - 修改 [backend/app/config/subagents.py](../../../backend/app/config/subagents.py)（启动时校验 team_subagents system_prompt 非空）
- **前端**：
  - 修改 `frontend/renderer/components/chat/TeamNodeCard.tsx`：`team_init.plan[].deps` 可选字段渲染（依赖箭头/缩进展示）
  - 不动 SSE 事件契约（`deps` 作为 plan item 的可选字段，前端旧版本忽略即可）
- **API**：无新增端点。
- **测试**：
  - 新增 `tests/python/unit/test_team_dag.py`：DAG 解析、拓扑排序、循环检测、分批 fan-out、结果注入、replan
  - 新增 `tests/python/unit/test_team_fallback.py`：未知 agent fallback、team_role 显式失败
- **依赖**：无新增依赖（Kahn 算法手写，不引入 networkx）。
- **文档**：更新 [AGENTS.md](../../../AGENTS.md) §9 Team 路径说明，补 DAG 依赖编排语义。
- **与现有 change 的关系**：
  - 本 change **显式 DEPENDS_ON** `2026-07-12-agent-team-architecture-cleanup`（Phase 2 架构清理）
  - 执行顺序：Phase 1（已落地）→ Phase 2 → **本 change** → Phase 3
  - D4（fallback）扩展 Phase 2 的 `_DEFAULT_CONFIG`，在 `_run_subtask_node` 内走 code runner 兜底
  - D5（显式失败）与 Phase 2 的 R3（`_inherit_workspace` 失败即返回失败）方向一致，互不冲突
  - D6（replan）的 `dispatch_batch` 复用 Phase 2 单一 `subtask` 节点（`Send("subtask", {task, config})`）
  - 本 change **不重复** Phase 1（稳定性）、Phase 2（架构清理）、Phase 3（UX）的任何工作
- **风险**：
  - DAG 编排改变了 Team 路径的核心控制流，需全量回归 Team 端到端
  - replan 可能导致 LLM 调用次数翻倍（max_replans=2 时最多 3 次 plan 调用），需评估 token 成本
  - `[after:]` 解析依赖正则，LLM 输出格式不稳定时需容错（详见 design.md）

## Rollback Plan

1. **代码层**：所有变更集中于单分支，回滚即 `git revert` 单个 merge commit；无数据迁移、无 schema 变更
2. **DAG 解析**：若 `[after:]` 正则匹配率低（LLM 不输出依赖标注），回退为「所有任务无依赖」= 现有并行 fan-out 行为
3. **分批 fan-out**：若 `check_next_level` 条件边回流引发 LangGraph 死锁，回退为一次性 fan-out（牺牲顺序语义，保并行）
4. **结果注入**：若 findings 拼接导致 input 过长触发 LLM context 限制，回退为「只传 summary 不传 full findings」（截断到 `agent_team_result_max_chars`）
5. **fallback**：若 code runner 兜底引发新异常（如 task.input 不适合 code 场景），回退为原 `_default_node` 失败行为
6. **显式失败**：若 team_role 配置校验过严阻塞启动，回退为 warn 不 error
7. **replan**：若 LLM 陷入「无限追加任务」循环，回退为 `max_replans=0` = 一次性 plan
8. **验证门禁**：合并前必须通过 `pytest tests/python/unit -m "not integration"` +
   Team 路径端到端冒烟（顺序任务 / 并行任务 / 混合任务 / replan 任务）

## Out of Scope

- Phase 1/2/3 已覆盖的工作（稳定性 / 架构清理 / UX）不重复
- 不新增 SSE 事件类型（`team_node_done` / `team_node_error` 属 Phase 3）
- 不做条件分支编排（DAG 是确定性依赖图，不支持 `if-else` 分支）
- 不做子任务失败后的自动重试（失败任务的结果进 errors，依赖任务可读取 errors 决定是否继续）
- 不做跨会话的 plan 持久化（plan 仅在单次 team 运行内有效）
