# Design: 偏好记忆 qoder 化改造

## Context

### 历史背景

2026-07-10 完成了偏好记忆工作区/全局双层隔离修复：全局画像存 `data/config/profile.json`，工作区画像存 `<workspace>/.agentx/memory/*.md`（YAML frontmatter + Markdown content）。读操作合并工作区 + 全局（工作区优先），写操作通过 `workspace_path` 参数指定层级。并发安全通过 `asyncio.Lock` 保证。

但条目结构仍为扁平 `key/category/content/source`，缺乏结构化元数据。参考 qoder 的偏好记忆方案，需要升级为 title/keywords/scenarios/content 四要素结构。

### 当前数据流

```
用户对话
  ├─→ profile_extractor.py LLM 抽取 (key/category/content)
  ├─→ profile_store.py / memory_store.py 写入 (扁平结构)
  └─→ build_profile_prompt 注入 system prompt ("- [category] content")
```

### 目标数据流

```
用户对话
  ├─→ profile_extractor.py LLM 抽取 (key/title/category/keywords/scenarios/content)
  ├─→ profile_store.py / memory_store.py 写入 (结构化)
  ├─→ build_profile_prompt 注入 system prompt ("- [category] title：content")
  └─→ WorkspacePanel MemoryReferencesPanel 卡片展示 (title + keywords + scenarios)
```

## Goals / Non-Goals

**Goals:**
- ProfileEntry / MemoryEntry 新增 title/keywords/scenarios 字段，向后兼容
- LLM 抽取器产出结构化输出
- system prompt 注入格式升级，有 title 时更可读
- 设置页编辑器支持 title/keywords/scenarios 输入
- WorkspacePanel 新增「偏好」「记忆」两个顶层 tab，qoder 式卡片展示

**Non-Goals:**
- 不改动全局/工作区双层隔离机制
- 不改动并发锁机制
- 不新增 SSE 事件
- 不做 References 面板的搜索/过滤功能（后续增强）
- 不改 fact/custom 类画像的管理位置（仍在设置页「用户画像」tab）

## Design

### 数据模型

#### ProfileEntry（全局画像 profile.json）

新增字段（Pydantic 默认值保证旧数据兼容）：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `title` | `str \| None` | `None` | 可读标题；为空时前端兜底用 key 或 content 首句 |
| `keywords` | `list[str]` | `[]` | 关键词标签，如 `["视觉对齐", "目录图标"]` |
| `scenarios` | `list[str]` | `[]` | 应用场景，如 `["调整会话列表UI布局"]` |

#### MemoryEntry（工作区记忆 .agentx/memory/*.md）

frontmatter 扩展：

```yaml
---
key: workspace_tech
category: project
title: 工作区技术栈
keywords:
  - FastAPI
  - React
scenarios:
  - 新成员理解项目技术选型
source: manual
updated_at: "2026-07-10T16:00:00+00:00"
---
```

旧 `.md` 文件缺少新字段时，读取时自动补空字符串/空列表。

### LLM 抽取

`profile_extractor.py` 中的 `ProfileEntry` 结构体增加 `title/keywords/scenarios`，prompt 要求：
- 为每条记忆生成可读 title（不超过 20 字）
- 提取 3-5 个 keywords
- 列出 1-3 个 scenarios

### system prompt 注入

`build_profile_prompt` 输出格式升级：
- 有 title：`- [category] title：content`
- 无 title：`- [category] content`（向后兼容）

### 前端编辑器

MemoryList 编辑器在现有 key/content 基础上增加：
- title 输入框（单行，可选）
- keywords TagInput 组件（逗号/回车分隔，最多 5 个）
- scenarios TagInput 组件（最多 3 个）

列表项展示从 `key + content` 改为 `title + keywords 标签 + content`。

### WorkspacePanel References 面板

WorkspacePanel Tab 类型增加 `preference` / `memory`，对应内容渲染 `MemoryReferencesPanel` 组件。

`MemoryReferencesPanel` 接受 `category` prop（`"preference"` 或 `"project"`），根据 category 拉取对应类别的画像（全局 + 工作区），不再有内部子 tab：
- WorkspacePanel「偏好」tab → `<MemoryReferencesPanel category="preference" />`
- WorkspacePanel「记忆」tab → `<MemoryReferencesPanel category="project" />`

卡片样式参考 qoder：
- 顶部：图标 + title（为空兜底 key）
- 中部：keywords 标签组
- 内容：content 正文
- 场景：scenarios 列表
- 底部小字：scope（全局/工作区）+ source（手动/LLM 抽取）+ updated_at

### 兼容性策略

- **profile.json**：Pydantic `title=None`、`keywords=[]`、`scenarios=[]` 默认值，旧数据无需迁移
- **workspace .md**：frontmatter 读取时缺失字段补默认值，写回时按新格式序列化
- **API**：新增字段均为可选，旧客户端不传时不影响后端
- **前端**：旧条目 `title` 为空时，列表展示 key 或 content 首句兜底

## Risks

- 数据结构一次性扩展 3 个字段，需确保 frontmatter 解析与序列化稳定
- WorkspacePanel 新增两个 tab，需检查 tab 按钮布局是否溢出（当前已有 tasks/files/git 三个）
- LLM 抽取增加字段后，单次 token 消耗略有增加，但 `_MAX_LLM_EXTRACT_ENTRIES` 仍为 20，可控
