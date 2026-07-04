## ADDED Requirements

### Requirement: 技能列表 API

系统 SHALL 提供 `GET /api/skills` 端点返回已加载技能列表，每项含 `name` / `description` / `trigger` / `tools` / `content_preview`（前 200 字符）。技能从 `data/skills/*.md` 加载，由 `skills_loader.get_skills()` 提供（带缓存）。

#### Scenario: 获取技能列表
- **WHEN** Renderer 调用 `GET /api/skills`
- **THEN** 返回 `{"skills": [{"name": "...", "description": "...", "trigger": "...", "tools": [...], "content_preview": "..."}]}`，`data/skills/` 不存在时返回空列表

#### Scenario: 技能目录不存在
- **WHEN** `data/skills/` 目录不存在且 Renderer 调用 `GET /api/skills`
- **THEN** 返回 `{"skills": []}`，HTTP 200，不抛异常

### Requirement: 技能重载 API

系统 SHALL 提供 `POST /api/skills/reload` 端点强制重载技能列表（清缓存重新扫描 `data/skills/`），供开发态使用。

#### Scenario: 重载技能
- **WHEN** Renderer 调用 `POST /api/skills/reload`
- **THEN** 系统调用 `reload_skills()`，返回 `{"reloaded": true, "count": <n>}`

### Requirement: @skill 触发与 system prompt 注入

系统 SHALL 在 `run_router` 入口解析用户消息中的 `@skill:<name>` 标记，从 `get_skills()` 取对应技能的 `content`，拼接到路径 system prompt 前。标记移除后剩余文本作为用户消息传入 LLM/agent。单 skill content 超过 4000 字符时截断并追加 `\n[skill content truncated]`。

#### Scenario: 触发技能
- **WHEN** 用户消息为 `@skill:search_and_summarize 帮我搜索 LangGraph`
- **THEN** Router 解析出 skill_name=`search_and_summarize`，取 `skill.content` 拼接到 system prompt，用户消息变为 `帮我搜索 LangGraph`，按正常分类路由

#### Scenario: 技能不存在
- **WHEN** 用户消息为 `@skill:nonexistent 做某事`
- **THEN** Router 不注入任何 skill content，用户消息变为 `做某事`，按正常分类路由，并在 SSE 流中先 yield `{"event": "token", "data": "[技能 nonexistent 未找到]"}`

#### Scenario: 无 @skill 标记
- **WHEN** 用户消息为 `帮我分析这个文件`
- **THEN** Router 不做技能注入，按正常分类路由

### Requirement: @skill 解析只匹配首个标记

系统 MUST 只解析用户消息中首个 `@skill:<name>` 标记，多个标记时忽略后续标记并在首 token 前提示 `[检测到多个 @skill 标记，仅使用首个]`。

#### Scenario: 多标记
- **WHEN** 用户消息为 `@skill:a @skill:b 做某事`
- **THEN** Router 注入 skill `a` 的 content，用户消息变为 `做某事`，SSE 先 yield 提示 token

### Requirement: preload 暴露技能 API

系统 SHALL 在 preload `contextBridge` 暴露 `window.api.skills.list()` 与 `window.api.skills.reload()`，分别调 `GET /api/skills` 与 `POST /api/skills/reload`。

#### Scenario: preload 暴露
- **WHEN** Renderer 调用 `window.api.skills.list()`
- **THEN** 返回 `GET /api/skills` 的 JSON 响应

### Requirement: 技能选择浮层

Renderer SHALL 在用户输入框输入 `@` 时弹出技能选择浮层，展示 `GET /api/skills` 返回的技能列表，选中后插入 `@skill:<name>` 到输入框光标位置。

#### Scenario: 弹出浮层
- **WHEN** 用户在输入框输入 `@` 且光标在行首或前一个字符是空格
- **THEN** 弹出技能选择浮层，展示技能 name + description

#### Scenario: 选择技能插入
- **WHEN** 用户在浮层中选中技能 `search_and_summarize`
- **THEN** 输入框光标位置插入 `@skill:search_and_summarize `（末尾带空格），浮层关闭

#### Scenario: 取消浮层
- **WHEN** 用户按 Esc 或点击浮层外
- **THEN** 浮层关闭，输入框 `@` 保留
