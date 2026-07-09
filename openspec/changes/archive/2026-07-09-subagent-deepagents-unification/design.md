# Design: 深度对齐 DeepAgents 框架特性

## Context

AgentX 主路径已迁移到 `deepagents.create_deep_agent`，但子代理层、RubricMiddleware、
FilesystemPermission、LocalShellBackend 仍有缺口。本设计覆盖 P0-P3 实施细节 + P4 评估结论。

## Goals / Non-Goals

**Goals:**
- P0: rag/web 子代理从 `create_react_agent` 迁移到 `harness.create_agent`
- P1: `build_deep_agent` / `build_work_supervisor` / `build_coding_expert` 暴露 `rubric` 参数
- P2: `harness.create_agent` 添加 `permissions=` 参数，注入 `FilesystemPermission` 静态安全基线
- P3: 创建 `SafeLocalShellBackend(LocalShellBackend)` 替代自研 `cli_execute`
- P4: 记录 `SandboxBackend` 评估结论（不实施）

**Non-Goals:**
- 不修改 `run_react_agent_stream`（事件流转换层）
- 不修改 `SessionSandbox`（动态授权层保持不变）
- 不迁移 Team 路径（另开提案）

## Decisions

### Decision 1: rag/web 迁移方案

复用 `harness.create_agent`（与 `custom_agent.py` 一致）。关键差异：
- `create_react_agent` 用 `prompt=` → `create_agent` 用 `system_prompt=`
- `create_agent` 额外注入 `interrupt_on` / `memory` / `skills` / `backend` / `middleware`
- `interrupt_on` 对无危险工具的子代理无影响
- `excluded_tools` 只隐藏 deepagents 内置工具，不影响 `rag_retrieve` / `web_search`
- `astream_events` v2：子代理无 interrupt 需求，直接执行工具是期望行为

### Decision 2: RubricMiddleware 暴露方案

`harness.create_agent` 已有 `rubric` + `grader_model` 参数（[harness.py:144-145](../../../backend/app/deep/harness.py#L144-L145)）。
在 `build_deep_agent` / `build_work_supervisor` / `build_coding_expert` 函数签名添加
`rubric: str | None = None` + `grader_model: Any | None = None`，透传给 `harness.create_agent`。
向后兼容：新增可选参数，默认 `None`（关闭）。

### Decision 3: FilesystemPermission 静态安全基线

在 `harness.create_agent` 添加 `permissions: list | None = None` 参数。
默认注入 `FilesystemPermission` deny 规则：

```python
_DEFAULT_PERMISSIONS = [
    FilesystemPermission(
        operations=["write", "edit"],
        paths=["/proc/**", "/sys/**", "/dev/**", "/etc/**"],
        mode="deny",
    ),
    FilesystemPermission(
        operations=["write", "edit"],
        paths=["**/.git/**"],
        mode="deny",
    ),
]
```

与 SessionSandbox 互补：FilesystemPermission = 框架级静态基线，SessionSandbox = 应用级动态授权。

### Decision 4: SafeLocalShellBackend 继承方案

**官方模式**: deepagents 文档展示 `GuardedBackend(FilesystemBackend)` 继承 override `write`/`edit`。
同模式创建 `SafeLocalShellBackend(LocalShellBackend)` override `execute`。

**实现**:
```python
from deepagents.backends import LocalShellBackend
from app.security.command_filter import is_command_blocked, has_forbidden_args

class SafeLocalShellBackend(LocalShellBackend):
    """LocalShellBackend with blocklist + metachar filtering."""

    def execute(self, command: str, **kwargs) -> str:
        # 1. 提取命令名（shell=True 下 command 是完整命令字符串）
        cmd_name = command.strip().split()[0] if command.strip() else ""
        if is_command_blocked(cmd_name):
            return f"命令 '{cmd_name}' 在黑名单中，禁止执行"

        # 2. 元字符过滤（阻断 shell 注入：; & | ` $ < >）
        if has_forbidden_args(command):
            return f"命令包含非法 shell 元字符: {command!r}"

        # 3. 调用父类执行（root_dir 限制工作目录）
        return super().execute(command, **kwargs)
```

**安全层对比**:
| 维度 | 原 `cli_execute` | `SafeLocalShellBackend.execute` |
|------|------------------|--------------------------------|
| blocklist | ✅ DEFAULT_BLOCKLIST + 用户配置 | ✅ 复用 `is_command_blocked` |
| 元字符过滤 | ✅ `has_forbidden_args` | ✅ 复用 `has_forbidden_args` |
| shell 模式 | `shell=False` | `shell=True` — 元字符过滤缓解 |
| cwd 授权 | ✅ SessionSandbox.check_write | root_dir 限制 + FilesystemPermission |
| 审批 | ✅ `interrupt_on` | ✅ `interrupt_on`（DANGEROUS_TOOLS 含 `execute`） |

**元字符过滤缓解 shell=True 风险**:
`FORBIDDEN_ARG_PATTERN = re.compile(r"[;&|\`$<>]")` 拦截：
- `;` — 命令链 (`cmd1; cmd2`)
- `&` — 后台执行 (`cmd &`) / 命令链 (`cmd1 && cmd2`)
- `|` — 管道 (`cmd1 | cmd2`)
- `` ` `` — 命令替换 (`` `cmd` ``)
- `$` — 变量替换 / 命令替换 (`$(cmd)`)
- `<` `>` — 重定向 (`cmd > file`)

元字符过滤使注入面收窄到与 `shell=False` 相近。

**工具名变更影响**:
- `cli_execute` → `execute`（deepagents 内置工具名）
- `DANGEROUS_TOOLS` 更新：`cli_execute` → `execute`
- `redact_args` 适配：`cli_execute` → `execute`
- `tools_enabled` 配置：添加 `execute` key
- `_EXCLUDED_BUILTIN_TOOLS`：移除 `"execute"`（让工具可用）
- `_make_deep_tools`：移除自研 `cli_execute` 工具注册
- 前端 PermissionToggle / 配置面板适配（另开前端任务）

### Decision 5: SandboxBackend 评估（不实施）

`SandboxBackend` 需要 Modal/Daytona 远程沙箱 provider，引入网络延迟 + 额外依赖。
AgentX 是本地桌面应用，远程沙箱模型不匹配。保留自研 `SessionSandbox`。

## Risks

### Risk 1: shell=True 安全降级

**风险**: `LocalShellBackend.execute` 用 `shell=True`，相比 `cli_execute` 的 `shell=False` 安全性降级。

**缓解**:
- 元字符过滤 `FORBIDDEN_ARG_PATTERN` 拦截 `; & | \` $ < >`
- blocklist 拦截危险命令名
- `interrupt_on` 审批（用户可拒绝可疑命令）
- `root_dir` 限制工作目录
- `FilesystemPermission` 静态 deny 系统目录

### Risk 2: RubricMiddleware 增加 LLM 调用成本

**缓解**: 默认 `rubric=None`（关闭），仅显式传入时启用。

### Risk 3: 工具名 cli_execute → execute 影响面

**风险**: 前端 PermissionToggle / 配置 / 测试可能依赖 `cli_execute` 工具名。

**缓解**:
- 后端先完成迁移
- 前端适配另开任务
- `redact_args` / `DANGEROUS_TOOLS` / `tools_enabled` 同步更新
- 测试中 mock 适配

### Risk 4: rag/web 迁移后 astream_events 事件流变化

**缓解**: `run_react_agent_stream` 只消费标准事件，`custom_agent.py` 已验证可行。

## Migration Strategy

1. P0: rag_agent.py / web_agent.py 迁移 + 删 deprecation 压制
2. P1: deep/agent.py / work_supervisor.py / coding.py 暴露 rubric
3. P2: deep/harness.py 添加 permissions 参数
4. P3: 创建 safe_shell_backend.py + 修改 harness.py / tools.py / dangerous_tools.py / command_filter.py
5. 测试验证

### 回滚策略

- P0/P1/P2/P3 互相独立，可分批合入
- P3 出问题可回退到 `FilesystemBackend` + 自研 `cli_execute`
