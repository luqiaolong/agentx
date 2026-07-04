## ADDED Requirements

### Requirement: 工具配置数据结构

系统 SHALL 在 `electron-store` 中持久化 `tools` 键，存储 8 个工具的启用状态：`read_file`、`list_dir`、`glob`、`grep`、`write_file`、`edit_file`、`web_search`、`rag_retrieve`。每个工具对应一个布尔值，`true` 表示启用（默认）、`false` 表示禁用。

#### Scenario: 默认全部启用

- **WHEN** 用户未配置工具，`electron-store` 中 `tools` 键不存在
- **THEN** 后端使用代码默认值（全部 `true`），所有工具在 DeepAgent 与子代理中正常暴露

#### Scenario: 序列化为 env

- **WHEN** 用户在设置页保存工具配置
- **THEN** `electron-store` 写入完整 `tools` 对象，spawn 时序列化为 `AGENT_PY_TOOLS_CONFIG` env 变量（JSON 字符串），后端 `Settings.tools_enabled` 字段反序列化为 `dict[str, bool]`

### Requirement: 工具配置 UI

Renderer SHALL 在 SettingsModal 新增「工具」tab（第 4 个 tab，位于「子代理」与「知识库」之间）。tab 内容展示 8 个工具的列表，每行含：工具名（等宽字体）、简短描述、启用/禁用开关。工具按类别分组：「文件系统（只读）」read_file/list_dir/glob/grep、「文件系统（写）」write_file/edit_file、「网络」web_search、「知识库」rag_retrieve。

#### Scenario: 禁用单个工具

- **WHEN** 用户关闭 `web_search` 开关并保存
- **THEN** `electron-store` 中 `tools.web_search=false` 持久化，提示「重启后端生效」，重启后 DeepAgent 与 web 子代理均不暴露 `web_search`

#### Scenario: 禁用危险工具

- **WHEN** 用户关闭 `edit_file` 开关并保存
- **THEN** 重启后端后 DeepAgent 工具集不含 `edit_file`，LLM 无法调用该工具；`DANGEROUS_TOOLS` 集合不变（仅控制暴露，不影响审批逻辑）

### Requirement: 后端工具配置读取

`backend/app/config.py` SHALL 新增 `tools_enabled: dict[str, bool]` 字段，从 `AGENT_PY_TOOLS_CONFIG` env 读取 JSON 字符串反序列化。默认值为全部 8 个工具 `true`。

#### Scenario: env 缺失使用默认

- **WHEN** `AGENT_PY_TOOLS_CONFIG` env 未设置
- **THEN** `settings.tools_enabled == {"read_file": True, "list_dir": True, ..., "rag_retrieve": True}`

#### Scenario: env 部分覆盖

- **WHEN** `AGENT_PY_TOOLS_CONFIG='{"web_search": false}'` env 设置
- **THEN** `settings.tools_enabled["web_search"] == False`，其余工具仍为 `True`

### Requirement: DeepAgent 工具集动态构建

`backend/app/paths/deep_path.py` `_make_deep_tools` SHALL 根据 `get_settings().tools_enabled` 过滤工具列表，仅暴露启用的工具。`run_deep_path` SHALL 在每次执行时构造 `dangerous_tools = DANGEROUS_TOOLS & set(enabled_tool_names)` 作为运行时危险工具集合（不修改模块级 `DANGEROUS_TOOLS` 常量），用于 `interrupt_before` 审批判断。

#### Scenario: 禁用写工具

- **WHEN** `tools_enabled.write_file=false` 且 `tools_enabled.edit_file=false`
- **THEN** `_make_deep_tools` 返回的工具集不含 write_file/edit_file，DeepAgent 无法调用写操作；运行时 `dangerous_tools` 集合为空，`interrupt_before` 审批流不会被触发

#### Scenario: 禁用只读工具

- **WHEN** `tools_enabled.read_file=false`
- **THEN** `_make_deep_tools` 返回的工具集不含 read_file，DeepAgent 无法读取文件

#### Scenario: DANGEROUS_TOOLS 常量不变

- **WHEN** 用户禁用 `edit_file`
- **THEN** 模块级 `DANGEROUS_TOOLS` 常量仍含 `edit_file`（代码不变），但运行时 `dangerous_tools` 不含 `edit_file`，审批流不会因 `edit_file` 触发

### Requirement: 工具禁用影响子代理

`backend/app/subagents/*.py` 的 `_make_*_tools` SHALL 根据 `get_settings().tools_enabled` 过滤工具。若子代理绑定的工具全部被禁用，`_make_*_tools` 返回空列表，子代理仍可构建但无工具可用（路径 B 命中时退回路径 A，见 `subagents-config` spec）。

#### Scenario: 子代理工具部分禁用

- **WHEN** `tools_enabled.grep=false` 且 code 子代理绑定 `[read_file, list_dir, glob, grep]`
- **THEN** `_make_fs_tools` 返回 `[read_file, list_dir, glob]`
