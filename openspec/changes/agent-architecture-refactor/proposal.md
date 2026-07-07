# Proposal: 智能体架构重构 — Supervisor + Expert 场景化体系

## 术语层次

本设计明确区分**外部展示术语**和**代码实现术语**：

| 层次 | 术语 | 说明 |
|---|---|---|
| 外部展示（用户/UI/API） | 场景：`work` / `coding` / ... | 用户可见的场景概念，前端 UI 文案、`agent_mode` 枚举值 |
| 外部展示 | 模式：`work` / `coding` / `coding_team` | `agent_mode` 枚举值，场景 + 可选 team 模式 |
| 代码实现（类名/文件名/内部架构） | `Supervisor` | 对应 work 场景的 agent，全能调度者 |
| 代码实现 | `Expert`（统称） | 场景绑定专家 agent，当前实例：coding Expert；未来：research Expert 等 |
| 代码实现 | `Subagent`（统称） | 轻量工具代理，分内置子代理（rag/web）和自定义子代理 |
| 代码实现 | `AgentTeam`（统称） | 场景级团队，当前实例：coding team；未来：research team 等 |

**映射关系**：
- `work` 场景 → `Supervisor`（代码实现）
- `coding` 场景 → `Expert`（代码实现，coding expert）
- `coding_team` 模式 → `AgentTeam`（代码实现，coding team）

**统称与实例**：
- `Expert` 是统称（类型），当前唯一实例是 `coding Expert`（类名 `CodingExpert`），未来扩展 `research Expert` / `trading Expert` 等
- `AgentTeam` 是统称（类型），当前唯一实例是 `coding team`（类名 `CodingTeam`），未来扩展 `research team` 等
- `Subagent` 是统称（类型），分为内置子代理（`rag` / `web`）和自定义子代理（用户配置）
- `Supervisor` 当前仅 `work` 场景一个实例（类名 `WorkSupervisor`），无统称必要

外部文档（用户指南、API 文档）使用场景术语；技术设计文档（design.md、tasks.md、代码注释）使用代码实现术语。

## Why

当前 AgentX 的智能体架构存在三个核心问题：

1. **层级语义混乱**：`code` 子代理（只读工具）与 AgentTeam 的 `backend_dev` 角色（也处理代码）能力边界不清，"子代理"一词被同时用于指代轻量工具代理和团队协作专家。
2. **场景维度缺失**：当前 `agent_mode` 只有 `agent` / `agent_team` 两个平级模式，无法表达"work 场景用全能 agent、coding 场景有 agent 和 team 两种模式"这种场景化绑定关系。未来还要扩展 research、trading 等场景，现有架构无法支撑。
3. **全能 agent 能力不完整**：当前 `agent` 模式实际是 Router 分类 + 四路径分发（CHAT/SINGLE_TOOL/DEEP_TASK/AgentTeam），主智能体本身不具备完整工具集，写操作必须走 DEEP_TASK 路径，导致"全能 agent"名不副实。

本设计建立 **Supervisor + Expert 场景化架构**：

- **场景（Scenario）**作为顶层概念：work 场景绑定 supervisor（全能 agent），其他每个场景绑定唯一 expert（专家 agent），可选绑定场景级 agent team
- **Supervisor + Expert 架构**：work 场景的全能 agent 作为 supervisor，负责调度所有 expert（coding/research/... 专家）；每个非 work 场景绑定唯一 expert（专家），由 supervisor 委派或用户直接选择进入
- **三层执行体**：Supervisor（work 全能 agent）→ Expert（场景绑定专家）→ Subagent（rag/web 轻量工具）；Agent Team 作为场景的可选团队模式（多 expert 协作）
- supervisor 自带完整工具集（含写操作+审批流），同时具备 `delegate_to_expert` 委派能力，由 LLM 自主决策自己执行还是委派 expert
- coding 场景绑定唯一 expert（coding 专家），并提供 coding agent team 作为团队协作模式

## What Changes

### 架构层

- **BREAKING**: 引入场景（Scenario）维度，`agent_mode` 枚举从 `"agent" / "agent_team"` 改为 `"work" / "coding" / "coding_team"`
- **BREAKING**: 建立 `backend/app/agents/` 包，实现四层智能体：顶级 Agent（work）、专家 Agent（coding）、场景级 Agent Team（coding team）
- **BREAKING**: 移除 Router 的 CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam 四路径分类逻辑，改为场景+模式直接分发
- **BREAKING**: 移除 `code` 内置子代理，升级为 `coding` 专家智能体
- **BREAKING**: 删除 `backend/app/subagents/code_agent.py`
- **BREAKING**: 删除 `backend/app/router/classifier.py` 的四路径分类逻辑
- **BREAKING**: AgentTeam 从顶层模式降级为场景子模式（coding 场景的 `coding_team` 模式）

### 智能体层

- 新增 `backend/app/agents/supervisor/work_supervisor.py`：work 场景的 supervisor（全能 agent），基于 `create_react_agent`，自带完整工具集（fs 读写+cli+git+rag+web）+ `delegate_to_expert` 委派工具，写操作走 `interrupt_before` 审批流
- 新增 `backend/app/agents/expert/coding.py`：coding 场景的唯一 expert（专家 agent），基于 `build_deep_agent`，自带代码专用工具集，可调用 rag/web 子代理
- 新增 `backend/app/agents/team/coding_team.py`：coding 场景的 Agent Team 模式（多 expert 协作），复用现有 `app.team` 框架
- 子代理层（Subagent，统称）分两类：内置子代理（`rag` / `web`，移除 `code`）和自定义子代理（用户配置）

### 配置层

- 新增 `backend/app/config/agents.py`：场景化智能体配置体系，定义 `WorkAgentSettings` / `ExpertSettings` / `ScenarioTeamSettings`
- 新增 `AGENTX_AGENTS_CONFIG` 环境变量，统一管理场景化智能体配置
- 移除 `AGENTX_SUBAGENTS_CONFIG` 中的 `code` 配置

### API 层

- `POST /api/chat` 的 `agent_mode` 字段枚举改为 `"work" / "coding" / "coding_team"`
- 移除旧 `"agent"` / `"agent_team"` 枚举值（推倒重来，不做映射）

### 前端层

- 模式选择器按场景分组：`[Work]` | `[Coding Agent] [Coding Team]`
- 输入框 `@` 语法仅在 work 场景生效，支持 `@coding` / `@rag` / `@web` 委派
- SSE 事件 `source` 字段扩展 `"work"` / `"coding"` / `"coding_team"` 标识

## Capabilities

### New Capabilities

- `primary-agent`: Supervisor（work 场景全能 agent），自带完整工具集+委派 expert 能力
- `expert-agent`: Expert 体系，场景绑定的领域专家（coding 为首个实现）
- `expert-delegation`: Supervisor→Expert 委派机制，supervisor 通过工具调用委派 expert/子代理
- `scenario-team`: 场景级 Agent Team，作为场景的模式之一（coding team 为首个实现，多 expert 协作）

### Modified Capabilities

- `subagent-dispatch`: 子代理分发体系，内置子代理从 `code/rag/web` 精简为 `rag/web`，移除 code 相关关键词和路由
- `agent-mode`: 代理模式体系，改为场景化枚举 `work` / `coding` / `coding_team`
- `input-at-mention`: 输入框 `@` 语法，仅 work 场景生效，范围调整为 `@coding` / `@rag` / `@web`
- `sse-event-contract`: SSE 事件契约扩展 `source="work"` / `"coding"` / `"coding_team"` 标识

## Impact

- **后端**:
  - 新增 `backend/app/agents/` 包（supervisor / expert / team 三层）
  - 改造 `backend/app/router/graph.py` 为场景+模式直接分发
  - 删除 `backend/app/router/classifier.py` 四路径分类逻辑
  - 删除 `backend/app/subagents/code_agent.py`
  - 改造 `backend/app/team/` 为场景级 team（coding team 为首个）
  - 扩展 `backend/app/config/` 配置体系
- **前端**:
  - `shared/api-types.ts` 扩展 `AgentMode` 为 `"work" | "coding" | "coding_team"`
  - 模式选择器 UI 改为场景分组
  - 输入框 `@` 语法仅 work 场景生效
- **API**: `POST /api/chat` 的 `agent_mode` 字段枚举变更（无兼容映射）
- **配置**: 新增 `AGENTX_AGENTS_CONFIG`，移除 `AGENTX_SUBAGENTS_CONFIG` 中的 `code`
- **测试**: 删除 code 子代理测试，新增场景化智能体测试
- **文档**: AGENTS.md 架构章节全面更新

## Future Extensibility

本架构为未来场景扩展预留清晰路径。新增场景（如 research、trading）只需：

1. 在 `agent_mode` 枚举新增 `<scenario>` 和 `<scenario>_team`（team 可选）
2. 在 `backend/app/agents/expert/` 新增场景的唯一 expert（专家 agent）
3. 在 `backend/app/agents/team/` 新增场景 team（可选，多 expert 协作）
4. 在 `AGENTX_AGENTS_CONFIG` 新增场景配置
5. 前端模式选择器新增场景分组
6. supervisor（work agent）自动获得对新 expert 的委派能力（通过 `delegate_to_expert` 工具）

无需修改核心调度框架，符合开闭原则。Supervisor + Expert 架构让新场景的接入仅需新增 expert，supervisor 调度逻辑不变。
