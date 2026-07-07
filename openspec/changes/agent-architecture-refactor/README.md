# agent-architecture-refactor

Supervisor + Expert 场景化智能体架构重构：建立场景（Scenario）→ Supervisor / Expert / Subagent / AgentTeam 的分层体系，将 code 子代理升级为 coding Expert，AgentTeam 降级为场景子模式。

## 术语层次

本设计明确区分**外部展示术语**和**代码实现术语**：

| 层次 | 术语 | 说明 |
|---|---|---|
| 外部展示（用户/UI/API） | 场景：`work` / `coding` / ... | 用户可见的场景概念 |
| 外部展示 | 模式：`work` / `coding` / `coding_team` | `agent_mode` 枚举值 |
| 代码实现（类名/文件名） | `Supervisor` / `Expert` / `Subagent` / `AgentTeam` | 内部架构术语 |

**映射关系**：`work` 场景 → `Supervisor`；`coding` 场景 → `Expert`；`coding_team` 模式 → `AgentTeam`

**统称与实例**：
- `Expert` 是统称（类型），当前唯一实例是 `coding Expert`，未来扩展 `research Expert` / `trading Expert` 等
- `AgentTeam` 是统称（类型），当前唯一实例是 `coding team`，未来扩展 `research team` 等
- `Subagent` 是统称（类型），分为内置子代理（`rag` / `web`）和自定义子代理（用户配置）

## 核心架构

**Supervisor + Expert 架构**（代码实现术语）：
- **Supervisor** = work 场景的全能 agent，自带完整工具集 + `delegate_to_expert` 委派能力，是所有 Expert 的统一调度入口
- **Expert** = 每个非 work 场景绑定的唯一专家 agent（coding 为首个，未来 research/trading 等）

**场景（Scenario）** 作为顶层概念（外部展示术语）：
- `work` 场景：Supervisor（全能 agent）
- `coding` 场景：coding Expert + coding Agent Team（两种模式）
- 未来扩展：research、trading 等（每个场景新增唯一 Expert）

**三层执行体 + 场景级 Team**：
1. Supervisor（work 全能 agent）— 调度所有 Expert
2. Expert（场景绑定专家）— coding 为首个
3. Subagent（rag / web / custom）— 轻量工具代理
4. AgentTeam（场景级团队）— coding team 为首个（多 Expert 协作）

**模式枚举**（外部展示）：`agent_mode` 从 `"agent" / "agent_team"` 改为 `"work" / "coding" / "coding_team"`（推倒重来，无兼容映射）

## 关键决策

- Supervisor = 完整工具集 + `delegate_to_expert` 委派工具，LLM 自主决策自己执行还是委派 Expert
- Expert 基于 `build_deep_agent` 框架，复用审批流，每个场景绑定唯一 Expert
- AgentTeam 降级为场景子模式，不再独立顶层，也不可被 Supervisor/Expert 内部触发
- 删除 Router 四路径分类（CHAT/SINGLE_TOOL/DEEP_TASK/AgentTeam），改为场景直接分发
- @mention 仅 work 场景生效，作为消息级 Expert 委派覆盖
- 遵循 project_memory 硬约束，推倒重来，删除所有兼容逻辑
- 新增场景仅需新增 Expert，Supervisor 调度逻辑不变（开闭原则）
