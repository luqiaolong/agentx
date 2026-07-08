# Spec: CLI 包结构（cli-package）

## 概述

将 AgentX CLI 从扁平文件重构为 `backend/app/cli/` 包，与 `team/` 包同构，每个文件单一职责。

## 包结构

```
backend/app/cli/
├── __init__.py          # 导出 main
├── app.py               # main() + argparse + 模式分发
├── repl.py              # run_repl + REPL 循环
├── one_shot.py          # run_one_shot
├── approval.py          # _handle_approval
├── commands.py          # handle_command + CommandResult + _cmd_*
├── renderer.py          # EventRenderer
└── store.py             # load_tauri_store_config + apply_config_to_env + decrypt_credential
```

## 模块职责

### `__init__.py`

```python
from app.cli.app import main
__all__ = ["main"]
```

入口点 `agentx = "app.cli:main"` 保持不变。

### `app.py`

| 函数 | 职责 |
|------|------|
| `_build_parser()` | 构建 argparse，支持 message/--thread/--work/--coding/--coding-team/--workspace/--verbose/--json/--version |
| `_resolve_agent_mode(args)` | 解析为 `work` / `coding` / `coding_team` |
| `_read_stdin_if_piped()` | 管道输入读取 |
| `main()` | 模式分发入口 |

**新增**：`config` 子命令分发（`agentx config show` / `get` / `set`）。

### `repl.py`

| 函数 | 职责 |
|------|------|
| `run_repl(...)` | REPL 主循环，加载配置、获取 checkpointer、循环读取输入 |
| `_consume_events(...)` | 消费 SSE 事件流，处理审批 |
| `_print_banner(...)` | 打印 banner |

### `one_shot.py`

| 函数 | 职责 |
|------|------|
| `run_one_shot(...)` | 单次任务执行，返回退出码 |

**新增**：SIGINT 信号处理，Ctrl+C 优雅退出。

### `approval.py`

| 函数 | 职责 |
|------|------|
| `_handle_approval(thread_id)` | 终端阻塞审批交互 |

输入：`y`/`n`/`o`/`s`，Ctrl+C/D → 拒绝。

### `commands.py`

#### CommandResult

```python
@dataclass
class CommandResult:
    action: CommandAction  # CONTINUE / SWITCH_MODE / QUIT
    new_mode: str | None = None
```

#### 命令清单

| 命令 | 行为 | 异步 |
|------|------|------|
| `/quit` `/q` | 返回 QUIT | 否 |
| `/help` | 打印帮助 | 否 |
| `/reset` | 清空会话历史 + 沙箱 | 是（await adelete_thread） |
| `/mode <mode>` | 切换模式 | 否 |
| `/init` | 授权 workspace | 否 |
| `/model` | 显示当前模型 | 否 |
| `/models` | 列出可用模型 | 否 |
| `/compact` | 压缩会话历史 | 是（await） |
| `/abort` | 中止生成 | 是（await set_abort） |
| `/pause` | 暂停生成 | 是（await set_pause） |
| `/resume` | 恢复生成 | 是（await clear_pause） |
| `/clear` | 清屏 | 否 |
| `/thread` | 显示 thread_id | 否 |
| `/threads` | 列出历史会话 | 是（await list_threads） |
| `/history [N]` | 显示当前 thread 最近 N 条消息 | 是（新增） |

**关键约束**：所有 async 命令必须 `await`，禁止 `asyncio.get_event_loop().create_task()` fire-and-forget。

### `renderer.py`

`EventRenderer` 类，迁移自 `cli_render.py`，支持事件类型：`token` / `reasoning` / `tool_call` / `tool_result` / `approval_request` / `todo_update` / `delegation` / `team_plan` / `team_progress` / `team_result` / `team_done` / `classification` / `plan` / `plan_update` / `error` / `done`。

### `store.py`

迁移自 `cli_store.py`，函数签名不变：
- `load_tauri_store_config() -> dict[str, str]`
- `apply_config_to_env(overrides) -> dict[str, str]`
- `decrypt_credential(stored) -> str | None`

## 测试覆盖

| 测试文件 | 覆盖点 |
|---------|--------|
| `test_cli_app.py` | 参数解析、模式选择、stdin、main 分发 |
| `test_cli_renderer.py` | 各事件渲染、verbose/json 模式 |
| `test_cli_store.py` | 配置解析、plain/裸解密、路径候选 |
| `test_cli_commands.py` | CommandResult 分发、各命令、await 验证 |
| `test_cli_approval.py` | 审批输入解析、EOF 处理 |

## 向后兼容

- `pyproject.toml` 入口点不变
- 旧文件 `cli.py` / `cli_render.py` / `cli_store.py` 删除
- 不保留 re-export shim
