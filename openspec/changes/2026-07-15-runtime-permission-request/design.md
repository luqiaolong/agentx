# Design: 运行时权限申请

## 架构概览

```
LLM 调用 execute("npm install")
  → SafeLocalShellBackend.execute
    → 沙箱拦截 (Permission denied / EACCES)
    → analyze_sandbox_failure 命中
    → sandbox_mode == "off"?
        ├─ Yes → subprocess.run 绕过沙箱重试 → 返回结果（不拦截）
        └─ No  → 追加 [SANDBOX_ESCALATION] hint + "可调用 request_permission 申请权限"
  → tool_result (error) 发给 LLM

LLM 看到 hint，自行判断：
  → 调用 request_permission(path="/usr/lib/node_modules", writable=True, reason="npm install 需要写入权限")
    → HumanInTheLoopMiddleware.after_model 触发 interrupt
    → approval_runner 检测 pending_calls 含 request_permission
      → 解析 args (path / writable / reason)
      → _make_approval_event(kind="directory_extension" | "sandbox_escalation")
      → yield approval_request SSE 事件
      → 前端 attachApprovalToToolCall → ToolCallCard 右下角展示「允许沙箱执行」按钮
      → _await_approval 轮询等待用户决策

用户点击「允许沙箱执行」
  → POST /api/chat/approve { approval_id, run_id, approved=true, decision="approve" }
  → consume_approval → submit_approval
  → approval_runner 收到 decision
    → sandbox.authorize_temp(thread_id, path, writable) 或 sandbox.authorize(...)
    → Command(resume=approve) → request_permission 工具执行返回「路径 {path} 已授权」
    → LLM 看到授权结果 → 重新调用原工具 → 成功

用户拒绝 / 超时
  → approval_runner 收到 deny / None
  → 注入 error ToolMessage("用户拒绝授权路径 {path}")
  → Command(resume=reject) → request_permission 工具返回错误
  → LLM 看到拒绝结果 → 换路径或停止
```

## 关键设计决策

### D1: 为什么用 `request_permission` 工具而非图谱节点

deepagents 0.6.x 的图结构由 `create_deep_agent` 固化为 ReAct 循环（model → tools → model），不支持自定义节点插入。加工具只需在 `tools` 列表注册 + `DANGEROUS_TOOLS` 集合声明，不需要改图结构。

`request_permission` 工具的优势：
- LLM 自行判断是否申请权限（符合用户要求「让 LLM 自行判断」）
- 复用现有 `interrupt_on` + `approval_runner` 审批机制
- 前端复用现有 `approvalRequest` 按钮，零改动
- 工具 docstring 是 LLM 判断的依据，可精确控制 LLM 行为

### D2: `request_permission` 工具的执行体

工具被 `Command(resume=approve)` 恢复后执行。执行体不执行任何副作用操作（授权已在 approval_runner 层完成），只返回确认消息：

```python
@tool
async def request_permission(path: str, writable: bool = False, reason: str = "") -> str:
    """当工具因权限不足失败时，调用此工具申请路径授权。

    触发条件：工具执行返回 Permission denied / EACCES / PathNotAuthorized /
    [SANDBOX_ESCALATION] 等权限错误时。

    Args:
        path: 需要授权的文件/目录绝对路径。
        writable: True 申请写权限，False 只读权限。
        reason: 申请原因（如"npm install 需要写入 node_modules"）。

    Returns:
      授权结果消息（approval_runner 在 resume 前已授权路径）。
    """
    # 执行体在 approval_runner 拦截后由 Command(resume=approve) 恢复执行。
    # 授权已在 approval_runner 层完成，此处只返回确认消息。
    return f"路径已授权: {path} (writable={writable})"
```

**注意**：如果 LLM 在未经 approval_runner 拦截的情况下直接调用（如 `sandbox_mode == "off"` 时 `interrupt_on` 仍触发但 approval_runner 自动放行），工具执行体仍返回确认消息，但不会实际授权。这是安全的降级——`sandbox_mode == "off"` 时沙箱本就不拦截。

### D3: approval_runner 拦截分支

在 `run_agent_with_approval` 的中断处理循环里，**在现有 `dangerous_calls` 检测之前**新增 `request_permission` 检测分支。

**关键**：`request_permission` 分支处理完后 `_stream(resume=approve)` 让 agent 执行工具（返回"已授权"），然后 `continue` 回到循环顶部，等待 LLM 下一轮 tool_call（通常是重新调用原失败的工具）。**不 `return`**，让 agent 继续执行。

```python
# 检测 request_permission 工具调用（LLM 主动申请权限）
permission_calls = [tc for tc in pending_calls if tc.get("name") == "request_permission"]

if permission_calls:
    for tc in permission_calls:
        args = tc.get("args", {})
        path = args.get("path", "")
        writable = args.get("writable", False)
        reason = args.get("reason", "")
        # kind 判断：有路径 → directory_extension；纯命令权限 → sandbox_escalation
        kind = "directory_extension" if path else "sandbox_escalation"
        # D7: 注册活跃审批请求，生成 approval_id + run_id
        approval_req = await register_approval_request(
            thread_id, _trace_id, kind, tc.get("id"),
        )
        yield await _forward(_make_approval_event(
            tc, thread_id, kind=kind,
            requested_path=path or None,
            writable=writable,
            reason=reason,
            approval_id=approval_req.approval_id,
            run_id=approval_req.run_id,
        ))

    decision = await _await_approval(thread_id, ...)

    if decision is None or not decision.approved:
        # 用户拒绝：注入 error + resume reject
        for tc in permission_calls:
            await _inject_call(agent, config, tc, f"用户拒绝授权路径: {tc['args'].get('path', '')}")
        yield await _forward(make_error_event("用户拒绝授权路径"))
        # 消费 HITL interrupt
        ...
        return

    # 审批通过：授权路径
    for tc in permission_calls:
        args = tc.get("args", {})
        path = args.get("path", "")
        writable = args.get("writable", False)
        if path:  # 有路径才授权
            if decision.decision in ("once", "approve"):
                await _sandbox.authorize_temp(thread_id, path, writable=writable)
            elif decision.decision == "session":
                await _sandbox.authorize(thread_id, path, writable=writable)
            elif decision.decision == ApprovalDecision.FULL_TRUST:
                await _sandbox.authorize(thread_id, path, writable=True)
                if hasattr(_sandbox, "set_full_trust"):
                    await _sandbox.set_full_trust(thread_id, True)

    # resume approve：_stream 恢复执行，request_permission 工具返回"已授权"
    # LLM 看到结果后重新调用原工具
    async for sse in _stream(
        agent,
        _make_hitl_resume_decisions(permission_calls, decision_type="approve"),
        config,
        source,
    ):
        yield await _forward(sse)
    _yielded_msg_count = await _state_msg_count()
    continue  # 回到循环顶部，等待 LLM 下一轮 tool_call
```

### D4: `sandbox_mode == "off"` 绕过沙箱

`safe_shell_backend.execute` 在沙箱执行失败 + `analyze_sandbox_failure.is_sandbox_limit` 时：

```python
if _SANDBOX_ESCALATION_ENABLED and result.exit_code != 0:
    analysis = analyze_sandbox_failure(command, result.exit_code, result.output)
    if analysis.is_sandbox_limit:
        # sandbox_mode == "off"：绕过沙箱直接重试
        if is_sandbox_off():
            # 用 subprocess.run 直接执行，不走沙箱
            import subprocess
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                cwd=kwargs.get("cwd"),
            )
            return ExecuteResponse(
                output=proc.stdout + (proc.stderr and f"\n{proc.stderr}" or ""),
                exit_code=proc.returncode,
                truncated=False,
            )

        # sandbox_mode != "off"：追加 hint + request_permission 提示
        upgrade_hint = (
            f"\n\n[SANDBOX_ESCALATION]"
            f"\nreason: {analysis.reason}"
            f"\nsuggested_action: {analysis.suggested_action}"
        )
        if analysis.suggested_path:
            upgrade_hint += f"\nsuggested_path: {analysis.suggested_path}"
        upgrade_hint += (
            f"\n提示: 可调用 request_permission(path, writable, reason) "
            f"工具申请路径授权后重试。"
        )
        return ExecuteResponse(
            output=result.output + upgrade_hint,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )
```

### D5: `_make_approval_event` 支持 `reason` 字段

当前 `_make_approval_event` 在 `kind == "directory_extension"` 时附加 `requestedPath` / `writable`。需要新增 `reason` 可选参数，在 `sandbox_escalation` kind 时附加到事件 data。

`request_permission` 场景下，工具 args 只有 `path` / `writable` / `reason`，不包含 `command` / `exit_code` / `suggested_action` / `suggested_path`（这些是 `safe_shell_backend` 分析结果的字段，LLM 已从 `[SANDBOX_ESCALATION]` hint 中看到，不需要结构化传递）。前端 `api-types.ts` 已定义这些字段为可选，`request_permission` 场景下它们为空，前端不展示。

修改后签名：
```python
def _make_approval_event(
    tool_call: dict,
    thread_id: str,
    kind: str = "dangerous_tool",
    requested_path: str | None = None,
    writable: bool = False,
    approval_id: str | None = None,
    run_id: str | None = None,
    reason: str | None = None,  # 新增
) -> dict[str, str]:
```

在 `kind == "sandbox_escalation"` 时附加 `reason` 字段到 data。

### D6: `DANGEROUS_TOOLS` 集合扩展

```python
DANGEROUS_TOOLS: frozenset[str] = frozenset(
    {
        "edit_file",
        "write_file",
        "delete_file",
        "request_permission",  # 新增：触发 interrupt_on 审批
    }
)
```

`compute_runtime_dangerous` 公式不变：`(DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names`。`request_permission` 在 `make_deep_tools` 中注册，会被 `enabled_tool_names` 包含。

### D7: approval_runner 必须注册活跃审批请求（修复已有 bug）

**已有 bug**：`register_approval_request` 在生产代码中未被调用，approval_runner 发 approval_request 事件时没生成 `approval_id` / `run_id`。但 `chat_approve` 端点（[chat.py:587-591](file:///d:/java/agentprojects/agentx/backend/app/api/chat.py#L587-591)）强制要求这两个字段，前端点击审批按钮后会收到 403 错误。

**修复**：approval_runner 在**所有** approval 分支（`request_permission` / `dangerous_tool` / `directory_extension`）发 approval_request 事件前，必须调用 `register_approval_request(thread_id, run_id, kind, tool_call_id)` 生成 `approval_id` + `run_id`，并传入 `_make_approval_event`。

- `run_id` 使用 `current_trace_id()`（已在 approval_runner 入口绑定到 ContextVar）
- `register_approval_request` 返回的 `approval_id` + `run_id` 传入 `_make_approval_event`
- 前端从 approval_request 事件 payload 获取 `approval_id` + `run_id`，点击按钮后回传给 `/api/chat/approve`
- `chat_approve` 调用 `consume_approval(approval_id, run_id)` 验证通过后才写入决策

**影响范围**：
- `approval_runner.py` — `dangerous_tool` 分支（L530-532）+ `request_permission` 分支
- `flow.py` — `_handle_directory_extension` 里的 `_make_approval_event` 调用（L392-400）

这个修复确保所有审批路径都能正确走通 `register → consume → submit` 生命周期。

## 数据流

### 场景 1：execute 命中沙箱限制，LLM 申请权限

```
1. LLM → execute("npm install")
2. SafeLocalShellBackend.execute → 沙箱拦截 → exit_code != 0
3. analyze_sandbox_failure 命中 → sandbox_mode != "off"
4. 返回 output + [SANDBOX_ESCALATION] hint + "可调用 request_permission"
5. tool_result(error) → LLM
6. LLM → request_permission(path="/usr/lib/node_modules", writable=True, reason="npm install")
7. HumanInTheLoopMiddleware interrupt
8. approval_runner 检测 request_permission
9. _make_approval_event(kind="directory_extension") → approval_request SSE
10. 前端 attachApprovalToToolCall → ToolCallCard 右下角展示按钮
11. 用户点击「允许沙箱执行」→ POST /api/chat/approve
12. approval_runner 收到 decision
13. sandbox.authorize_temp(thread_id, path, writable=True)
14. Command(resume=approve) → request_permission 执行返回"已授权"
15. LLM 看到授权结果 → 重新调用 execute("npm install")
16. 沙箱已授权 → 执行成功 → tool_result(success)
```

### 场景 2：sandbox_mode == "off"，自动绕过

```
1. LLM → execute("npm install")
2. SafeLocalShellBackend.execute → 沙箱拦截 → exit_code != 0
3. analyze_sandbox_failure 命中 → sandbox_mode == "off"
4. subprocess.run 绕过沙箱重试 → 成功
5. 返回 ExecuteResponse(success) → tool_result(success) → LLM
```

### 场景 3：用户拒绝授权

```
1-10. 同场景 1
11. 用户不点击 / 点击拒绝 → approval 超时或 deny
12. approval_runner 收到 None / not approved
13. 注入 error ToolMessage("用户拒绝授权路径")
14. Command(resume=reject) → request_permission 返回错误
15. LLM 看到拒绝 → 换路径或停止
```

## 边界情况

### E1: LLM 在 sandbox_mode == "off" 时仍调用 request_permission

`sandbox_mode == "off"` 时 `interrupt_on` 仍会触发（`request_permission` 在 `DANGEROUS_TOOLS` 中），但 approval_runner 在 `is_full_trust` 分支会自动放行（`full_trust` 模式）。如果 `sandbox_mode == "off"` 但 `permission_mode != "full_trust"`，approval_runner 会正常拦截并展示按钮。用户审批后 `sandbox.authorize` 仍会执行（幂等），工具返回"已授权"。这是安全降级，无副作用。

### E2: request_permission 的 path 为空

LLM 可能调用 `request_permission(path="", reason="需要管理员权限")`。此时 kind 判断为 `sandbox_escalation`，`_make_approval_event` 不附加 `requestedPath`。approval_runner 在审批通过后不调用 `sandbox.authorize`（无路径可授权），只 resume 工具返回"已授权"。LLM 看到结果后重试原工具——如果沙箱限制是路径无关的（如进程创建被拦），重试仍可能失败。这是预期行为：`sandbox_escalation` 的 `execute_unsandboxed` 路径需要更大改动，本期不实现。

### E3: 多个 request_permission 同时调用

LLM 可能在一次 turn 里调用多个 `request_permission`（如同时申请多个路径）。approval_runner 批量发 approval_request 事件 + 统一等待一个 decision + 批量授权。复用现有 `_handle_directory_extension` 的批量处理模式。

### E4: request_permission 与 directory_extension 预检查冲突

`request_permission` 是 LLM 主动申请，`directory_extension` 是 approval_runner 预检查。两者互补：
- 预检查在工具执行前拦截未授权路径 → 用户审批 → 执行
- `request_permission` 在工具执行失败后由 LLM 主动申请 → 用户审批 → LLM 重试

两者不会同时触发：预检查通过的工具不会进入 `request_permission` 流程；预检查未通过的工具会被 `directory_extension` 拦截，LLM 看不到 error 不会调用 `request_permission`。`request_permission` 主要用于 `execute` 命令（不在 `DANGEROUS_TOOLS` 中，不走预检查）的运行时权限失败。

## 测试策略

### 单元测试

1. **`test_request_permission_tool.py`**：
   - `make_deep_tools` 返回列表包含 `request_permission` 工具
   - 工具 docstring 含触发条件 + 参数说明
   - 工具执行体返回确认消息

2. **`test_approval_runner_request_permission.py`**：
   - pending_calls 含 `request_permission` → 发 approval_request 事件
   - kind 判断：有 path → `directory_extension`；无 path → `sandbox_escalation`
   - 审批通过 → `sandbox.authorize_temp` 调用 + resume approve
   - 审批拒绝 → 注入 error + resume reject
   - 多个 `request_permission` 批量处理

3. **`test_safe_shell_backend.py`**（修改）：
   - `sandbox_mode == "off"` + 沙箱失败 → `subprocess.run` 绕过重试
   - `sandbox_mode != "off"` + 沙箱失败 → hint 含 `request_permission` 提示

4. **`test_dangerous_tools.py`**（修改）：
   - `DANGEROUS_TOOLS` 包含 `request_permission`
   - `compute_runtime_dangerous` 结果含 `request_permission`

### 集成测试

5. **端到端**：LLM 调用 execute 失败 → 调用 request_permission → 用户审批 → 重试成功
   - Mock LLM 序列：execute(error) → request_permission → execute(success)
   - Mock approval：`submit_approval` 自动 approve
   - 断言：sandbox.authorize 被调用 + tool_result 最终 success
