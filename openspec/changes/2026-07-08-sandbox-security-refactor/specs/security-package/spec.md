# Spec: 安全策略与审批包（security-package）

## 概述

`backend/app/security/` 包提供安全策略管理，包括危险工具分类、CLI 命令过滤、参数脱敏、
审批状态机、审批执行流。与 `deep/` / `team/` / `tools/` 平行。

## 包结构

```
backend/app/security/
├── __init__.py                 ← 聚合导出
├── approval/
│   ├── __init__.py             ← 审批子包聚合导出
│   ├── decision.py             ← ApprovalDecision Enum + ApprovalResult
│   └── state.py                ← 审批状态机（TTL reaper + 原子原语）
├── dangerous_tools.py          ← DANGEROUS_TOOLS + FORBIDDEN_SUBAGENT_TOOLS + runtime 计算
├── command_filter.py           ← CLI 命令黑名单 + 元字符过滤 + 参数脱敏
└── approval_flow.py            ← 审批执行流（公共 interrupt_before 循环）
```

## 公共 API

### `approval/decision.py`

```python
from enum import Enum

class ApprovalDecision(str, Enum):
    APPROVE = "approve"   # 批准（dangerous_tool）/ 会话级批准（directory_extension）
    ONCE = "once"         # 仅本次批准（directory_extension）
    SESSION = "session"   # 会话级批准（directory_extension，等价 APPROVE）
    DENY = "deny"         # 拒绝

@dataclass
class ApprovalResult:
    decision: ApprovalDecision
    path: str | None = None
    writable: bool = False
    
    @property
    def approved(self) -> bool:
        """派生属性：decision != DENY。"""
        return self.decision != ApprovalDecision.DENY
```

### `approval/state.py`

```python
async def submit_approval(thread_id: str, result: ApprovalResult) -> None:
    """提交审批决策。更新 timestamp。"""

async def pop_approval(thread_id: str) -> ApprovalResult | None:
    """弹出审批决策（非阻塞）。更新 timestamp。"""

async def set_abort(thread_id: str) -> None:
    """设置中止标志 + set Event。"""

async def is_aborted(thread_id: str) -> bool:
    """检查中止标志。"""

async def clear_abort(thread_id: str) -> None:
    """清除中止标志 + Event。"""

async def wait_for_abort(thread_id: str, timeout: float | None = None) -> bool:
    """原子原语：检查中止 + 等待 Event。"""

async def set_pause(thread_id: str) -> None:
    """设置暂停标志。"""

async def clear_pause(thread_id: str) -> None:
    """清除暂停标志 + set Event。"""

async def wait_for_resume(thread_id: str, timeout: float | None = None) -> bool:
    """原子原语：检查暂停 + 等待恢复 Event。消除 check-then-wait 竞态。"""

async def start_reaper() -> asyncio.Task:
    """启动后台 reaper 协程。每 5 分钟清理 30 分钟无活动的 thread_id。"""
```

### `dangerous_tools.py`

```python
DANGEROUS_TOOLS: frozenset[str] = frozenset({
    "edit_file", "write_file", "cli_execute",
    "git_clone", "git_pull", "git_checkout", "git_stage", "git_commit",
})
# 注：已移除 "shell_exec" 死代码

FORBIDDEN_SUBAGENT_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file",
    "git_clone", "git_pull", "git_checkout", "git_stage", "git_commit",
})
# 注：已移除 "shell_exec" 死代码；cli_execute 由 _make_custom_tools 的白名单过滤另行拦截

def compute_runtime_dangerous(
    enabled_tool_names: set[str],
    mcp_untrusted_names: set[str],
) -> frozenset[str]:
    """计算运行时危险工具集合 = (DANGEROUS_TOOLS ∩ enabled) ∪ mcp_untrusted。"""
```

### `command_filter.py`

```python
DEFAULT_BLOCKLIST: frozenset[str] = frozenset({
    "rm", "del", "format", "sudo", "chmod", "kill", "killall",
    "shutdown", "reboot", "mkfs", "dd",
})

FORBIDDEN_ARG_PATTERN: re.Pattern = re.compile(r"[;&|`$<>]")

def is_command_blocked(command: str) -> bool:
    """检查命令是否在黑名单或含元字符。"""

def has_forbidden_args(args: str) -> bool:
    """检查参数是否含 shell 元字符。"""

def redact_args(tool_name: str, args: dict | list | str) -> dict:
    """统一参数脱敏。
    
    - write_file / edit_file: 脱敏 content / new_text / old_text
    - cli_execute: 脱敏 command / arguments 中的 token=xxx / password=xxx / user:pass@host
    - git_clone: 脱敏 URL 中的凭证
    - 其他: 原样返回
    """
```

### `approval_flow.py`

```python
async def run_approval_loop(
    graph,
    config: dict,
    thread_id: str,
    workspace_path: str | None,
    permission_mode: str,  # "standard" | "full_trust"
    runtime_dangerous: frozenset[str],
    agent_tools: list,
    yield_event,  # Callable[[dict], Awaitable[None]]
    is_aborted_fn,  # Callable[[str], Awaitable[bool]]
    sandbox: SessionSandbox,
    parent_thread_id: str | None = None,
) -> AsyncIterator[dict]:
    """统一的 interrupt_before 审批循环。
    
    流程：
    1. graph.astream 消费事件
    2. 检测 interrupt → 提取 pending tool_calls
    3. full_trust 模式 → 跳过所有审批（含 directory_extension 预检查）
    4. 判定 dangerous_calls（runtime_dangerous ∩ tool_names）
       - workspace 授权仅放行 fs 工具，cli_execute 仍需审批命令内容
    5. dangerous_tool 审批：逐个 yield approval_request → _await_approval
       - 路径检查传 base=workspace_path
       - approval_max_wait=0 时上限 3600s
    6. directory_extension 审批：收集越界只读路径 → 批量审批
       - 修复工作区免审批死代码（is_path_authorized 逻辑修正）
    7. 恢复执行或注入错误
    """
```

## 审批决策语义

| 决策 | dangerous_tool 场景 | directory_extension 场景 |
|---|---|---|
| `APPROVE` | 批准执行，恢复 graph | 会话级授权（持久化） |
| `ONCE` | 等同 APPROVE | 仅本次授权（临时，不持久化） |
| `SESSION` | 等同 APPROVE | 会话级授权（等价 APPROVE） |
| `DENY` | 拒绝，注入 ToolMessage 错误 | 拒绝，注入错误 |

## TTL Reaper 机制

- `_pending_approvals` / `_abort_flags` / `_abort_events` / `_pause_flags` / `_pause_events`
  的 value 携带 timestamp（最后活动时间）
- reaper 协程每 5 分钟扫描，清理 30 分钟无活动的 thread_id
- `submit_approval` / `pop_approval` / `get_*_event` / `set_*` / `clear_*` 更新 timestamp
- `app.main` lifespan 启动 `asyncio.create_task(start_reaper())`，关闭时取消

## 并发安全

- `approval/state.py` 所有公共方法为 `async`，用 `_state_lock = asyncio.Lock()` 保护
- `wait_for_resume` / `wait_for_abort` 在锁内完成 check + event 获取，消除竞态
- 单进程约束：模块级 dict 仅进程内可见，多 worker 部署需 `--workers 1`（文档标注）

## 审批流路径基准

- 所有 `is_path_authorized` / `check_read` / `check_write` 调用传 `base=workspace_path`
- 审批层与执行层路径基准一致（修复旧 bug：审批层用 PROJECT_ROOT，执行层用 workspace_path）
- `_extract_paths_from_tool_call` 接受 `workspace_path` 参数，对 `cli_execute` 无 cwd 时不回退 workspace 自动放行

## full_trust 模式行为

- `permission_mode == "full_trust"` 时跳过所有审批（dangerous_tool + directory_extension）
- `sandbox.set_full_trust(thread_id, True)` 在 run 开始时启用，finally 块关闭
- full_trust 不持久化（重启后自动降级）
- 仍拒绝系统关键目录（`is_critical` 检查不可跳过）
