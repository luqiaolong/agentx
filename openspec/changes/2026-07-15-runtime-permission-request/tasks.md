# Tasks: 运行时权限申请

## 规模判定
- 涉及文件数: 10 → 规模: L（大改）
- 涉及模块数: 3（security / deepagent / tests）
- 流程: 全流程（Worktree + TDD + 双轨 Review + 部署验证 + 归档）

## 预期修改文件
- [ ] `backend/app/security/dangerous_tools.py` — DANGEROUS_TOOLS 新增 request_permission
- [ ] `backend/app/deepagent/tool_assembly.py` — make_deep_tools 新增 request_permission 工具
- [ ] `backend/app/deepagent/approval_runner.py` — request_permission 拦截 + register_approval_request 修复
- [ ] `backend/app/security/approval/flow.py` — _make_approval_event 新增 reason 参数 + register_approval_request
- [ ] `backend/app/deepagent/safe_shell_backend.py` — sandbox_mode off 绕过 + hint 提示
- [ ] `tests/python/unit/test_request_permission_tool.py` — 新增
- [ ] `tests/python/unit/test_approval_runner_request_permission.py` — 新增
- [ ] `tests/python/unit/test_safe_shell_backend.py` — 修改
- [ ] `tests/python/unit/test_dangerous_tools.py` — 修改
- [ ] `tests/python/integration/test_runtime_permission_flow.py` — 新增

## T1: `DANGEROUS_TOOLS` 集合新增 `request_permission`

| 文件 | 改动 |
|---|---|
| `backend/app/security/dangerous_tools.py` | `DANGEROUS_TOOLS` frozenset 新增 `"request_permission"` |

**验收**：
- `DANGEROUS_TOOLS` 含 `"request_permission"`
- `compute_runtime_dangerous` 结果含 `request_permission`（当 enabled_tool_names 含它时）
- `build_interrupt_config` 生成 `{..., "request_permission": True}`

## T2: 新增 `request_permission` 工具

| 文件 | 改动 |
|---|---|
| `backend/app/deepagent/tool_assembly.py` | `make_deep_tools` 新增 `request_permission` 工具定义 |

**验收**：
- `make_deep_tools` 返回列表含 `request_permission` 工具
- 工具 docstring 含触发条件（Permission denied / EACCES / PathNotAuthorized / [SANDBOX_ESCALATION]）
- 工具参数：`path: str`、`writable: bool = False`、`reason: str = ""`
- 工具执行体返回确认消息（不执行副作用）

## T3: approval_runner 拦截 `request_permission` + 修复 approval_id/run_id 注册

| 文件 | 改动 |
|---|---|
| `backend/app/deepagent/approval_runner.py` | 中断处理分支新增 `request_permission` 拦截逻辑；**所有 approval 分支**（request_permission / dangerous_tool）发事件前调用 `register_approval_request` 生成 approval_id + run_id |
| `backend/app/security/approval/flow.py` | `_make_approval_event` 新增 `reason` 可选参数（sandbox_escalation kind 时附加）；`_handle_directory_extension` 里的 `_make_approval_event` 调用前调用 `register_approval_request` |

**验收**：
- pending_calls 含 `request_permission` → 发 `approval_request` 事件
- kind 判断：有 path → `directory_extension`；无 path → `sandbox_escalation`
- 审批通过 → `sandbox.authorize_temp` / `authorize` / `full_trust` + `_stream(resume=approve)` + `continue`
- 审批拒绝 → 注入 error ToolMessage + `Command(resume=reject)` + `return`
- 多个 `request_permission` 批量处理
- `_make_approval_event` 在 `sandbox_escalation` kind 时附加 `reason` 字段
- **D7 修复**：所有 approval 分支发事件前调用 `register_approval_request(thread_id, run_id, kind, tool_call_id)`，生成的 `approval_id` + `run_id` 传入 `_make_approval_event`
- `run_id` = `current_trace_id()`（已在 approval_runner 入口绑定）

## T4: `safe_shell_backend` 沙箱失败处理

| 文件 | 改动 |
|---|---|
| `backend/app/deepagent/safe_shell_backend.py` | `sandbox_mode == "off"` 绕过沙箱 + hint 追加 `request_permission` 提示 |

**验收**：
- `sandbox_mode == "off"` + 沙箱失败 + `is_sandbox_limit` → `subprocess.run` 绕过重试
- `sandbox_mode != "off"` + 沙箱失败 + `is_sandbox_limit` → hint 含「可调用 request_permission(path, writable, reason) 工具申请路径授权后重试」

## T5: 单元测试

| 文件 | 改动 |
|---|---|
| `tests/python/unit/test_request_permission_tool.py` | 新增：工具注册 + docstring + 执行体 |
| `tests/python/unit/test_approval_runner_request_permission.py` | 新增：拦截逻辑 + kind 判断 + 审批通过/拒绝 |
| `tests/python/unit/test_safe_shell_backend.py` | 修改：sandbox_mode off 绕过 + hint 提示 |
| `tests/python/unit/test_dangerous_tools.py` | 修改：DANGEROUS_TOOLS 含 request_permission |

**验收**：
- 所有新增 / 修改测试通过
- 覆盖设计文档中的 E1-E4 边界情况

## T6: 集成测试

| 文件 | 改动 |
|---|---|
| `tests/python/integration/test_runtime_permission_flow.py` | 新增：端到端权限申请流程 |

**验收**：
- Mock LLM 序列：execute(error) → request_permission → execute(success)
- Mock approval 自动 approve
- 断言：sandbox.authorize 被调用 + tool_result 最终 success
