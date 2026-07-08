# 任务追踪 — CLI 模式支持

## 预期修改文件
- [ ] `backend/app/cli_store.py` (新建)
- [ ] `backend/app/cli_render.py` (新建)
- [ ] `backend/app/cli.py` (新建)
- [ ] `pyproject.toml` (修改: 添加 [project.scripts] + colorama 依赖)
- [ ] `tests/python/test_cli_store.py` (新建)
- [ ] `tests/python/test_cli_render.py` (新建)
- [ ] `tests/python/test_cli.py` (新建)

## OpenSpec Tasks
| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | cli_store.py: Tauri store 配置读取 + enc: 解密 | cli_store.py | 能解析 config.json 并映射到 AGENTX_* env vars | ⬜ |
| T2 | cli_render.py: 终端事件渲染器 | cli_render.py | 支持 token/reasoning/tool_call/tool_result/approval_request/done/error 渲染 | ⬜ |
| T3 | cli.py: REPL + One-shot 主入口 | cli.py | 支持 REPL 交互、One-shot、管道、--json、审批交互 | ⬜ |
| T4 | pyproject.toml: 注册 CLI 入口 | pyproject.toml | pip install -e . 后生成 agentx.exe | ⬜ |
| T5 | 单元测试 | test_cli_*.py | 覆盖配置加载、事件渲染、REPL 循环 | ⬜ |

## 规模判定
- 涉及文件数: 7 → 规模: M
- 涉及模块数: 1 (backend/app)
- 路径: 快速路径（需求明确，有完整 OpenSpec）
