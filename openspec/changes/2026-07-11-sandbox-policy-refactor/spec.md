# Spec: 沙箱风险分级重构（sandbox-policy）

> 本 spec 是 2026-07-08 sandbox-package / security-package spec 的**增量条款**（delta）。
> 既有条款不变；本 spec 仅追加能力与调整。

## 概述

把 `app/security/` 与 `app/sandbox/` 中独立运行的 4 条规则（命令黑名单 / 元字符过滤 / Git 写操作 / 路径白名单）整合为
**以 `RiskClassifier` 为中心、`ExecutionContext` 为驱动、`sandbox_mode` 为顶层 alias** 的统一风险评估体系。

## 新增能力

### 1. `app/security/risk.py` — 风险等级与评估

```python
from enum import IntEnum

class RiskLevel(IntEnum):
    """风险等级。值越大越严重。"""
    NONE = 0       # 完全安全
    LOW = 1        # 元字符在引号内 / argv 模式 — 可放行
    MEDIUM = 2     # Git 写 / 路径未授权 — 走审批或引导
    HIGH = 3       # 命令在黑名单 — 拒绝 + 提示
    SEVERE = 4     # 系统关键目录 / 路径穿越 — 拒绝 + 审计


@dataclass(frozen=True)
class RiskAssessment:
    """单条风险评估结果。"""
    level: RiskLevel
    policy_name: str       # "blocklist" / "metachar" / "git_write" / "path"
    rule_id: str           # "forbidden_cmd:rm" / "ps_pipe_in_shell" / ...
    message: str           # 人类可读的原因
    matched_chars: tuple[str, ...] = ()
    suggestion: str = ""


class RiskClassifier:
    """风险分类器。组合 4 个 Policy，统一处理 sandbox_mode alias。"""

    def __init__(self, policies: Sequence[RiskPolicy] | None = None): ...

    def assess(self, command: str, ctx: ExecutionContext) -> list[RiskAssessment]:
        """评估命令风险。

        - sandbox_mode=off：仅返回 critical 相关 (SEVERE)
        - sandbox_mode=manual：全部降级为 MEDIUM + 附引导建议
        - sandbox_mode=sandbox：正常评估
        """


class RiskPolicy(Protocol):
    """单条规则的最小接口。"""
    name: str

    def assess(self, command: str, ctx: ExecutionContext) -> list[RiskAssessment]: ...
```

### 2. `app/security/context.py` — 执行上下文

```python
@dataclass(frozen=True)
class ExecutionContext:
    """RiskClassifier 的输入。frozen 保证不可变。"""
    exec_mode: Literal["argv_list", "shell_string"]
    trust_mode: Literal["workspace", "full_trust"]
    thread_id: str
    authorized_paths: tuple[Path, ...]
    scratch_path: Path
    sandbox_mode: Literal["sandbox", "off", "manual"]

    @property
    def is_argv(self) -> bool: ...
    @property
    def is_full_trust(self) -> bool: ...
    @property
    def is_path_unrestricted(self) -> bool: ...


def build_cli_execute_context(thread_id: str, sandbox: SessionSandbox) -> ExecutionContext: ...
def build_shell_backend_context(thread_id: str, sandbox: SessionSandbox) -> ExecutionContext: ...
```

### 3. `app/security/policies/metachar.py` — 元字符 Policy

**核心行为**（`assess(command, ctx)`）：

| 条件 | 行为 |
|---|---|
| `ctx.is_argv` | 返回 `[]`（argv 模式天然免疫） |
| shell_string + 顶层是 `powershell` / `pwsh` / `cmd` | **PowerShell 模式**：单/双引号内 `$_` `$Var` `@{...}` `[...]` 字面量放行；`;` `&` `|` `` ` `` `<` `>` 在引号外仍拦 |
| shell_string + 顶层是其他 | **POSIX 严格模式**：单引号内全放行；双引号内仅 `$` `` ` `` 仍拦；其余位置全拦 |
| argv 列表中包含元字符 | 委托 `MetacharPolicy.assess_for_argv` 检查（**不**拦，仅记录为 INFO 级） |

**PowerShell 字面量白名单正则**：

```python
_PS_LITERAL_RE = re.compile(
    r"\$_\b"                          # $_  (PS current pipeline object)
    r"|\$\{?[A-Za-z_]\w*\}?"          # $var / ${var}
    r"|@\{[^}]*\}"                    # @{k=v; ...} hashtable
    r"|\[[A-Za-z_][\w\.:]*\]"         # [Type] / [math]::Round
)
```

**关键不变式**：
- `;` `&` 在 PS 模式仍拦
- `` ` `` 在 PS 模式仍拦
- `|` 在 PS 双引号外仍拦（管道是 PS shell 注入主战场）
- `<` `>` 在 PS 模式仍拦（重定向可被利用）

### 4. `app/security/policies/blocklist.py` — 命令黑名单 Policy

**直接复用** `command_filter.is_command_blocked`，包成 `assess(command, ctx) -> list[RiskAssessment]`：

- 命令在 `DEFAULT_BLOCKLIST` ∪ 用户配置 → 返回 1 条 HIGH 级 assessment
- 递归解析包装器（`cmd /c` / `powershell -Command` / `python -c` 等）— 复用 `_extract_inner_command`
- 包装器内命令在 blocklist → 同样 HIGH

### 5. `app/security/policies/git_write.py` — Git 写操作 Policy

**直接复用** `command_filter.is_git_write_command`，包成 `assess(command, ctx) -> list[RiskAssessment]`：

- Git 写操作（commit/push/checkout/...）→ 返回 1 条 MEDIUM 级 assessment
- 包装器内的 Git 命令也识别

### 6. `app/security/policies/path_policy.py` — 路径快速预检 Policy

**用途**：`cli_execute` / `safe_shell_backend` 启动前的"路径预检"。
**不替代** `session_sandbox.check_*`（那是独立校验入口）。

```python
class PathPolicy:
    def assess(self, command: str, ctx: ExecutionContext) -> list[RiskAssessment]:
        """提取命令中的路径 token，未授权时返回 MEDIUM。"""
```

### 7. `app/security/reporter.py` — 错误聚合

```python
def aggregate(
    assessments: list[RiskAssessment],
    command: str,
    ctx: ExecutionContext,
) -> str:
    """把所有命中聚合成 LLM 友好的错误信息。

    格式：
        命令无法执行（N 项命中）：
          [1] policy.rule_id（LEVEL）
              原因：xxx
              建议：xxx
              命中字符：'x', 'y'
          [2] ...

        原命令：xxx
    """
```

### 8. `app/security/path_hint.py` — 路径错误信息增强

```python
def format_unauthorized_hint(
    rejected_path: str | Path,
    action: Literal["read", "write"],
    authorized_paths: list[tuple[Path, bool]],
    scratch_path: Path,
) -> str:
    """为 LLM 列出"该改写到哪"。

    - 列出当前 thread 所有可写目录（最多 5 个）
    - 推荐 data/workspace/.scratch/{filename}
    - 引导通过 dialog 授权该目录
    """
```

**集成点**：`session_sandbox._unauthorized_read_hint` / `_unauthorized_write_hint` 调用 `format_unauthorized_hint` 替换字符串模板。

## 调整既有能力

### 调整 1：`sandbox_mode` 语义

`Settings.sandbox_mode` 由"独立开关"升级为 **`RiskClassifier` 的顶层 alias**：

| sandbox_mode | RiskClassifier 行为 | `check_read/write` 行为 |
|---|---|---|
| `"sandbox"`（默认） | 正常评估 | 正常校验 |
| `"off"` | 仅返回 critical 相关 (SEVERE) | 跳过所有路径校验（仍拒 critical） |
| `"manual"` | 全部降级 MEDIUM + 附引导建议 | 拒绝时返回人工执行提示 |

**兼容性**：既有 `check_read/write` 在 `sandbox_mode` 下的行为不变（`off` 短路 / `manual` 引导分支保留）。

### 调整 2：`cli_execute` 元字符过滤

`backend/app/tools/cli.py` 在 argv 列表模式下**取消**对每个参数跑 `has_forbidden_args`。
- 改由 `RiskClassifier.assess(command, ctx)` 在命令名层面统一评估
- `redact_args`（token/password 脱敏）保留
- `MetacharPolicy.assess_for_argv` 在 argv 模式只生成 INFO 级记录，不拦

### 调整 3：`safe_shell_backend.execute` 走 RiskClassifier

替换现有 4 条独立 if（`is_command_blocked` / `has_forbidden_args` / `is_git_write_command` / `_extract_inner_command`），
统一为：

```python
def execute(self, command, **kwargs) -> ExecuteResponse:
    command = command.strip()
    if not command:
        return ExecuteResponse(output="command 不能为空", exit_code=1, truncated=False)

    ctx = build_shell_backend_context(thread_id, sandbox)
    assessments = risk_classifier.assess(command, ctx)

    if assessments:
        # 分离 git 写（需走审批流）与其他（直接拒绝）
        git_writes = [a for a in assessments if a.policy_name == "git_write"]
        others = [a for a in assessments if a.policy_name != "git_write"]
        if git_writes and not others:
            return ExecuteResponse(output="git 写操作需审批...", exit_code=126, truncated=False)
        return ExecuteResponse(
            output=aggregate(assessments, command, ctx),
            exit_code=126,
            truncated=False,
        )

    result = super().execute(command, **kwargs)
    # 沙箱升级分析（既有逻辑保留）
    ...
    return result
```

**注**：`thread_id` 需从 `self.root_dir` 或调用方传入（沿用现有 `LocalShellBackend` 接口约束；若不可得则用 `"unknown"`）。

### 调整 4：路径错误信息

`session_sandbox._unauthorized_read_hint` / `_unauthorized_write_hint` 改用 `format_unauthorized_hint`。
现有字符串模板作为 fallback（若 `format_unauthorized_hint` 抛异常）。

## 兼容性

| 旧 API | 新 API | 保留策略 |
|---|---|---|
| `has_forbidden_args(value, respect_quotes)` | `MetacharPolicy.assess(command, ctx)` | **保留旧 API**，内部委托新实现 |
| `is_command_blocked(command)` | `BlocklistPolicy.assess(command, ctx)` | 完全保留 |
| `is_git_write_command(command)` | `GitWritePolicy.assess(command, ctx)` | 完全保留 |
| `redact_args(tool_name, args)` | 不变 | 完全保留 |
| `get_forbidden_chars(value)` | 替换为 `assessments` 聚合 | **保留**作为解析辅助 |
| `FORBIDDEN_ARG_PATTERN`（模块级常量） | 不变 | 保留（测试用 + 旧 API 兼容） |
| `sandbox_mode`（3 模式） | 调整为 RiskClassifier alias | 行为不变 |

## 错误处理

| 场景 | 行为 |
|---|---|
| `MetacharPolicy` 解析失败（正则匹配抛错） | 安全降级为 MEDIUM + 提示"解析失败，按风险评估" |
| `RiskClassifier.assess` 任意 Policy 抛异常 | 记录日志 + 跳过该 Policy，其余 Policy 继续 |
| `ExecutionContext` builder 取授权列表失败 | 退化为空 tuple（不会误授权，只会让 PathPolicy 拒绝） |
| `format_unauthorized_hint` 抛异常 | fallback 到旧字符串模板 |

## 测试覆盖

新增 `tests/python/unit/test_risk_classifier.py`（核心）：

- `test_argv_mode_skips_metachar` — `python -c "import time; print(time.time())"` 不拦
- `test_powershell_var_in_double_quotes_allowed` — `$_` `$var` `@{Name='X'}` `[math]::Round` 在双引号内放行
- `test_powershell_pipe_outside_quotes_blocked` — `Get-Process | Sort-Object` 仍拦（管道是注入主战场）
- `test_powershell_semicolon_always_blocked` — `;` 在 PS 任何位置都拦
- `test_sandbox_mode_off_short_circuits_to_critical` — off 模式只拦 critical
- `test_sandbox_mode_manual_downgrades_to_medium` — manual 模式全部 MEDIUM
- `test_multiple_assessments_aggregated` — 多条命中聚合输出
- `test_path_hint_lists_authorized_dirs` — 路径错误信息列出已授权目录
- `test_path_hint_recommends_scratch` — 路径错误信息推荐 scratch 改写
- `test_blocklist_in_wrapper_caught` — `cmd /c del file.txt` 仍拦
- `test_git_write_in_powershell_caught` — `powershell -Command "git commit ..."` 仍拦
- `test_legacy_has_forbidden_args_still_works` — 旧 API 兼容

更新 `test_security_command_filter.py` / `test_safe_shell_backend.py` / `test_session_sandbox.py`：
- 新增 PowerShell 合法语法 case 8 个
- 新增 cli_execute 取消元字符过滤 case 4 个
- 新增路径错误信息聚合 case 3 个
