## ADDED Requirements

### Requirement: 子代理配置数据结构

系统 SHALL 在 `electron-store` 中持久化 `subagents` 键，存储 code/rag/web 三个子代理的配置。每个子代理配置含 5 个字段：`enabled`（布尔）、`temperature`（0.0-2.0 浮点）、`systemPrompt`（字符串，空表示用代码默认）、`tools`（工具名列表）、`keywords`（触发关键词列表，仅路径 B 分发时生效）。

#### Scenario: 默认配置零行为变更

- **WHEN** 用户未配置子代理，`electron-store` 中 `subagents` 键不存在
- **THEN** 后端使用代码默认值（code: temp=0.2/tools=[read_file,list_dir,glob,grep]/keywords=[]；rag: temp=0.2/tools=[rag_retrieve]/keywords=[知识库,文档库,检索,向量,rag,知识,文档]；web: temp=0.2/tools=[web_search]/keywords=[搜索,网页,联网,查一下,search,web,google,百度]），行为与当前硬编码一致

#### Scenario: 配置序列化为 env

- **WHEN** 用户在设置页保存子代理配置
- **THEN** `electron-store` 写入完整 `subagents` 对象，spawn 时序列化为 `AGENT_PY_SUBAGENTS_CONFIG` env 变量（JSON 字符串），后端 `Settings.subagents` 字段反序列化为嵌套 pydantic 模型

### Requirement: 子代理配置 UI

Renderer SHALL 在 SettingsModal 新增「子代理」tab（第 3 个 tab，位于「系统提示词」与「工具」之间）。tab 内容展示 code/rag/web 三个折叠卡片，每个卡片含：启用开关、温度滑块（0.0-2.0，步长 0.1）、system prompt textarea、绑定工具复选框组（从全部 8 个工具中勾选）、关键词输入框（逗号分隔）。

#### Scenario: 切换启用状态

- **WHEN** 用户在 code 子代理卡片关闭「启用」开关并保存
- **THEN** `electron-store` 中 `subagents.code.enabled=false` 持久化，提示「重启后端生效」

#### Scenario: 调整温度

- **WHEN** 用户拖动 rag 子代理温度滑块到 0.5 并保存
- **THEN** `electron-store` 中 `subagents.rag.temperature=0.5` 持久化，重启后端后 `build_rag_agent` 使用 0.5 温度

#### Scenario: 绑定工具全部被禁用警告

- **WHEN** 用户在 code 子代理绑定的工具 `[read_file, list_dir]` 中，`read_file` 和 `list_dir` 在「工具」tab 均被禁用
- **THEN** code 子代理卡片显示黄色警告「绑定的工具全部被禁用，子代理将不可用」

### Requirement: 后端子代理配置读取

`backend/app/config.py` SHALL 新增 `SubagentSettings` pydantic 嵌套模型与 `subagents: dict[str, SubagentSettings]` 字段，从 `AGENT_PY_SUBAGENTS_CONFIG` env 读取 JSON 字符串反序列化。`backend/app/subagents/*.py` 的 `build_*_agent` 函数 SHALL 从 `get_settings().subagents[agent_name]` 读取温度与 system prompt，覆盖硬编码默认值。

#### Scenario: 配置缺失回退默认

- **WHEN** `AGENT_PY_SUBAGENTS_CONFIG` env 未设置
- **THEN** `settings.subagents` 使用代码默认值，`build_code_agent` 使用 temperature=0.2

#### Scenario: 配置覆盖

- **WHEN** `AGENT_PY_SUBAGENTS_CONFIG='{"code":{"enabled":false,"temperature":0.2,"systemPrompt":"","tools":["read_file"],"keywords":[]}}'` env 设置
- **THEN** `settings.subagents.code.enabled == False`，`build_code_agent` 不被调用（路径 B 命中 code 关键词时退回路径 A）

### Requirement: 子代理工具集动态过滤

`backend/app/subagents/*.py` 的 `_make_*_tools` 函数 SHALL 根据 `get_settings().tools_enabled` 过滤工具列表，仅暴露启用的工具。若子代理绑定的工具全部被禁用，`_make_*_tools` SHALL 返回空列表。

#### Scenario: 部分工具禁用

- **WHEN** `tools_enabled.grep=false` 且 code 子代理绑定 `[read_file, list_dir, glob, grep]`
- **THEN** `_make_fs_tools` 返回 `[read_file, list_dir, glob]`，grep 不暴露给 LLM

#### Scenario: 全部工具禁用

- **WHEN** code 子代理绑定的 4 个工具全部 `tools_enabled=false`
- **THEN** `_make_fs_tools` 返回空列表，`build_code_agent` 仍可构建但无工具可用

### Requirement: 路径 B 子代理禁用静默退回路径 A

`backend/app/router/graph.py` `_select_subagent` 函数 SHALL 在以下情况返回 `None` 退回路径 A（CHAT）：(1) 命中的子代理 `enabled=false`；(2) 命中的子代理绑定的工具全部被禁用。`run_router` 中 `_select_subagent` 返回 `None` 时 SHALL 走 `_run_chat_path`，不报错。

#### Scenario: 子代理禁用退回

- **WHEN** 用户消息含「搜索网页」匹配 web 关键词，但 `subagents.web.enabled=false`
- **THEN** `_select_subagent` 返回 `None`，`run_router` 走路径 A，LLM 直接回答

#### Scenario: 工具全部禁用退回

- **WHEN** 用户消息含「知识库」匹配 rag 关键词，但 `subagents.rag.tools=[rag_retrieve]` 且 `tools_enabled.rag_retrieve=false`
- **THEN** `_select_subagent` 返回 `None`，`run_router` 走路径 A

### Requirement: 子代理关键词可配置

`backend/app/router/graph.py` SHALL 从 `get_settings().subagents[agent_name].keywords` 读取触发关键词，替换硬编码的 `_WEB_KEYWORDS` 与 `_RAG_KEYWORDS`。code 子代理的 keywords 默认为空（code 是 fallback，无关键词）。

#### Scenario: 自定义关键词

- **WHEN** 用户在 web 子代理关键词中添加「帮我查」并保存
- **THEN** 重启后端后，用户消息「帮我查天气」命中 web 子代理，走路径 B

#### Scenario: 关键词为空

- **WHEN** web 子代理 keywords 配置为空列表
- **THEN** `_select_subagent` 不匹配任何子代理，所有消息退回路径 A 或路径 C
