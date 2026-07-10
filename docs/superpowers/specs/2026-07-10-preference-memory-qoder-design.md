# 偏好记忆 qoder 化改造设计文档

## 1. 目标

参考 qoder 的偏好记忆方案，将 AgentX 的偏好记忆从扁平 `key/content` 改造为结构化条目，并在聊天主区域右侧新增 **References** 面板，提供「偏好」与「记忆」两个平行 tab，提升记忆的可读性、可发现性与引用价值。

## 2. 范围

- 后端：`profile_store.py`、`workspace/memory_store.py`、API schema、LLM 抽取器。
- 前端：`lib/utils.ts` 类型、`MemoryList.tsx` 编辑器、新增 `ReferencesPanel.tsx`、集成到聊天主区域右侧。
- 不改动：fact/custom 类画像的管理位置（仍在设置页「用户画像」tab）；技能文件（SKILL.md）管理；全局/工作区双层隔离与并发锁机制。

## 3. 数据模型改造

### 3.1 ProfileEntry（全局画像 profile.json）

新增字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | `str \| None` | 否 | 可读标题；为空时前端从 `content` 自动提取首句/key 兜底 |
| `keywords` | `list[str]` | 否 | 关键词标签，如 `["视觉对齐", "目录图标"]` |
| `scenarios` | `list[str]` | 否 | 应用场景，如 `["调整会话列表UI布局", "评审UI设计稿"]` |

其余字段 `key/category/content/source/scope/created_at/updated_at` 保持不变。Pydantic 默认值保证旧数据兼容。

### 3.2 MemoryEntry（工作区记忆 .agentx/memory/*.md）

frontmatter 扩展：

```yaml
---
key: workspace_tech
category: project
title: 工作区技术栈
keywords:
  - FastAPI
  - React
  - Tauri
scenarios:
  - 新成员理解项目技术选型
  - 生成代码时匹配技术栈
source: manual
updated_at: "2026-07-10T16:00:00+00:00"
---
```

旧 `.md` 文件缺少新字段时，读取时自动补空字符串/空列表。

## 4. API 变更

### 4.1 Schema

`ProfileEntryRequest` 新增：

```python
title: str | None = None
keywords: list[str] = Field(default_factory=list)
scenarios: list[str] = Field(default_factory=list)
```

`ProfileUpdateRequest` 同步新增。

### 4.2 端点行为

- `POST /api/memory/profile`：写入时携带新字段。
- `PUT /api/memory/profile/{key}`：更新时支持修改新字段。
- `GET /api/memory/profile`：返回条目包含新字段。
- `POST /api/memory/profile/extract`：LLM 抽取结果包含新字段。

## 5. LLM 抽取改造

`profile_extractor.py` 中的 `ProfileEntry` 结构体增加 `title/keywords/scenarios`，prompt 增加要求：

- 为每条记忆生成一个可读 `title`（不超过 20 字）。
- 提取 `3-5` 个 `keywords`。
- 列出 `1-3` 个 `scenarios`（何时会用到这条记忆）。

示例抽取输出：

```json
{
  "entries": [
    {
      "key": "session_list_alignment",
      "title": "会话列表视觉对齐规范",
      "category": "preference",
      "keywords": ["视觉对齐", "小圆点", "目录图标", "会话名称"],
      "scenarios": ["调整会话列表UI布局", "评审UI设计稿"],
      "content": "会话列表中的小圆点需与目录分组的图标左边缘严格对齐，会话名称需与目录名称左边缘严格对齐。"
    }
  ]
}
```

## 6. 前端设置页改造

### 6.1 类型更新

`lib/utils.ts` 中的 `ProfileEntry` 增加 `title?: string`、`keywords: string[]`、`scenarios: string[]`。

### 6.2 MemoryList 编辑器

在现有 key/content 编辑器基础上增加：

- `title` 输入框（单行，可选）。
- `keywords` 标签输入组件（逗号/回车分隔，最多 5 个）。
- `scenarios` 标签输入组件（最多 3 个）。

### 6.3 列表展示

列表项从 `key + content` 改为 `title + keywords 标签 + 摘要 content`，key 退居次要（monospace 小字）。

## 7. 聊天区 References 面板

### 7.1 位置与布局

- 位于聊天主区域右侧，可折叠/展开（默认展开）。
- 宽度固定 `240px` 或支持拖拽调整（最小 `180px`，最大 `360px`）。
- 与消息流、输入区共存，不遮挡核心聊天区域。

### 7.2 Tab 划分

| Tab | 内容 | 数据来源 |
|---|---|---|
| 偏好 | preference 类画像 | 全局 `data/config/profile.json` + 工作区 `.agentx/memory/*.md`（category=preference） |
| 记忆 | project 类画像 | 全局 + 工作区（category=project） |

### 7.3 卡片样式

参考 qoder 截图：

- 每个条目一张卡片。
- 顶部：图标 + `title`。
- 中部：`keywords` 标签组（圆角小标签）。
- 点击/悬停展开：`content` + `scenarios`。
- 底部小字：`scope`（全局/工作区）+ `source`（手动/LLM 抽取）+ `updated_at`。

### 7.4 生命周期

- 当前会话启动时，根据 `workspace_path` 拉取 preference + project 两类条目。
- 监听 SSE `profile_update` 事件（新增）或用户在设置页修改后，重新拉取。

## 8. 系统 Prompt 注入

`build_profile_prompt` 输出格式升级，优先展示 `title` 与 `content`：

```text
用户画像（请遵循以下偏好与约定）:
- [preference] 会话列表视觉对齐规范：会话列表中的小圆点需与目录分组的图标左边缘严格对齐...
- [project] 工作区技术栈：项目使用 FastAPI + React + Tauri 2.x...
```

当 `title` 为空时，退回到 `[category] content` 旧格式。

## 9. 兼容性策略

- **profile.json**：Pydantic `title=None`、`keywords=[]`、`scenarios=[]` 默认值，旧数据无需迁移。
- **workspace .md**：frontmatter 读取时缺失字段补默认值，写回时按新格式序列化。
- **API**：新增字段均为可选，旧客户端不传时不影响后端。
- **前端**：旧条目 `title` 为空时，列表展示 key 或 content 首句兜底。

## 10. 测试计划

- 后端：
  - `profile_store.py` 新增/更新条目含 title/keywords/scenarios 的读写测试。
  - `workspace/memory_store.py` 新旧 frontmatter 兼容测试。
  - `profile_extractor.py` LLM 抽取结构化输出含新字段的 mock 测试。
  - `/api/memory/profile` CRUD 端到端测试。
- 前端：
  - `pnpm typecheck` 通过。
  - `pnpm test` 新增 `ReferencesPanel` 渲染测试。

## 11. 风险提示

- 数据结构一次性扩展 3 个字段，需确保 `workspace/memory_store.py` 的 frontmatter 解析与序列化稳定。
- References 面板新增右侧固定布局，需检查对现有聊天主区域响应式布局的影响。
- LLM 抽取增加字段后，单次 token 消耗略有增加，但 `_MAX_LLM_EXTRACT_ENTRIES` 仍为 20，可控。

## 12. 后续可选增强（本次不做）

- References 面板支持搜索/过滤 keywords。
- 点击卡片可将记忆内容插入当前输入框作为引用。
- Memory tab 支持展示 skill 文件作为参考。
