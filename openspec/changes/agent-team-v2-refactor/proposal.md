# Proposal: AgentTeam v2 全量重构

## Why

AgentTeam 路径自 2026-07-06 引入以来，经历了 deepagents 迁移、Phase 1 稳定性硬化，
但核心架构仍是「plan-once + parallel fan-out + aggregate-once」，且 `backend/app/team/orchestrator.py`
已膨胀至约 780 行单文件，承载 8 个节点 + 派发逻辑 + 图构建 + `run_team_path` 入口。

上一会话（2026-07-13）的深度分析识别出 **P0+P1+P2 共 13 项缺陷**，分布在正确性、可靠性、
可维护性三个层面。两个 prior change（`agent-team-dag-orchestration` 与
`2026-07-12-agent-team-architecture-cleanup`）尝试分别解决 DAG 编排（P0）与架构清理（Phase 2），
但二者高度耦合——DAG 编排依赖架构清理引入的统一节点 / 派发表，分两步落地会引入跨 change 硬冲突。
两个 prior change 至今均未落地。

本 change 合二为一，**直接推倒重来**：一次性完成模块拆分 + 13 项改动，删除旧代码。

### 当前架构的 5 个核心缺陷

#### 缺陷 1：无依赖编排能力，并行 fan-out 破坏顺序语义

[orchestrator.py:192-233](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L192-L233)
`_dispatch_node` 一次性把所有子任务并行发出，[planner.py:82-102](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L82-L102)
Orchestrator prompt 也无法表达「先 A 后 B」。

**实际危害**：用户问「读 src/main.py 并添加日志」，LLM 拆成 `[code] 读取 src/main.py` + `[deep] 修改 src/main.py`
→ 两个并行执行 → deep 任务在没有 code 读取结果的情况下盲改文件。「读→改」「检索→基于检索结果操作」
是软件开发团队的最常见协作模式，当前架构无法支持。

#### 缺陷 2：子任务之间无结果传递

子任务 input 只来自 Orchestrator 一次拆解（[planner.py:233](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L233) `input_text`），
无法引用其他子任务的中间产物。`rag` 检索到的文档无法传给 `deep` 去基于文档修改代码；
子任务之间是「信息孤岛」，Orchestrator 拆解时的天然依赖关系被并行 fan-out 强行抹平。

#### 缺陷 3：危险任务误判，安全降级失真

[planner.py:272-276](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L272-L276)
`_looks_like_dangerous_task` 用子串匹配，`"修改"` 会误命中「查看修改历史」、`"write"` 会误命中「read the writeup」，
导致非危险任务被强制改写到 `deep` agent，破坏任务路由准确性。

#### 缺陷 4：无并发限流，Send 全部并行

[orchestrator.py:192-233](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L192-L233)
`_dispatch_node` 通过 LangGraph Send API 把所有子任务并行发出，无全局并发上限。
当 LLM 拆出 10+ 子任务时，会同时启动 10+ 个 LLM 调用，触发上游限流、OOM、token 突刺。

#### 缺陷 5：Orchestrator prompt 无结构化输出

[planner.py:108-110](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L108-L110)
`_AGENT_PREFIX_RE` 用正则解析 `[agent:xxx]` 行，LLM 输出格式不稳定时（多余空格、嵌套括号、
Markdown 列表符号）解析失败率高达 ~15%，导致子任务丢失或 agent 名错误识别。

### 模块膨胀问题

[orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py) 单文件承载：

- 8 个节点函数（`_plan_node` / `_dispatch_node` / 6 个分类节点 / `_default_node` / `_aggregate_node`）
- 派发与路由逻辑（`_route_node_for_task`）
- StateGraph 构建（`_build_team_graph`）
- 入口流处理（`run_team_path`）
- abort 检查、状态更新 helper、SSE 事件发射

每次行为修复要改 5 处分类节点（漂移风险），删字段要查 3 个文件，异常被静默吞掉导致排查链路断裂。

## What Changes

本 change 共 **13 项改动（D1-D13）**，覆盖 P0+P1+P2 全部缺陷。

### D1. 模块拆分（11 文件，单一职责）

将 `orchestrator.py` ~780 行单文件拆为 11 个模块，每个文件单一职责：

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

关键文件位置：[backend/app/team/](file:///d:/java/agentprojects/agentx/backend/app/team/)

### D2. 结构化任务通信

[planner.py](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py) Orchestrator LLM 改用 `with_structured_output(TeamPlan)`，替代正则解析：

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

保留正则解析（[planner.py:108-110](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L108-L110) `_AGENT_PREFIX_RE`）作为 LLM 不支持结构化输出时的 fallback。

### D3. DAG 多波次派发（P0 核心）

[dispatcher.py](file:///d:/java/agentprojects/agentx/backend/app/team/dispatcher.py) 用 Kahn 算法
`resolve_waves(tasks) -> list[list[TeamTask]]`，每 wave 内并行 Send fan-out，wave 间串行通过
`barrier_node` 控制流转。图结构：

```
START -> plan -> dispatch -> [并行 execute_node via Send] -> barrier
       -> (有下一wave?) dispatch -> [并行 execute_node] -> barrier
       -> (无下一wave?) aggregate -> (质量门失败?) replan -> plan -> END
```

`barrier_node` 检查 `state.pending_waves`，有则回到 `dispatch`，无则进 `aggregate`。
替代 [orchestrator.py:192-233](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L192-L233) `_dispatch_node` 的一次性 fan-out。

### D4. 子任务结果传递（P0）

`SubtaskState` 新增 `upstream_findings: dict[str, Finding]`。
[dispatcher.py](file:///d:/java/agentprojects/agentx/backend/app/team/dispatcher.py) 生成 Send 时
根据 `task.depends_on` 从 `state.findings` 注入上游结果。子任务 runner 把 `upstream_findings`
拼入 agent context（`[依赖任务结果]` 段 + 当前任务 input）。

### D5. 并发限流（P1）

[scheduler.py](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py) 新增全局
`asyncio.Semaphore(settings.team_max_concurrency)`（默认 5）。每个子任务执行
`async with semaphore` 后再调用 runner。避免 10+ 子任务同时启动触发上游限流。

### D6. 危险任务 LLM 分类器（P0）

[classifier.py](file:///d:/java/agentprojects/agentx/backend/app/team/classifier.py) 的
`DangerousTaskClassifier`：LLM `with_structured_output(ClassificationResult)`。
输入 `task.description` + `agent` + 可用工具；输出 `is_dangerous` + `reason` + `suggested_agent`。
缓存按 `task.description` hash。LLM 不可用时降级到改进的关键词匹配（带词边界 `\b` 防止
「修改」误命中「查看修改历史」）。替代 [planner.py:272-276](file:///d:/java/agentprojects/agentx/backend/app/team/planner.py#L272-L276)
`_looks_like_dangerous_task` 的子串匹配。

### D7. 未知 Agent Fallback（P1）

`_default_node` 改名 `_fallback_node`（或并入统一 `execute_node` 的 default 配置分支）：
尝试用 `code` agent 执行，发射 `warning` SSE 事件，写入 `state.warnings`。
替代 [orchestrator.py:556-564](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L556-L564)
`_default_node` 的硬错误行为。

### D8. Abort 响应 LLM 长调用（P1）

[scheduler.py](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py) 把子任务 runner
包装为 `asyncio.Task`，注册到 `_running_tasks[thread_id]`。abort handler 调用 `task.cancel()`
中断 LLM 长调用。`CancelledError` 被捕获返回 `TeamSubtaskResult(success=False, payload="用户中止")`。
替代 [scheduler.py:311](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L311)
仅在事件边界检查的缓解方案。

### D9. 失败重试（P2）

[scheduler.py](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py) 新增
`max_retries=2`，指数退避 `1s/2s/4s`。仅重试瞬态失败（`TimeoutError`、网络异常），逻辑错误不重试。
记录 `retries` 字段到 `subtask_results`。

### D10. 迭代式拆解 / Replanner（P2）

质量门失败时 `replan_node` 把原任务 + findings + errors 重新喂给 planner。
`max_replan_attempts=1`（可配置）。发射 `replan` SSE 事件。图结构见 D3 的
`aggregate -> (质量门失败?) replan -> plan`。

### D11. 静默降级消除（P1）

[orchestrator.py](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py)
`_run_team_role_subtask` 配置缺失时发射 `warning` 事件 + 写入 `state.warnings`，不再静默 fallback。
替代 [scheduler.py:262-281](file:///d:/java/agentprojects/agentx/backend/app/team/scheduler.py#L262-L281)
静默降级到 coding Expert 的行为。配置层新增启动校验 `validate_team_subagents`：
所有 `enabled=True` 的 `team_subagents` 必须有非空 `system_prompt`，否则启动失败。

### D12. Token 透传一致性（P2）

所有 `execute` 节点统一 token 事件 schema：`{agent: str, content: str}`。
aggregator token 事件：`{agent: "aggregator", content: str}`。前端无需特殊处理。
消除当前 6 个分类节点 token 事件字段不一致问题。

### D13. Findings Key 区分（P2）

Key 格式改为 `{agent}:{task_id}:{wave_index}`（如 `code:t1:0`）。
`_make_subtask_state_update` 使用复合 key。同类型多任务不再互相覆盖。
替代当前 `{agent}-{task_index}` 格式（同类型多任务在多波次场景下会覆盖）。

## Dependencies

**自包含，无外部 DEPENDS_ON**。

本 change **supersedes** 两个 prior change：

- `agent-team-dag-orchestration`（DAG 依赖编排，未落地）
- `2026-07-12-agent-team-architecture-cleanup`（架构清理，未落地）

理由：两个 prior change 的工作高度耦合——DAG 编排依赖架构清理引入的统一 `execute_node` + 派发表，
分两步落地会增加跨 change 的硬冲突（DAG 编排要改的节点函数正是架构清理要删除的旧结构）。
本 change 合二为一，一次性完成模块拆分 + 13 项改动，删除旧代码，无需先落地任一 prior change。

## Capabilities

### New Capabilities

- `agent-team-v2`：AgentTeam v2 全量重构后的能力契约。涵盖 DAG 多波次派发、结构化任务通信、
  并发限流、危险任务 LLM 分类、未知 agent fallback、abort 响应 LLM 长调用、失败重试、
  迭代式 replan、静默降级消除、token 透传一致性、findings key 区分。详见
  [specs/agent-team-v2/spec.md](specs/agent-team-v2/spec.md)。

### Modified Capabilities

- `agent-team`（[archive/2026-07-06-agent-team/spec.md](file:///d:/java/agentprojects/agentx/openspec/changes/archive/2026-07-06-agent-team/specs/agent-team/spec.md)）：
  本 change 推倒重写 Team 路径的核心控制流。`TeamState` / `TeamPlanTask` schema 变更，
  `_dispatch_node` 改为 `dispatcher.resolve_waves` + 多波次 Send，6 个分类节点合并为单一
  `execute_node`，`_default_node` 改为 fallback 分支。项目约定无需向后兼容，直接删除旧代码。

## Impact

### 后端文件清单

**新增 11 文件**（[backend/app/team/](file:///d:/java/agentprojects/agentx/backend/app/team/)）：

- `backend/app/team/state.py`
- `backend/app/team/planner.py`（重写，从原文件提取并扩展 `with_structured_output`）
- `backend/app/team/dispatcher.py`
- `backend/app/team/nodes.py`
- `backend/app/team/graph_builder.py`
- `backend/app/team/runner.py`
- `backend/app/team/scheduler.py`（重写，新增 Semaphore + retry + abort cancel）
- `backend/app/team/aggregator.py`（重写，新增结构化 findings）
- `backend/app/team/blackboard.py`（重写，reducers 集中）
- `backend/app/team/classifier.py`
- `backend/app/team/__init__.py`（导出公共 API）

**删除 1 文件**：

- `backend/app/team/orchestrator.py`（约 780 行，职责拆分到上述 11 文件后删除）

**修改文件**：

- [backend/app/config/settings.py](file:///d:/java/agentprojects/agentx/backend/app/config/settings.py)：
  新增 `team_max_concurrency: int = 5`、`team_max_replan_attempts: int = 1`、`team_max_retries: int = 2` 配置
- [backend/app/config/subagents.py](file:///d:/java/agentprojects/agentx/backend/app/config/subagents.py)：
  新增 `validate_team_subagents(settings)` 启动校验函数
- [backend/app/main.py](file:///d:/java/agentprojects/agentx/backend/app/main.py)：
  lifespan 启动阶段调用 `validate_team_subagents(get_settings())`

### 前端

无改动。SSE 事件契约不新增类型，仅 token 事件 schema 统一为 `{agent, content}`（前端无需特殊处理）。

### 测试

新增：

- `tests/python/unit/test_team_v2.py`：模块拆分后的核心流程（plan / dispatch / execute / aggregate）
- `tests/python/unit/test_team_v2_dag.py`：DAG 解析、wave 派发、barrier 路由、upstream_findings 注入
- `tests/python/unit/test_team_v2_fallback.py`：未知 agent fallback、team_role 显式失败、启动校验
- `tests/python/unit/test_team_v2_concurrency.py`：Semaphore 限流、abort cancel、retry 策略
- `tests/python/unit/test_team_v2_classifier.py`：DangerousTaskClassifier LLM 路径 + 关键词降级
- `tests/python/unit/test_team_v2_replan.py`：replan_node、max_replan_attempts 限制、findings key 复合格式

测试运行命令：
- `uv run pytest tests/python/unit -m "not integration" -k "team"`
- `uv run ruff check backend/app/team/`

### 依赖

无新增外部依赖。Kahn 算法手写（不引入 networkx），`asyncio.Semaphore` / `asyncio.Task` 是标准库。

## Rollback Plan

1. **代码层**：所有变更集中于单分支，回滚即 `git revert` 单个 merge commit；无数据迁移、无 schema 变更
2. **模块拆分（D1）**：若拆分后引入循环依赖或导入错误，回退为单文件 `orchestrator.py`（保留 git 历史中的旧版本）
3. **结构化输出（D2）**：若 `with_structured_output` 在某些 LLM provider 上不支持，回退为正则解析 fallback（保留旧 `_AGENT_PREFIX_RE`）
4. **DAG 多波次（D3）**：若 `barrier_node` 条件边回流引发 LangGraph 死锁，回退为一次性 fan-out（牺牲顺序语义，保并行）
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
15. **验证门禁**：合并前必须通过 `uv run pytest tests/python/unit -m "not integration" -k "team"` +
    `uv run ruff check backend/app/team/` + Team 路径端到端冒烟（顺序任务 / 并行任务 / 混合任务 / replan 任务 / abort / retry）

## Out of Scope

- 不新增 SSE 事件类型（`team_node_done` / `team_node_error` 等留待未来 UX 增强）
- 不做条件分支编排（DAG 是确定性依赖图，不支持 `if-else` 分支）
- 不做跨会话的 plan 持久化（plan 仅在单次 team 运行内有效）
- 不做子任务失败后的自动扩 plan（失败任务的结果进 errors，依赖任务可读取 errors 决定是否继续）
- 不做前端渲染改动（token 事件 schema 统一后前端无需适配）
- 不引入灰度开关 / 版本兼容层 / `@Deprecated`（项目约定直接推倒重来）
