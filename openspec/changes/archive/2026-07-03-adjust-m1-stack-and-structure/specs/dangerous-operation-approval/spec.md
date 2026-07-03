## ADDED Requirements

### Requirement: 危险操作列表与 interrupt 触发

系统 SHALL 对以下"危险操作"在执行前通过 LangGraph `interrupt_on` 暂停 DeepAgent 执行，等待用户审批：`edit_file` / `write_file` / `shell_exec` / 任何 shell 命令工具。暂停 MUST 是**无限期**的（不设默认超时），直至用户显式批准或拒绝。

#### Scenario: write_file 触发 interrupt
- **WHEN** DeepAgent 调用 `write_file("data/workspace/out.txt", content)` 工具
- **THEN** LangGraph 在该 tool node 前 `interrupt`，DeepAgent state 进入 `awaiting_approval`，工具**不**执行

#### Scenario: 非危险工具不触发 interrupt
- **WHEN** DeepAgent 调用 `read_file` / `list_dir` / `glob` / `grep` / `web_search` 等非危险工具
- **THEN** 工具正常执行，不触发 interrupt

#### Scenario: shell_exec 触发 interrupt
- **WHEN** DeepAgent 调用 `shell_exec("git status")` 或任何 shell 命令工具
- **THEN** LangGraph 在该 tool node 前 `interrupt`，DeepAgent state 进入 `awaiting_approval`，命令**不**执行

### Requirement: 审批 UI 推送流程

系统 SHALL 在 DeepAgent 进入 `awaiting_approval` 状态时通过 SSE 推送 `approval_request` 事件到 Renderer，Renderer 显示 `ApprovalDialog` 让用户审批。事件包含 `tool_name` / `args` / `preview`（如 diff 或命令文本）字段。

#### Scenario: 推送审批事件
- **WHEN** DeepAgent 进入 `awaiting_approval` 状态
- **THEN** FastAPI 通过 SSE 推送 `{"event": "approval_request", "tool_name": "write_file", "args": {"path": "...", "content": "..."}, "preview": "<diff 或命令文本>"}` 到 Renderer

#### Scenario: 用户批准后继续执行
- **WHEN** 用户在 `ApprovalDialog` 点击"批准"
- **THEN** Renderer 通过 IPC 调 `POST /api/chat/approve` body `{"thread_id": "abc", "approval": true}`，LangGraph `Command(resume=...)` 恢复执行，原工具调用继续

#### Scenario: 用户拒绝后转错误
- **WHEN** 用户在 `ApprovalDialog` 点击"拒绝"
- **THEN** Renderer 调 `POST /api/chat/approve` body `{"thread_id": "abc", "approval": false}`，LangGraph 把工具调用标记为失败（`"用户拒绝执行该操作"`），DeepAgent 在 state 中记录错误并继续规划（可重试或换路径）

### Requirement: auto_approve_after_seconds 配置

系统 SHALL 提供可选配置 `AGENT_PY_AUTO_APPROVE_AFTER_SECONDS`（默认 0=禁用），>0 时在 `approval_request` 推送后启动倒计时定时器，到时自动批准。

#### Scenario: 默认禁用自动批准
- **WHEN** `AGENT_PY_AUTO_APPROVE_AFTER_SECONDS=0`（默认）且审批请求推送
- **THEN** 不启动倒计时，无限期等待用户操作

#### Scenario: 配置自动批准倒计时
- **WHEN** `AGENT_PY_AUTO_APPROVE_AFTER_SECONDS=30` 且审批请求推送
- **THEN** Renderer `ApprovalDialog` 显示 30s 倒计时，倒计时归零后自动调 `POST /api/chat/approve approval=true`，用户可在归零前手动批准/拒绝（手动操作取消倒计时）

#### Scenario: 用户操作取消倒计时
- **WHEN** 倒计时进行中用户点击"批准"或"拒绝"
- **THEN** 倒计时立即取消，按用户操作执行（不等待归零）

### Requirement: 审批操作 LangSmith 追踪

系统 SHALL 在每次审批/拒绝时记录 LangSmith trace 事件，包含 `thread_id` / `tool_name` / `action` / `auto_approved` 字段，但 MUST NOT 包含工具参数原文（避免泄漏敏感文件内容或命令）。

#### Scenario: 用户批准被追踪
- **WHEN** 用户显式点击"批准"
- **THEN** LangSmith 中可见 `approval.user_approve` 事件，metadata 含 `thread_id` / `tool_name=write_file` / `action=approve` / `auto_approved=false`，args 字段为 `<redacted>`

#### Scenario: 自动批准被追踪
- **WHEN** 倒计时归零触发自动批准
- **THEN** LangSmith 中可见 `approval.auto_approve` 事件，metadata 含 `thread_id` / `tool_name` / `action=approve` / `auto_approved=true`

#### Scenario: 用户拒绝被追踪
- **WHEN** 用户点击"拒绝"
- **THEN** LangSmith 中可见 `approval.user_reject` 事件，metadata 含 `thread_id` / `tool_name` / `action=reject`
