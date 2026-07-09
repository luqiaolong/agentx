# 前端主界面模块化命名规范

> 原 `AGENTS.md` §9.5 拆分。**强约束**：所有 AI 代理讨论 / 修改 / 评审前端 UI 时
> **必须**使用本规范术语。
>
> 本文件位于 `docs/agents/`（仓库文档，随 git 同步）。
> 开发者本地可按需把内容拷贝到 `.agentx/rules/01-frontend-naming.md`
> 让 deepagents memory= 自动加载到 agent 上下文（`.agentx/` 目录不进 git，
> 由 `workspace/config/generator.py` 动态生成）。

---

主界面采用**单窗口会话模式**：顶部自定义标题栏 + 左侧栏 + 中央主区域 + 右侧工作面板四列布局，
另设设置面板、浮层、日志弹窗三类瞬时或独立窗口。本节定义主界面的**视觉区域术语**、
**目录命名约定**、**组件 / 文件命名约定**，所有 AI 代理讨论前端 UI 时**必须使用**。

## §9.5.1 视觉区域术语表（强制）

主界面划分为 8 个标准视觉区域，**所有 AI 代理在讨论前端 UI 时必须使用下表中的中文术语**，
禁止使用"左边/右边/上面/下面/中间"等模糊描述。

| # | 中文术语 | 英文术语 | 位置 | 典型内容 | 备注 |
|---|---|---|---|---|---|
| 1 | **标题栏** | Title Bar | 顶部 40px | 品牌区、场景切换、窗口控制 | 自定义无边框窗口的标题栏，含场景 tab |
| 2 | **侧栏** | Side Bar | 左侧 240px | 会话列表 + 底部入口 | Home / 工作区分组 |
| 3 | **主区域** | Main Area | 中央 flex-1 | 消息流、输入区、任务进度、消息导航 | 也常被叫"聊天区"，但术语规范用"主区域" |
| 4 | **工作面板** | Work Panel | 右侧 288px | 任务 / 文件 / Git 三标签页 | 与 session.workspacePath 无关，仅是 UI 区域名 |
| 5 | **设置面板** | Settings Panel | 全屏浮层（z-50） | 10 个子域设置 tab | 仅打开时存在，左侧 tab 导航 + 右侧内容区 |
| 6 | **浮层** | Overlay | 全屏遮罩（z-40/50） | 审批弹窗、代码查看器、启动 / 失败遮罩 | 与设置面板并列，但属瞬时反馈层 |
| 7 | **日志弹窗** | Log Window | 独立 Tauri 窗口 | 日志控制台 + 工具栏 | `log-window.tsx` 入口，渲染层独立 |
| 8 | **通用 UI** | Common UI | 跨区域复用 | ErrorBoundary、ConfirmDialog、Modal / Popover hooks | 无业务语义，仅做交互与视觉基础 |

主区域（Main Area）内部进一步划分为 4 个**子区域**，同样必须用规范中文术语：

- **消息流**（Message Stream）— 历史消息列表
- **输入区**（Composer）— 文本输入框 + 底部工具栏
- **任务进度**（Task Progress）— TodoProgress 卡片
- **消息导航**（Message Navigator）— 消息流右侧分段导航条

## §9.5.2 顶层目录命名约定

`components/` 下的顶层目录名 = 视觉区域英文术语（小写、英文、单数），与 §9.5.1 一一对应：

```
components/
├── titlebar/        # 标题栏（场景切换、窗口控制）
├── sidebar/         # 侧栏（会话列表、底部入口）
├── main/            # 主区域（含 parts/ 消息片段）
├── workspace/       # 工作面板（任务/文件/Git 标签）
├── settings/        # 设置面板（按子域拆子目录）
├── overlay/         # 浮层（审批、代码查看、启动遮罩）
├── log/             # 日志弹窗
└── ui/              # 通用 UI（无业务语义）
```

**反例（禁止的目录名）**：

- `chat/` — 视觉上不存在"chat 区"；统一用 `main/`
- `code/` — 仅是浮层的一个组件；用 `overlay/CodeViewer.tsx`
- `Workspace` / `Main` / `Sidebar` — PascalCase 不能用作目录名
- 在 `components/` 根目录散落独立组件（`ErrorBoundary.tsx` / `StatusIndicator.tsx`）— 应入 `ui/`

## §9.5.3 组件命名约定

1. **视觉角色作前缀**：`SidebarHeader` 而非 `Header`，`WorkPanelTabs` 而非 `Tabs`
2. **业务对象作后缀**：`MessageItem`、`SessionGroup`、`ComposerToolbar`
3. **文件名 = 默认导出组件名**（PascalCase，一一对应）
4. **一文件一组件**：私有子组件可同文件但**不导出**；非平凡子组件 > 50 行 → 拆文件
5. **避免通用名**：禁止直接命名 `Header` / `Footer` / `Tabs` / `Modal` 等，必须带视觉区域前缀

## §9.5.4 消息片段命名（`main/parts/`）

`main/parts/` 是消息流的"片段渲染层"，命名规则：

- 使用 `<Role>Part.tsx` 形式：`TextPart`、`ToolCallPart`、`ReasoningPart`、`DelegationPart`、
  `ClassificationPart`、`TeamNodePart`
- 共享标题行用 `TraceCardHeader.tsx`（所有片段复用同一视觉规范）
- 禁止用 `<X>Card.tsx` 命名 — 卡片是视觉外观，不是角色；**角色名才是术语**

## §9.5.5 Store / Hook 命名补强

- **域 store**（zustand）：`useXxxStore`（`useChatStore` / `useTasksStore` / `useSettingsStore`），
  文件名同 store 名，存放域状态（消息、会话、任务等）
- **UI 临时态**（picker / popover / 模态）：仍用 `useXxxStore`，文件名以业务对象命名
  （`commands.ts` / `mention.ts`）
- **业务 hook**：`useXxx`，放 `hooks/`
- **UI 内部 hook**（a11y / 焦点陷阱 / 外部点击）：放 `components/ui/hooks/`

## §9.5.6 现状目录偏离清单（迁移参考，不强制）

| 现状目录 / 文件 | 偏离点 | 建议（未来 PR 渐进迁移） |
|---|---|---|
| `components/chat/` | `chat` 不是视觉区域术语 | 重命名为 `components/main/` |
| `components/code/` | `code` 是文件类型不是区域 | 合并到 `components/overlay/CodeViewer.tsx` |
| `components/ErrorBoundary.tsx`、`StatusIndicator.tsx` | 散落根目录 | 移入 `components/ui/` |
| `components/chat/parts/*Card.tsx` | 用 Card 命名片段 | 改用 `*Part.tsx` 命名 |
| `SettingsModal.tsx` 命名 | "弹窗"在术语表里改用"面板" | 文件名逐步重命名为 `SettingsPanel.tsx` |

## §9.5.7 反例术语表

禁止使用以下说法：

- "左边 / 右边 / 上面 / 下面 / 中间"（模糊，违反 §9.5.1）
- "聊天区 / 聊天窗口"（应改为"主区域"）
- "设置弹窗"（应改为"设置面板"，强调是工作区而非提示）
- "右侧工作区"（应改为"工作面板"，"工作区" 在 §10 已被 session.workspacePath 占用）
- 混合 `chat` / `main` 指代同一区域（必须统一为 `main`）
- 把视觉区域目录命名为业务后缀（`agents/` / `tasks/` / `files/` 等）

## §9.5.8 场景 vs agent 类型的派生关系

标题栏（Title Bar）中的**场景 tab** 与主区域输入区（Composer）旁的 **agent 类型选择器**
是两层独立的 UI 维度，必须分别命名：

- **场景**（UI 维度，取值 `work` / `coding`）：位于标题栏，Bot icon 与 `v0.1` badge 之间。
  点击 tab 触发联动修改 `agent_mode`：`work` 强制 mode=work；`coding` 保留原 mode，
  work→coding 升级为 `coding`。
- **agent 类型**（UI 维度）：位于主区域输入区（Composer）左下角 ModeToggle，
  仅展示当前场景下的选项：work 场景下为 `Work`；coding 场景下为 `Coding Agent` / `Coding Team`
  （`coding_team_enabled=false` 时隐藏 Team）。

后端 `agent_mode` 仍为单字段 `work` / `coding` / `coding_team`
（见 [shared/api-types.ts::AgentMode](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts#L89)），
后端契约零改动。
场景从 mode 派生：`scene = mode === "work" ? "work" : "coding"`
（见 [stores/scene.ts::getSceneFromMode](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/scene.ts)）。
场景 tab 写回 mode 的逻辑见
[stores/scene.ts::applySceneChange](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/scene.ts)。