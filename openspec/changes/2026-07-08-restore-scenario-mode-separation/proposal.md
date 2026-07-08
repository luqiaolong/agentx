# Proposal: 恢复场景/模式双层 UI 交互

## Why

2026-07-08 的「[场景化智能体架构重构](file:///d:/java/agentprojects/agentx/openspec/changes/agent-architecture-refactor/proposal.md)」将 `agent_mode` 从 `{agent, agent_team}` 改造为 `{work, coding, coding_team}` 单字段，并把场景（work / coding）与模式合并到一个枚举里表达。重构同时也**移除了 App.tsx 左上角的场景切换 tab**，让用户在输入框的 ModeToggle popover 内通过分组标题（Work 场景 / Coding 场景）来选择。

该决策对后端模型是清晰的（Supervisor + Expert 架构里 work 场景绑定唯一 Supervisor、coding 场景绑定唯一 Expert + 可选 Team，场景与模式不是正交维度）。但**牺牲了 UI 的双层心智模型**：

- 用户感知的第一层是「我现在要用 work 还是 coding」——这是场景分类；
- 第二层才是「work 下只有 Work Supervisor，coding 下选 Coding Agent 还是 Coding Team」——这是模式选择。

把这两层合并到一个 popover 里、按"场景"做分组标题，导致：
1. 场景信息**失去了常驻可见性**（必须点开 popover 才能看到当前在哪个场景）。
2. 场景切换必须先打开 popover、再选分组下的选项，操作路径长。

本次变更**只动前端 UI 状态**与渲染，不修改后端 `agent_mode` 单字段契约：场景作为**纯 UI 状态**从 mode 派生，发送消息时仍向后端透传单字段。

## What Changes

### 前端层

- **新增 `frontend/renderer/stores/scene.ts`**：UI 状态的场景 store，字段 `scene: "work" | "coding"`，持久化到 `localStorage` (`agentx-scene` key)，提供 `setScene`。
- **场景从 mode 派生**：scene 不是独立可变的状态，**由 `agent_mode.mode` 派生**——`scene = mode === "work" ? "work" : "coding"`。`setScene` 实际触发对 `mode` 的联动修改。
- **App.tsx 顶部 title bar 加回场景 tab**：在 `Bot icon` + `AgentX` 字样 + `v0.1` badge 之后，插入 `[Work | Coding]` tablist（与重构前同款 `bg-brand-700 text-brand-200` 高亮样式）。
- **ModeToggle 改造为「agent 类型选择器」**：
  - 移除 popover 内的「Work 场景」「Coding 场景」分组标题；
  - 仅展示**当前场景下可选的 agent 类型**（work 场景：`[Work]`；coding 场景：`[Coding Agent] [Coding Team]`）；
  - trigger button 仍展示当前 agent 类型的 icon + 短名 + chevron。
- **联动规则**（顶部 tab 与 ModeToggle 双向同步）：
  - 用户切顶部 tab 到 work → 强制 `mode = "work"`；
  - 用户切顶部 tab 到 coding → 若当前 mode === "work" 则升级为 `"coding"`，若已是 `coding` / `coding_team` 保持；
  - 用户在 ModeToggle 选 Work → `mode = "work"` 且 scene 派生为 work；
  - 用户在 ModeToggle 选 Coding Agent / Coding Team → `mode = "coding"` / `"coding_team"` 且 scene 派生为 coding。
- **后端契约不变**：`POST /api/chat` 的 `agent_mode` 字段仍为 `work` / `coding` / `coding_team`，由前端透传。Router 分发、SSE `source` 字段、Supervisor/Expert/Team 实现零改动。

### 文档层

- 更新 `AGENTS.md` §9.5 与 §12：明确"场景（顶部 tab）与模式（输入框旁的 ModeToggle）为正交的两个 UI 维度；后端 `agent_mode` 仍为单字段，前端派生"。
- 更新 `claude.md` §13：保留单字段契约说明，前端双层结构作为 UI 概念补充。

### 不变更

- 后端 `backend/app/agents/`、`backend/app/router/graph.py`、`backend/app/api/schemas.py` 等零改动；
- `frontend/shared/api-types.ts::AgentMode` 枚举不变；
- ModeToggle 的 `coding_team_enabled=false` 时隐藏 Team 选项的逻辑保留（迁移到 ModeToggle 内部过滤）。

## Capabilities

### Modified Capabilities

- `agent-mode`：UI 层引入「场景」概念，场景从 mode 派生；后端 agent_mode 单字段契约不变。

## Impact

- **后端**：零改动。
- **前端**：
  - 新增 `frontend/renderer/stores/scene.ts`（约 30 行）。
  - 修改 `frontend/renderer/App.tsx`（新增顶部场景 tab 与联动 useEffect，约 40 行新增）。
  - 修改 `frontend/renderer/components/chat/ModeToggle.tsx`（移除场景分组、按当前 scene 过滤选项，约 20 行变更）。
  - 其他引用 `useAgentModeStore` 的地方（ChatComposer / ChatView）无需改动——mode 字段含义不变。
- **测试**：
  - 新增 `tests/renderer/stores/scene.test.ts`（scene 派生、联动规则）。
  - 更新 `tests/renderer/mode-toggle.test.tsx`（按 scene 过滤后的选项数量）。
- **文档**：AGENTS.md / claude.md。

## Why 不走 OpenSpec 「架构重构」分类

本次仅是 UX 体验的微调（恢复 UI 状态的双层表达），不是架构层面变更，因此归类为「Modified Capability: agent-mode」而非「New Capability」。后端 `agent_mode` 单字段契约、SSE 事件、Router 分发全部不变。

## Future Extensibility

如果将来要新增场景（如 `research` / `trading`）：
1. `AgentMode` 枚举新增 `<scenario>` 与 `<scenario>_team`；
2. `scene.ts` 类型扩展 `<scenario>` 联合；
3. 顶部 tab 新增按钮 + ModeToggle 内部按 scene 过滤逻辑扩展。

本方案在 UI 维度已为多场景预留扩展点，无需重构 store。