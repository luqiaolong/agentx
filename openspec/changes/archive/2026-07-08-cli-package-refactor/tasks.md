# 任务追踪 — CLI 包重构

## 预期修改文件

### 新增（backend/app/cli/ 包）
- [x] `backend/app/cli/__init__.py` — 包导出 main
- [x] `backend/app/cli/app.py` — 主入口 + argparse + 模式分发
- [x] `backend/app/cli/repl.py` — REPL 主循环
- [x] `backend/app/cli/one_shot.py` — One-shot 模式
- [x] `backend/app/cli/approval.py` — 审批交互
- [x] `backend/app/cli/commands.py` — CommandResult + 命令分发 + _cmd_*
- [x] `backend/app/cli/renderer.py` — EventRenderer（迁移自 cli_render.py）
- [x] `backend/app/cli/store.py` — Tauri store 配置读取（迁移自 cli_store.py）

### 删除（扁平文件）
- [x] `backend/app/cli.py` — 删除（内容拆分到 cli/ 包）
- [x] `backend/app/cli_render.py` — 删除（迁移到 cli/renderer.py）
- [x] `backend/app/cli_store.py` — 删除（迁移到 cli/store.py）

### 测试新增
- [x] `tests/python/unit/test_cli_app.py`
- [x] `tests/python/unit/test_cli_renderer.py`
- [x] `tests/python/unit/test_cli_store.py`（已有，更新 import 路径）
- [x] `tests/python/unit/test_cli_commands.py`
- [x] `tests/python/unit/test_cli_approval.py`
- [x] `tests/python/unit/test_cli_repl.py`

### 文档更新
- [x] `AGENTS.md` — 文件地图更新 cli 包结构

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | 创建 cli/ 包骨架 + __init__.py | cli/__init__.py | `from app.cli import main` 可用 | ✅ |
| T2 | 迁移 store.py（cli_store.py → cli/store.py） | cli/store.py | 函数签名不变，行为等价 | ✅ |
| T3 | 迁移 renderer.py（cli_render.py → cli/renderer.py） | cli/renderer.py | EventRenderer 类等价 | ✅ |
| T4 | 新建 commands.py：CommandResult + handle_command + _cmd_* | cli/commands.py | 所有 async 命令 await，无 fire-and-forget | ✅ |
| T5 | 新建 approval.py：handle_approval | cli/approval.py | 审批输入解析正确 | ✅ |
| T6 | 新建 repl.py：run_repl + consume_events | cli/repl.py | REPL 循环正确，命令调用 await | ✅ |
| T7 | 新建 one_shot.py：run_one_shot + SIGINT | cli/one_shot.py | One-shot 执行正确，Ctrl+C 优雅退出 | ✅ |
| T8 | 新建 app.py：main + argparse + config 子命令 | cli/app.py | 参数解析正确，config 子命令可用 | ✅ |
| T9 | 删除旧扁平文件 | cli.py/cli_render.py/cli_store.py | 旧文件删除，import 不报错 | ✅ |
| T10 | 新增 /history 命令 | cli/commands.py | 列出当前 thread 最近 N 条消息 | ✅ |
| T11 | 单元测试 test_cli_app.py | test_cli_app.py | 参数解析、模式选择、main 分发 | ✅ |
| T12 | 单元测试 test_cli_renderer.py | test_cli_renderer.py | 各事件渲染、verbose/json | ✅ |
| T13 | 单元测试 test_cli_store.py | test_cli_store.py | 配置解析、解密、路径候选 | ✅ |
| T14 | 单元测试 test_cli_commands.py | test_cli_commands.py | CommandResult 分发、各命令、await 验证 | ✅ |
| T15 | 单元测试 test_cli_approval.py | test_cli_approval.py | 审批输入、EOF 处理 | ✅ |
| T16 | 文档更新 AGENTS.md 文件地图 | AGENTS.md | cli 包结构描述更新 | ✅ |

## 规模判定

- 涉及文件数：16（8 新增 + 3 删除 + 5 测试） → 规模: **L**
- 涉及模块数：1（backend/app/cli）
- 路径：完整 Ralph 流程（Worktree + TDD + 双轨 Review + 部署验证）

## 验收结果

1. ✅ `python -m app.cli.app --version` 输出 `AgentX CLI v0.2.0`
2. ✅ 149 单元测试全部通过（`pytest tests/python/unit/test_cli*.py -v`）
3. ✅ 所有 async 命令正确 `await`（无 fire-and-forget）
4. ✅ 旧文件已删除，新 cli/ 包结构生效
5. ✅ 迁移至 master 的 app.security.approval + app.sandbox 新 API
6. ✅ 已合并到 master（merge commit `711b194` + fix commit `984d573`）
