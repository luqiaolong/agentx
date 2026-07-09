# 任务追踪 — 深度对齐 DeepAgents 框架特性

## 预期修改文件

- [x] `backend/app/subagents/rag_agent.py` — `create_react_agent` → `create_agent`
- [x] `backend/app/subagents/web_agent.py` — `create_react_agent` → `create_agent`
- [x] `pyproject.toml` — 删除 `filterwarnings` 中 `create_react_agent` 条目
- [x] `backend/app/__init__.py` — 删除 `warnings.filterwarnings` 块
- [x] `backend/app/deep/agent.py` — `build_deep_agent` 暴露 `rubric` + `grader_model`
- [x] `backend/app/agents/supervisor/work_supervisor.py` — `build_work_supervisor` 暴露 `rubric` + `grader_model`
- [x] `backend/app/agents/expert/coding.py` — `build_coding_expert` 暴露 `rubric` + `grader_model`
- [x] `backend/app/deep/harness.py` — 添加 `permissions=` + `SafeLocalShellBackend` 替代 `FilesystemBackend`
- [x] `backend/app/deep/safe_shell_backend.py` — **新文件** `SafeLocalShellBackend(LocalShellBackend)`
- [x] `backend/app/deep/tools.py` — 移除自研 `cli_execute` 工具注册
- [x] `backend/app/security/dangerous_tools.py` — `DANGEROUS_TOOLS` 中 `cli_execute` → `execute`
- [x] `backend/app/security/command_filter.py` — `redact_args` 适配 `execute` 工具名
- [x] `tests/python/unit/test_deepagents_integration.py` — 扩展全特性验证

## OpenSpec Tasks

| ID | 优先级 | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|--------|---------|---------|---------|------|
| T1 | P0 | rag_agent 迁移到 create_agent | `subagents/rag_agent.py` | `build_rag_agent` 用 `create_agent`，不再 import `create_react_agent` | ✅ |
| T2 | P0 | web_agent 迁移到 create_agent | `subagents/web_agent.py` | `build_web_agent` 用 `create_agent`，不再 import `create_react_agent` | ✅ |
| T3 | P0 | 删除 deprecation 压制 | `pyproject.toml`, `backend/app/__init__.py` | grep `create_react_agent has been moved` 全 0 命中 | ✅ |
| T4 | P1 | build_deep_agent 暴露 rubric | `deep/agent.py` | `build_deep_agent(rubric=...)` 透传到 `create_agent` | ✅ |
| T5 | P1 | build_work_supervisor 暴露 rubric | `agents/supervisor/work_supervisor.py` | `build_work_supervisor(rubric=...)` 透传到 `create_agent` | ✅ |
| T6 | P1 | build_coding_expert 暴露 rubric | `agents/expert/coding.py` | `build_coding_expert(rubric=...)` 透传到 `create_agent` | ✅ |
| T7 | P2 | harness 添加 FilesystemPermission | `deep/harness.py` | `create_agent` 接受 `permissions=` 参数，默认注入 deny 规则 | ✅ |
| T8 | P3 | 创建 SafeLocalShellBackend | `deep/safe_shell_backend.py` | 继承 `LocalShellBackend`，override `execute` 添加 blocklist + 元字符过滤 | ✅ |
| T9 | P3 | harness 用 SafeLocalShellBackend | `deep/harness.py` | `resolve_backend` 返回 `SafeLocalShellBackend`，`_EXCLUDED_BUILTIN_TOOLS` 移除 `execute` | ✅ |
| T10 | P3 | 移除自研 cli_execute 工具 | `deep/tools.py` | `_make_deep_tools` 不再注册 `cli_execute` | ✅ |
| T11 | P3 | DANGEROUS_TOOLS 更新 | `security/dangerous_tools.py` | `cli_execute` → `execute` | ✅ |
| T12 | P3 | redact_args 适配 | `security/command_filter.py` | `redact_args` 支持 `execute` 工具名 | ✅ |
| T13 | P0 | 测试验证 + 扩展 | `tests/python/unit/test_deepagents_integration.py` | 现有测试全通过 + 新增 SafeLocalShellBackend / RubricMiddleware / FilesystemPermission 验证 | ✅ |

## 规模判定

- 涉及文件数: 13（12 修改 + 1 新增） → 规模: **L（大改）**
- 涉及模块数: 5（subagents/ + deep/ + agents/ + security/ + 配置 + tests）
- 规模: **L（大改）** — 全流程（Worktree + TDD + 双轨 Review + 部署验证 + 归档）

## P4 评估结论（不实施）

- **SandboxBackend**: 远程沙箱（Modal/Daytona）不适用本地桌面应用，不替代 `SessionSandbox`

## 完成情况

- T1-T13 全部完成
- 单元测试: 1052 passed / 3 skipped / 2 xfailed（2 个预存在 `test_workspace_api.py` 失败与本次改动无关）
- T13 新增 13 个测试覆盖: permissions 默认注入 / 自定义透传 / 无 workspace 静态基线 / RubricMiddleware 注入 / grader_model 优先 / SafeLocalShellBackend 继承链 + blocklist + 元字符过滤 + 空命令拦截 / execute 进入 DANGEROUS_TOOLS
