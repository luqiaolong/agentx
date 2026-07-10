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

