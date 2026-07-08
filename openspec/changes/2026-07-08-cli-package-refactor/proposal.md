# Proposal: CLI 包重构 — 对齐 team/ 包结构

## Why

当前 CLI 相关代码以 3 个扁平文件散落在 `backend/app/` 根目录（`cli.py` / `cli_render.py` / `cli_store.py`），与已规范化的 `team/` 包（5 个子模块）形成鲜明对比。存在以下问题：

1. **`cli.py` 过胖（690 行）**：混杂参数解析、REPL 循环、命令分发、审批交互、One-shot 入口——违反单一职责。
2. **结构不一致**：`team/` 是规范包，CLI 是扁平文件，新贡献者难以快速定位。
3. **测试缺失**：尽管旧 OpenSpec（`2026-07-08-cli-mode-support`）声称已建测试，实际 `tests/python/unit/test_cli*.py` 不存在。
4. **fire-and-forget bug**：`/compact`、`/threads`、`/abort`、`/pause`、`/resume` 命令用 `asyncio.get_event_loop().create_task()` 派发后从未 await，任务可能被 GC 回收或异常被吞。
5. **返回类型不一致**：`_handle_command` 返回 `str | tuple[str] | None`，调用方判断逻辑脆弱。

## What Changes

### 结构重构：扁平文件 → `backend/app/cli/` 包

```
backend/app/cli/
├── __init__.py          # 包导出：main
├── app.py               # main() + argparse + 模式分发
├── repl.py              # run_repl + REPL 主循环
├── one_shot.py          # run_one_shot
├── approval.py          # _handle_approval + 终端审批交互
├── commands.py          # _handle_command + 所有 _cmd_* 命令
├── renderer.py          # EventRenderer（迁移自 cli_render.py）
└── store.py             # Tauri store 配置读取 + 解密（迁移自 cli_store.py）
```

### Bug 修复

- **fire-and-forget**：`/compact` / `/threads` / `/abort` / `/pause` / `/resume` 改为 `await` 正确调用
- **返回类型**：`_handle_command` 统一返回 `CommandResult` dataclass（`CONTINUE` / `SWITCH_MODE` / `QUIT`）
- **event loop**：移除 `asyncio.get_event_loop()`（已 deprecated），改用 `asyncio` 直接 `await`

### 缺失功能补全

- **单元测试**：新增 `test_cli_render.py` / `test_cli_store.py` / `test_cli_commands.py` / `test_cli_app.py`
- **`/history` 命令**：列出当前 thread 最近 N 条消息摘要
- **信号处理**：One-shot 模式下 SIGINT 优雅退出（REPL 已有处理）
- **`config` 子命令**：`agentx config show` / `agentx config get <key>` / `agentx config set <key> <value>`（终端直接管理配置，无需 GUI）

### 向后兼容

- `pyproject.toml` 入口点更新：`agentx = "app.cli:main"`（包导出同名，外部调用零感知）
- 旧路径 `app.cli_render` / `app.cli_store` 删除（开发阶段无需灰度，按项目约束直接推倒）

## Capabilities

### Modified Capabilities

- `cli-mode`：从扁平文件重构为包结构，补全测试和缺失功能

## Impact

- **后端**：
  - 新增 `backend/app/cli/` 包（8 个文件）
  - 删除 `backend/app/cli.py` / `cli_render.py` / `cli_store.py`
  - 修改 `pyproject.toml`（入口点不变，仅路径解析变化）
  - 新增 4 个测试文件
- **前端**：无改动
- **API**：无新增端点
- **配置**：无变化
- **测试**：新增 CLI 包全覆盖单元测试
- **文档**：更新 AGENTS.md 文件地图

## Future Extensibility

- 后续可扩展 `--file` 参数（文件上传）
- 后续可扩展 `agentx chat` / `agentx config` / `agentx models` 子命令结构
