# 任务追踪 — settings-agents-tools-memory

> **注**：本 change 聚焦设置页新增 3 tab（子代理/工具/记忆），共 3 个 capability。
> 每阶段独立 commit，验收以单测 + typecheck + smoke 测试为准。

## 预期修改/新增文件

### 后端 (backend/)

#### subagents-config + tools-config
- [ ] `backend/app/config.py`（新增 `SubagentSettings` 嵌套模型 + `subagents: dict[str, SubagentSettings]` 字段 + `tools_enabled: dict[str, bool]` 字段 + `profile_auto_extract: bool` 字段，从 `AGENT_PY_SUBAGENTS_CONFIG` / `AGENT_PY_TOOLS_CONFIG` / `AGENT_PY_PROFILE_AUTO_EXTRACT` env 读取）
- [ ] `backend/app/subagents/code_agent.py`（`build_code_agent` 从 `get_settings().subagents.code` 读取 temperature/systemPrompt；`_make_fs_tools` 根据 `tools_enabled` 过滤工具）
- [ ] `backend/app/subagents/rag_agent.py`（同上，读取 rag 配置）
- [ ] `backend/app/subagents/web_agent.py`（同上，读取 web 配置）
- [ ] `backend/app/paths/deep_path.py`（`_make_deep_tools` 根据 `tools_enabled` 过滤；`build_deep_agent` 从 `get_settings().subagents` 读取默认温度）
- [ ] `backend/app/router/graph.py`（`_select_subagent` 改为读 `settings.subagents` 关键词 + 检查 enabled + 检查工具可用性，禁用时返回 None 退回路径 A；`run_router` 处理 None 返回值）
- [ ] `tests/python/unit/test_subagents_config.py`（mock settings，验证：禁用子代理退回路径 A、温度读取、关键词自定义、工具过滤）
- [ ] `tests/python/unit/test_tools_config.py`（mock settings，验证：DeepAgent 工具集过滤、子代理工具集过滤、DANGEROUS_TOOLS 解耦）

#### memory-management
- [ ] `backend/app/memory/skills_store.py`（新建：`list_skills_files` / `get_skill_file` / `save_skill_file` / `delete_skill_file`，含名称正则校验 + 路径逃逸校验）
- [ ] `backend/app/memory/profile_store.py`（新建：`get_all` / `get` / `add` / `update` / `delete` / `extract_from_conversation`，读写 `data/config/profile.json`）
- [ ] `backend/app/memory/checkpointer_view.py`（新建：`list_threads` / `get_db_size` / `delete_thread`，直接查 sqlite）
- [ ] `backend/app/memory/__init__.py`（导出新模块）
- [ ] `backend/app/main.py`（新增 10 个端点：`GET/POST/DELETE /api/memory/skills*` 4 个含 GET 列表/GET 单个/POST 新建或覆盖/DELETE、`GET/POST/PUT/DELETE /api/memory/profile*` 5 个含 extract、`GET/DELETE /api/memory/checkpointer*` 2 个）
- [ ] `backend/app/router/graph.py`（`run_router` 入口读画像注入 system prompt；路径 C 结束后异步触发画像抽取）
- [ ] `tests/python/unit/test_memory_skills.py`（验证：CRUD + 名称校验 + 路径逃逸 + reload 触发）
- [ ] `tests/python/unit/test_memory_profile.py`（验证：CRUD + key 重复 409 + LLM 抽取 mock + 注入 system prompt）
- [ ] `tests/python/unit/test_memory_checkpointer.py`（验证：list_threads + delete_thread + thread_id 校验）

### 前端 (frontend/)

#### settings 扩展
- [ ] `frontend/main/store.ts`（新增 `getSubagentsConfig` / `setSubagentsConfig` / `getToolsConfig` / `setToolsConfig` / `getProfileAutoExtract` / `setProfileAutoExtract`）
- [ ] `frontend/main/python/spawn.ts`（`PythonCredentials` 扩展 `subagentsConfig` / `toolsConfig` / `profileAutoExtract`，spawn 时注入 `AGENT_PY_SUBAGENTS_CONFIG` / `AGENT_PY_TOOLS_CONFIG` / `AGENT_PY_PROFILE_AUTO_EXTRACT` env）
- [ ] `frontend/preload/index.ts`（`settings` 命名空间新增 `getSubagentsConfig` / `setSubagentsConfig` / `getToolsConfig` / `setToolsConfig`；新增 `memory` 命名空间含 9 个方法走 HTTP）
- [ ] `frontend/renderer/lib/utils.ts`（同步 `WindowAPI` 类型声明）

#### SubagentsSettings.tsx
- [ ] `frontend/renderer/components/settings/SubagentsSettings.tsx`（新建：3 个折叠卡片 code/rag/web，每卡片含启用开关/温度滑块/systemPrompt textarea/工具复选框组/关键词输入；dirty 状态 + 离开提示 + 保存后 toast「重启后端生效」+「重启后端」按钮）
- [ ] `frontend/renderer/components/settings/SettingsModal.tsx`（TABS 数组扩到 9 项，新增 `subagents`/`tools`/`memory` 三项，渲染对应组件）

#### ToolsSettings.tsx
- [ ] `frontend/renderer/components/settings/ToolsSettings.tsx`（新建：8 个工具按类别分组展示，每行含工具名/描述/启用开关；保存后 toast + 重启提示）

#### MemorySettings.tsx
- [ ] `frontend/renderer/components/settings/MemorySettings.tsx`（新建：二级 tab 容器，切换「技能文件」/「Checkpointer」/「用户画像」三个子面板）
- [ ] `frontend/renderer/components/settings/memory/SkillsManager.tsx`（新建：技能列表 + 编辑器 + 新建/删除按钮，调 `memory.listSkills` / `getSkill` / `saveSkill` / `deleteSkill`）
- [ ] `frontend/renderer/components/settings/memory/CheckpointerManager.tsx`（新建：db 大小 + thread 列表 + 单会话删除，调 `memory.getCheckpointer` / `deleteThread`）
- [ ] `frontend/renderer/components/settings/memory/ProfileManager.tsx`（新建：画像条目列表 + 可编辑 + 新建/删除 + 自动抽取开关，调 `memory.getProfile` / `saveProfile` / `deleteProfile`）

#### 类型与 store
- [ ] `frontend/renderer/stores/settings.ts`（新增 `SubagentsConfig` / `ToolsConfig` / `ProfileEntry` 类型 + zustand 字段 + setters）

## OpenSpec Tasks 映射

| ID | Capability | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|-----------|---------|---------|---------|------|
| T1 | subagents-config | 后端配置项扩展 | config.py | `Settings.subagents` 含 code/rag/web 三键，字段类型正确；env 缺失时默认值与硬编码一致 | ⬜ |
| T2 | subagents-config | 子代理读取配置 | subagents/*.py, deep_path.py | `build_*_agent` 从 settings 读取温度/prompt；`_make_*_tools` 按 `tools_enabled` 过滤 | ⬜ |
| T3 | subagents-config | 路径 B 禁用退回 | router/graph.py | `_select_subagent` 返回 None 时走路径 A；关键词从 settings 读取 | ⬜ |
| T4 | tools-config | DeepAgent 工具集动态构建 | paths/deep_path.py | `_make_deep_tools` 按 `tools_enabled` 过滤；DANGEROUS_TOOLS 不变 | ⬜ |
| T5 | memory-management | skills_store 实现 | memory/skills_store.py | CRUD + 名称校验 + 路径逃逸校验 + reload 触发 | ⬜ |
| T6 | memory-management | profile_store 实现 | memory/profile_store.py | CRUD + LLM 抽取 + JSON 损坏兜底 | ⬜ |
| T7 | memory-management | checkpointer_view 实现 | memory/checkpointer_view.py | list_threads + delete_thread + thread_id 校验 | ⬜ |
| T8 | memory-management | 10 个端点注册 | main.py | 端点契约与 spec 一致；端点权限校验通过 | ⬜ |
| T9 | memory-management | 画像注入 system prompt | router/graph.py | `run_router` 入口读画像拼到 system prompt 前；超 30 条截断 | ⬜ |
| T10 | memory-management | 路径 C 自动抽取 | paths/deep_path.py | 流式结束后异步调 `extract_from_conversation`；失败不报错 | ⬜ |
| T11 | subagents-config | SubagentsSettings UI | SubagentsSettings.tsx, SettingsModal.tsx | 3 卡片 5 字段可编辑；保存持久化 + toast；工具全禁用警告 | ⬜ |
| T12 | tools-config | ToolsSettings UI | ToolsSettings.tsx | 8 工具按类别分组 + 启用开关；保存持久化 | ⬜ |
| T13 | memory-management | MemorySettings UI | MemorySettings.tsx + 3 子组件 | 二级 tab 切换；技能 CRUD；checkpointer 列表+删除；画像 CRUD + 自动抽取开关 | ⬜ |
| T14 | 跨切面 | preload + main/store + spawn 注入 | preload/index.ts, main/store.ts, spawn.ts | IPC 桥 + env 注入；typecheck 通过 | ⬜ |
| T15 | 测试 | 后端单测 5 文件 | tests/python/unit/ | 5 个测试文件全绿；smoke 测试新增断言通过 | ⬜ |

## 阶段划分与 commit 策略

### 阶段 1：后端配置与子代理/工具（独立 commit）

- T1 config.py 扩展
- T2 subagents/*.py + deep_path.py 改造
- T3 router/graph.py 路径 B 退回
- T4 deep_path.py 工具集动态构建
- 测试：test_subagents_config.py + test_tools_config.py

### 阶段 2：后端记忆模块（独立 commit）

- T5 skills_store.py
- T6 profile_store.py
- T7 checkpointer_view.py
- T8 main.py 9 端点
- T9 + T10 画像注入与抽取
- 测试：test_memory_*.py 3 文件

### 阶段 3：前端 UI（独立 commit）

- T11 SubagentsSettings.tsx
- T12 ToolsSettings.tsx
- T13 MemorySettings.tsx + 3 子组件
- T14 preload + main/store + spawn

### 阶段 4：测试与验证（独立 commit）

- T15 smoke 测试断言扩展
- 全量回归：`cd backend && uv run pytest tests/python/unit` + `npm run typecheck` + `npm test`

## 规模判定

- 涉及文件数: 25+ → 规模: **L**
- 涉及模块数: backend(config/subagents/paths/router/memory/main) + frontend(main/preload/renderer/stores/components) + tests → 跨模块

## 验证说明

- 每阶段独立 commit 后跑：`cd backend && uv run pytest tests/python/unit` + `npm run typecheck` + `npm test`
- 全部完成后跑 `tests/python/integration/test_smoke.py`（需 myserver 可用）验证端到端
- 验收标准：
  - 子代理 tab 配置 code 温度 0.5，重启后 `build_code_agent` 用 0.5
  - 工具 tab 禁用 web_search，重启后 DeepAgent 与 web 子代理均不暴露
  - 子代理 tab 禁用 web，路径 B 命中「搜索」时退回路径 A
  - 记忆 tab 新建技能文件 `test_skill.md`，`@skill:test_skill` 触发注入
  - 记忆 tab 删除 thread，`/reset` 不再恢复历史
  - 记忆 tab 编辑画像「喜欢简洁回复」，路径 A system prompt 含该条
  - 路径 C 对话「我用 TypeScript」，画像自动新增 `uses_typescript` 条目
