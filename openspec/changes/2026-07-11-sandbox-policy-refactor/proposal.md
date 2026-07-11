# 沙箱风险分级重构 — Proposal

## Why

AgentX 沙箱目前由 4 层独立规则（命令黑名单 / 元字符过滤 / Git 写操作拦截 / 路径白名单）拼装而成，**没有协调机制**。导致 3 类生产 bug：

1. **元字符误伤 PowerShell 合法语法**
   - `safe_shell_backend.execute` 的 `has_forbidden_args(respect_quotes=True)` 对 `$_`、`$Var`、`@{Name='X'}`、`[...]` 等 PowerShell 字面量在双引号内**仍误判为危险字符**
   - 用户 trace：`powershell -Command "Get-Process | Sort-Object WorkingSet64 ..."` 被 exit_code=126 拦截

2. **write_file 路径认知错位（LLM 重试循环）**
   - `session_sandbox.check_read` 错误信息不列出"当前 thread 的所有可用目录"，LLM 写错路径后陷入 2-3 轮重试
   - 用户 trace：`write_file(C:\Users\luqia\Desktop\foo.py)` 拦 → 改 `/workspace/foo.py` 仍拦 → 改 `D:\java\book\book\foo.py` 还是拦

3. **`cli_execute` 冗余拦截**
   - `tools/cli.py` 用 `subprocess.run(argv_list, shell=False)`，元字符不会触发 shell 解析，但仍跑 `has_forbidden_args`
   - 用户写 `python -c "import time; print(time.time())"` 也会被拦

**新代码已实现的能力**（工作树未提交 17 个文件、约 372 行新增）：

- ✅ 3 模式全局开关 `sandbox` / `off` / `manual`（`Settings.sandbox_mode` + `AGENTX_SANDBOX_MODE` env 链路）
- ✅ Tauri 端 settings store + IPC commands + 前端 SandboxSettings.tsx 3 选项 UI
- ✅ `session_sandbox` 的 4 个 check 方法（read/write + sync/async）都接入了 `sandbox_mode` 短路
- ✅ `_manual_read_hint` / `_manual_write_hint` 错误信息分支
- ✅ 6 个新单元测试覆盖 off / manual / sync / critical 仍拒

**新代码没解决的**：上述 3 个 bug 中的元字符误伤 + cli_execute 冗余 + 路径错误信息智能引导**仍未处理**。

## What Changes

把沙箱从"4 层独立规则"重构为"以 **RiskClassifier** 为中心、**ExecutionContext** 为驱动、**sandbox_mode** 为顶层 alias 的统一架构"。

### 新增 4 个文件
- `backend/app/security/risk.py` — `RiskLevel` 枚举 + `RiskAssessment` 数据类 + `RiskClassifier` 聚合器
- `backend/app/security/context.py` — `ExecutionContext` 数据类（frozen dataclass）+ builder 函数
- `backend/app/security/policies/metachar.py` — `MetacharFilter`（支持 PowerShell 字面量白名单 + argv 模式短路）
- `backend/app/security/policies/path_hint.py` — `PathHintFormatter`（智能引导到 scratch + 已授权目录列表）

### 修改 5 个文件
- `backend/app/security/command_filter.py` — `FORBIDDEN_ARG_PATTERN` 保留兼容，新接口走 `MetacharFilter.assess`
- `backend/app/sandbox/session_sandbox.py` — `_unauthorized_read_hint` / `_unauthorized_write_hint` 调用 `PathHintFormatter.format`
- `backend/app/deepagent/safe_shell_backend.py` — `execute` 走 `RiskClassifier`（替换 4 条独立 if）
- `backend/app/tools/cli.py` — 取消 argv 模式的元字符过滤（保留 redact token/password）
- `backend/app/config/settings.py` — `sandbox_mode` 注释更新为"RiskClassifier 顶层 alias"

### 测试更新
- `tests/python/unit/test_security_command_filter.py` — 增补 PS 合法语法 case
- `tests/python/unit/test_safe_shell_backend.py` — 增补 PS 管道 case
- `tests/python/unit/test_session_sandbox.py` — 路径错误信息聚合 case
- 新增 `tests/python/unit/test_risk_classifier.py`（核心）
- 新增 `tests/python/unit/test_execution_context.py`

## Impact

- **能力变更**：现有 3 模式（sandbox/off/manual）行为不变；新增"PS 合法语法放行 + 路径错误智能引导"
- **API 兼容**：`safe_shell_backend.execute` / `cli_execute` 签名不变；返回值类型不变
- **风险面**：
  - 元字符层放宽 → 需新增 ~12 个 PS case 防止回归
  - 路径层信息增强 → 前端 ApprovalDialog / LLM 提示可能视觉变长，需回归 UI
  - `FORBIDDEN_ARG_PATTERN` 字符串保留为旧 API 兼容层

## Out of Scope

- macOS App Sandbox / Windows Job Object 真内核级沙箱（属方案 C 范畴）
- 沙箱策略远程热更新（Nacos config 集成）
- 沙箱事件审计（OTel span 现有 trace 已覆盖大部分）
- `execute_unsandboxed` 升级为用户审批流（已有 sandbox_escalation 流程，本提案不动）
