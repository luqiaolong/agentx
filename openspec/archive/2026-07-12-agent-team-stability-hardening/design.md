# Design: AgentTeam 稳定性硬化（Phase 1 — P0 直击痛点）

## Context

AgentTeam 模式上线后暴露一个被 trace `ee346b48ce5a41a7` 实证复现的硬故障：375 秒后 SSE 断流，
前端 `TeamNodeCard` 永久卡在 `running`。根因不在网络层，而在"配置失效 → 子任务无界 → 终态不可靠 → 恢复失败"
的级联失败链。本设计文档覆盖 Phase 1 的全部稳定性修复决策，不触及 Phase 2 架构清理与 Phase 3 UX 增强。

### 当前失败链全景

1. **配置层**：[backend/app/config/agents.py:109-111](../../../backend/app/config/agents.py#L109-L111) 声明
   `max_parallel=3` / `subtask_timeout=300`，[backend/app/config/settings.py:187-189](../../../backend/app/config/settings.py#L187-L189)
   镜像同一份。但在 `backend/app/team/` 全量 grep 这两个字段名，零处读取。
2. **调度层**：[backend/app/team/orchestrator.py:192-233](../../../backend/app/team/orchestrator.py#L192-L233) `_dispatch_node`
   用 LangGraph `Send` API 全量 fan-out，无 `asyncio.Semaphore`；
   [backend/app/team/scheduler.py:186-229](../../../backend/app/team/scheduler.py#L186-L229) `_run_subtask_stream`
   与 [scheduler.py:232-364](../../../backend/app/team/scheduler.py#L232-L364) `_run_team_role_subtask`
   无 `asyncio.wait_for`。单次 LLM 长生成可拖死整条 SSE。
3. **终态层**：[frontend/renderer/hooks/useChatStream.ts:459-481](../../../frontend/renderer/hooks/useChatStream.ts#L459-L481)
   `team_done` 显式不调用 `setSessionRunning(false)`，把收尾责任推给后续 `done`。
   [chat.ts:336-418](../../../frontend/renderer/lib/api/chat.ts#L336-L418) `tryRecoverResult` 恢复时只合成 `token`+`done`，
   不合成 `team_done`，`TeamNodeCard` 无法 finalize。
4. **恢复层**：[chat.ts:342-344](../../../frontend/renderer/lib/api/chat.ts#L342-L344) 120s 窗口 + 无退避，
   对 375s+ 团队运行必然不够；[chat.ts:192](../../../frontend/renderer/lib/api/chat.ts#L192) `INACTIVITY_TIMEOUT=90s`
   对合法长工具调用会误判。
5. **错误路径**：[orchestrator.py:620-623](../../../backend/app/team/orchestrator.py#L620-L623) `_aggregate_node`
   except 块发射 `team_done{status:error}` 后 `raise`，[coding_team/agent.py:88-91](../../../backend/app/scenarios/coding_team/agent.py#L88-L91)
   外层 catch 再发一次。前端收到两次 `team_done{status:error}`，状态机抖动。

## Goals / Non-Goals

**Goals:**

- 让 `subtask_timeout` 真正生效：每个子任务 runner 必须在配置超时（默认 300s）内完成或失败。
- 让 `max_parallel` 真正生效：同时执行的子任务数受信号量限制（默认 3）。
- 让 `team_done` 成为团队生命周期的真终态：后端紧跟 `done`，前端有 2s done watchdog 兜底。
- 让 `tryRecoverResult` 在断流恢复时合成 `team_done`（团队模式运行）。
- 拉长恢复窗口到 300s 并加指数退避；`INACTIVITY_TIMEOUT` 提升到 180s。
- 统一 `team_done` 错误路径 owner 为 `run_coding_team`，移除 `_aggregate_node` 的重复发射。

**Non-Goals:**

- **不做 Phase 2 架构清理**：不合并 5 个重复 node 函数、不删除 `_in_progress_tasks` 全局、
  不修复 `approval_runner` 异常吞咽、不迁移 `astream_events` v2→v3、不修 `_looks_like_dangerous_task` 词边界、
  不恢复 `_quality_gate` identical-findings 检查。
- **不做 Phase 3 UX 增强**：不新增 `team_node_done` / `team_node_error` 事件、不实时流式子代理输出、
  不加团队进度条、不支持单子代理取消、不整合 role→label 映射、不流式 planner token。
- **不改 SSE 事件契约**：不新增事件类型、不修改现有事件 schema（仅扩展 `team_done` 后必跟 `done` 的语义保证）。
- **不改配置 schema**：`max_parallel` / `subtask_timeout` 字段已存在，仅开始被读取。
- **不改审批流**：`write_file`/`edit_file` 仍走 `interrupt_before=["tools"]`（Phase 0 已稳定）。
- **不重构 `app/team/` 模块结构**：信号量、超时通过最小侵入式包装实现。
- **abort 响应优化（R4）推迟到 Phase 2**：不在 Phase 1 实现 `asyncio.wait_for(runner.__anext__(), timeout=1.0)` 的 1s abort 响应（与 Phase 2 D6 计划用 `asyncio.wait(..., FIRST_COMPLETED, timeout=5)` + 5s SLA 统一处理冲突）；Phase 1 仅保留 `async for event in stream` 循环中已有的 `abort_event.is_set()` 间隙检查点。

## Decisions

### Decision D1: 用 `asyncio.wait_for` 包裹 runner，而非改 LangGraph Send 语义

**问题**: `_dispatch_node` 用 LangGraph `Send` API 并行 fan-out，LangGraph 内部用 `asyncio.create_task`
调度每个子任务。要在 LangGraph 层限并发，要么改 Send 语义（不可行，框架不暴露并发限制 hook），
要么在每个子任务节点内部用信号量限流。要在 LangGraph 层加超时，要么改 graph 编译配置（不支持 per-node 超时），
要么在每个子任务节点内部用 `asyncio.wait_for`。

**选项**:
- A. 改 LangGraph 源码加 per-node 超时/并发 → 维护负担重，升级即丢
- B. 在 `_dispatch_node` 中把 `Send` 改为顺序派发 → 破坏并行语义，团队模式性能崩塌
- C. 在 per-agent 节点（`_deep_node` 等）内部包裹 `asyncio.wait_for(runner, timeout)` + `async with semaphore`
  → 最小侵入，保留 LangGraph 并行 fan-out

**选择**: C
- per-agent 节点入口处先 `async with _team_semaphore:` 获取配额，再调用 `_run_subtask_stream(...)` /
  `_run_team_role_subtask(...)`（传入 `subtask_timeout`）。
- `asyncio.wait_for(..., timeout=subtask_timeout)` 包裹位于 `_run_subtask_stream` / `_run_team_role_subtask`
  **内部**，包裹 `async for event in stream` 迭代循环（而非节点调用处），确保 Phase 2 节点统一重构不会丢失该保护。
- 超时 `TimeoutError` 捕获后返回 `TeamSubtaskResult(success=False, payload=f"子任务超时（{subtask_timeout}s）")`。
- 信号量在 `run_team_path` 入口按 `max_parallel` 创建一次，通过 `TeamState["team_semaphore"]` 传递
  （`TeamState` 是 `TypedDict`，可直接加字段；不引入 `contextvars` 避免 LangGraph 内部 task 传播问题）。

**理由**: 选项 C 不动框架、不破坏并行语义。信号量在节点层获取（控制 LangGraph `Send` fan-out 并行度），
超时在 `scheduler.py` 内部包裹（Phase 2 删除/合并节点函数时不丢失）。改动集中在 5 个 per-agent node 函数、
`run_team_path` 入口与 `scheduler.py` 内部。信号量 + 超时是 Python 标准库能力，零新增依赖。

**备选方案的弃用原因**:
- A 不可行：LangGraph 不在项目控制范围，且 0.6+ 版本可能改变内部 Send 调度实现。
- B 不可接受：团队模式 5 个子任务若顺序执行，最坏情况总时长 = sum(单任务时长)，比串行单 agent 更慢，无存在价值。

### Decision D2: 信号量通过 `TeamState` 字段传递，而非 `contextvars.ContextVar`

**问题**: 信号量是 asyncio 原语，需在 `run_team_path` 入口创建一次，然后在 5 个 per-agent 节点中共享。
LangGraph 用 `asyncio.create_task` 调度每个 `Send` 目标节点，`contextvars` 在 `create_task` 中会复制父任务上下文，
但复制的是**引用**，信号量本身是共享的。然而 `contextvars` 在 LangGraph 内部的 stateless graph + checkpoint
恢复路径中行为不确定。

**选项**:
- A. `contextvars.ContextVar("team_semaphore", default=None)` + 在 `run_team_path` 入口 `set()`
  → 依赖 LangGraph 内部 `create_task` 行为，checkpoint 恢复后可能丢失
- B. `TeamState["team_semaphore"]: asyncio.Semaphore | None` 字段
  → LangGraph state 直接传递，checkpoint 不会序列化 Semaphore（它是运行时对象，应排除在 checkpoint 外）
- C. 模块级全局 `_team_semaphore: asyncio.Semaphore | None = None` + 每次 `run_team_path` 重置
  → 多线程/多 trace 并发会串号（与现有 `_in_progress_tasks` 全局同病，属 Phase 2 清理范围）

**选择**: B
- 在 `TeamState` TypedDict 中新增 `team_semaphore: asyncio.Semaphore | None` 字段。
- LangGraph state 的 checkpoint 序列化需排除该字段（Semaphore 不可序列化）。
  现有 `TeamState` 已有 `subtask_runners` 等运行时对象字段（[orchestrator.py:229](../../../backend/app/team/orchestrator.py#L229)），
  采用相同的序列化排除策略。
- 若现有 checkpoint 序列化未排除运行时字段（即已有 bug），Phase 1 不修此 bug，仅在 design.md 记录，
  转入 Phase 2 一并清理（与 `_in_progress_tasks` 全局一并处理）。

**理由**: 选项 B 与现有 `subtask_runners` 字段处理方式一致，最小新增表面积。
选项 A 依赖 LangGraph 内部行为，不可靠。选项 C 引入全局可变状态，违反 Phase 1 "不引入新架构问题"原则。

### Decision D3: 超时分支发射 `delegation` 事件，而非引入 `team_node_error`

**问题**: 超时发生时需让前端 trace 可见。Phase 3 计划引入 `team_node_error` 事件作为 per-agent 错误信号，
但 Phase 1 不能做 Phase 3 工作。

**选项**:
- A. 仅依赖失败 `TeamSubtaskResult` 流入 aggregator，由 `team_done{status:error}` 兜底
  → trace 不可见，用户无法知道哪个子任务超时
- B. 引入 `team_node_error` 事件（提前做 Phase 3 的一部分）→ 违反 Phase 边界
- C. 超时分支发射 `delegation` 事件，`source="team"`，`data={"event":"timeout","agent":<name>,"timeout":<s>}`
  → 复用现有 `delegation` 事件（前端已支持渲染），不引入新事件类型

**选择**: C
- 超时分支在返回失败 `TeamSubtaskResult` 之前，发射 `delegation` 事件携带超时信息。
- 前端 `useChatStream` 已有 `delegation` 事件处理分支，会将其渲染为 trace 节点，无需前端改动。
- `delegation` 事件本用于"子代理委派链路追踪"，此处复用为"团队子任务执行追踪"，语义相近。

**备选方案 A 的弃用原因**: trace 不可见会导致用户与开发者无法定位"哪个子任务超时"，
诊断成本高，违背 Phase 1 "稳定性硬化"目标（稳定性不仅是防卡死，还包括可观测）。

**理由**: 选项 C 零新增事件类型，零前端改动，trace 可见。Phase 3 引入 `team_node_error` 后，
可平滑迁移超时分支到新事件，`delegation` 复用作为过渡方案无技术债。

### Decision D4: `run_team_path` 在 `team_done` 后紧跟发射 `done`

**问题**: 当前 `team_done` 与 `done` 分离，`team_done` 由 `run_team_path` 发射，`done` 由更上层
（`run_coding_team` 或 Router）发射。这导致 `done` 可能因 SSE 中断、上层异常等原因丢失，
前端 `TeamNodeCard` 永远卡在 `running`。

**选项**:
- A. 让 `team_done` 直接调用 `setSessionRunning(false)` → 改变 `team_done` 语义，与单 agent 路径
  `done` 事件语义不一致（单 agent 路径 `done` 才是终态）
- B. 让 `run_team_path` 在 `team_done` 后紧跟发射 `done` → 后端保证终态成对，前端仍等 `done` 收尾
- C. 前端 `team_done` 触发 done watchdog，2s 内 `done` 未到则强制收尾 → 前端兜底，后端不变

**选择**: B + C（双保险）
- B 是主方案：`run_team_path` 在发射 `team_done` 后，无论 `status` 是 `done` 还是 `error`，
  立即发射 `done` 事件（`data` 携带 `team_done` 的 `status` 摘要）。
  这让后端成为"终态成对"的唯一 owner，前端逻辑简化。
- C 是兜底：[useChatStream.ts:459-481](../../../frontend/renderer/hooks/useChatStream.ts#L459-L481) 在收到 `team_done` 时启动 2s watchdog，
  若 `done` 未到则强制 `setSessionRunning(false)`。应对后端 bug、SSE 中途断流等极端情况。

**理由**: B 让后端契约清晰（`team_done` 后必跟 `done`），C 提供前端最后防线。
单靠 B 无法应对 SSE 物理断流（`done` 在路上丢失），单靠 C 让后端契约模糊（`done` 可有可无）。
两者结合：B 是规则，C 是例外处理。

**备选方案 A 的弃用原因**: 改变 `team_done` 语义会让前端状态机出现"team_done 即终态"vs"done 即终态"
两套规则，单 agent 路径与团队路径行为分裂，未来 Phase 3 引入 `team_node_done` 时更难统一。

### Decision D5: `tryRecoverResult` 合成 `team_done` 的触发条件

**问题**: SSE 断流恢复时，`tryRecoverResult` 需判断是否为团队模式运行，以决定是否合成 `team_done`。
误判会导致非团队运行被合成 `team_done`，前端 `TeamNodeCard` 出现空 part。

**选项**:
- A. 始终合成 `team_done`，前端 `createIfMissing:false` 自动忽略 → 依赖前端容错
- B. 检查 `/api/observation/runs/{traceId}` 响应的 `agent_mode` 字段，仅团队模式才合成
- C. 检查前端消息中是否已存在 `team` part（由 `team_init` 创建），存在才合成

**选择**: B + C（双重校验）
- B 是主判据：`/api/observation/runs/{traceId}` 响应需携带 `agent_mode` 字段
  （若当前未携带，Phase 1 顺带补齐后端响应字段，零前端改动）。
  仅 `agent_mode === "team"` 时进入合成路径。
- C 是合成时的 `createIfMissing:false` 语义：即便 `agent_mode === "team"`，
  若前端消息中不存在 `team` part（运行可能在 `team_init` 之前降级），跳过合成 `team_done`，
  仅合成 `token` + `done`。

**理由**: B 防止非团队运行被误合成，C 防止团队运行但 `team_init` 未到达时被误创建空 part。
两者结合覆盖所有降级路径。

**备选方案 A 的弃用原因**: 依赖前端 `createIfMissing:false` 容错，等于把后端契约责任推给前端兜底，
违背"后端契约清晰"原则。且 `createIfMissing:false` 仅在 `upsertTeamNode` 内部生效，
若 `TeamNodeCard` 渲染逻辑有 bug 仍可能创建空 part，风险不可控。

### Decision D6: 恢复窗口指数退避的具体节奏

**问题**: [chat.ts:342-344](../../../frontend/renderer/lib/api/chat.ts#L342-L344) 当前 120s / 5s-3s 固定间隔。
拉长到 300s 后若仍固定间隔，最坏情况 300s/3s = 100 次轮询 `/api/observation/runs`，对已过载后端是灾难。

**选项**:
- A. 固定 5s/3s 间隔，仅拉长窗口到 300s → 最坏 100 次轮询
- B. 两段式：前 60s 用 5s/3s，60s 后切到 15s/10s → 最坏约 60/3 + 240/10 = 44 次轮询
- C. 三段式 + 上限 30s：前 60s 用 5s/3s，60-120s 用 10s/5s，120s 后用 30s/15s → 最坏约 20 + 12 + 6 = 38 次

**选择**: B（两段式）
- 前 60s：`/api/chat/result` 5s 间隔，`/api/observation/runs` 3s 间隔（保持现状，快速恢复优先）。
- 60s 后：分别提升到 15s / 10s（减少对过载后端的压力）。
- 不引入 30s 上限：300s 窗口内两段式已足够，30s 间隔会让用户等待感过强（最坏 30s 才检测到完成）。
- 每次轮询同时检查两个端点（已有逻辑，保持）；任一返回 `status === "completed"` 或 `status === "failed"` 即停止。

**理由**: 选项 B 在轮询次数（44 vs 38）与用户体验（最坏 15s 检测延迟）间取得平衡。
选项 C 的 30s 间隔在用户感知上是"卡住了"，违背恢复路径的"快速恢复"初衷。
选项 A 的 100 次轮询在团队模式高频场景下会压垮 observation 表。

**备选方案 A/C 的弃用原因**:
- A：轮询次数过多，加剧后端负载，与 Phase 1 "稳定性硬化"目标相悖。
- C：30s 间隔用户感知过差，且 38 vs 44 次轮询的边际收益不足以抵消用户体验损失。

### Decision D7: 统一 `team_done` 错误路径 owner 为 `run_coding_team`

**问题**: [orchestrator.py:620-623](../../../backend/app/team/orchestrator.py#L620-L623) `_aggregate_node` except 块
与 [coding_team/agent.py:88-91](../../../backend/app/scenarios/coding_team/agent.py#L88-L91) `run_coding_team` except 块
都会发射 `team_done{status:error}`，导致前端收到两次。

**选项**:
- A. 保留 `_aggregate_node` 的发射，移除 `run_coding_team` 的发射 → `run_coding_team` 仅 yield `error` 事件
- B. 移除 `_aggregate_node` 的发射，保留 `run_coding_team` 的发射 → `_aggregate_node` 仅 `raise`
- C. 两者都保留，前端去重 → 前端复杂度上升，且 `team_done` 之间可能夹其他事件，去重困难

**选择**: B
- `_aggregate_node` except 块仅 `logger.exception` + `raise`，不发射 `team_done`。
- `run_coding_team` 作为错误路径 `team_done{status:error}` 的唯一 owner（外层 try/except 覆盖最广）。
- `_aggregate_node` except 块加注释：`# team_done{status:error} 由 run_coding_team 统一发射`。
- 配合 D4：`run_coding_team` 在发射 `team_done{status:error}` 后也必须发射 `done` 事件。

**理由**: `run_coding_team` 是最外层包装，其 try/except 覆盖 `run_team_path` 的所有失败路径
（包括 `_aggregate_node` 之前的 `_plan_node` / `_dispatch_node` / per-agent node 失败）。
选项 A 会让 `_plan_node` 失败时无 `team_done`（因为 `_aggregate_node` 没被调用）。
选项 B 保证任何错误路径都有且仅有一个 `team_done{status:error}` + `done`。

**备选方案 A/C 的弃用原因**:
- A：覆盖面不足，`_aggregate_node` 无法捕获 plan/dispatch 阶段错误。
- C：前端去重增加复杂度，且 `team_done` 之间可能夹其他事件，去重困难。

## Detailed Design

### D1 详细设计：`asyncio.wait_for` + `asyncio.Semaphore` 包裹

**`run_team_path` 入口**（[orchestrator.py](../../../backend/app/team/orchestrator.py) `run_team_path` 函数）：

```python
async def run_team_path(message, thread_id, state, *, ...):
    settings = get_settings()
    team_cfg = _resolve_team_settings(settings)  # 读取 agents.teams.coding 或 settings.agent_team_*
    max_parallel = team_cfg.max_parallel  # 默认 3
    subtask_timeout = team_cfg.subtask_timeout  # 默认 300
    team_semaphore = asyncio.Semaphore(max_parallel)
    # 通过 TeamState 传递（D2）
    state["team_semaphore"] = team_semaphore
    state["subtask_timeout"] = subtask_timeout
    # ... 后续构建 graph 并 astream ...
```

**per-agent 节点**（如 `_deep_node`）——仅负责信号量获取，超时包裹在 scheduler 内部：

```python
async def _deep_node(state: TeamState) -> dict:
    semaphore: asyncio.Semaphore | None = state.get("team_semaphore")
    subtask_timeout: int = state.get("subtask_timeout", 300)
    runner = ...
    async with semaphore:  # 若 semaphore 为 None（降级），跳过限流
        result = await _run_subtask_stream(
            runner, ..., abort_event, writer,
            subtask_timeout=subtask_timeout,  # 传入超时，由 scheduler 内部包裹
        )
    return {"results": [result]}
```

**scheduler 内部超时包裹**（`_run_subtask_stream` / `_run_team_role_subtask`）——`asyncio.wait_for`
包裹 `async for event in stream` 迭代循环，而非节点调用处：

```python
# scheduler.py _run_subtask_stream 内部
async def _run_subtask_stream(runner, ..., abort_event, writer, *, subtask_timeout):
    stream = runner(*runner_args, **runner_kwargs)

    async def _iterate():
        async for event in stream:  # 实际迭代循环
            if abort_event.is_set():
                return TeamSubtaskResult(agent=agent_name, success=False, payload="用户中止")
            # 处理 event ...
        return final_result

    try:
        return await asyncio.wait_for(_iterate(), timeout=subtask_timeout)
    except asyncio.TimeoutError:
        writer(make_sse_event("delegation", {  # D3：复用 delegation 事件
            "source": "team",
            "event": "timeout",
            "agent": agent_name,
            "timeout": subtask_timeout,
        }))
        return TeamSubtaskResult(
            agent=agent_name,
            success=False,
            payload=f"子任务超时（{subtask_timeout}s）",
        )
```

> **注意**：timeout 包裹位于 scheduler.py 内部，确保 Phase 2 节点统一重构不会丢失该保护。
> Phase 2 计划删除/合并 5 个 per-agent node 函数（`_deep_node` / `_code_node` / `_team_role_node` /
> `_custom_node` / `_builtin_node`），若 `asyncio.wait_for` 写在节点层，重构后会丢失超时保护；
> 写在 `_run_subtask_stream` / `_run_team_role_subtask` 内部则不受节点重构影响。信号量获取仍保留在节点层
> （控制 LangGraph `Send` fan-out 的并行度）。

**降级路径**：若 `semaphore` 为 `None`（不应发生，`run_team_path` 总会设置；此处为防御性 None 检查），
跳过限流仅保留 `wait_for` 超时。`subtask_timeout` 同理，缺省用 300。

### D2 详细设计：`TeamState` 字段传递信号量

**`TeamState` TypedDict 新增字段**（[orchestrator.py](../../../backend/app/team/orchestrator.py)）：

```python
class TeamState(TypedDict, total=False):
    # ... 现有字段 ...
    team_semaphore: asyncio.Semaphore  # 运行时对象，不参与 checkpoint 序列化
    subtask_timeout: int
```

**checkpoint 序列化排除**：LangGraph state 的 checkpoint 序列化由 `StateGraph` 的 serde 处理。
现有 `subtask_runners` 字段（[orchestrator.py:229](../../../backend/app/team/orchestrator.py#L229)）已是运行时对象，
若现有代码未处理其序列化（即已有 bug），Phase 1 不修此 bug，仅记录。
`team_semaphore` 采用与 `subtask_runners` 相同的序列化排除策略（若存在）。

**`_dispatch_node` 传递**（[orchestrator.py:192-233](../../../backend/app/team/orchestrator.py#L192-L233)）：

```python
def _dispatch_node(state: TeamState) -> list[Send]:
    # ... 现有逻辑 ...
    sends.append(
        Send(
            node_name,
            {
                # ... 现有字段 ...
                "team_semaphore": state.get("team_semaphore"),  # 新增
                "subtask_timeout": state.get("subtask_timeout", 300),  # 新增
            },
        )
    )
```

### D3 详细设计：超时分支发射 `delegation` 事件

**`delegation` 事件 payload**：

```json
{
  "source": "team",
  "event": "timeout",
  "agent": "deep",
  "timeout": 300,
  "message": "子任务超时（300s）"
}
```

**前端处理**：`useChatStream` 现有 `delegation` 事件分支会将其渲染为 trace 节点（显示委派链路）。
`source: "team"` 让前端可区分团队超时与专家委派超时（若需差异化渲染，Phase 3 再做）。
Phase 1 前端无需改动，`delegation` 事件复用现有渲染逻辑。

### D4 详细设计：`run_team_path` 发射 `done`

**`run_team_path` 收尾**（[orchestrator.py:710](../../../backend/app/team/orchestrator.py#L710) 附近）：

```python
# 正常路径
writer(make_sse_event("team_done", {"status": "done", "agents": agent_summaries}))
writer(make_sse_event("done", {"team_status": "done"}))  # 新增：紧跟 done

# 错误路径（由 run_coding_team 负责，见 D7）
# run_team_path 内部异常不发射 team_done，让异常传播到 run_coding_team
```

**`run_coding_team` 错误路径**（[coding_team/agent.py:88-91](../../../backend/app/scenarios/coding_team/agent.py#L88-L91)）：

```python
except Exception as exc:
    logger.exception("coding_team failed", thread_id=thread_id)
    yield make_error_event(f"Team 执行失败: {exc}")
    yield make_sse_event("team_done", {"status": "error"})
    yield make_sse_event("done", {"team_status": "error"})  # 新增：紧跟 done
```

**前端 done watchdog**（[useChatStream.ts:459-481](../../../frontend/renderer/hooks/useChatStream.ts#L459-L481)）：

```typescript
case "team_done": {
  // ... 现有 upsertTeamNode + markReasoningDone + markRunningToolCallsComplete ...
  // 新增：done watchdog
  if (doneWatchdogRef.current) clearTimeout(doneWatchdogRef.current);
  doneWatchdogRef.current = setTimeout(() => {
    // 2s 内 done 未到达，强制收尾
    setSessionRunning(threadId, false);
    setStreaming(false);
  }, 2000);
  break;
}
case "done": {
  // done 到达，取消 watchdog
  if (doneWatchdogRef.current) {
    clearTimeout(doneWatchdogRef.current);
    doneWatchdogRef.current = null;
  }
  // ... 现有 done 处理逻辑 ...
  break;
}
```

### D5 详细设计：`tryRecoverResult` 合成 `team_done`

**[chat.ts:336-418](../../../frontend/renderer/lib/api/chat.ts#L336-L418) 修改**：

```typescript
async function tryRecoverResult(traceId, conn, receivedTextLength = 0) {
  const MAX_WAIT_MS = 300_000;  // B3：120s → 300s
  const start = Date.now();
  let lastResultPoll = 0;
  let lastObsPoll = 0;
  while (Date.now() - start < MAX_WAIT_MS) {
    // B3：指数退避
    const elapsed = Date.now() - start;
    const resultInterval = elapsed < 60_000 ? 5_000 : 15_000;
    const obsInterval = elapsed < 60_000 ? 3_000 : 10_000;
    await sleep(Math.min(resultInterval, obsInterval));
    if (conn.traceId !== traceId) return false;

    // 路径 2：/api/chat/result/{traceId}
    if (Date.now() - lastResultPoll >= resultInterval) {
      lastResultPoll = Date.now();
      const r = await fetch(`${API_BASE}/api/chat/result/${traceId}`);
      if (r.ok) {
        const data = await r.json();
        if (data.status === "completed" || data.status === "failed") {
          // D5：团队模式合成 team_done
          if (data.agent_mode === "team") {
            await synthesizeTeamDone(conn);  // createIfMissing:false 语义
          }
          // 合成 token + done（现有逻辑）
          synthesizeTokenAndDone(conn, data.result_text, data.token_count);
          return true;
        }
      }
    }

    // 路径 1：/api/observation/runs/{traceId}
    if (Date.now() - lastObsPoll >= obsInterval) {
      lastObsPoll = Date.now();
      const r = await fetch(`${API_BASE}/api/observation/runs/${traceId}`);
      if (r.ok) {
        const data = await r.json();
        if (data.run?.ended_at) {
          if (data.run.agent_mode === "team") {
            await synthesizeTeamDone(conn);
          }
          synthesizeTokenAndDone(conn, data.run.result_text, data.run.result_token_count);
          return true;
        }
      }
    }
  }
  return false;
}

async function synthesizeTeamDone(conn: ChatConnection) {
  // createIfMissing:false 语义：仅当消息中已存在 team part 才合成
  if (!hasTeamPart(conn.pendingIdRef.current)) return;
  conn.eventHandlers.forEach((h) =>
    h({ type: "team_done", data: { status: "done", agents: [] } } as unknown as ChatEvent),
  );
}
```

**`agent_mode` 字段补齐**：若 `/api/observation/runs/{traceId}` 响应未携带 `agent_mode`，
Phase 1 顺带在后端响应序列化中补齐（字段已存在于 `ObservationRun` 模型，仅需序列化暴露）。

### D6 详细设计：指数退避节奏

| 时间段 | `/api/chat/result` 间隔 | `/api/observation/runs` 间隔 | 累计轮询次数（上限） |
|---|---|---|---|
| 0-60s | 5s | 3s | 12 + 20 = 32 |
| 60-300s | 15s | 10s | 16 + 24 = 40 |
| 总计 | — | — | ≤ 72（最坏情况） |

实际轮询次数更少：任一端点返回终态即停止。最坏情况 72 次轮询分散在 300s 内，
平均 4.2s 一次，对后端压力可接受（且 60s 后间隔翻倍）。

### D7 详细设计：`_aggregate_node` 移除错误路径 `team_done`

**[orchestrator.py:620-623](../../../backend/app/team/orchestrator.py#L620-L623) 修改**：

```python
# 之前
except Exception as exc:  # noqa: BLE001 — C2: 异常时仍发射 team_done，并 re-raise 让 chat.py 也能感知错误
    logger.exception("team aggregate_node failed")
    writer(make_sse_event("team_done", {"status": "error", "error": str(exc)}))
    raise

# 之后
except Exception as exc:  # noqa: BLE001 — team_done{status:error} 由 run_coding_team 统一发射
    logger.exception("team aggregate_node failed")
    raise  # 不发射 team_done，让异常传播到 run_coding_team
```

**`run_coding_team` 保持现有错误路径**（[coding_team/agent.py:88-91](../../../backend/app/scenarios/coding_team/agent.py#L88-L91)），
但配合 D4 新增 `done` 事件发射。

## Risk & Mitigation

### Risk 1: 信号量在 LangGraph 内部 `create_task` 调度下行为异常

**风险**: LangGraph 用 `asyncio.create_task` 调度每个 `Send` 目标节点。`asyncio.Semaphore` 在 `create_task`
间是共享的（信号量本身是 asyncio 原语，不依赖 `contextvars`），但若 LangGraph 内部对超时/取消有特殊处理，
信号量可能未被释放（如任务被取消时 `async with semaphore` 的 `__aexit__` 是否执行）。

**缓解**: `async with` 语句保证即使任务被取消也会释放信号量（`__aexit__` 在 `CancelledError` 时仍执行）。
Phase 1 测试用例覆盖：取消一个子任务后，信号量配额是否恢复。

### Risk 2: `asyncio.wait_for` 在 runner 内部 fork 子任务时无法真正中止

**风险**: `_run_team_role_subtask` 内部可能调用 `astream_events` 或 `create_task` fork 子任务。
`asyncio.wait_for` 取消的是外层 coroutine，内部 `create_task` 创建的子任务不会被取消
（除非用 `TaskGroup` 或显式传递 `CancellationScope`）。

**缓解**: Phase 1 接受该限制：超时后外层返回失败 `TeamSubtaskResult`，但内部子任务可能仍在运行
（资源浪费但不影响正确性）。在 design.md 与代码注释中明确该限制，转入 Phase 2 用 `TaskGroup` 重构。

### Risk 3: done watchdog 与 `done` 事件竞态

**风险**: `done` 事件在 watchdog 2s 定时器触发瞬间到达，导致 `setSessionRunning(false)` 被调用两次。

**缓解**: `setSessionRunning` 应是幂等的（设为 `false` 两次无副作用）。watchdog 在 `done` 到达时
通过 `clearTimeout` 取消，竞态窗口极小（JS 事件循环单线程，`setTimeout` 回调与 SSE 事件处理不会真正并发）。
若仍担心，watchdog 回调内检查 `isStreaming` 状态，已停止则跳过。

### Risk 4: `tryRecoverResult` 合成 `team_done` 时 `team` part 已被用户手动清除

**风险**: 用户在 SSE 断流期间手动删除了消息中的 `team` part，`createIfMissing:false` 检查会跳过合成，
但 `token` + `done` 仍会合成，导致消息以非团队形态收尾。

**缓解**: 该场景属用户主动操作，非 Phase 1 修复范围。`createIfMissing:false` 已是正确行为
（不强制创建已不存在的 part）。文档化该行为：用户手动删除 team part 后，恢复路径以非团队形态收尾。

### Risk 5: 恢复窗口 300s 期间用户发新消息

**风险**: 用户在 300s 恢复窗口内发新消息，`tryRecoverResult` 应停止轮询旧 trace
（现有逻辑 [chat.ts:350](../../../frontend/renderer/lib/api/chat.ts#L350) `if (conn.traceId !== traceId) return false` 已处理）。

**缓解**: 现有逻辑已覆盖，Phase 1 不改该路径。仅需验证拉长窗口后该检查仍及时
（每次循环开始都检查，最长延迟 = 单次轮询间隔 = 15s）。

### Risk 6: `agent_mode` 字段缺失导致 `team_done` 合成失败

**风险**: 若 `/api/observation/runs/{traceId}` 响应未携带 `agent_mode`（后端未补齐），
`tryRecoverResult` 无法判断是否为团队模式，`team_done` 不会被合成。

**缓解**: Phase 1 顺带补齐后端 `agent_mode` 序列化（零前端改动，仅后端响应字段）。
若后端补齐延迟，退化为选项 A（始终合成 `team_done`，依赖前端 `createIfMissing:false` 兜底）。
