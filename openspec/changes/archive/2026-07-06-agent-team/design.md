# Design: Agent Team 多代理协作

## Context

当前项目状态（截至 2026-07-06）：
- Router 三路径已通：[router/graph.py](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py) 分类后走路径 A/B/C
- 4 类专家可用：code / rag / web / custom，分别位于 [subagents/](file:///d:/java/agentprojects/agentx/backend/app/subagents)
- 所有子代理只暴露只读/安全工具，危险工具仅在 DeepAgent 中暴露
- SSE 事件契约已定义：[AGENTS.md §13](file:///d:/java/agentprojects/agentx/AGENTS.md)
- 前端输入区已有 ModelToggle、PermissionToggle、ContextUsage、Send：[ChatComposer.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ChatComposer.tsx)

## Goals / Non-Goals

**Goals:**
- 输入区左下角新增 `Agent` / `AgentTeam` 模式切换按钮，默认 `Agent`
- `Agent` 模式行为 100% 保持现状
- `AgentTeam` 模式实现：Orchestrator 拆任务 → 并行/串行调度多专家 → 共享黑板 → Aggregator 汇总
- Team 模式下仍遵守安全红线：危险操作走 DeepAgent 审批
- 新增 Team 专属 SSE 事件，前后端契约对齐
- 新增/修改端点有单测覆盖
- 前端 typecheck + 后端 pytest 通过

**Non-Goals:**
- 不改现有 Router 三路径核心分类逻辑
- 不在 Team 模式外暴露危险工具给子代理
- 不实现持久化 Team 计划到数据库（仅内存黑板，随请求结束丢弃）
- 不做子代理间双向消息循环 / 辩论机制（留 M2）
- 不做自定义 Team 编排 DSL（留 M2）

## Decisions

### D1. 模式状态管理：跨会话保留

**选择**：新增 `useAgentModeStore`（zustand），`mode: "agent" | "agent_team"`，跨会话持久化到 `localStorage`。

**理由**：模式切换是用户工作偏好，与 ModelToggle 一致；不是安全状态，无需像 PermissionToggle 那样会话级 reset。

### D2. 模式切换 UI 位置与样式

**选择**：放在 [ChatComposer.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ChatComposer.tsx) 底部 toolbar 左侧，workspace chip 左边。

**样式**：复刻 PermissionToggle，trigger 为 `[icon] 标签 [chevron]`，Popover 底部弹出，激活态 `agent_team` 用 brand 紫色。

### D3. API 契约扩展

**选择**：
- `shared/api-types.ts` 新增 `AgentMode = "agent" | "agent_team"`
- `ElectronAPI.chat.send` 的 `opts` 增加 `agentMode?: AgentMode`
- `preload/index.ts::streamChat` body 增加 `agent_mode` 字段，默认 `"agent"`
- 后端 `ChatRequest` 增加 `agent_mode: str = "agent"`
- `run_router` 增加 `agent_mode` 参数，在分类后判断走哪条分支

**理由**：最小侵入，向后兼容；不传 `agent_mode` 默认走老逻辑。

### D4. Team 执行架构

```
用户消息
   ↓
[Orchestrator LLM]  --prompt 要求输出 JSON 子任务计划-->
   ↓
plan = [{"agent": "code", "input": "...", "purpose": "..."}, ...]
   ↓
[Scheduler] 并行执行可并行子任务（默认全部并行，受 MAX_PARALLEL 限制）
   ↓
blackboard = {"code": result, "rag": result, ...}
   ↓
[Aggregator LLM] 读取 blackboard + 用户原消息 -> 最终回复
   ↓
yield token / reasoning / team_plan / team_progress / team_result SSE
```

**D4-1. Orchestrator 输出格式**

```json
{
  "plan": [
    {
      "agent": "code",
      "input": "请读取 src/main.py 并总结入口逻辑",
      "purpose": "获取项目入口结构"
    },
    {
      "agent": "rag",
      "input": "AgentX 的 Router 三路径设计",
      "purpose": "检索设计文档"
    }
  ],
  "reasoning": "需要同时查看代码和文档"
}
```

约束：
- `agent` 必须是可用内置/自定义专家名（`code` / `rag` / `web` / `custom-*`）
- 若计划含写/编辑/shell 类任务，标记为 `deep` agent，该子任务单独走 DeepAgent
- 最大子任务数 `MAX_TEAM_TASKS = 5`，超出截断

**D4-2. 调度策略**

**选择**：首轮 MVP 采用 **全部并行**（受 `MAX_PARALLEL = 3` 限制），下一批补齐。

理由：实现简单，能覆盖 80% 场景（读文件 + 检索 + 搜索可并行）；串行依赖留 M2。

**D4-3. 共享黑板（Blackboard）**

```python
class Blackboard:
    findings: dict[str, str]   # agent_name -> 该 agent 最终输出摘要
    errors: dict[str, str]     # agent_name -> 错误信息
    meta: dict[str, Any]       # 可选元数据
```

每个子任务完成后把结果摘要写入 `blackboard.findings[agent_name]`。Aggregator 阶段把 blackboard 序列化进 prompt。

**D4-4. 专家调用方式**

复用现有 `run_code_agent` / `run_rag_agent` / `run_web_agent` / `run_custom_agent` 的异步生成器，但**不直接 yield 它们的 token 事件**，而是：
1. 内部累积每个 agent 的输出文本
2. 完成后生成一条摘要（取前 N 字符 + 工具调用痕迹）
3. 向客户端发送 `team_progress` 事件：某 agent 完成
4. 把摘要写入黑板

这样前端不会收到子任务内部细碎 token，只感知团队级进度；避免 UI 混乱。

**D4-5. Aggregator**

```python
aggregator_prompt = f"""
你是团队汇总专家。以下是一群专家针对用户问题的协作结果：

用户问题：{user_message}

专家发现：
{blackboard_summary}

请综合以上信息，给出完整、准确的最终回答。如果专家结果有冲突，请说明并给出判断依据。
"""
```

Aggregator 用 `get_chat_model(streaming=True)`，直接 yield `token` / `reasoning` 事件。

### D5. Team 模式下的安全红线

**选择**：子任务只能调用只读/安全工具；任何写、编辑、shell 操作必须作为 `agent: "deep"` 子任务，由 `run_deep_path` 执行（含 `interrupt_before` 审批）。

实现：
- Orchestrator prompt 中明确禁止分配写操作给 `code` / `rag` / `web` / `custom`
- 子任务执行前校验：若子任务涉及危险工具关键词且 agent 不是 `deep`，强制替换为 `deep` 子任务
- `deep` 子任务复用 [paths/deep_path.py::run_deep_path](file:///d:/java/agentprojects/agentx/backend/app/paths/deep_path.py#L592)，完整继承审批与 sandbox 授权

### D6. 新增 SSE 事件

| event | data | 说明 |
|---|---|---|
| `team_plan` | `{"plan": [{agent, input, purpose}], "reasoning": "..."}` | Orchestrator 生成的团队计划 |
| `team_progress` | `{"agent": "code", "status": "running" / "done" / "error", "message": "..."}` | 某个专家开始/完成/失败 |
| `team_result` | `{"agent": "code", "summary": "..."}` | 某个专家完成后写入黑板的摘要 |

注意：最终 Aggregator 的 token 仍走现有 `token` / `reasoning` 事件，不新增类型。

### D7. 错误降级

| 场景 | 行为 |
|---|---|
| Orchestrator 输出非法 JSON | yield `error` 事件，中断 |
| 某个子任务失败 | 记录到 `blackboard.errors`，继续其他子任务；Aggregator prompt 中包含失败说明 |
| 全部子任务失败 | yield `error` 事件 |
| LLM 不可用 | yield `error` 事件 |

### D8. 配置项

`config.py` 新增：

```python
agent_team_max_tasks: int = Field(default=5, ge=1, le=10)
agent_team_max_parallel: int = Field(default=3, ge=1, le=5)
agent_team_result_max_chars: int = Field(default=2000, ge=500, le=8000)
agent_team_enabled: bool = True  # 总开关，可在设置页关闭
```

通过 `AGENTX_AGENT_TEAM_*` env 注入（Electron Main 后续扩展，本次先加后端读取）。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 并行调用多次 LLM，成本与延迟不可控 | 上限 `MAX_PARALLEL=3` / `MAX_TASKS=5`；提供总开关 |
| Orchestrator 拆任务幻觉 | 计划返回 UI，用户可见；非法 plan 降级 |
| 黑板摘要丢失细节 | `result_max_chars` 可调；未来支持原始结果引用 |
| 子任务事件不实时展示 | 设计如此，避免 UI 混乱；M2 可加入子任务展开面板 |
| Team 与 DeepAgent 审批交织 | deep 子任务复用现有审批流，不新建机制 |

## Migration Plan

1. **OpenSpec 文档**（本文件 + proposal.md + tasks.md + specs/spec.md）
2. **前端 Store + UI**：`agentMode.ts` + `ModeToggle.tsx` + `ChatComposer.tsx` 集成
3. **API 契约**：`shared/api-types.ts` + `preload/index.ts` + `ChatView.tsx`
4. **后端配置**：`config.py` 加 Team 配置项
5. **后端核心**：`team_path.py` + `graph.py` 分发 + `state.py` 扩展
6. **后端端点**：`main.py` `ChatRequest` 加 `agent_mode` 并透传
7. **SSE 事件**：前后端对齐 `team_plan` / `team_progress` / `team_result`
8. **测试**：后端单测 + 前端 typecheck

## Open Questions

| # | 问题 | 默认假设 |
|---|---|---|
| Q1 | 是否允许用户编辑 Orchestrator 生成的计划？ | M1 不允许，仅展示；M2 可增加「重新生成/跳过某任务」 |
| Q2 | 自定义子代理能否加入 Team？ | 可以，agent 名用 `custom-<key>` |
| Q3 | Team 模式是否使用 checkpointer 历史？ | 是，与 Agent 模式一样加载历史并截断 |
| Q4 | Team 模式下是否触发画像抽取？ | 是，Aggregator 最终回复后异步抽取（复用路径 C 逻辑） |
| Q5 | 是否暴露 Team 配置 UI？ | M1 仅后端 env 配置；M2 在前端设置页加 tab |
