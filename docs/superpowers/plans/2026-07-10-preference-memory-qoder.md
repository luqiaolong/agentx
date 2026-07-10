# 偏好记忆 qoder 化改造实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AgentX 偏好记忆从扁平 `key/content` 升级为结构化条目（title/keywords/scenarios），并在 WorkspacePanel 新增「偏好」「记忆」两个顶层 tab 以 qoder 式卡片展示。

**Architecture:** 后端扩展 `ProfileEntry`/`MemoryEntry` 与新字段，保持全局/工作区双层隔离和并发锁；前端更新 shared 类型、MemoryList 编辑器、WorkspacePanel 增加 References 式双 tab 视图；通过现有 `/api/memory/profile` 端点复用拉取逻辑。

**Tech Stack:** Python + FastAPI + Pydantic / React + TypeScript + Tailwind / YAML frontmatter

---

## 文件清单

| 文件 | 责任 |
|---|---|
| `backend/app/memory/profile_store.py` | 全局画像 `ProfileEntry` 模型扩展 + `build_profile_prompt` 格式升级 |
| `backend/app/workspace/memory_store.py` | 工作区记忆 `MemoryEntry` 模型扩展 + frontmatter 解析/序列化 |
| `backend/app/memory/profile_extractor.py` | LLM 抽取结构化输出增加 title/keywords/scenarios |
| `backend/app/api/schemas.py` | `ProfileEntryRequest` / `ProfileUpdateRequest` 新增字段 |
| `backend/app/api/memory.py` | API 端点透传新字段 |
| `frontend/shared/api-types.ts` | `ProfileEntry` / `ProfileEntryRequest` 类型扩展 |
| `frontend/renderer/lib/api/http.ts` | `saveProfile` / `updateProfile` 透传新字段 |
| `frontend/renderer/components/settings/memory/MemoryList.tsx` | 编辑器增加 title/keywords/scenarios，列表展示升级 |
| `frontend/renderer/components/settings/memory/PreferenceManager.tsx` | 适配新编辑器配置 |
| `frontend/renderer/components/settings/memory/ProfileManager.tsx` | 适配新编辑器配置 |
| `frontend/renderer/components/settings/memory/ProjectMemoryManager.tsx` | 适配新编辑器配置 |
| `frontend/renderer/components/workspace/MemoryReferencesPanel.tsx` | 新增：WorkspacePanel 内「偏好」「记忆」qoder 式卡片面板 |
| `frontend/renderer/components/workspace/WorkspacePanel.tsx` | 增加 `preference` / `memory` 两个顶层 tab |
| `backend/tests/python/unit/memory/test_profile_store.py`（或同路径） | 新增/更新测试 |
| `backend/tests/python/unit/workspace/test_memory_store.py`（或同路径） | 新增/更新测试 |
| `frontend/renderer/components/workspace/__tests__/MemoryReferencesPanel.test.tsx`（可选） | 新增渲染测试 |

---

## Task 1: 扩展后端全局画像模型

**Files:**
- Modify: `backend/app/memory/profile_store.py`
- Test: `backend/tests/python/unit/memory/test_profile_store.py`

- [ ] **Step 1: 写失败测试**

```python
def test_profile_entry_accepts_new_fields(tmp_path, monkeypatch):
    from app.memory.profile_store import ProfileEntry, add, get_all
    monkeypatch.setattr("app.memory.profile_store._PROFILE_FILE", tmp_path / "profile.json")
    entry = ProfileEntry(
        key="align_rule",
        title="会话列表视觉对齐规范",
        category="preference",
        keywords=["视觉对齐", "小圆点"],
        scenarios=["调整会话列表UI布局"],
        content="小圆点与目录图标左边缘对齐。",
        source="manual",
        created_at="",
        updated_at="",
    )
    import asyncio
    asyncio.run(add(entry))
    loaded = get_all()[0]
    assert loaded.title == "会话列表视觉对齐规范"
    assert loaded.keywords == ["视觉对齐", "小圆点"]
    assert loaded.scenarios == ["调整会话列表UI布局"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/python/unit/memory/test_profile_store.py::test_profile_entry_accepts_new_fields -v`
Expected: FAIL with `unexpected keyword argument 'title'`

- [ ] **Step 3: 修改 ProfileEntry 模型**

在 `backend/app/memory/profile_store.py` 的 `ProfileEntry` 类中新增字段：

```python
class ProfileEntry(BaseModel):
    """单条用户画像。"""

    key: str
    title: str | None = None
    category: str
    keywords: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    content: str
    source: str
    scope: str = "global"
    created_at: str
    updated_at: str
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/python/unit/memory/test_profile_store.py::test_profile_entry_accepts_new_fields -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/memory/profile_store.py backend/tests/python/unit/memory/test_profile_store.py
git commit -m "feat(memory): extend ProfileEntry with title/keywords/scenarios"
```

---

## Task 2: 扩展工作区记忆 frontmatter

**Files:**
- Modify: `backend/app/workspace/memory_store.py`
- Test: `backend/tests/python/unit/workspace/test_memory_store.py`（如不存在则创建对应路径）

- [ ] **Step 1: 写失败测试**

```python
def test_memory_entry_roundtrip_new_fields(tmp_path):
    import asyncio
    from app.workspace.memory_store import save_entry, get_entry, list_entries
    ws = str(tmp_path / "ws")
    asyncio.run(save_entry(
        ws, "tech", "project", "使用 FastAPI + React",
        title="工作区技术栈",
        keywords=["FastAPI", "React"],
        scenarios=["新成员理解项目"],
    ))
    e = get_entry(ws, "tech")
    assert e.title == "工作区技术栈"
    assert e.keywords == ["FastAPI", "React"]
    assert e.scenarios == ["新成员理解项目"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/python/unit/workspace/test_memory_store.py::test_memory_entry_roundtrip_new_fields -v`
Expected: FAIL with `unexpected keyword argument 'title'`

- [ ] **Step 3: 扩展 MemoryEntry + 序列化/解析**

在 `backend/app/workspace/memory_store.py`：

1. `MemoryEntry` dataclass 新增 `title: str | None = None`、`keywords: list[str]`、`scenarios: list[str]`，并提供默认值：

```python
@dataclass
class MemoryEntry:
    key: str
    category: str
    content: str
    source: str
    updated_at: str
    title: str | None = None
    keywords: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
```

2. `_serialize_entry` frontmatter 包含新字段：

```python
frontmatter = {
    "key": entry.key,
    "category": entry.category,
    "title": entry.title,
    "keywords": entry.keywords,
    "scenarios": entry.scenarios,
    "source": entry.source,
    "updated_at": entry.updated_at,
}
```

3. `_read_entry_file` 解析时缺失字段补默认值：

```python
title = frontmatter.get("title")
if title is not None:
    title = str(title)
keywords = frontmatter.get("keywords") or []
scenarios = frontmatter.get("scenarios") or []
```

4. `save_entry` 签名增加 `title`, `keywords`, `scenarios` 参数并写入 `MemoryEntry`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/python/unit/workspace/test_memory_store.py::test_memory_entry_roundtrip_new_fields -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/workspace/memory_store.py backend/tests/python/unit/workspace/test_memory_store.py
git commit -m "feat(workspace-memory): extend MemoryEntry frontmatter with title/keywords/scenarios"
```

---

## Task 3: 升级 API Schema 与端点

**Files:**
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/memory.py`

- [ ] **Step 1: 修改 ProfileEntryRequest / ProfileUpdateRequest**

在 `backend/app/api/schemas.py`：

```python
class ProfileEntryRequest(BaseModel):
    key: str
    category: str
    content: str
    title: str | None = None
    keywords: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)


class ProfileUpdateRequest(BaseModel):
    content: str
    category: str | None = None
    title: str | None = None
    keywords: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: 修改 API 端点透传新字段**

在 `backend/app/api/memory.py`：

- `memory_profile_add` 工作区分支调用 `save_entry(..., title=req.title, keywords=req.keywords, scenarios=req.scenarios)`；全局分支构造 `ProfileEntry(..., title=req.title, keywords=req.keywords, scenarios=req.scenarios)`。
- `memory_profile_update` 工作区分支调用 `save_entry(..., title=req.title, keywords=req.keywords, scenarios=req.scenarios)`；全局分支 `update(..., title=req.title, keywords=req.keywords, scenarios=req.scenarios)`。
- 更新 `profile_store.update` 签名以接收新字段。

- [ ] **Step 3: 更新 profile_store.update 方法**

在 `backend/app/memory/profile_store.py` 的 `update` 函数：

```python
async def update(
    key: str,
    content: str,
    category: str | None = None,
    title: str | None = None,
    keywords: list[str] | None = None,
    scenarios: list[str] | None = None,
    workspace_path: str | None = None,
) -> ProfileEntry:
```

在更新 `model_copy` 时加入：

```python
"title": title if title is not None else entry.title,
"keywords": keywords if keywords is not None else entry.keywords,
"scenarios": scenarios if scenarios is not None else entry.scenarios,
```

- [ ] **Step 4: 验证 ruff 检查**

Run: `uv run ruff check backend/app/memory/profile_store.py backend/app/workspace/memory_store.py backend/app/api/memory.py backend/app/api/schemas.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/schemas.py backend/app/api/memory.py backend/app/memory/profile_store.py
git commit -m "feat(api): propagate title/keywords/scenarios through profile endpoints"
```

---

## Task 4: 升级 LLM 抽取器

**Files:**
- Modify: `backend/app/memory/profile_extractor.py`
- Test: `backend/tests/python/unit/memory/test_profile_extractor.py`（如不存在则创建）

- [ ] **Step 1: 扩展 ProfileEntry 模型 + prompt**

在 `backend/app/memory/profile_extractor.py`：

```python
class ProfileEntry(BaseModel):
    key: str = Field(description="条目唯一键")
    title: str = Field(description="可读标题，不超过20字")
    category: str = Field(description="分类：preference / project / fact")
    keywords: list[str] = Field(description="3-5个关键词标签")
    scenarios: list[str] = Field(description="1-3个应用场景")
    content: str = Field(description="条目内容描述")
```

更新 `_PROFILE_SYSTEM`：

```python
_PROFILE_SYSTEM = (
    "你是一个用户画像抽取器。分析对话，抽取「值得跨会话记住的事实」。"
    "为每条抽取结果生成：key、title（20字内）、category、keywords（3-5个）、scenarios（1-3个）、content。"
    "若无可抽取内容，返回空 entries。不要编造。"
)
```

- [ ] **Step 2: 写 mock 测试**

```python
def test_extract_profile_returns_new_fields():
    import asyncio
    from app.memory.profile_extractor import extract_profile_via_llm
    # 通过 monkeypatch get_chat_model 返回固定结构化结果
    ...
    result = asyncio.run(extract_profile_via_llm("我喜欢简洁回复", "好的"))
    assert result[0]["title"]
    assert result[0]["keywords"]
    assert result[0]["scenarios"]
```

- [ ] **Step 3: 运行测试确认通过**

Run: `uv run pytest tests/python/unit/memory/test_profile_extractor.py -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add backend/app/memory/profile_extractor.py backend/tests/python/unit/memory/test_profile_extractor.py
git commit -m "feat(extract): profile extractor emits title/keywords/scenarios"
```

---

## Task 5: 升级系统 Prompt 注入格式

**Files:**
- Modify: `backend/app/memory/profile_store.py`
- Test: `backend/tests/python/unit/memory/test_profile_store.py`

- [ ] **Step 1: 修改 build_profile_prompt**

在 `build_profile_prompt` 的 lines 构建循环中：

```python
for entry in sorted_entries:
    content = getattr(entry, "content", "")
    category = getattr(entry, "category", "custom")
    title = getattr(entry, "title", None)
    if title:
        lines.append(f"- [{category}] {title}：{content}")
    else:
        lines.append(f"- [{category}] {content}")
```

- [ ] **Step 2: 写测试**

```python
def test_build_profile_prompt_uses_title():
    ...
    prompt = build_profile_prompt()
    assert "会话列表视觉对齐规范" in prompt
    assert "小圆点" in prompt
```

- [ ] **Step 3: 运行测试确认通过**

Run: `uv run pytest tests/python/unit/memory/test_profile_store.py -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add backend/app/memory/profile_store.py backend/tests/python/unit/memory/test_profile_store.py
git commit -m "feat(prompt): include title in profile prompt injection"
```

---

## Task 6: 前端类型与 API 层扩展

**Files:**
- Modify: `frontend/shared/api-types.ts`
- Modify: `frontend/renderer/lib/api/http.ts`

- [ ] **Step 1: 扩展 ProfileEntry / ProfileEntryRequest**

在 `frontend/shared/api-types.ts`：

```typescript
export interface ProfileEntry {
  key: string;
  title?: string;
  category: string;
  keywords: string[];
  scenarios: string[];
  content: string;
  source: string;
  created_at: string;
  updated_at: string;
  scope?: "workspace" | "global";
}

export interface ProfileEntryRequest {
  key: string;
  title?: string;
  category: string;
  keywords: string[];
  scenarios: string[];
  content: string;
}
```

- [ ] **Step 2: 修改 saveProfile / updateProfile 透传字段**

在 `frontend/renderer/lib/api/http.ts`：

```typescript
saveProfile: async (
  entry: ProfileEntryRequest,
  workspacePath?: string | null,
): Promise<unknown> => {
  ...
  body: JSON.stringify(entry),
  ...
},

updateProfile: async (
  key: string,
  content: string,
  category?: ProfileCategory | string,
  title?: string,
  keywords?: string[],
  scenarios?: string[],
  workspacePath?: string | null,
): Promise<unknown> => {
  ...
  body: JSON.stringify({ content, category, title, keywords, scenarios }),
  ...
},
```

- [ ] **Step 3: typecheck**

Run: `pnpm typecheck`
Expected: 可能暂时报错，后续 MemoryList 改完后再次运行

- [ ] **Step 4: 提交**

```bash
git add frontend/shared/api-types.ts frontend/renderer/lib/api/http.ts
git commit -m "feat(types): extend ProfileEntry and http client with new fields"
```

---

## Task 7: 前端 MemoryList 编辑器升级

**Files:**
- Modify: `frontend/renderer/components/settings/memory/MemoryList.tsx`
- Modify: `frontend/renderer/components/settings/memory/PreferenceManager.tsx`
- Modify: `frontend/renderer/components/settings/memory/ProfileManager.tsx`
- Modify: `frontend/renderer/components/settings/memory/ProjectMemoryManager.tsx`

- [ ] **Step 1: 扩展 MemoryListConfig 与 useProfileCrud**

在 `MemoryList.tsx`：

1. `MemoryListConfig` 增加：

```typescript
getTitle: (item: T) => string;
setTitle: (item: T, title: string) => T;
getKeywords: (item: T) => string[];
setKeywords: (item: T, keywords: string[]) => T;
getScenarios: (item: T) => string[];
setScenarios: (item: T, scenarios: string[]) => T;
```

2. `useProfileCrud` 的 `initialItem` 改为：

```typescript
initialItem: () => ({
  key: "", category: defaultCat, title: "", keywords: [], scenarios: [], content: "", source: "manual", created_at: "", updated_at: "",
}),
```

3. `updater` 调用改为传递 title/keywords/scenarios：

```typescript
updater: async (i) => {
  await memory.updateProfile(i.key, i.content, i.category, i.title, i.keywords, i.scenarios, workspacePath);
},
```

- [ ] **Step 2: 新增 TagInput 组件（内联简单版）**

在 `MemoryList.tsx` 内实现：

```typescript
function TagInput({ values, onChange, placeholder, max }: { values: string[]; onChange: (v: string[]) => void; placeholder: string; max: number }) {
  const [input, setInput] = useState("");
  const add = (v: string) => {
    const trimmed = v.trim();
    if (!trimmed) return;
    if (values.includes(trimmed)) return;
    if (values.length >= max) return;
    onChange([...values, trimmed]);
    setInput("");
  };
  return (
    <div className="input-field flex flex-wrap gap-1 p-1">
      {values.map((v) => (
        <span key={v} className="inline-flex items-center gap-1 rounded bg-brand-600/10 px-1.5 py-0.5 text-brand-500" style={{ fontSize: "var(--fs-settings-badge)" }}>
          {v}
          <button type="button" onClick={() => onChange(values.filter((x) => x !== v))}><X className="h-3 w-3" /></button>
        </span>
      ))}
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(input); } }}
        placeholder={values.length === 0 ? placeholder : ""}
        className="min-w-[80px] flex-1 bg-transparent outline-none"
        style={{ fontSize: "var(--fs-settings-form-input)" }}
      />
    </div>
  );
}
```

- [ ] **Step 3: 在编辑表单中增加 title/keywords/scenarios 字段**

在 `MemoryList` 编辑区 `config.getKey` 之后、`renderExtraFields` 之前插入：

```tsx
<div>
  <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: "var(--fs-settings-form-label)" }}>标题（可选）</label>
  <input
    type="text"
    value={config.getTitle(editing)}
    onChange={(e) => editing && updateDraft(config.setTitle(editing, e.target.value))}
    placeholder="可读标题，为空时自动使用 key"
    className="input-field"
    style={{ fontSize: "var(--fs-settings-form-input)" }}
  />
</div>

<div>
  <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: "var(--fs-settings-form-label)" }}>关键词（最多5个）</label>
  <TagInput values={config.getKeywords(editing)} onChange={(v) => editing && updateDraft(config.setKeywords(editing, v))} placeholder="输入后回车" max={5} />
</div>

<div>
  <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: "var(--fs-settings-form-label)" }}>应用场景（最多3个）</label>
  <TagInput values={config.getScenarios(editing)} onChange={(v) => editing && updateDraft(config.setScenarios(editing, v))} placeholder="输入后回车" max={3} />
</div>
```

- [ ] **Step 4: 升级 profileListConfig 工厂函数**

填充新增 getter/setter：

```typescript
return {
  ...existing,
  getTitle: (e) => e.title ?? "",
  setTitle: (e, t) => ({ ...e, title: t }),
  getKeywords: (e) => e.keywords ?? [],
  setKeywords: (e, k) => ({ ...e, keywords: k }),
  getScenarios: (e) => e.scenarios ?? [],
  setScenarios: (e, s) => ({ ...e, scenarios: s }),
};
```

- [ ] **Step 5: 升级列表项展示**

在 `MemoryList` 列表项中：

```tsx
<div className="flex items-center gap-2">
  <span className="font-medium text-primary-c">{config.getTitle(item) || config.getIdLabel?.(item) || id}</span>
  {config.renderBadges?.(item)}
  ...
</div>
<div className="mt-1 flex flex-wrap gap-1">
  {config.getKeywords(item).map((kw) => (
    <span key={kw} className="rounded-full bg-subtle px-1.5 py-0.5 text-muted-c" style={{ fontSize: "var(--fs-settings-badge)" }}>{kw}</span>
  ))}
</div>
<p className="mt-1 whitespace-pre-wrap break-words text-secondary-c">{config.getContent(item)}</p>
```

- [ ] **Step 6: 运行 typecheck**

Run: `pnpm typecheck`
Expected: PASS（可能需同步修改 PreferenceManager/ProfileManager/ProjectMemoryManager 的调用签名）

- [ ] **Step 7: 提交**

```bash
git add frontend/renderer/components/settings/memory/MemoryList.tsx
git commit -m "feat(ui): MemoryList editor supports title/keywords/scenarios"
```

---

## Task 8: 适配三个 Manager 组件

**Files:**
- Modify: `frontend/renderer/components/settings/memory/PreferenceManager.tsx`
- Modify: `frontend/renderer/components/settings/memory/ProfileManager.tsx`
- Modify: `frontend/renderer/components/settings/memory/ProjectMemoryManager.tsx`

- [ ] **Step 1: PreferenceManager 增加 placeholder 与标题默认值**

```typescript
profileListConfig(Heart, "用户偏好（data/config/profile.json）", {
  ...
  titlePlaceholder: "例如：会话列表视觉对齐规范",
  contentPlaceholder: "具体规则描述...",
})
```

（如 `profileListConfig` 未定义 `titlePlaceholder`，可在 Step 7 中加入。）

- [ ] **Step 2: ProfileManager / ProjectMemoryManager 同步适配**

确保三者的 `renderBadges` 与 `profileListConfig` 调用不报错；如果之前传入了自定义 `renderExtraFields` 等，位置正确即可。

- [ ] **Step 3: 运行 typecheck**

Run: `pnpm typecheck`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add frontend/renderer/components/settings/memory/*.tsx
git commit -m "feat(ui): adapt preference/profile/project managers to new editor"
```

---

## Task 9: 新增 MemoryReferencesPanel 组件

**Files:**
- Create: `frontend/renderer/components/workspace/MemoryReferencesPanel.tsx`

- [ ] **Step 1: 实现组件**

```tsx
import { useEffect, useState } from "react";
import { Lightbulb, Briefcase, Heart } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { memory } from "@/lib/api/http";
import type { ProfileEntry } from "@/lib/utils";
import { formatTime } from "@/lib/format";

type Tab = "preference" | "project";

export function MemoryReferencesPanel() {
  const [active, setActive] = useState<Tab>("preference");
  const [entries, setEntries] = useState<ProfileEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const currentId = useChatStore((s) => s.currentId);
  const session = useChatStore((s) => (currentId ? s.sessions[currentId] ?? null : null));
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const workspacePath = session?.workspacePath ?? homeWorkspacePath ?? null;

  const load = async () => {
    setLoading(true);
    try {
      const [pref, proj] = await Promise.all([
        memory.getProfile("preference", workspacePath),
        memory.getProfile("project", workspacePath),
      ]);
      setEntries(active === "preference" ? pref.entries : proj.entries);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [active, workspacePath]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-default p-1.5">
        <button
          type="button"
          onClick={() => setActive("preference")}
          className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-xs font-medium ${active === "preference" ? "bg-brand-600/10 text-brand-500" : "text-muted-c hover:bg-hover-soft"}`}
        >
          <Heart className="h-3 w-3" /> 偏好
        </button>
        <button
          type="button"
          onClick={() => setActive("project")}
          className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-xs font-medium ${active === "project" ? "bg-brand-600/10 text-brand-500" : "text-muted-c hover:bg-hover-soft"}`}
        >
          <Briefcase className="h-3 w-3" /> 记忆
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {loading && <div className="shimmer-bg h-20 rounded-lg" />}
        {!loading && entries.length === 0 && (
          <p className="text-center text-muted-c" style={{ fontSize: "var(--fs-empty-title)" }}>
            暂无{active === "preference" ? "偏好" : "记忆"}条目
          </p>
        )}
        {!loading && entries.map((e) => (
          <div key={e.key} className="mb-2 rounded-lg border border-default bg-surface p-2">
            <div className="flex items-start gap-1.5">
              <Lightbulb className="mt-0.5 h-3 w-3 shrink-0 text-brand-500" />
              <span className="font-medium text-secondary-c" style={{ fontSize: "var(--fs-settings-desc)" }}>
                {e.title || e.key}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {e.keywords?.map((kw) => (
                <span key={kw} className="rounded-full bg-subtle px-1.5 py-0.5 text-muted-c" style={{ fontSize: "var(--fs-settings-badge)" }}>{kw}</span>
              ))}
            </div>
            <p className="mt-1 text-secondary-c" style={{ fontSize: "var(--fs-settings-desc)" }}>{e.content}</p>
            {e.scenarios && e.scenarios.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-muted-c" style={{ fontSize: "var(--fs-card-meta)" }}>
                {e.scenarios.map((s) => <li key={s}>{s}</li>)}
              </ul>
            )}
            <div className="mt-1.5 flex items-center gap-2 text-muted-c" style={{ fontSize: "var(--fs-card-meta)" }}>
              <span>{e.scope === "workspace" ? "工作区" : "全局"}</span>
              <span>{e.source === "manual" ? "手动" : "LLM 抽取"}</span>
              <span>{formatTime(e.updated_at)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: typecheck**

Run: `pnpm typecheck`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add frontend/renderer/components/workspace/MemoryReferencesPanel.tsx
git commit -m "feat(ui): add MemoryReferencesPanel with preference/project tabs"
```

---

## Task 10: WorkspacePanel 集成新 tab

**Files:**
- Modify: `frontend/renderer/components/workspace/WorkspacePanel.tsx`

- [ ] **Step 1: 扩展 Tab 类型与图标导入**

```typescript
import { Heart, Briefcase } from "lucide-react";
import { MemoryReferencesPanel } from "./MemoryReferencesPanel";

type Tab = "tasks" | "files" | "git" | "preference" | "memory";
```

- [ ] **Step 2: tab 按钮加入 preference/memory**

在 tab 按钮渲染区增加：

```tsx
{tabBtn("preference", "偏好", Heart)}
{tabBtn("memory", "记忆", Briefcase)}
```

- [ ] **Step 3: 内容区渲染新 tab**

在内容区增加：

```tsx
{active === "preference" && <MemoryReferencesPanel />}
{active === "memory" && <MemoryReferencesPanel />}
```

（`MemoryReferencesPanel` 自身管理 active 子 tab，所以两个顶层 tab 都渲染同一组件即可；或者将子 tab 状态提升到 WorkspacePanel。为简单起见，组件内部管理。）

- [ ] **Step 4: typecheck**

Run: `pnpm typecheck`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/renderer/components/workspace/WorkspacePanel.tsx
git commit -m "feat(ui): integrate preference/memory tabs into WorkspacePanel"
```

---

## Task 11: 全量验证

- [ ] **Step 1: 后端单元测试**

Run: `uv run pytest tests/python/unit -m "not integration" -q`
Expected: PASS

- [ ] **Step 2: 后端 ruff**

Run: `uv run ruff check backend/`
Expected: PASS

- [ ] **Step 3: 前端 typecheck**

Run: `pnpm typecheck`
Expected: PASS

- [ ] **Step 4: 前端测试**

Run: `pnpm test`
Expected: PASS（已有测试 + 新增测试）

- [ ] **Step 5: 端到端冒烟**

1. 启动应用：`pnpm tauri dev`
2. 打开设置 → 用户偏好，新建一条带 title/keywords/scenarios 的偏好。
3. 打开右侧工作区面板的「偏好」tab，确认卡片展示。
4. 打开「记忆」tab，确认 project 类条目展示。
5. 发送一条消息，确认无报错。

- [ ] **Step 6: 提交所有未提交变更**

```bash
git add .
git commit -m "feat(memory): qoder-style preference memory with title/keywords/scenarios and References panel"
```

---

## Self-Review

- **Spec coverage:**
  - 数据模型扩展 → Task 1/2
  - API schema/端点 → Task 3
  - LLM 抽取 → Task 4
  - 系统 prompt 注入 → Task 5
  - 前端类型/API → Task 6
  - 前端编辑器 → Task 7/8
  - References 面板 → Task 9/10
  - 测试 → 各 Task 测试步骤
- **Placeholder scan:** 无 TBD/TODO/实现 later。
- **类型一致性：** `ProfileEntry` 前后端字段名一致；`updateProfile` 参数顺序与调用方一致。
