# 任务追踪 — 偏好记忆 qoder 化改造

## 阶段一:后端数据模型扩展

### T1: ProfileEntry 模型扩展

- [ ] T1.1 [profile_store.py:90-99](../../../backend/app/memory/profile_store.py#L90-L99) `ProfileEntry` 类新增字段:
  - `title: str | None = None`
  - `keywords: list[str] = Field(default_factory=list)`
  - `scenarios: list[str] = Field(default_factory=list)`
- [ ] T1.2 写测试 `test_profile_store.py`:验证新建条目含 title/keywords/scenarios 的读写 round-trip
- [ ] T1.3 运行测试确认通过

### T2: MemoryEntry frontmatter 扩展

- [ ] T2.1 [memory_store.py:90-98](../../../backend/app/workspace/memory_store.py#L90-L98) `MemoryEntry` dataclass 新增:
  - `title: str | None = None`
  - `keywords: list[str]` (default_factory=list)
  - `scenarios: list[str]` (default_factory=list)
- [ ] T2.2 [memory_store.py:130-149](../../../backend/app/workspace/memory_store.py#L130-L149) `_serialize_entry` frontmatter 包含新字段
- [ ] T2.3 [memory_store.py:152-171](../../../backend/app/workspace/memory_store.py#L152-L171) `_read_entry_file` 解析时缺失字段补默认值
- [ ] T2.4 [memory_store.py:208-255](../../../backend/app/workspace/memory_store.py#L208-L255) `save_entry` 签名增加 `title/keywords/scenarios` 参数
- [ ] T2.5 写测试 `test_memory_store.py`:验证新字段 round-trip + 旧 frontmatter 兼容
- [ ] T2.6 运行测试确认通过

### T3: profile_store.update 签名扩展

- [ ] T3.1 [profile_store.py:349-410](../../../backend/app/memory/profile_store.py#L349-L410) `update` 函数新增参数:
  - `title: str | None = None`
  - `keywords: list[str] | None = None`
  - `scenarios: list[str] | None = None`
- [ ] T3.2 `model_copy` 更新时加入新字段(None 时保留原值)
- [ ] T3.3 验证 ruff 检查通过

### T4: API Schema 扩展

- [ ] T4.1 [schemas.py:97-109](../../../backend/app/api/schemas.py#L97-L109) `ProfileEntryRequest` 新增:
  - `title: str | None = None`
  - `keywords: list[str] = Field(default_factory=list)`
  - `scenarios: list[str] = Field(default_factory=list)`
- [ ] T4.2 `ProfileUpdateRequest` 同步新增同名字段

### T5: API 端点透传新字段

- [ ] T5.1 [memory.py:169-206](../../../backend/app/api/memory.py#L169-L206) `memory_profile_add`:
  - 工作区分支 `save_entry(..., title=req.title, keywords=req.keywords, scenarios=req.scenarios)`
  - 全局分支 `ProfileEntry(..., title=req.title, keywords=req.keywords, scenarios=req.scenarios)`
- [ ] T5.2 [memory.py:208-237](../../../backend/app/api/memory.py#L208-L237) `memory_profile_update`:
  - 工作区分支 `save_entry(..., title=req.title, keywords=req.keywords, scenarios=req.scenarios)`
  - 全局分支 `update(..., title=req.title, keywords=req.keywords, scenarios=req.scenarios)`
- [ ] T5.3 验证 ruff 检查通过

### T6: LLM 抽取器升级

- [ ] T6.1 [profile_extractor.py:19-30](../../../backend/app/memory/profile_extractor.py#L19-L30) `ProfileEntry` 结构体增加:
  - `title: str = Field(description="可读标题,不超过20字")`
  - `keywords: list[str] = Field(description="3-5个关键词标签")`
  - `scenarios: list[str] = Field(description="1-3个应用场景")`
- [ ] T6.2 [profile_extractor.py:33-38](../../../backend/app/memory/profile_extractor.py#L33-L38) `_PROFILE_SYSTEM` prompt 增加结构化输出要求
- [ ] T6.3 写 mock 测试验证抽取结果含新字段
- [ ] T6.4 运行测试确认通过

### T7: system prompt 注入格式升级

- [ ] T7.1 [profile_store.py:574-584](../../../backend/app/memory/profile_store.py#L574-L584) `build_profile_prompt` lines 构建循环:
  - 有 title: `lines.append(f"- [{category}] {title}：{content}")`
  - 无 title: `lines.append(f"- [{category}] {content}")`
- [ ] T7.2 写测试验证有 title 时 prompt 含 title
- [ ] T7.3 运行测试确认通过

## 阶段二:前端类型与 API 层

### T8: 前端类型扩展

- [ ] T8.1 [api-types.ts:306-315](../../../frontend/shared/api-types.ts#L306-L315) `ProfileEntry` 新增:
  - `title?: string`
  - `keywords: string[]`
  - `scenarios: string[]`
- [ ] T8.2 [api-types.ts:317-321](../../../frontend/shared/api-types.ts#L317-L321) `ProfileEntryRequest` 同步新增

### T9: 前端 API 层透传

- [ ] T9.1 [http.ts:328-340](../../../frontend/renderer/lib/api/http.ts#L328-L340) `saveProfile` body 改为 `JSON.stringify(entry)`(entry 已含新字段)
- [ ] T9.2 [http.ts:342-357](../../../frontend/renderer/lib/api/http.ts#L342-L357) `updateProfile` 签名增加 `title/keywords/scenarios` 参数,body 透传

## 阶段三:前端设置页编辑器升级

### T10: MemoryList 编辑器扩展

- [ ] T10.1 [MemoryList.tsx:114-137](../../../frontend/renderer/components/settings/memory/MemoryList.tsx#L114-L137) `MemoryListConfig` 接口新增:
  - `getTitle: (item: T) => string`
  - `setTitle: (item: T, title: string) => T`
  - `getKeywords: (item: T) => string[]`
  - `setKeywords: (item: T, keywords: string[]) => T`
  - `getScenarios: (item: T) => string[]`
  - `setScenarios: (item: T, scenarios: string[]) => T`
- [ ] T10.2 [MemoryList.tsx:35-68](../../../frontend/renderer/components/settings/memory/MemoryList.tsx#L35-L68) `useProfileCrud`:
  - `initialItem` 增加 `title: "", keywords: [], scenarios: []`
  - `updater` 调用 `memory.updateProfile(i.key, i.content, i.category, i.title, i.keywords, i.scenarios, workspacePath)`
- [ ] T10.3 新增 `TagInput` 组件(内联在 MemoryList.tsx):逗号/回车分隔,支持 max 限制
- [ ] T10.4 编辑表单增加 title 输入框 + keywords/scenarios TagInput
- [ ] T10.5 列表项展示改为 title + keywords 标签 + content

### T11: profileListConfig 工厂函数填充新 getter/setter

- [ ] T11.1 [MemoryList.tsx:74-112](../../../frontend/renderer/components/settings/memory/MemoryList.tsx#L74-L112) `profileListConfig` 返回值填充:
  - `getTitle: (e) => e.title ?? ""`
  - `setTitle: (e, t) => ({ ...e, title: t })`
  - `getKeywords: (e) => e.keywords ?? []`
  - `setKeywords: (e, k) => ({ ...e, keywords: k })`
  - `getScenarios: (e) => e.scenarios ?? []`
  - `setScenarios: (e, s) => ({ ...e, scenarios: s })`

### T12: 三个 Manager 组件适配

- [ ] T12.1 `PreferenceManager.tsx`:确认 `profileListConfig` 调用不报错(新字段有默认值)
- [ ] T12.2 `ProfileManager.tsx`:同上
- [ ] T12.3 `ProjectMemoryManager.tsx`:同上
- [ ] T12.4 运行 `pnpm typecheck` 确认通过

## 阶段四:WorkspacePanel References 面板

### T13: 新建 MemoryReferencesPanel 组件

- [ ] T13.1 创建 `frontend/renderer/components/workspace/MemoryReferencesPanel.tsx`
- [ ] T13.2 组件接受 `category: "preference" | "project"` prop,根据 category 拉取对应类别的画像(全局 + 工作区)
- [ ] T13.3 根据 workspacePath 拉取画像数据
- [ ] T13.4 卡片样式:title + keywords 标签 + content + scenarios + scope/source/updated_at
- [ ] T13.5 空状态与 loading 状态

### T14: WorkspacePanel 集成新 tab

- [ ] T14.1 [WorkspacePanel.tsx:23](../../../frontend/renderer/components/workspace/WorkspacePanel.tsx#L23) Tab 类型增加 `"preference" | "memory"`
- [ ] T14.2 导入 `Heart` / `Briefcase` 图标 + `MemoryReferencesPanel`
- [ ] T14.3 tab 按钮区增加 `tabBtn("preference", "偏好", Heart)` + `tabBtn("memory", "记忆", Briefcase)`
- [ ] T14.4 内容区增加 `active === "preference" && <MemoryReferencesPanel category="preference" />` + `active === "memory" && <MemoryReferencesPanel category="project" />`
- [ ] T14.5 运行 `pnpm typecheck` 确认通过

## 阶段五:全量验证

### T15: 后端验证

- [ ] T15.1 `uv run pytest tests/python/unit -m "not integration" -q` 全部通过
- [ ] T15.2 `uv run ruff check backend/` 通过

### T16: 前端验证

- [ ] T16.1 `pnpm typecheck` 通过
- [ ] T16.2 `pnpm test` 通过

### T17: 端到端冒烟

- [ ] T17.1 启动应用 `pnpm tauri dev`
- [ ] T17.2 设置 → 用户偏好,新建一条带 title/keywords/scenarios 的偏好
- [ ] T17.3 右侧工作区面板「偏好」tab 确认卡片展示
- [ ] T17.4 「记忆」tab 确认 project 类条目展示
- [ ] T17.5 发送一条消息,确认无报错
