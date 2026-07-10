# Spec: 偏好记忆 qoder 化改造(preference-memory-qoder)

## 概述

偏好记忆从扁平 `key/content` 升级为结构化条目（title/keywords/scenarios），WorkspacePanel 新增「偏好」「记忆」两个顶层 tab 以 qoder 式卡片展示。向后兼容旧数据。

## 包结构

```
backend/app/
├── memory/
│   ├── profile_store.py       ← 修改:ProfileEntry 新增字段,build_profile_prompt 格式升级,update 签名扩展
│   └── profile_extractor.py   ← 修改:LLM 抽取结构化输出增加 title/keywords/scenarios
├── workspace/
│   └── memory_store.py        ← 修改:MemoryEntry frontmatter 扩展,save_entry 签名扩展
└── api/
    ├── schemas.py             ← 修改:ProfileEntryRequest/ProfileUpdateRequest 新增字段
    └── memory.py              ← 修改:API 端点透传新字段

frontend/
├── shared/
│   └── api-types.ts           ← 修改:ProfileEntry/ProfileEntryRequest 类型扩展
└── renderer/
    ├── lib/api/
    │   └── http.ts            ← 修改:saveProfile/updateProfile 透传新字段
    └── components/
        ├── settings/memory/
        │   ├── MemoryList.tsx           ← 修改:编辑器增加 title/keywords/scenarios,列表展示升级
        │   ├── PreferenceManager.tsx    ← 修改:适配新编辑器
        │   ├── ProfileManager.tsx       ← 修改:适配新编辑器
        │   └── ProjectMemoryManager.tsx ← 修改:适配新编辑器
        └── workspace/
            ├── MemoryReferencesPanel.tsx ← 新增:qoder 式卡片面板
            └── WorkspacePanel.tsx       ← 修改:增加 preference/memory tab
```

## 公共 API

### `profile_store.py` 变更

```python
# 修改:ProfileEntry 新增字段
class ProfileEntry(BaseModel):
    key: str
    title: str | None = None               # 新增
    category: str
    keywords: list[str] = Field(default_factory=list)  # 新增
    scenarios: list[str] = Field(default_factory=list)  # 新增
    content: str
    source: str
    scope: str = "global"
    created_at: str
    updated_at: str

# 修改:update 函数签名扩展
async def update(
    key: str,
    content: str,
    category: str | None = None,
    title: str | None = None,              # 新增
    keywords: list[str] | None = None,     # 新增
    scenarios: list[str] | None = None,    # 新增
    workspace_path: str | None = None,
) -> ProfileEntry:

# 修改:build_profile_prompt 注入格式升级
# 有 title: "- [category] title：content"
# 无 title: "- [category] content"
```

### `workspace/memory_store.py` 变更

```python
# 修改:MemoryEntry dataclass 新增字段
@dataclass
class MemoryEntry:
    key: str
    category: str
    content: str
    source: str
    updated_at: str
    title: str | None = None               # 新增
    keywords: list[str] = field(default_factory=list)  # 新增
    scenarios: list[str] = field(default_factory=list)  # 新增

# 修改:save_entry 签名扩展
async def save_entry(
    workspace_path: str,
    key: str,
    category: str,
    content: str,
    source: str = "manual",
    title: str | None = None,              # 新增
    keywords: list[str] | None = None,     # 新增
    scenarios: list[str] | None = None,    # 新增
) -> MemoryEntry:
```

### `api/schemas.py` 变更

```python
class ProfileEntryRequest(BaseModel):
    key: str
    category: str
    content: str
    title: str | None = None               # 新增
    keywords: list[str] = Field(default_factory=list)  # 新增
    scenarios: list[str] = Field(default_factory=list)  # 新增

class ProfileUpdateRequest(BaseModel):
    content: str
    category: str | None = None
    title: str | None = None               # 新增
    keywords: list[str] = Field(default_factory=list)  # 新增
    scenarios: list[str] = Field(default_factory=list)  # 新增
```

### `memory/profile_extractor.py` 变更

```python
class ProfileEntry(BaseModel):
    key: str = Field(description="条目唯一键")
    title: str = Field(description="可读标题,不超过20字")  # 新增
    category: str = Field(description="分类:preference / project / fact")
    keywords: list[str] = Field(description="3-5个关键词标签")  # 新增
    scenarios: list[str] = Field(description="1-3个应用场景")  # 新增
    content: str = Field(description="条目内容描述")
```

### 前端 `api-types.ts` 变更

```typescript
export interface ProfileEntry {
  key: string;
  title?: string;          // 新增
  category: string;
  keywords: string[];      // 新增
  scenarios: string[];     // 新增
  content: string;
  source: string;
  created_at: string;
  updated_at: string;
  scope?: "workspace" | "global";
}

export interface ProfileEntryRequest {
  key: string;
  title?: string;          // 新增
  category: string;
  keywords: string[];      // 新增
  scenarios: string[];     // 新增
  content: string;
}
```

### 前端 `http.ts` 变更

```typescript
// saveProfile: body 改为 JSON.stringify(entry) (entry 已含新字段)

// updateProfile: 签名增加 title/keywords/scenarios
updateProfile: async (
  key: string,
  content: string,
  category?: ProfileCategory | string,
  title?: string,          // 新增
  keywords?: string[],     // 新增
  scenarios?: string[],    // 新增
  workspacePath?: string | null,
): Promise<unknown> => {
  body: JSON.stringify({ content, category, title, keywords, scenarios }),
}
```

## 行为规约

### BR-1: 旧数据兼容

- profile.json 旧条目无 title/keywords/scenarios 字段时,Pydantic 反序列化自动补 `title=None` / `keywords=[]` / `scenarios=[]`
- workspace .md 旧 frontmatter 无新字段时,`_read_entry_file` 补默认值
- API 请求体不含新字段时,后端按默认值处理

### BR-2: system prompt 注入

- 有 title:`- [category] title：content`
- 无 title:`- [category] content`
- 注入上限不变(前 30 条按 updated_at 降序)

### BR-3: WorkspacePanel References 面板

- WorkspacePanel「偏好」tab 渲染 `<MemoryReferencesPanel category="preference" />`,拉取 category=preference 的画像(全局 + 工作区)
- WorkspacePanel「记忆」tab 渲染 `<MemoryReferencesPanel category="project" />`,拉取 category=project 的画像(全局 + 工作区)
- MemoryReferencesPanel 无内部子 tab,仅根据 category prop 过滤展示
- 卡片 title 为空时兜底显示 key
- 切换 workspacePath 时重新拉取

### BR-4: LLM 抽取

- 抽取结果含 title/keywords/scenarios
- 写入 store 时透传新字段
- 单次抽取上限不变(20 条)

## 测试要求

### 后端

- `test_profile_store.py`:新建/更新条目含新字段的读写 round-trip
- `test_memory_store.py`:新字段 round-trip + 旧 frontmatter 兼容
- `test_profile_extractor.py`:mock LLM 返回含新字段的结构化输出

### 前端

- `pnpm typecheck` 通过
- `MemoryReferencesPanel` 渲染测试(可选)
