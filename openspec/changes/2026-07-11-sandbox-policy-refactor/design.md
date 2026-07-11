# 沙箱风险分级重构 — Design

## 1. 目标架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      Sandbox 入口（业务代码）                      │
│   cli_execute / safe_shell_backend.execute / check_read / check_write  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 传入 ExecutionContext
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     RiskClassifier (新)                          │
│   class RiskClassifier:                                         │
│       def assess(command, ctx) -> list[RiskAssessment]         │
│                                                                 │
│   内部组合 4 个 Policy：                                          │
│   ├─ BlocklistPolicy    → 命令黑名单 (HIGH/SEVERE)              │
│   ├─ MetacharPolicy     → 元字符（PS 白名单） (LOW/MEDIUM)      │
│   ├─ GitWritePolicy     → Git 写操作 (MEDIUM)                   │
│   └─ PathPolicy         → 路径白名单 (MEDIUM)                   │
│                                                                 │
│   顶层处理 sandbox_mode alias：                                  │
│   ├─ "off"     → 短路：仅返回 critical 相关 (SEVERE)            │
│   ├─ "manual"  → 全部降级为 MEDIUM，错误信息走引导分支         │
│   └─ "sandbox" → 正常评估                                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 聚合后的 list[RiskAssessment]
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  错误聚合 + 智能建议 (新)                          │
│   PathHintFormatter.format(rejected_path, ctx) -> str           │
│   RiskReporter.aggregate(assessments, command, ctx) -> str      │
│   - 列出当前 thread 所有可写目录                                 │
│   - 推荐 data/workspace/.scratch/{filename}                     │
│   - 每条 assessment 附"如何重写"建议                              │
└─────────────────────────────────────────────────────────────────┘
```

## 2. 关键数据类

### 2.1 `RiskLevel`（`app/security/risk.py`）

```python
class RiskLevel(IntEnum):
    """风险等级。值越大越严重。"""
    NONE = 0       # 完全安全
    LOW = 1        # 元字符在引号内 / argv 模式 — 可放行
    MEDIUM = 2     # Git 写 / 路径未授权 — 走审批或引导
    HIGH = 3       # 命令在黑名单 — 拒绝 + 提示
    SEVERE = 4     # 系统关键目录 / 路径穿越 — 拒绝 + 审计
```

**为何用 IntEnum**：方便 `max()` 聚合最高风险；Future-proof 加新等级不破坏兼容。

### 2.2 `RiskAssessment`（`app/security/risk.py`）

```python
@dataclass(frozen=True)
class RiskAssessment:
    """单条风险评估结果。"""
    level: RiskLevel
    policy_name: str       # "blocklist" / "metachar" / "git_write" / "path"
    rule_id: str           # "forbidden_cmd:rm" / "ps_pipe_in_shell" / ...
    message: str           # 人类可读的原因
    matched_chars: tuple[str, ...] = ()  # 元字符场景：命中的字符
    suggestion: str = ""   # 如何重写（如 "改用 ';' 拆分"）
```

### 2.3 `ExecutionContext`（`app/security/context.py`）

```python
@dataclass(frozen=True)
class ExecutionContext:
    """执行上下文 — RiskClassifier 的输入。"""
    exec_mode: Literal["argv_list", "shell_string"]
    trust_mode: Literal["workspace", "full_trust"]
    thread_id: str
    authorized_paths: tuple[Path, ...]   # 不可变快照
    scratch_path: Path
    sandbox_mode: Literal["sandbox", "off", "manual"]

    @property
    def is_argv(self) -> bool:
        return self.exec_mode == "argv_list"

    @property
    def is_full_trust(self) -> bool:
        return self.trust_mode == "full_trust"

    @property
    def is_path_unrestricted(self) -> bool:
        """off 模式：所有路径放行（除 critical）。"""
        return self.sandbox_mode == "off"
```

**Builder**：
```python
def build_cli_execute_context(
    thread_id: str,
    sandbox: SessionSandbox,
) -> ExecutionContext:
    """cli_execute 调用方使用的 builder。"""
    settings = get_settings()
    return ExecutionContext(
        exec_mode="argv_list",
        trust_mode="full_trust" if sandbox.is_full_trust_sync(thread_id) else "workspace",
        thread_id=thread_id,
        authorized_paths=tuple(p for p, _ in sandbox.list_authorized_sync(thread_id)),
        scratch_path=SCRATCH_DIR,
        sandbox_mode=settings.sandbox_mode,
    )

def build_shell_backend_context(
    thread_id: str,
    sandbox: SessionSandbox,
) -> ExecutionContext:
    """safe_shell_backend 调用方使用的 builder。"""
    settings = get_settings()
    return ExecutionContext(
        exec_mode="shell_string",
        trust_mode="full_trust" if sandbox.is_full_trust_sync(thread_id) else "workspace",
        thread_id=thread_id,
        authorized_paths=tuple(p for p, _ in sandbox.list_authorized_sync(thread_id)),
        scratch_path=SCRATCH_DIR,
        sandbox_mode=settings.sandbox_mode,
    )
```

## 3. Policy 实现要点

### 3.1 `MetacharPolicy`（`app/security/policies/metachar.py`）

**核心算法**（`assess(command, ctx) -> list[RiskAssessment]`）：

```
1. 若 ctx.is_argv → 返回 [] (argv 模式天然免疫)
2. shell_string 模式：
   a. 扫描命令顶层是否在 powershell / pwsh / cmd 包装器
      → 是：进入 PowerShell 模式
      → 否：进入 POSIX 严格模式
3. 扫描未引号区域 + 引号感知区域
   - POSIX 严格：单引号内放行 / 双引号内仅 $ 与反引号仍拦 / 未引号全拦
   - PowerShell 模式：单引号 + 双引号内仅 $_ $Var @{...} [...] 字面量放行
     其余元字符 (; & | ` < > $) 仍拦
4. 每条命中生成 RiskAssessment(level=LOW 或 MEDIUM)
```

**PowerShell 字面量白名单**（正则）：
```python
_PS_LITERAL_RE = re.compile(
    r"\$_\b"              # $_ (current pipeline object)
    r"|\$\{?[A-Za-z_]\w*\}?"  # $var / ${var}
    r"|@\{[^}]*\}"        # @{key=val; ...} hashtable literal
    r"|\[[A-Za-z_][\w\.:]*\]"  # [Type] / [math]::Round(...)
)
```

**关键不变式**：`;` `&` `` ` `` 在 PS 模式仍拦；`|` 在 PS 双引号外仍拦（PS 管道是 shell 注入主战场）。

### 3.2 `BlocklistPolicy` / `GitWritePolicy`

**直接复用现有 `is_command_blocked` / `is_git_write_command`**，包成 `assess(command, ctx) -> list[RiskAssessment]`。

### 3.3 `PathPolicy`

**用途**：检查命令字符串中是否含路径 token，未授权时报 MEDIUM。
**注意**：**不**替代 `session_sandbox.check_*`（那是路径级独立校验）。本 policy 只在 `cli_execute` / `safe_shell_backend` 路径上做"快速预检"，避免对 `subprocess.run` 启动后才发现路径不可写。

### 3.4 `RiskClassifier.assess` 聚合

```python
def assess(self, command: str, ctx: ExecutionContext) -> list[RiskAssessment]:
    if ctx.is_path_unrestricted:
        # off 模式：仅 critical 相关返回 SEVERE
        return self._assess_critical_only(command, ctx)

    assessments: list[RiskAssessment] = []
    for policy in self._policies:  # [Blocklist, Metachar, GitWrite, Path]
        assessments.extend(policy.assess(command, ctx))

    if ctx.sandbox_mode == "manual":
        # 全部降级为 MEDIUM + 附引导建议
        return [
            RiskAssessment(
                level=RiskLevel.MEDIUM,
                policy_name=a.policy_name,
                rule_id=a.rule_id,
                message=a.message,
                suggestion=_manual_suggestion(a, ctx),
            )
            for a in assessments
        ]
    return assessments
```

## 4. 错误信息聚合（`RiskReporter.aggregate`）

```python
def aggregate(
    assessments: list[RiskAssessment],
    command: str,
    ctx: ExecutionContext,
) -> str:
    """把所有命中的风险聚合成一段 LLM 友好的错误信息。"""
    if not assessments:
        return ""

    lines: list[str] = [f"命令无法执行（{len(assessments)} 项命中）：\n"]
    for i, a in enumerate(assessments, 1):
        lines.append(f"  [{i}] {a.policy_name}.{a.rule_id}（{a.level.name}）")
        lines.append(f"      原因：{a.message}")
        if a.suggestion:
            lines.append(f"      建议：{a.suggestion}")
        if a.matched_chars:
            lines.append(f"      命中字符：{', '.join(repr(c) for c in a.matched_chars)}")
    lines.append(f"\n原命令：{command[:200]}{'...' if len(command) > 200 else ''}")
    return "\n".join(lines)
```

## 5. 路径错误信息增强（`PathHintFormatter.format`）

```python
def format(
    rejected_path: str | Path,
    action: Literal["read", "write"],
    ctx: ExecutionContext,
    authorized_paths: list[tuple[Path, bool]],
) -> str:
    """为 LLM 列出"该改写到哪"。"""
    raw = str(rejected_path).strip()
    fname = Path(raw).name or "tmp"

    lines: list[str] = [f"路径 {raw} 未授权{action}。"]

    # 1. 列出已授权目录（最多 5 个 + scratch）
    writable = [p for p, w in authorized_paths if w]
    if writable:
        lines.append("\n当前会话已授权的可写目录：")
        for p in writable[:5]:
            lines.append(f"  • {p}")
    lines.append(f"\n推荐改写到 scratch（始终可写）：")
    lines.append(f"  data/workspace/.scratch/{fname}")

    if action == "write":
        lines.append("\n或通过 dialog 授权该目录（勾选'允许写入'）后重试。")
    else:
        lines.append("\n或通过 dialog 授权该目录后重试。")

    return "\n".join(lines)
```

## 6. 关键时序

### 6.1 `cli_execute`（argv 模式）

```
用户 → cli_execute(command, arguments, thread_id, ...)
       ↓
       build_cli_execute_context(thread_id, sandbox) → ctx
       ↓
       RiskClassifier.assess(command, ctx)  # argv 模式：Metachar 短路
       ↓ (可能命中：Blocklist 命中 / Path 预检失败)
       RiskReporter.aggregate(...) → 返回字符串
       ↓
       shutil.which(command) → 启动子进程
       ↓
       subprocess.run([exe, *arguments], shell=False, env=safe_env)
       ↓
       格式化输出返回
```

### 6.2 `safe_shell_backend.execute`（shell 模式）

```
用户 → execute(command)
       ↓
       build_shell_backend_context(...) → ctx
       ↓
       RiskClassifier.assess(command, ctx)  # PS 模式：$_ 放行
       ↓
       若 git 写：return ExecuteResponse(走审批流)
       ↓
       若其他命中：return ExecuteResponse(聚合错误信息, exit_code=126)
       ↓
       super().execute(command) → LocalShellBackend
       ↓
       沙箱升级分析（已有逻辑保留）
```

## 7. 兼容性策略

| 旧 API | 新 API | 保留策略 |
|---|---|---|
| `has_forbidden_args(value, respect_quotes)` | `MetacharPolicy.assess(command, ctx)` | **保留旧 API**，内部委托新实现 |
| `is_command_blocked(command)` | `BlocklistPolicy.assess(command, ctx)` | 完全保留 |
| `is_git_write_command(command)` | `GitWritePolicy.assess(command, ctx)` | 完全保留 |
| `redact_args(tool_name, args)` | 不变 | 完全保留 |
| `get_forbidden_chars(value)` | 替换为 `assessments` 聚合 | **保留**作为 `RiskAssessment.matched_chars` 解析辅助 |
| `FORBIDDEN_ARG_PATTERN`（模块级常量） | 不变 | 保留（测试用 + 旧 API 兼容） |

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| PowerShell 放宽后误放行真正的注入 | 保留 `;` `&` `` ` `` 拦截；`|` 在双引号外仍拦；新增 12+ 个 PS 回归 case |
| `argv` 模式取消元字符过滤后日志注入 | `redact_args` token/password 脱敏保留；前端 ApprovalDialog 渲染时做 HTML escape |
| `ExecutionContext` 在多线程下被误改 | `frozen=True` dataclass + builder 函数，无 setter |
| 路径信息增强暴露用户私人目录 | 只在 LLM 调用的拒绝路径上输出；前端 UI 不显示 |
| `RiskClassifier` 单例与 settings 缓存 | 每次 `assess` 时通过 builder 重新生成 context（cheap） |
