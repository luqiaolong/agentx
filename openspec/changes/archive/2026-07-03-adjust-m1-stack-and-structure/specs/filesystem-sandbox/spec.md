## ADDED Requirements

### Requirement: 会话级授权目录机制

系统 SHALL 维护 `SessionSandbox` 类，按 `thread_id` 隔离授权目录列表。沙箱白名单 = `data/workspace` + `data/uploads` + 当前会话授权目录集合。filesystem 工具的 read 操作 MUST 在执行前调用 `SessionSandbox.check_read(path)`，写操作 MUST 调用 `SessionSandbox.check_write(path)`。

#### Scenario: 默认白名单始终可读
- **WHEN** DeepAgent 调用 `read_file("data/workspace/foo.txt")` 且会话无授权目录
- **THEN** `check_read` 返回通过，文件读取正常执行

#### Scenario: 未授权路径拒绝读取
- **WHEN** DeepAgent 调用 `read_file("d:/secrets/passwords.txt")` 且该路径未在会话授权目录中
- **THEN** `check_read` 抛 `PathNotAuthorized`，filesystem 工具返回错误 `"路径 d:/secrets/passwords.txt 未授权，请通过 dialog 选择目录后重试"`，DeepAgent 在 state 中记录错误

#### Scenario: 授权目录可读但不可写
- **WHEN** 用户授权 `d:/docs` 目录（默认 read-only）且 DeepAgent 调用 `write_file("d:/docs/out.txt", ...)`
- **THEN** `check_write` 抛 `PathNotAuthorized`，filesystem 工具返回错误 `"路径 d:/docs 仅授权读取，写入请使用 data/workspace 或在授权时勾选允许写入"`

### Requirement: 授权生命周期管理

系统 SHALL 通过 `/api/sandbox/authorize` 端点接受授权请求，授权与当前 `thread_id` 绑定。**"会话结束"明确定义为**：用户显式点击"删除会话"（thread_id 永久删除）或执行 `/reset` 命令。关闭 chat tab、退出应用 **不** 触发会话结束（因 checkpoint 保留，授权应跨重开恢复）。

授权撤销时机：
- 用户显式 `POST /api/sandbox/revoke` 主动撤销
- 会话结束时（按上述定义）自动 `clear(thread_id)`
- "跨会话保留授权目录"开关关闭时，会话结束触发 clear；开关开启时，会话结束**不** clear（写入 checkpoint 持久化）

#### Scenario: 用户授权目录
- **WHEN** Renderer 调用 `POST /api/sandbox/authorize` body `{"thread_id": "abc", "path": "d:/docs", "writable": false}`
- **THEN** 系统将 `d:/docs` 加入 thread_id=abc 的 `SessionSandbox.authorized_dirs`，返回 `{"authorized": true, "path": "d:/docs", "writable": false}`

#### Scenario: 授权可写目录
- **WHEN** 授权请求 `writable=true`
- **THEN** 授权目录可读可写，但 `data/workspace` 与 `data/uploads` 仍是唯一默认可写路径

#### Scenario: 显式撤销授权
- **WHEN** Renderer 调用 `POST /api/sandbox/revoke` body `{"thread_id": "abc", "path": "d:/docs"}`
- **THEN** 系统从 thread_id=abc 的授权列表中移除 `d:/docs`，返回 `{"revoked": true}`，后续对该路径的访问被拒绝

#### Scenario: 删除会话时撤销（开关开启，默认）
- **WHEN** 用户点击"删除会话"删除 thread_id=abc，且 Settings 中"跨会话保留授权目录"开关为**开启**（默认）
- **THEN** 系统调用 `SessionSandbox.clear(thread_id)`，授权目录列表清空，checkpoint 中 `authorized_dirs` 字段同步清空（删除会话等于彻底销毁，无论开关状态）

#### Scenario: /reset 时撤销（开关关闭）
- **WHEN** 用户执行 `/reset` 重置 thread_id=abc，且 Settings 中"跨会话保留授权目录"开关为**关闭**
- **THEN** 系统调用 `SessionSandbox.clear(thread_id)`，授权目录列表清空，checkpoint 中 `authorized_dirs` 字段同步清空

#### Scenario: /reset 时保留（开关开启，默认）
- **WHEN** 用户执行 `/reset` 重置 thread_id=abc，且 Settings 中"跨会话保留授权目录"开关为**开启**（默认）
- **THEN** 系统**不**调用 `clear`，授权目录列表写入 checkpoint `authorized_dirs` 字段，下次进入同 thread_id 时自动恢复

#### Scenario: 关闭 chat tab 不触发撤销
- **WHEN** 用户关闭 thread_id=abc 的 chat tab（thread_id 仍保留在 sidebar）
- **THEN** 系统**不**调用 `SessionSandbox.clear`，授权目录列表保留在内存与 checkpoint 中

#### Scenario: 退出应用不触发撤销
- **WHEN** 用户退出应用（process 退出）
- **THEN** 系统**不**调用 `SessionSandbox.clear`，授权目录列表已写入 checkpoint，下次启动进入同 thread_id 时自动恢复

### Requirement: 授权持久化与恢复

系统 SHALL 将授权目录列表写入 LangGraph `RouterState.authorized_dirs: list[str]`，会话重开时从 checkpoint 恢复授权状态。

#### Scenario: 重开会话授权恢复
- **WHEN** 用户关闭应用后重开，进入同一 thread_id 会话
- **THEN** `SessionSandbox` 从 checkpoint 加载 `authorized_dirs`，授权目录继续生效

#### Scenario: 跨会话授权开关关闭时 /reset 触发 clear
- **WHEN** 用户在 Settings 中关闭"跨会话保留授权目录"开关，并执行 `/reset`
- **THEN** 系统调用 `clear(thread_id)`，不写入 checkpoint `authorized_dirs` 字段（与上文 "/reset 时撤销（开关关闭）" scenario 一致）

### Requirement: 授权目录路径规范化与校验

系统 MUST 在授权前对路径做规范化处理（resolve `..` / 符号链接 / 大小写），MUST NOT 授权系统关键目录（如 `C:/Windows` / `C:/Program Files` / 用户主目录根）。

#### Scenario: 路径规范化
- **WHEN** 授权请求 path 为 `d:/docs/../secrets`
- **THEN** 系统规范化为 `d:/secrets` 后再加入授权列表，后续 `check_read("d:/docs/../secrets/x")` 也能通过（先规范化再比对）

#### Scenario: 系统关键目录拒绝授权
- **WHEN** 授权请求 path 为 `C:/Windows/System32`
- **THEN** 系统返回 400 错误 `"路径 C:/Windows/System32 是系统关键目录，不可授权"`，授权列表不变

#### Scenario: 符号链接解析
- **WHEN** 授权请求 path 为 `d:/link`（符号链接指向 `d:/real`）
- **THEN** 系统解析为 `d:/real` 后授权，访问 `d:/link/x` 与 `d:/real/x` 均通过

### Requirement: 授权操作 LangSmith 追踪

系统 SHALL 在每次授权/撤销/拒绝访问时记录 LangSmith trace 事件，包含 `thread_id` / `path` / `writable` / `action` 字段。

#### Scenario: 授权操作被追踪
- **WHEN** 用户授权目录 `d:/docs`
- **THEN** LangSmith 中可见 `sandbox.authorize` 事件，metadata 含 `thread_id` / `path=d:/docs` / `writable=false` / `action=authorize`

#### Scenario: 拒绝访问被追踪
- **WHEN** DeepAgent 尝试访问未授权路径 `d:/secrets`
- **THEN** LangSmith 中可见 `sandbox.deny` 事件，metadata 含 `thread_id` / `path=d:/secrets` / `action=deny_read`
