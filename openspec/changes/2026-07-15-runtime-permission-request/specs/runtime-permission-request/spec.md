# Spec: Runtime Permission Request

## Purpose

允许 LLM 在工具执行因权限不足失败后，主动调用 `request_permission` 工具申请路径授权，由用户在执行轨迹节点右下角审批，而非直接停止执行。

## Requirements

### REQ-RPR-1: `request_permission` 工具必须注册到 agent 工具集

`make_deep_tools` 返回的工具列表必须包含 `request_permission` 工具，且工具 docstring 必须明确描述触发条件（`Permission denied` / `EACCES` / `PathNotAuthorized` / `[SANDBOX_ESCALATION]` hint）和参数语义（`path` / `writable` / `reason`）。

#### Scenario: LLM 可调用 request_permission

- **Given** agent 工具集已构建（`make_deep_tools` 返回）
- **When** LLM 查看可用工具列表
- **Then** 列表中存在 `request_permission` 工具
- **And** 工具 docstring 含触发条件说明
- **And** 工具参数为 `path: str`、`writable: bool = False`、`reason: str = ""`

### REQ-RPR-2: `request_permission` 必须触发 `interrupt_on` 审批中断

`request_permission` 必须在 `DANGEROUS_TOOLS` 集合中，使 `build_interrupt_config` 生成 `{"request_permission": True}`，触发 `HumanInTheLoopMiddleware.after_model` interrupt。

#### Scenario: LLM 调用 request_permission 后中断

- **Given** LLM 生成 `request_permission` tool_call
- **When** LangGraph 执行到 tools 节点前
- **Then** `HumanInTheLoopMiddleware.after_model` 触发 interrupt
- **And** `approval_runner` 检测到 `state.interrupts` 非空

### REQ-RPR-3: approval_runner 必须拦截 `request_permission` 并发 approval_request

`run_agent_with_approval` 在中断处理分支里，检测到 pending_calls 含 `request_permission` 时，必须：
1. 从 args 解析 `path` / `writable` / `reason`
2. 根据 path 是否为空判断 kind：有 path → `directory_extension`；无 path → `sandbox_escalation`
3. 发 `approval_request` SSE 事件（携带 `approval_id` + `run_id` + `tool_call_id`）
4. 等待用户审批（`_await_approval`）

#### Scenario: 有路径的 request_permission

- **Given** LLM 调用 `request_permission(path="/usr/lib/node_modules", writable=True, reason="npm install")`
- **When** approval_runner 处理中断
- **Then** 发 `approval_request` 事件，`kind="directory_extension"`
- **And** 事件含 `requestedPath="/usr/lib/node_modules"`、`writable=true`
- **And** 事件含 `approval_id` + `run_id` + `tool_call_id`

#### Scenario: 无路径的 request_permission

- **Given** LLM 调用 `request_permission(path="", reason="进程创建被沙箱阻止")`
- **When** approval_runner 处理中断
- **Then** 发 `approval_request` 事件，`kind="sandbox_escalation"`
- **And** 事件含 `reason="进程创建被沙箱阻止"`

### REQ-RPR-4: 审批通过后必须授权路径并 resume

用户审批通过后，approval_runner 必须：
1. 根据 decision 类型调用 `sandbox.authorize_temp`（once/approve）或 `sandbox.authorize`（session）或 `sandbox.authorize` + `set_full_trust`（full_trust）
2. `Command(resume=approve)` 恢复执行
3. `request_permission` 工具执行体返回「路径已授权」确认消息
4. LLM 看到确认消息后可重新调用原失败的工具

#### Scenario: 用户点击「本次允许」

- **Given** approval_runner 已发 `approval_request` 事件
- **When** 用户点击「允许沙箱执行」按钮（decision="approve"）
- **Then** `sandbox.authorize_temp(thread_id, path, writable)` 被调用
- **And** `Command(resume=approve)` 恢复执行
- **And** `request_permission` 工具返回 `f"路径已授权: {path} (writable={writable})"`

#### Scenario: 用户点击「会话内允许」

- **Given** approval_runner 已发 `approval_request` 事件
- **When** 用户选择「会话内允许」（decision="session"）
- **Then** `sandbox.authorize(thread_id, path, writable)` 被调用
- **And** `Command(resume=approve)` 恢复执行

### REQ-RPR-5: 审批拒绝后必须注入 error 并 resume reject

用户拒绝或审批超时后，approval_runner 必须：
1. 为每个 `request_permission` tool_call 注入 error ToolMessage
2. `Command(resume=reject)` 消费 HITL interrupt
3. 发 `error` SSE 事件
4. LLM 看到错误后可换路径或停止

#### Scenario: 用户拒绝授权

- **Given** approval_runner 已发 `approval_request` 事件
- **When** 用户拒绝（decision="deny"）或超时（decision=None）
- **Then** 为每个 `request_permission` 注入 `ToolMessage(content="用户拒绝授权路径: {path}")`
- **And** `Command(resume=reject, message="用户拒绝授权路径")` 消费 interrupt
- **And** 发 `error` SSE 事件

### REQ-RPR-6: `sandbox_mode == "off"` 必须绕过沙箱重试

`safe_shell_backend.execute` 在沙箱执行失败 + `analyze_sandbox_failure.is_sandbox_limit == True` + `sandbox_mode == "off"` 时，必须用 `subprocess.run` 绕过沙箱重试一次，不追加 `[SANDBOX_ESCALATION]` hint。

#### Scenario: sandbox_mode off 时沙箱失败

- **Given** `sandbox_mode == "off"`
- **And** `execute("npm install")` 沙箱执行失败（exit_code != 0）
- **And** `analyze_sandbox_failure` 命中沙箱限制模式
- **When** `safe_shell_backend.execute` 处理失败
- **Then** 用 `subprocess.run(command, shell=True)` 绕过沙箱重试
- **And** 返回 `ExecuteResponse` 含重试结果
- **And** 不追加 `[SANDBOX_ESCALATION]` hint

### REQ-RPR-7: `sandbox_mode != "off"` 时 hint 必须含 request_permission 提示

`safe_shell_backend.execute` 在沙箱执行失败 + `analyze_sandbox_failure.is_sandbox_limit == True` + `sandbox_mode != "off"` 时，`[SANDBOX_ESCALATION]` hint 必须追加「可调用 `request_permission(path, writable, reason)` 工具申请路径授权后重试」提示。

#### Scenario: sandbox_mode sandbox 时沙箱失败

- **Given** `sandbox_mode == "sandbox"`
- **And** `execute("npm install")` 沙箱执行失败
- **And** `analyze_sandbox_failure` 命中
- **When** `safe_shell_backend.execute` 处理失败
- **Then** 返回的 output 含 `[SANDBOX_ESCALATION]` hint
- **And** hint 含 `reason`、`suggested_action`、`suggested_path`（如适用）
- **And** hint 含「可调用 request_permission(path, writable, reason) 工具申请路径授权后重试」

### REQ-RPR-8: 前端零改动，复用现有 approvalRequest 按钮

`request_permission` 的 `approval_request` 事件必须复用现有 `attachApprovalToToolCall` 机制，自动挂载到对应 `ToolCallCard`，右下角展示「允许沙箱执行」按钮 + 下拉菜单（本次允许 / 会话内允许 / 允许所有操作）。

#### Scenario: 前端自动展示按钮

- **Given** 后端发 `approval_request` 事件（含 `tool_call_id`）
- **When** 前端 `useChatStream` 收到事件
- **Then** `attachApprovalToToolCall` 把 `approvalRequest` 挂载到对应 tool-call part
- **And** `ToolCallCard` 右下角展示「允许沙箱执行」按钮
- **And** 用户点击 → `POST /api/chat/approve`

### REQ-RPR-9: 多个 request_permission 批量处理

LLM 在一次 turn 里调用多个 `request_permission` 时，approval_runner 必须批量发 approval_request 事件 + 统一等待一个 decision + 批量授权。

#### Scenario: 两个路径同时申请

- **Given** LLM 调用 `request_permission(path="/pathA")` + `request_permission(path="/pathB")`
- **When** approval_runner 处理中断
- **Then** 发两个 `approval_request` 事件
- **And** 等待一个 decision
- **And** 审批通过后 `sandbox.authorize_temp` 对两个路径都调用

### REQ-RPR-10: 所有 approval 分支必须注册活跃审批请求

approval_runner 在**所有** approval 分支（`request_permission` / `dangerous_tool` / `directory_extension`）发 approval_request 事件前，必须调用 `register_approval_request(thread_id, run_id, kind, tool_call_id)` 生成 `approval_id` + `run_id`，并传入 `_make_approval_event`。这修复已有 bug：`register_approval_request` 在生产代码中未被调用，导致 `chat_approve` 端点 403。

#### Scenario: approval_request 事件携带 approval_id + run_id

- **Given** approval_runner 检测到 `request_permission`（或 `dangerous_tool` / `directory_extension`）工具调用
- **When** 发 `approval_request` SSE 事件
- **Then** 事件 payload 含 `approval_id`（UUID4）
- **And** 事件 payload 含 `run_id`（= `current_trace_id()`）
- **And** `register_approval_request` 已被调用，活跃请求已注册

#### Scenario: 前端审批按钮点击后 consume 成功

- **Given** 用户点击「允许沙箱执行」按钮
- **When** `POST /api/chat/approve { approval_id, run_id }` 提交
- **Then** `consume_approval(approval_id, run_id)` 返回非 None
- **And** 审批决策写入 `submit_approval`
- **And** approval_runner `_await_approval` 收到决策
