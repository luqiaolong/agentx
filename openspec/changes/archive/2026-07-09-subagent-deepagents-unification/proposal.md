# Proposal: 深度对齐 DeepAgents 框架特性，消除自造轮子

## Why

AgentX 主路径（Supervisor / Coding Expert / DeepAgent）已完成 DeepAgents 0.6.12 迁移，
但仍有以下自造轮子与框架特性缺口：

1. **rag/web 子代理仍用 `create_react_agent`**（已 deprecation），项目通过 `filterwarnings`
   压制警告——违反 AGENTS.md §3 R17
2. **`RubricMiddleware` 未在生产路径启用**：`harness.create_agent` 已预留 `rubric` 参数位，
   但 `build_deep_agent` / `build_work_supervisor` / `build_coding_expert` 均未暴露——
   运行时自纠能力缺失
3. **`FilesystemPermission` 未用作安全基线**：项目仅有 `SessionSandbox`（动态授权），
   缺少 deepagents 框架级的静态路径权限声明
4. **`LocalShellBackend` 未使用**：自研 `cli_execute` 可通过继承 `LocalShellBackend` +
   override `execute` 实现 blocklist，用框架 backend 替代自研工具
5. **`SandboxBackend` 未评估**：需明确是否可替代自研 `SessionSandbox`

## What Changes

### P0: rag/web 子代理迁移（低风险）

- [rag_agent.py](../../../backend/app/subagents/rag_agent.py): `create_react_agent` → `harness.create_agent`
- [web_agent.py](../../../backend/app/subagents/web_agent.py): 同上
- [pyproject.toml](../../../pyproject.toml): 删除 `filterwarnings` 中 `create_react_agent` 条目
- [backend/app/__init__.py](../../../backend/app/__init__.py): 删除 `warnings.filterwarnings` 块

### P1: RubricMiddleware 生产可用（低风险）

- [deep/agent.py](../../../backend/app/deep/agent.py): `build_deep_agent` 暴露 `rubric` + `grader_model`
- [agents/supervisor/work_supervisor.py](../../../backend/app/agents/supervisor/work_supervisor.py): 同上
- [agents/expert/coding.py](../../../backend/app/agents/expert/coding.py): 同上
- 默认 `rubric=None`（关闭），调用方可选启用运行时自纠

### P2: FilesystemPermission 静态安全基线（中风险）

- [deep/harness.py](../../../backend/app/deep/harness.py): `create_agent` 添加 `permissions=` 参数
- 用 `FilesystemPermission` 声明关键目录 deny 规则（系统目录 / .git / node_modules）
- 与 `SessionSandbox` 互补：静态基线 + 动态授权

### P3: LocalShellBackend 替代自研 cli_execute（中高风险）

**方案**: 创建 `SafeLocalShellBackend(LocalShellBackend)` 继承类，override `execute` 方法添加 blocklist + 元字符过滤。

- [deep/safe_shell_backend.py](../../../backend/app/deep/safe_shell_backend.py) **新文件**: `SafeLocalShellBackend` 继承类
- [deep/harness.py](../../../backend/app/deep/harness.py): `resolve_backend` 返回 `SafeLocalShellBackend` 替代 `FilesystemBackend`
- [deep/tools.py](../../../backend/app/deep/tools.py): 移除自研 `cli_execute` 工具注册
- [security/dangerous_tools.py](../../../backend/app/security/dangerous_tools.py): `DANGEROUS_TOOLS` 中 `cli_execute` → `execute`
- [security/command_filter.py](../../../backend/app/security/command_filter.py): `redact_args` 适配 `execute` 工具名
- `_EXCLUDED_BUILTIN_TOOLS` 移除 `"execute"`（让 deepagents 的 execute 工具可用）

**安全层迁移**:
| 安全层 | 原 `cli_execute` | `SafeLocalShellBackend.execute` |
|--------|------------------|--------------------------------|
| blocklist | ✅ DEFAULT_BLOCKLIST + 用户配置 | ✅ 复用 `is_command_blocked` |
| 元字符过滤 | ✅ `has_forbidden_args` | ✅ 复用 `has_forbidden_args` |
| shell 模式 | `shell=False`（参数列表） | `shell=True`（字符串）— 元字符过滤缓解 |
| cwd 授权 | ✅ SessionSandbox.check_write | ❌ → root_dir 限制 + FilesystemPermission |
| 审批 | ✅ `interrupt_on` | ✅ `interrupt_on`（DANGEROUS_TOOLS 含 `execute`） |
| 输出截断 | ✅ `max_output_chars` | ✅ `max_output_bytes` |
| 超时 | ✅ `cli_tool_timeout` | ✅ `timeout` 参数 |

**元字符过滤缓解 shell=True 风险**:
`FORBIDDEN_ARG_PATTERN = re.compile(r"[;&|`$<>]")` 拦截 `;` `&` `|` `` ` `` `$` `<` `>`，
阻断命令链（`cmd1; cmd2`）、管道（`cmd1 | cmd2`）、命令替换（`` `cmd` `` / `$(cmd)`）、
重定向（`cmd > file`）。虽然 `shell=True`，但元字符过滤使注入面收窄到与 `shell=False` 相近。

### P4: SandboxBackend 评估结论（不实施）

**SandboxBackend** — **不替代 `SessionSandbox`**：
- `SandboxBackend` 是 Modal/Daytona **远程沙箱**，需网络连接 + 额外依赖
- AgentX 是**本地桌面应用**（Tauri + Python 后端），远程沙箱延迟不可接受
- `SessionSandbox` 的动态授权（交互式审批目录越界）是 `SandboxBackend` 不具备的能力
- 保留自研 `SessionSandbox`

### 不变项

- `run_rag_agent` / `run_web_agent` 事件流契约不变
- 子代理只读工具集不变
- `SessionSandbox` 不变（FilesystemPermission 作为补充层）
- 消费方签名向后兼容（新增可选参数）

## Capabilities

### Modified Capabilities

- `rag-subagent`: 从 `create_react_agent` 迁移到 `create_deep_agent`
- `web-subagent`: 同上
- `deprecation-cleanup`: 移除 `create_react_agent` deprecation 压制
- `rubric-middleware`: RubricMiddleware 生产可用
- `filesystem-permission`: FilesystemPermission 静态安全基线
- `safe-shell-backend`: SafeLocalShellBackend 替代自研 cli_execute

## Impact

- **后端**: 修改 12 个文件 + 新增 1 个文件
- **前端**: 工具名 `cli_execute` → `execute` 需适配（PermissionToggle / tools_enabled 配置）
- **API**: 无新增端点
- **测试**: 现有测试适配 + 新增 SafeLocalShellBackend / RubricMiddleware / FilesystemPermission 验证
- **依赖**: 无变化（deepagents 0.6.12 已在 pyproject.toml）
- **安全**: FilesystemPermission 增强路径保护 + SafeLocalShellBackend 保留 blocklist + 元字符过滤

## Design Decisions

### D1: RubricMiddleware 默认关闭

RubricMiddleware 会增加 LLM 调用（自纠循环 `max_iterations=3`），带来延迟和成本。
默认 `rubric=None`（关闭），仅在调用方显式传入 rubric 文本时启用。

### D2: FilesystemPermission 不替代 SessionSandbox

`FilesystemPermission` 是**静态声明**，无法表达 SessionSandbox 的**动态授权**（交互式审批 + 三模式）。
两者互补：FilesystemPermission = 静态基线（框架级），SessionSandbox = 动态层（应用级）。

### D3: SafeLocalShellBackend 继承方案

deepagents 官方文档展示 `GuardedBackend(FilesystemBackend)` 继承 override `write`/`edit` 的模式
（[backends.md](https://docs.langchain.com/oss/python/deepagents/backends)）。
同模式创建 `SafeLocalShellBackend(LocalShellBackend)` override `execute`：
- 复用 `app.security.command_filter` 的 blocklist + 元字符过滤
- `root_dir=workspace_path` 限制工作目录
- `interrupt_on` 审批（`DANGEROUS_TOOLS` 含 `execute`）

### D4: SandboxBackend 不适用本地桌面应用

`SandboxBackend` 需要 Modal/Daytona 远程沙箱 provider，引入网络延迟 + 额外依赖。
AgentX 是 Tauri 桌面应用，用户期望本地即时响应。远程沙箱模型不匹配。
