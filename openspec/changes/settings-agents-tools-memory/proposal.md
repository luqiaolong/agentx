## Why

当前 SettingsModal 已收敛 6 个 tab（模型/系统提示词/知识库/审批/沙箱/日志），但 agent 的"行为可配置性"仍停留在代码硬编码层：

1. **子代理不可调** — `subagents/code_agent.py`、`rag_agent.py`、`web_agent.py` 的 `temperature`、system prompt、工具集、触发关键词全部硬编码；用户无法禁用某个子代理，也无法调整其行为。
2. **工具集不可控** — `paths/deep_path.py` `_make_deep_tools` 暴露的工具清单写死在代码里；用户无法按需关闭某个工具（如禁用 `web_search` 节省 token、禁用 `edit_file` 提升安全）。
3. **记忆无入口** — `data/skills/*.md` 由 `skills_loader.py` 加载但前端无管理界面；`data/agent_py.db` checkpointer 不可见也不可控；长期用户画像完全没有存储与读写机制。

本次 change 把这三块能力收敛到设置页，使 agent 达到"可调、可控、可记忆"的发布前状态。

## What Changes

### P0 — 设置页新增 3 个 tab

- **NEW** `subagents-config`：在 SettingsModal 新增「子代理」tab，可视化配置 code/rag/web 三个子代理的启用状态、温度、system prompt、绑定工具集、触发关键词；配置通过 `electron-store` 持久化，spawn 时注入 `AGENT_PY_*` env，**重启后端生效**
- **NEW** `tools-config`：在 SettingsModal 新增「工具」tab，列出全部工具（read_file/list_dir/glob/grep/write_file/edit_file/web_search/rag_retrieve），每个工具可单独启用/禁用；禁用的工具在 DeepAgent 与子代理工具集中均不暴露
- **NEW** `memory-management`：在 SettingsModal 新增「记忆」tab，覆盖三个子模块：
  - **技能文件管理**：列出 `data/skills/*.md`、查看/编辑/删除/新建（YAML frontmatter + Markdown body）、触发 `reload_skills()`
  - **Checkpointer 控制**：显示 `data/agent_py.db` 大小、列出已持久化的 thread_id、清空指定会话状态、切换 `persist_authorized_dirs`
  - **长期用户画像**：跨会话记忆用户偏好/项目约定；DeepAgent 路径结束后调 LLM 自动抽取"值得记住的事实"写入 `data/config/profile.json`；设置页可查看/编辑/删除画像条目

### P0 — 后端配置与 API

- **NEW** 后端 `config.py` 新增子代理与工具启用配置项，从 env 读取
- **NEW** 后端 `paths/deep_path.py` 与 `subagents/*.py` 改为从 `get_settings()` 读取温度/工具集/启用状态
- **NEW** 后端 `router/graph.py` 路径 B 选择子代理时跳过禁用的子代理（静默退回路径 A）
- **NEW** 后端新增 10 个 `/api/memory/*` 端点：
  - 技能文件：`GET /api/memory/skills`（列表）、`GET /api/memory/skills/{name}`（读）、`POST /api/memory/skills`（新建/覆盖）、`DELETE /api/memory/skills/{name}`（删）共 4 个
  - 用户画像：`GET /api/memory/profile`（列表）、`POST /api/memory/profile`（新建）、`PUT /api/memory/profile/{key}`（更新）、`DELETE /api/memory/profile/{key}`（删）、`POST /api/memory/profile/extract`（LLM 抽取）共 5 个
  - Checkpointer：`GET /api/memory/checkpointer`（状态）、`DELETE /api/memory/checkpointer/{thread_id}`（清理）共 2 个（去重后实际 10 个唯一路由）

### 配置生效策略

子代理/工具配置走 `electron-store` → spawn 注入 env → 后端 `lru_cache` 重启生效（与现有 6 tab 一致）；画像/技能/checkpointer 走后端 HTTP 端点即时生效（内容频繁变化、需 LLM 动态抽取）。

## Capabilities

### New Capabilities

- `subagents-config`: 子代理可视化配置 — 启用/禁用、温度、system prompt、绑定工具集、触发关键词；electron-store 持久化，重启生效
- `tools-config`: 工具启用/禁用 — 单个工具开关，影响 DeepAgent 与子代理工具集；electron-store 持久化，重启生效
- `memory-management`: 记忆三件套 — 技能文件 CRUD（即时生效）、checkpointer 状态查看与清理、长期用户画像 LLM 自动抽取 + 手动编辑

### Modified Capabilities

无（本次全部为新增 capability，不修改已有 spec 的 requirement）。

## Impact

- **代码影响**：
  - 后端：`backend/app/config.py` 新增子代理/工具/画像配置项；`backend/app/subagents/*.py` 读取配置驱动行为；`backend/app/paths/deep_path.py` 工具集动态构建；`backend/app/router/graph.py` 路径 B 跳过禁用子代理；`backend/app/memory/` 新增 `profile_store.py` `skills_store.py` `checkpointer_view.py`；`backend/app/main.py` 新增 10 个端点
  - 前端：`frontend/renderer/components/settings/` 新增 `SubagentsSettings.tsx` `ToolsSettings.tsx` `MemorySettings.tsx`；`frontend/renderer/components/settings/SettingsModal.tsx` 扩到 9 tab；`frontend/main/store.ts` 新增 `getSubagentsConfig` `setSubagentsConfig` `getToolsConfig` `setToolsConfig`；`frontend/main/python/spawn.ts` 注入新 env；`frontend/preload/index.ts` 暴露新 IPC
- **API 影响**：新增 10 个 `/api/memory/*` 端点（skills 4 + profile 5 含 extract + checkpointer 2，去重后 10 个唯一路由）
- **依赖影响**：无新增依赖（`react-hook-form` `zod` `electron-store` `safeStorage` 均已在）
- **数据影响**：新增 `data/config/profile.json`（用户画像）、`data/skills/` 目录（已存在，本次新增 CRUD）；`data/agent_py.db` 不变（仅新增只读视图）
- **测试影响**：新增 `test_subagents_config.py` `test_tools_config.py` `test_memory_profile.py` `test_memory_skills.py` `test_memory_checkpointer.py` 单测；`test_smoke.py` 增加子代理禁用退回、工具禁用、画像抽取断言
- **运维影响**：用户画像累积需要 LLM 调用，可在设置页关闭"自动抽取"开关降级为纯手动
