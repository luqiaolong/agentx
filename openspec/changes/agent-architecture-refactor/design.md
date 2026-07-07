# Design: 智能体架构重构 — Supervisor + Expert 场景化体系

## 术语映射

本设计文档使用**代码实现术语**（supervisor/expert/subagent/agent_team），与外部展示术语（场景 work/coding）的映射关系如下：

| 代码实现术语 | 外部展示术语 | 说明 |
|---|---|---|
| `Supervisor` | `work` 场景 | work 场景的全能 agent，调度所有 expert |
| `Expert`（统称） | `coding` 场景 | 场景绑定专家 agent；当前实例：coding Expert；未来：research Expert 等 |
| `AgentTeam`（统称） | `coding_team` 模式 | 场景级团队；当前实例：coding team；未来：research team 等 |
| `Subagent`（统称） | （内部，不直接暴露） | 轻量工具代理，分内置子代理（rag/web）和自定义子代理；被 supervisor/expert 调用 |

**统称与实例**：
- `Expert` 是统称（类型），当前唯一实例是 `coding Expert`（类名 `CodingExpert`），未来扩展 `research Expert` / `trading Expert` 等
- `AgentTeam` 是统称（类型），当前唯一实例是 `coding team`（类名 `CodingTeam`），未来扩展 `research team` 等
- `Subagent` 是统称（类型），分为内置子代理（`rag` / `web`）和自定义子代理（用户配置）
- `Supervisor` 当前仅 `work` 场景一个实例（类名 `WorkSupervisor`），无统称必要

代码实现：
- 类名：`WorkSupervisor` / `CodingExpert` / `CodingTeam`
- 文件路径：`agents/supervisor/` / `agents/expert/` / `agents/team/`
- 工具名：`delegate_to_expert` / `delegate_to_subagent`

外部展示：
- `agent_mode` 枚举：`work` / `coding` / `coding_team`
- 前端 UI：`[Work] | [Coding Agent] [Coding Team]`
- API 文档：使用场景术语

## Context

当前 AgentX 后端采用 Router 四路径架构（CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam），配合 `agent_mode`（`"agent"` / `"agent_team"`）进行模式切换。子代理层包含 `code` / `rag` / `web` 三个内置 ReAct 子代理，其中 `code` 子代理仅具备只读文件系统工具，写操作需通过 DEEP_TASK 路径进入 DeepAgent 审批流。AgentTeam 内部又定义了 `frontend_dev` / `backend_dev` 等软件开发角色，也处理代码相关任务但能力受限。

这种架构存在三个核心问题：

1. **场景维度缺失**：`agent` / `agent_team` 两个平级模式无法表达"work 场景用全能 agent、coding 场景有 agent 和 team 两种模式"的场景化绑定关系。未来扩展 research、trading 等场景无路径可走。
2. **全能 agent 名不副实**：`agent` 模式实际是 Router 分类 + 四路径分发，主智能体本身不具备完整工具集，写操作必须走 DEEP_TASK，"全能"只是路由器而非执行者。
3. **层级语义混乱**：`code` 子代理（只读）与 AgentTeam 的 `backend_dev`（也处理代码）能力边界不清，"子代理"一词被同时用于轻量工具代理和团队协作专家。

本设计建立 **Supervisor + Expert 场景化架构**：

- **场景（Scenario）**作为顶层概念：work 场景绑定 supervisor（全能 agent），其他每个场景绑定唯一 expert（专家 agent）
- **Supervisor + Expert 架构**：work 场景的全能 agent 作为 supervisor，负责调度所有 expert；每个非 work 场景绑定唯一 expert，由 supervisor 委派或用户直接选择进入
- **三层执行体 + 场景级 Team**：Supervisor（work 全能 agent）→ Expert（场景绑定专家）→ Subagent（rag/web 轻量工具）；Agent Team 作为场景的可选团队模式（多 expert 协作）
- supervisor 自带完整工具集（含写操作+审批流），同时具备 `delegate_to_expert` 委派能力，由 LLM 自主决策自己执行还是委派 expert

## Goals / Non-Goals

**Goals:**
- 引入场景（Scenario）维度，work 场景绑定 supervisor，其他每个场景绑定唯一 expert，可选绑定场景级 agent team
- supervisor（work 全能 agent）自带完整工具集（含写操作+审批流），同时具备 `delegate_to_expert` 委派能力，由 LLM 自主决策
- coding 场景绑定唯一 expert（coding 专家），并提供 coding agent team 作为团队协作模式
- 建立 Supervisor + Expert 清晰分层：Supervisor → Expert → Subagent；Agent Team 作为场景的可选团队模式
- 子代理层精简为 `rag` / `web` / `custom`，语义统一为"单一工具封装"
- 删除 Router 四路径分类，改为场景+模式直接分发
- 为未来场景扩展（research、trading）预留清晰路径：新增场景仅需新增 expert

**Non-Goals:**
- 不重构 AgentTeam 内部角色体系（`frontend_dev` / `backend_dev` 等保持不变，仅改变入口为场景子模式）
- 不新增除 coding 之外的其他 expert（为未来扩展预留接口即可）
- 不修改沙箱安全模型和审批流核心机制（复用现有 `interrupt_before`）
- 不修改前端消息渲染的底层逻辑（仅扩展模式选择器、@语法、source 标识）
- 不做向后兼容（遵循项目硬约束，推倒重来，删除旧 `agent` / `agent_team` 模式、删除 `code` 子代理、删除 DEEP_TASK 路径）

## Decisions

### Decision 1: 场景化 `agent_mode` 枚举

**选择**: `agent_mode` 枚举改为 `"work" / "coding" / "coding_team"`，单一字段表达"场景+模式"。

**理由**:
- 单字段保持 API 简单，前端枚举清晰
- `<scenario>` 表示该场景的 agent 模式，`<scenario>_team` 表示该场景的 team 模式
- 新增场景只需加枚举值（如未来 `research` / `research_team`），符合开闭原则
- 用户场景描述明确：work 场景 = 全能 agent；coding 场景 = coding agent 或 coding agent team

**替代方案**:
- 二维结构 `{scenario, mode}` → 拒绝，API 改动大，前端处理复杂，且 mode 在 work 场景无意义
- 保留 `agent` / `agent_team` 平级 + 新增 `coding` → 拒绝，无法表达 coding 场景的 team 归属

### Decision 2: Supervisor = 完整工具集 + 委派 Expert 工具

**选择**: work 场景的 supervisor 基于 `create_react_agent` 构建，自带完整工具集（fs 读写 + cli + git + rag + web），同时具备 `delegate_to_expert` 工具委派各场景 expert（coding/research/...）。写操作走 `interrupt_before` 审批流。LLM 自主决策自己执行还是委派 expert。

**理由**:
- 符合 Supervisor + Expert 架构语义：supervisor 自己能干活，也能委派 expert
- `create_react_agent` 是 LangGraph 成熟框架，自带 ReAct 循环和 ToolNode 调度
- 完整工具集 + 委派工具的组合让 LLM 根据任务性质自主选择：通用任务自己干，领域专业任务委派 expert
- 写操作走审批流保证安全，与 DEEP_TASK 原有机制一致但不再需要独立路径
- supervisor 是所有 expert 的统一调度入口，新场景接入仅需新增 expert，supervisor 调度逻辑不变

**替代方案**:
- 纯调度者（只委派不自己执行）→ 拒绝，违反"全能 agent"语义，简单任务也要走委派增加延迟
- 纯执行者（只自己干不委派）→ 拒绝，无法利用 expert 的领域专用 prompt 和工具集优势

### Decision 3: Expert 基于 `build_deep_agent` 框架

**选择**: 各场景的 expert（如 coding 专家）基于 `build_deep_agent` / `run_deep_path` 构建，替换 system prompt 为领域专用，工具集扩展为完整领域工具（fs 读写 + cli + git + rag + web）。

**理由**:
- DeepAgent 已具备完整的 `interrupt_before` 审批流、checkpoint 管理、暂停/恢复机制
- 不重复造轮子（AGENTS.md §1.1 优先使用成熟框架）
- expert 与 DeepAgent 的差异仅在于角色定位（通用 → 领域专用），执行框架相同
- expert 可调用 rag/web 子代理（通过 `delegate_to_subagent` 工具）
- 每个场景绑定唯一 expert，职责清晰，避免多专家能力重叠

**替代方案**: 为 expert 独立构建执行框架 → 拒绝，重复代码且审批流容易出错

### Decision 4: AgentTeam 降级为场景子模式

**选择**: AgentTeam 不再是顶层模式，而是场景的子模式。coding 场景提供 `coding_team` 模式，复用现有 `app.team` 框架。work 场景无 team 模式。

**理由**:
- 符合用户场景描述：coding 场景有 coding agent 和 coding agent team 两种模式
- 避免 AgentTeam 既是顶层模式、又被 work agent 和 coding 专家内部触发的入口重叠问题
- 场景级 team 让团队角色定义更聚焦（coding team 专注代码开发角色，未来 research team 专注研究角色）
- 用户通过模式切换选择 team，而非 agent 内部触发，语义清晰

**替代方案**:
- 保留 AgentTeam 为顶层模式 → 拒绝，与用户场景描述冲突，且导致入口重叠
- work agent 内部可触发 AgentTeam → 拒绝，违反"work 场景无 team"的场景划分

### Decision 5: 删除 Router 四路径分类，改为场景+模式直接分发

**选择**: 删除 `router/classifier.py` 的 CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam 四路径分类逻辑，`run_router()` 改为根据 `agent_mode` 直接分发到对应场景的 agent 入口。

**理由**:
- 四路径分类是为旧 `agent` 模式服务的，新架构每个场景的 agent 自主决策执行路径
- work 全能 agent 自带完整工具集，无需分类是否需要 DEEP_TASK（写操作自己走审批流）
- coding 专家和 coding team 各有明确入口，无需分类
- 删除分类逻辑减少一层间接性，降低路由错误率

**分发逻辑**:
```
agent_mode == "work"         → run_work_agent()
agent_mode == "coding"       → run_coding_expert()
agent_mode == "coding_team"  → run_coding_team()
```

**替代方案**: 保留 classifier 做"智能路由"→ 拒绝，违反场景化设计，且 LLM 自主决策已足够

### Decision 6: @mention 仅 work 场景生效，作为消息级专家委派覆盖

**选择**: 输入框 `@<agent-name>` 语法仅在 work 场景生效，支持 `@coding` / `@rag` / `@web`。在 work 场景下，@mention 强制委派到指定专家/子代理，覆盖 work agent 的自主决策。coding 和 coding_team 场景下 @mention 被剥离忽略。

**理由**:
- work 场景的全能 agent 支持 @mention 让用户显式控制委派目标，适合临时/单条消息的专家调用
- coding/coding_team 场景已经是领域专用入口，@mention 委派无意义
- @mention = 消息级覆盖，模式切换 = 会话级策略，两者互补
- 不支持 @team，因为 team 是场景模式，通过模式切换进入而非 @mention

**替代方案**: 所有场景都支持 @mention → 拒绝，coding 场景下 @coding 无意义，@rag/@web 应由 coding 专家自主决策

### Decision 7: 推倒重来，无兼容层

**选择**: 遵循 project_memory 硬约束，删除所有旧逻辑，不做兼容映射：
- 删除 `backend/app/subagents/code_agent.py`
- 删除 `router/classifier.py` 四路径分类逻辑
- 删除旧 `agent` / `agent_team` 模式枚举（无映射到新枚举）
- 删除 DEEP_TASK 路径（审批流机制保留到工具层，路径分发删除）
- 删除 `AGENTX_SUBAGENTS_CONFIG` 中的 `code` 配置
- 不保留 `@Deprecated` 标记、不写灰度开关、不写兼容 shim

**理由**:
- project_memory 明确禁止灰度开关、兼容层、`@Deprecated`、向后兼容 shim
- 开发阶段推倒重来，删除旧代码，降低维护复杂度
- 新架构语义变化大，兼容映射会导致语义混乱

**替代方案**: 保留 `agent` → `work` 映射 → 拒绝，违反硬约束

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| [Risk] 删除 DEEP_TASK 路径后，原依赖该路径的写操作审批流行为变化 | [Mitigation] work 全能 agent 和 coding 专家自带 `interrupt_before` 审批流，写操作审批机制保留到工具层，用户感知行为不变（仍需审批） |
| [Risk] supervisor 自主决策路由不稳定（如把 coding 任务自己干了而非委派） | [Mitigation] supervisor 的 system prompt 明确各 expert 职责边界，`delegate_to_expert` 工具描述清晰；LLM 语义路由准确率可控；用户可通过 @coding 强制委派 |
| [Risk] 删除 `agent` / `agent_team` 模式导致旧客户端请求失败 | [Mitigation] 开发阶段推倒重来，前后端同步重构，无外部客户端依赖；API 返回明确错误提示新枚举值 |
| [Risk] coding 专家和 coding_team 模式入口重叠（用户困惑选哪个） | [Mitigation] 前端 UI 明确分组提示：Coding Agent（单人专家）/ Coding Team（团队协作）；模式描述说明适用场景 |
| [Risk] 前端模式选择器从二模式变三模式，UI 空间紧张 | [Mitigation] 按场景分组布局 `[Work] | [Coding Agent] [Coding Team]`，紧凑按钮组；`coding_team_enabled=false` 时隐藏 team 按钮 |
| [Risk] work 全能 agent 工具集过大导致 LLM 工具选择准确率下降 | [Mitigation] 工具按类别组织（fs/cli/git/rag/web/delegate），system prompt 明确各类工具使用场景；可考虑工具分组加载策略 |

## Migration Plan

### Phase 1: 配置体系和 agents 包骨架
- 创建 `backend/app/config/agents.py`，定义 `SupervisorSettings` / `ExpertSettings` / `ScenarioTeamSettings`
- 创建 `backend/app/agents/` 包骨架（supervisor / expert / team 三层目录 + `__init__.py`）
- 创建 `backend/app/config/prompts/agent.py`，定义 supervisor 和 coding expert 的 system prompt

### Phase 2: 实现 supervisor（work 全能 agent）
- 实现 `backend/app/agents/supervisor/work_supervisor.py`，基于 `create_react_agent`
- 集成完整工具集（fs 读写 + cli + git + rag + web）
- 实现 `delegate_to_expert` 工具（委派 coding expert）
- 实现 `delegate_to_subagent` 工具（委派 rag/web）
- 写操作接入 `interrupt_before` 审批流
- 实现 @mention 解析逻辑（`@coding` / `@rag` / `@web`）

### Phase 3: 实现 coding expert（专家 agent）
- 实现 `backend/app/agents/expert/coding.py`，基于 `build_deep_agent`
- 集成代码专用工具集（fs 读写 + cli + git + rag + web）
- 实现 `delegate_to_subagent` 工具（coding expert 内部调用 rag/web）
- 写操作接入 `interrupt_before` 审批流

### Phase 4: 改造 coding team 为场景级
- 改造 `backend/app/agents/team/coding_team.py`，复用现有 `app.team` 框架
- 调整 `app/team/orchestrator.py` 和 `app/team/scheduler.py`，移除对旧 `agent_team` 模式的依赖
- 移除 team 内部对 `code` 子代理的引用，映射到 coding expert

### Phase 5: 改造 Router 为场景分发
- 改造 `backend/app/router/graph.py` 的 `run_router()`，改为场景+模式直接分发
- 删除 `backend/app/router/classifier.py` 四路径分类逻辑
- 更新 `backend/app/router/state.py`，`RouterState` 添加 `agent_mode` 字段

### Phase 6: 删除旧逻辑
- 删除 `backend/app/subagents/code_agent.py`
- 从 `backend/app/subagents/__init__.py` 和 `dispatch.py` 移除 code 相关代码
- 从 `backend/app/config/subagents.py` 移除 code 配置，`BUILTIN_SUBAGENT_KEYS` 改为 `{"rag", "web"}`
- 从 `backend/app/config/prompts/builtin.py` 移除 code 相关 prompt
- 删除 `backend/app/deep/` 中 DEEP_TASK 路径分发逻辑（保留 `build_deep_agent` 框架供 coding expert 复用）

### Phase 7: API 和前端改造
- 扩展 `backend/app/api/schemas.py` 的 `ChatRequest.agent_mode`，枚举改为 `"work" / "coding" / "coding_team"`
- 更新 `backend/app/api/chat.py` 透传新 `agent_mode` 值
- 前端 `shared/api-types.ts` 扩展 `AgentMode` 类型
- 前端模式选择器改为场景分组 `[Work] | [Coding Agent] [Coding Team]`
- 前端输入框 @mention 自动补全和高亮（仅 work 场景生效）
- 前端 SSE 事件处理支持新 source 标识

### Phase 8: 测试和文档
- 删除 code 子代理相关测试
- 新增 supervisor / coding expert / coding team 的单元测试和集成测试
- 更新 AGENTS.md 架构章节
- 更新 `.env.example` 添加 `AGENTX_AGENTS_CONFIG`

## Open Questions

（已在 review 阶段全部解决，记录如下）

1. **supervisor 是否禁止自己执行代码写操作？** → 不禁止。supervisor 自带完整工具集，可以自己执行写操作（走审批流），也可以委派 coding expert，LLM 自主决策。这正是 Supervisor + Expert 架构的灵活性体现。
2. **coding expert 内部调用 rag/web 子代理时，SSE 事件的 `source` 字段标记？** → 标记为子代理自己的标识（`"rag"` / `"web"`），保持事件可观测性，前端可渲染嵌套执行轨迹。
3. **未来新增 expert（如 research）时，前端是否硬编码 UI？** → 当前版本硬编码 work/coding/coding_team，未来通过后端动态返回可用场景列表，前端动态渲染（但不在本次重构范围内）。supervisor 的 `delegate_to_expert` 工具自动支持新 expert，无需修改 supervisor 逻辑。
