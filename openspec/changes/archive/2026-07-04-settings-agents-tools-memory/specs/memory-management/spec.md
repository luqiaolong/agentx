## ADDED Requirements

### Requirement: 记忆 tab UI 结构

Renderer SHALL 在 SettingsModal 新增「记忆」tab（第 8 个 tab，位于「沙箱目录」与「日志」之间）。tab 内部用二级 tab 切换三个子模块：「技能文件」、「Checkpointer」、「用户画像」。每个子模块独立加载、独立保存，互不影响。

#### Scenario: 切换二级 tab

- **WHEN** 用户在「记忆」tab 内从「技能文件」切换到「用户画像」
- **THEN** 「技能文件」面板卸载，「用户画像」面板挂载并从 `GET /api/memory/profile` 加载画像条目

### Requirement: 技能文件列表

Renderer SHALL 在「技能文件」子模块展示 `data/skills/*.md` 文件列表，每行含：技能名、文件大小、修改时间、操作按钮（查看/编辑/删除）。列表通过 `GET /api/memory/skills` 加载。

#### Scenario: 加载技能列表

- **WHEN** 用户切到「技能文件」子模块
- **THEN** 调 `GET /api/memory/skills`，返回 `[{name, size, mtime, content_preview}]`，渲染为列表

#### Scenario: 空列表

- **WHEN** `data/skills/` 目录为空或不存在
- **THEN** 列表为空，显示「暂无技能文件，点击新建」引导

### Requirement: 技能文件查看与编辑

Renderer SHALL 提供技能文件编辑器，支持查看与编辑完整文件内容（YAML frontmatter + Markdown body）。编辑器通过 `GET /api/memory/skills/{name}` 加载内容，保存时统一调 `POST /api/memory/skills` body `{name, content}`（已存在则覆盖，不存在则新建）。

#### Scenario: 编辑已有技能

- **WHEN** 用户点击技能「search_and_summarize」的「编辑」按钮，修改 content 后点击「保存」
- **THEN** 调 `POST /api/memory/skills` body `{name: "search_and_summarize", content: "..."}`，后端覆盖 `data/skills/search_and_summarize.md` 并触发 `reload_skills()`，前端提示「已保存」

#### Scenario: 新建技能

- **WHEN** 用户点击「新建」按钮，输入名称 `code_review` 与 content 后点击「保存」
- **THEN** 调 `POST /api/memory/skills` body `{name: "code_review", content: "..."}`，后端校验名称合法后写入 `data/skills/code_review.md`，触发 `reload_skills()`

#### Scenario: 名称非法

- **WHEN** 用户输入名称 `../escape` 或 `a b c`
- **THEN** 前端 zod 校验失败，提示「名称只能含字母、数字、下划线、连字符」

### Requirement: 技能文件删除

Renderer SHALL 提供技能文件删除功能，删除前弹确认框。删除调 `DELETE /api/memory/skills/{name}`，后端删除文件并触发 `reload_skills()`。

#### Scenario: 删除技能

- **WHEN** 用户点击技能「old_skill」的「删除」按钮并确认
- **THEN** 调 `DELETE /api/memory/skills/old_skill`，后端删除 `data/skills/old_skill.md`，触发 `reload_skills()`，前端列表移除该项

#### Scenario: 删除不存在的技能

- **WHEN** 用户删除一个已被外部删除的技能文件
- **THEN** 后端返回 404，前端提示「技能文件不存在」

### Requirement: 技能文件名安全校验

后端 `memory/skills_store.py` SHALL 对技能文件名做严格校验：正则 `^[a-zA-Z0-9_-]+$`，长度 1-64。`save_skill_file` 与 `delete_skill_file` SHALL 在写入前用 `Path.resolve()` 校验最终路径在 `DATA_DIR / "skills"` 内，防止目录逃逸。

#### Scenario: 名称含路径分隔符

- **WHEN** 调 `POST /api/memory/skills` body `{name: "../etc/passwd", content: "..."}`
- **THEN** 后端返回 400，错误信息「名称只能含字母、数字、下划线、连字符」

#### Scenario: 名称含空格

- **WHEN** 调 `POST /api/memory/skills` body `{name: "a b c", content: "..."}`
- **THEN** 后端返回 400

#### Scenario: resolve 后逃逸

- **WHEN** 调 `DELETE /api/memory/skills/{name}` 其中 name 经符号链接 resolve 后指向 `data/skills` 之外
- **THEN** 后端返回 400，错误信息「路径逃逸」

### Requirement: Checkpointer 状态查看

Renderer SHALL 在「Checkpointer」子模块展示 `data/agent_py.db` 文件大小与已持久化的 thread_id 列表，每个 thread 含：thread_id、checkpoint 数量、最后更新时间。数据通过 `GET /api/memory/checkpointer` 加载。

#### Scenario: 加载 checkpointer 状态

- **WHEN** 用户切到「Checkpointer」子模块
- **THEN** 调 `GET /api/memory/checkpointer`，返回 `{db_size: 102400, threads: [{thread_id: "abc", checkpoint_count: 5, last_updated: "2026-07-04T10:00:00", size_bytes: 20480}]}`，渲染为列表

#### Scenario: 无 checkpoint

- **WHEN** `data/agent_py.db` 中无任何 checkpoint
- **THEN** 列表为空，显示「暂无持久化会话」

### Requirement: Checkpointer 单会话清理

Renderer SHALL 在每个 thread 行提供「删除」按钮，删除前弹确认框。删除调 `DELETE /api/memory/checkpointer/{thread_id}`，后端删除该 thread 的所有 checkpoint。

#### Scenario: 删除单个会话

- **WHEN** 用户点击 thread「abc」的「删除」按钮并确认
- **THEN** 调 `DELETE /api/memory/checkpointer/abc`，后端删除该 thread 的所有 checkpoint，返回 `{deleted: 5}`，前端列表移除该项

#### Scenario: thread_id 不存在

- **WHEN** 用户删除一个不存在的 thread_id
- **THEN** 后端返回 `{deleted: 0}`，前端提示「无 checkpoint 可删除」

#### Scenario: thread_id 注入攻击

- **WHEN** 调 `DELETE /api/memory/checkpointer/{thread_id}` 其中 thread_id 含 SQL 注入字符
- **THEN** 后端校验失败（正则 `^[a-zA-Z0-9_-]+$`），返回 400

### Requirement: 长期用户画像存储

后端 SHALL 在 `data/config/profile.json` 持久化用户画像，结构为 `{"entries": [{"key", "category", "content", "source", "created_at", "updated_at"}]}`。`category` 取值为 `preference` / `project` / `fact` / `custom`。`source` 取值为 `manual` / `llm_extracted`。

#### Scenario: 文件不存在

- **WHEN** `data/config/profile.json` 不存在时读取画像
- **THEN** 后端返回 `{entries: []}`，不报错

#### Scenario: 文件损坏

- **WHEN** `data/config/profile.json` 内容非法 JSON
- **THEN** 后端记 warning，返回 `{entries: []}`，不阻塞启动

### Requirement: 用户画像 CRUD API

后端 SHALL 提供 4 个端点管理画像：`GET /api/memory/profile`（列表）、`POST /api/memory/profile`（新建）、`PUT /api/memory/profile/{key}`（更新）、`DELETE /api/memory/profile/{key}`（删除）。`key` 校验正则 `^[a-zA-Z0-9_-]+$`，长度 1-64；`content` 限 500 字符。

#### Scenario: 新建画像条目

- **WHEN** 调 `POST /api/memory/profile` body `{key: "prefers_concise_reply", category: "preference", content: "用户喜欢简洁回复，不要长篇大论"}`
- **THEN** 后端写入 `data/config/profile.json`，返回 `{ok: true, entry: {...}`

#### Scenario: key 重复

- **WHEN** 调 `POST /api/memory/profile` body `{key: "prefers_concise_reply", ...}` 但该 key 已存在
- **THEN** 后端返回 409，错误信息「key 已存在，请用 PUT 更新」

#### Scenario: 更新画像条目

- **WHEN** 调 `PUT /api/memory/profile/prefers_concise_reply` body `{content: "用户喜欢简洁回复"}`
- **THEN** 后端更新该条目的 `content` 与 `updated_at`，`source` 保持原值

#### Scenario: 删除画像条目

- **WHEN** 调 `DELETE /api/memory/profile/prefers_concise_reply`
- **THEN** 后端从 `data/config/profile.json` 移除该条目

### Requirement: 用户画像 UI

Renderer SHALL 在「用户画像」子模块展示画像条目列表，每行含：key、category（下拉选择 preference/project/fact/custom）、content（可编辑文本框）、source 标签（manual/llm_extracted）、操作按钮（保存/删除）。底部提供「新建条目」按钮。

#### Scenario: 加载画像

- **WHEN** 用户切到「用户画像」子模块
- **THEN** 调 `GET /api/memory/profile`，返回 `{entries: [...]}`，渲染为可编辑列表

#### Scenario: 手动编辑保存

- **WHEN** 用户修改某条目的 content 后点击「保存」
- **THEN** 调 `PUT /api/memory/profile/{key}`，成功后提示「已保存」

#### Scenario: 手动新建

- **WHEN** 用户点击「新建条目」，输入 key/category/content 后点击「保存」
- **THEN** 调 `POST /api/memory/profile`，成功后列表新增一行

### Requirement: LLM 自动抽取用户画像

后端 SHALL 提供 `POST /api/memory/profile/extract` 端点，接收 `{thread_id, message, assistant_reply}`，调 LLM 抽取「值得跨会话记住的事实」并写入 `data/config/profile.json`。`backend/app/paths/deep_path.py` 在路径 C 流式结束后 SHALL 异步触发画像抽取（不阻塞 `done` 事件）。

#### Scenario: 抽取成功

- **WHEN** 路径 C 结束，用户消息「我用 TypeScript 写前端」，助手回复「好的，已记住」
- **THEN** 后端调 LLM 抽取 prompt，返回 `{"entries": [{"key": "uses_typescript", "category": "project", "content": "用户用 TypeScript 写前端"}]}`，写入 profile.json（`source: "llm_extracted"`）

#### Scenario: 无可抽取内容

- **WHEN** 路径 C 结束，对话为「你好」「你好，有什么可以帮你？」
- **THEN** LLM 返回 `{"entries": []}`，不写入 profile.json

#### Scenario: key 重复时更新

- **WHEN** LLM 抽取的 key `uses_typescript` 已存在
- **THEN** 后端更新该条目的 `content` 与 `updated_at`，`source` 改为 `llm_extracted`

#### Scenario: 抽取失败不报错

- **WHEN** LLM 调用失败或返回非法 JSON
- **THEN** 后端记 warning，不写入 profile.json，不向前端报错

### Requirement: 画像注入 system prompt

`backend/app/router/graph.py` `run_router` SHALL 在路径 A 与路径 C 入口读取画像条目，拼接到 system prompt 前。注入格式为「用户画像（请遵循以下偏好与约定）:」+ 每条 `- [category] content`。条目按 `updated_at` 降序排列（最近更新的优先），总数超 30 条时截断到前 30 条。

#### Scenario: 画像注入

- **WHEN** 用户画像含 `[{key: "prefers_concise", content: "简洁回复", updated_at: "2026-07-04T10:00:00"}]`，用户发送「介绍 Python」
- **THEN** 路径 A system prompt 为「用户画像（请遵循以下偏好与约定）:\n- [preference] 简洁回复\n{default_system_prompt}」

#### Scenario: 无画像不注入

- **WHEN** 用户画像为空
- **THEN** system prompt 不含画像前缀，行为不变

#### Scenario: 超过 30 条截断

- **WHEN** 用户画像含 35 条条目
- **THEN** system prompt 仅注入按 `updated_at` 降序前 30 条，第 31-35 条不注入

### Requirement: 自动抽取开关

`backend/app/config.py` SHALL 新增 `profile_auto_extract: bool = True` 字段，从 `AGENT_PY_PROFILE_AUTO_EXTRACT` env 读取。设置页「用户画像」子模块 SHALL 提供开关切换该值。开关关闭时路径 C 不触发 LLM 抽取。

#### Scenario: 关闭自动抽取

- **WHEN** 用户在设置页关闭「自动抽取」开关并保存
- **THEN** `electron-store` 中 `profileAutoExtract=false`，spawn 时注入 `AGENT_PY_PROFILE_AUTO_EXTRACT=false`，重启后端后路径 C 不调画像抽取

#### Scenario: 开启自动抽取

- **WHEN** 开关开启且路径 C 完成
- **THEN** 路径 C 异步调 `POST /api/memory/profile/extract`
