# Design: CLI 包重构

## 1. 设计目标

1. **结构对齐**：与 `team/` 包同构，每个文件单一职责，≤200 行
2. **可测试**：所有可纯函数化的逻辑（arg 解析、命令分发、渲染器）独立成模块，可单测
3. **修复 bug**：消除 fire-and-forget、返回类型不一致
4. **补全能力**：测试覆盖、`/history`、`config` 子命令、信号处理

## 2. 模块拆分

### 2.1 `app/cli/__init__.py`

仅导出 `main`，保持 `pyproject.toml` 入口点 `agentx = "app.cli:main"` 零改动。

### 2.2 `app/cli/app.py` — 主入口 + 参数解析

职责：
- `_build_parser()` → `argparse.ArgumentParser`
- `_resolve_agent_mode(args)` → `str`
- `_read_stdin_if_piped()` → `str | None`
- `main()` → 模式分发（REPL / One-shot / config 子命令）

迁移自 `cli.py` 的入口段。**新增** `config` 子命令分发到 `app/cli/commands.py:_cmd_config_*`。

### 2.3 `app/cli/repl.py` — REPL 交互

职责：
- `run_repl()` 主循环
- `_consume_events()` 事件消费
- `_print_banner()`

迁移自 `cli.py` 的 REPL 段。**修复**：所有命令调用改为 `await`。

### 2.4 `app/cli/one_shot.py` — One-shot 模式

职责：
- `run_one_shot()` 单次任务

迁移自 `cli.py` 的 One-shot 段。**新增**：SIGINT 信号处理（One-shot 下 Ctrl+C 优雅退出）。

### 2.5 `app/cli/approval.py` — 审批交互

职责：
- `_handle_approval(thread_id)` 终端阻塞审批

迁移自 `cli.py` 的 `_handle_approval`。无逻辑改动，独立成模块便于单测。

### 2.6 `app/cli/commands.py` — REPL 命令分发 + 命令实现

职责：
- `CommandResult` dataclass：`CONTINUE` / `SWITCH_MODE(mode)` / `QUIT`
- `handle_command(cmd, ctx) -> CommandResult`：统一入口，替代旧 `_handle_command` 的 `str | tuple | None`
- 各 `_cmd_*` 命令实现（全部 async，正确 await）：
  - `_cmd_reset` / `_cmd_mode` / `_cmd_init` / `_cmd_model` / `_cmd_models`
  - `_cmd_compact` / `_cmd_abort` / `_cmd_pause` / `_cmd_resume` / `_cmd_clear`
  - `_cmd_thread` / `_cmd_threads` / `_cmd_history`（**新增**）

**关键修复**：
- 旧 `_cmd_compact` / `_cmd_threads` / `_cmd_abort` / `_cmd_pause` / `_cmd_resume` 用 `asyncio.get_event_loop().create_task()` fire-and-forget → 改为 `await` 直接调用
- `_cmd_reset` 的 `adelete_thread` 同样改为 `await`

**新增**：
- `_cmd_history(thread_id, checkpointer, limit=10)`：从 checkpointer 读取最近 N 条消息摘要打印

### 2.7 `app/cli/renderer.py` — 事件渲染器

迁移自 `cli_render.py`，类名 `EventRenderer` 保持不变。无逻辑改动。

### 2.8 `app/cli/store.py` — Tauri store 配置读取

迁移自 `cli_store.py`，函数名 `load_tauri_store_config` / `apply_config_to_env` / `decrypt_credential` 保持不变。无逻辑改动。

## 3. CommandResult 设计

```python
from dataclasses import dataclass
from enum import Enum

class CommandAction(Enum):
    CONTINUE = "continue"
    SWITCH_MODE = "switch_mode"
    QUIT = "quit"

@dataclass
class CommandResult:
    action: CommandAction
    new_mode: str | None = None  # 仅 SWITCH_MODE 时使用

    @classmethod
    def continue_(cls) -> "CommandResult":
        return cls(action=CommandAction.CONTINUE)

    @classmethod
    def quit(cls) -> "CommandResult":
        return cls(action=CommandAction.QUIT)

    @classmethod
    def switch_mode(cls, mode: str) -> "CommandResult":
        return cls(action=CommandAction.SWITCH_MODE, new_mode=mode)
```

调用方：
```python
result = await handle_command(cmd, ctx)
if result.action == CommandAction.QUIT:
    break
if result.action == CommandAction.SWITCH_MODE:
    current_mode = result.new_mode
```

## 4. 测试策略

| 测试文件 | 覆盖模块 | 覆盖点 |
|---------|---------|--------|
| `test_cli_app.py` | app.py | 参数解析、模式选择、stdin 读取、main 分发 |
| `test_cli_renderer.py` | renderer.py | 各事件类型渲染、verbose/json 模式、reset |
| `test_cli_store.py` | store.py | 配置解析、凭证解密（plain/裸/enc）、路径候选 |
| `test_cli_commands.py` | commands.py | CommandResult 分发、各 _cmd_* 命令、fire-and-forget 修复验证 |
| `test_cli_approval.py` | approval.py | 审批输入解析（y/n/o/s）、EOF/Ctrl+C 处理 |

## 5. 向后兼容策略

按项目硬约束（开发阶段无需灰度）：
- 删除旧文件 `cli.py` / `cli_render.py` / `cli_store.py`
- 不保留 re-export shim
- `pyproject.toml` 入口点 `agentx = "app.cli:main"` 不变（包 `__init__.py` 导出 `main`）

## 6. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| `asyncio.get_event_loop()` 移除后某些命令行为变化 | 低 | 中 | 测试覆盖每个命令 |
| checkpointer `adelete_thread` / `aput` 在某些实现不存在 | 中 | 低 | 已有 `hasattr` 保护，保持 |
| DPAPI 解密测试无法在非 Windows 运行 | 高 | 低 | 测试用 `skipif(not win32)` 跳过 |
