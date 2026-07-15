# Proposal: 运行时权限申请 — 节点右下角申请权限而非直接停止

## Why

当前 agent 执行 `execute` / `write_file` / `edit_file` 等工具时，若命中沙箱限制（`Permission denied` / `EACCES` / `PathNotAuthorized` / `CreateProcess` 拦截），处理链路存在两个问题：

1. **`safe_shell_backend.execute` 仅追加 hint，不触发审批**：`analyze_sandbox_failure` 在 [safe_shell_backend.py:172](file:///d:/java/agentprojects/agentx/backend/app/deepagent/safe_shell_backend.py#L172) 命中后，只把 `[SANDBOX_ESCALATION]` 文本追加到命令输出返回给 LLM。LLM 看到文本后只能换路径或放弃，**用户无法在执行轨迹节点上申请权限**。
2. **前端 error 节点无按钮**：`tool_result` 事件带 error 发给前端，`ToolCallCard` 显示为 error 态，`approvalRequest` 未挂载，右下角不展示「申请权限」按钮。用户感知为「直接停止」。
3. **沙箱模式 `off` 仍走拦截逻辑**：`sandbox_mode == "off"`（允许沙箱外执行）时，`safe_shell_backend` 仍调用沙箱执行路径，命中限制后仍追加 hint，本应直接绕过沙箱重试。

用户期望：执行轨迹节点在执行中遇到权限错误时，可以在该节点右下角申请权限（而非直接停止），点击后授权路径并基于 checkpoint resume 重试工具调用；沙箱模式 `off` 时不拦截。

## What Changes

### R1. 新增 `request_permission` 工具

- 在 `make_deep_tools` 中注册 `request_permission(path, writable, reason)` 工具
- 工具 docstring 明确告知 LLM：当工具因权限不足（`Permission denied` / `EACCES` / `PathNotAuthorized` / `[SANDBOX_ESCALATION]` hint）失败时，调用此工具申请路径授权
- 工具执行体本身只返回占位字符串，**真正的授权在 approval_runner 层完成**
- 把 `request_permission` 加入 `DANGEROUS_TOOLS` 集合，触发 `interrupt_on` 审批中断

### R2. approval_runner 拦截 `request_permission`

- 在 `run_agent_with_approval` 中断处理分支里，检测 pending_calls 含 `request_permission` 时：
  - 从 args 解析 `path` / `writable` / `reason`
  - 根据 `reason` 内容判断 kind：含路径 → `directory_extension`；纯命令权限 → `sandbox_escalation`
  - 发 `approval_request` SSE 事件（复用 `_make_approval_event`，携带 `approval_id` + `run_id`）
  - 等待用户审批（`_await_approval`）
  - 审批通过 → `sandbox.authorize` / `sandbox.authorize_temp` + `Command(resume=approve)` → 工具返回「路径已授权」
  - 审批拒绝 → 注入 error ToolMessage + `Command(resume=reject)`

### R3. 修改 `safe_shell_backend` 沙箱失败处理

- `sandbox_mode == "off"` 时：沙箱执行失败且 `analyze_sandbox_failure.is_sandbox_limit == True` → 直接绕过沙箱用 `subprocess.run` 重试一次，不追加 hint
- `sandbox_mode != "off"` 时：在 `[SANDBOX_ESCALATION]` hint 里追加提示「可调用 `request_permission(path, writable, reason)` 工具申请权限」

### R4. 前端零改动

- `approval_request` 事件复用现有 `attachApprovalToToolCall` 机制，自动挂载到 `request_permission` 工具调用对应的 `ToolCallCard`
- `ToolCallCard` 右下角按钮（「允许沙箱执行」+ 下拉菜单）自动展示
- 用户点击 → `POST /api/chat/approve` → `consume_approval` → `submit_approval` → approval_runner resume

### R5. LLM 自行判断

- LLM 看到工具失败 hint 后，自行决定是否调用 `request_permission`（符合「让 LLM 自行判断」）
- `request_permission` 工具 docstring 是 LLM 判断的依据，需明确描述触发条件和参数语义

## Capabilities

### New Capabilities

- `runtime-permission-request`

### Modified Capabilities

- 无（复用现有 `approval-request-lifecycle` + `sse-event-contract`）

## Impact

- **后端**：
  - `backend/app/deepagent/tool_assembly.py` — `make_deep_tools` 新增 `request_permission` 工具
  - `backend/app/security/dangerous_tools.py` — `DANGEROUS_TOOLS` 集合新增 `request_permission`
  - `backend/app/deepagent/approval_runner.py` — 中断处理分支新增 `request_permission` 拦截逻辑；**所有 approval 分支**发事件前调用 `register_approval_request` 生成 approval_id + run_id（修复已有 bug：`register_approval_request` 在生产代码中未被调用，导致 `chat_approve` 端点 403）
  - `backend/app/deepagent/safe_shell_backend.py` — `sandbox_mode == "off"` 绕过沙箱 + hint 追加 `request_permission` 提示
  - `backend/app/security/approval/flow.py` — `_make_approval_event` 新增 `reason` 可选参数（sandbox_escalation kind 时附加）；`_handle_directory_extension` 调用 `register_approval_request`
- **前端**：零改动（复用现有 approvalRequest 按钮 + SSE 事件解析）
- **测试**：
  - 新增 `tests/python/unit/test_request_permission_tool.py` — 工具注册 + docstring
  - 新增 `tests/python/unit/test_approval_runner_request_permission.py` — approval_runner 拦截逻辑 + approval_id/run_id 注册
  - 修改 `tests/python/unit/test_safe_shell_backend.py` — `sandbox_mode == "off"` 绕过沙箱 + hint 提示
  - 修改 `tests/python/unit/test_dangerous_tools.py` — `DANGEROUS_TOOLS` 含 `request_permission`

## Rollback Plan

1. `request_permission` 工具注册是增量改动，回滚只需从 `DANGEROUS_TOOLS` 移除并从 `make_deep_tools` 删除工具定义
2. `safe_shell_backend` 改动独立，可单独回滚
3. approval_runner 的 `request_permission` 拦截分支是新增 if 块，删除即回滚
4. 前端零改动，无回滚需求

## Out of Scope

- 不修改 deepagents / LangGraph 图结构（只加工具 + 拦截逻辑）
- 不新增 HTTP 端点（复用 `/api/chat/approve`）
- 不处理 OS 级管理员权限（UAC / sudo），仅处理沙箱路径授权
- 不改动 `directory_extension` 预检查逻辑（`request_permission` 是 LLM 主动申请，与预检查互补）
- 不改动 Team 子代理审批流（子代理无 `interrupt_on`，`request_permission` 仅主 agent 可用）
