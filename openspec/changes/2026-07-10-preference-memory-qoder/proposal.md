# Proposal: 偏好记忆 qoder 化改造——结构化条目 + References 面板

## Why

当前 AgentX 偏好记忆系统已完成工作区/全局双层隔离（[2026-07-10 记忆隔离修复](../../../docs/superpowers/specs/2026-07-10-preference-memory-qoder-design.md)），
但条目仍为扁平 `key/content` 结构，缺乏可读标题、关键词标签和应用场景，导致：

### 现状问题

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 1 | **条目无结构化元数据** | [profile_store.py:90-99](../../../backend/app/memory/profile_store.py#L90-L99) ProfileEntry 只有 key/category/content/source | 用户在设置页看到的是 key + content 原文，无法快速扫视偏好概要；LLM 抽取结果也只是一段文字 |
| 2 | **工作区面板无偏好/记忆展示入口** | [WorkspacePanel.tsx:23](../../../frontend/renderer/components/workspace/WorkspacePanel.tsx#L23) Tab 类型只有 tasks/files/git | 用户在聊天时无法快速查看当前生效的偏好与工作区记忆，必须打开设置弹窗才能看到 |
| 3 | **LLM 抽取 prompt 不要求结构化输出** | [profile_extractor.py:33-38](../../../backend/app/memory/profile_extractor.py#L33-L38) 只抽取 key/category/content | 抽取结果质量参差不齐，缺少关键词和应用场景，注入 system prompt 时可读性差 |
| 4 | **system prompt 注入格式简陋** | [profile_store.py:579-583](../../../backend/app/memory/profile_store.py#L579-L583) 格式为 `- [category] content` | 无标题时 LLM 难以快速定位哪条偏好适用于当前对话 |

### qoder 方案可借鉴点

- **结构化条目**：每条记忆有 title / keywords / scenarios / content 四要素
- **References 面板**：在侧边展示当前生效的记忆卡片，含标题、关键词标签、应用场景
- **关键词驱动发现**：通过 keywords 标签快速扫视偏好概要

## What Changes

### 后端：数据模型扩展

- [profile_store.py:90-99](../../../backend/app/memory/profile_store.py#L90-L99) `ProfileEntry` 新增 `title: str | None`、`keywords: list[str]`、`scenarios: list[str]`
- [workspace/memory_store.py:90-98](../../../backend/app/workspace/memory_store.py#L90-L98) `MemoryEntry` 新增同名字段，frontmatter 序列化/解析支持
- [profile_store.py:538-584](../../../backend/app/memory/profile_store.py#L538-L584) `build_profile_prompt` 注入格式升级：有 title 时 `- [category] title：content`，无 title 退回旧格式
- [profile_extractor.py:19-38](../../../backend/app/memory/profile_extractor.py#L19-L38) LLM 抽取结构化输出增加 title/keywords/scenarios，prompt 增加要求

### 后端：API Schema 扩展

- [schemas.py:97-109](../../../backend/app/api/schemas.py#L97-L109) `ProfileEntryRequest` / `ProfileUpdateRequest` 新增字段
- [memory.py:169-262](../../../backend/app/api/memory.py#L169-L262) API 端点透传新字段到 store 层

### 前端：类型与 API 层

- [api-types.ts:306-321](../../../frontend/shared/api-types.ts#L306-L321) `ProfileEntry` / `ProfileEntryRequest` 类型扩展
- [http.ts:312-385](../../../frontend/renderer/lib/api/http.ts#L312-L385) `saveProfile` / `updateProfile` 透传新字段

### 前端：设置页编辑器升级

- [MemoryList.tsx](../../../frontend/renderer/components/settings/memory/MemoryList.tsx) 编辑器增加 title 输入框 + keywords/scenarios 标签输入组件（TagInput）
- 列表项展示从 `key + content` 改为 `title + keywords 标签 + content`
- `PreferenceManager` / `ProfileManager` / `ProjectMemoryManager` 适配新编辑器

### 前端：WorkspacePanel 新增偏好/记忆 tab

- [WorkspacePanel.tsx:23](../../../frontend/renderer/components/workspace/WorkspacePanel.tsx#L23) Tab 类型增加 `preference` / `memory`
- 新建 `MemoryReferencesPanel.tsx`：qoder 式卡片展示，内部管理「偏好」「记忆」子 tab
- 偏好 tab 展示 preference 类画像，记忆 tab 展示 project 类画像

## Capabilities

### Modified Capabilities

- `preference-memory`: 偏好记忆从扁平 key/content 升级为结构化条目（title/keywords/scenarios），支持 qoder 式卡片展示与 References 面板快速查看

## Impact

- **后端**：修改 5 个文件（profile_store.py、memory_store.py、profile_extractor.py、schemas.py、memory.py）
- **前端**：修改 5 个文件（api-types.ts、http.ts、MemoryList.tsx、PreferenceManager.tsx、ProfileManager.tsx、ProjectMemoryManager.tsx、WorkspacePanel.tsx），新增 1 个文件（MemoryReferencesPanel.tsx）
- **兼容性**：Pydantic 默认值保证旧数据无需迁移；frontmatter 缺失字段补默认值；API 新增字段均为可选
- **测试**：后端新增 title/keywords/scenarios 读写测试 + frontmatter 兼容测试；前端 typecheck + 组件测试
- **SSE 事件契约**：无变更（不涉及 SSE 事件）
- **system prompt 注入**：格式微调，有 title 时更可读，无 title 向后兼容

## Future Extensibility

- References 面板支持搜索/过滤 keywords
- 点击卡片可将记忆内容插入当前输入框作为引用
- Memory tab 支持展示 skill 文件作为参考
