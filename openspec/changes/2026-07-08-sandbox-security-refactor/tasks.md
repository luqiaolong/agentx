# 任务追踪 — 沙箱与安全权限包重构

## 预期修改文件

### 后端新建文件
- [x] `backend/app/sandbox/__init__.py`
- [x] `backend/app/sandbox/path_guard.py`
- [x] `backend/app/sandbox/store.py`
- [x] `backend/app/sandbox/session_sandbox.py`
- [x] `backend/app/sandbox/schemas.py`
- [x] `backend/app/sandbox/api.py`
- [x] `backend/app/security/__init__.py`
- [x] `backend/app/security/approval/__init__.py`
- [x] `backend/app/security/approval/decision.py`
- [x] `backend/app/security/approval/state.py`
- [x] `backend/app/security/dangerous_tools.py`
- [x] `backend/app/security/command_filter.py`
- [x] `backend/app/security/approval_flow.py`

### 后端删除文件
- [x] `backend/app/utils/security.py`
- [x] `backend/app/api/sandbox.py`
- [x] `backend/app/memory/sandbox_store.py`
- [x] `backend/app/approval/` 整个目录
- [x] `backend/app/deep/approval.py`

### 后端修改文件
- [x] `backend/app/agents/supervisor/work_supervisor.py`（调用 run_approval_loop + await sandbox）
- [x] `backend/app/agents/expert/coding.py`（调用 run_approval_loop + parent_thread_id）
- [x] `backend/app/team/scheduler.py`（传 parent_thread_id + _inherit_workspace async）
- [x] `backend/app/router/graph.py`（await sandbox.authorize + import 路径）
- [x] `backend/app/deep/tools.py`（DANGEROUS_TOOLS 迁出 + 保留工具构建）
- [x] `backend/app/deep/agent.py`（import 路径 + parent_thread_id 参数）
- [x] `backend/app/tools/filesystem.py`（import 路径 + await sandbox.check_*）
- [x] `backend/app/tools/cli.py`（命令过滤迁出 + import 路径）
- [x] `backend/app/api/__init__.py`（register_sandbox_routes 路径）
- [x] `backend/app/api/schemas.py`（移除 AuthorizeRequest/RevokeRequest）
- [x] `backend/app/api/chat.py`（import 路径）
- [x] `backend/app/config/subagents.py`（FORBIDDEN_SUBAGENT_TOOLS 迁出）
- [x] `backend/app/main.py`（lifespan 启动 reaper）
- [x] `backend/app/subagents/base.py`（import 路径）
- [x] `backend/app/subagents/custom_agent.py`（import 路径）
- [x] `backend/app/api/project_config.py`（合并后迁移到 app.sandbox 新 API）

### 前端修改文件
- [x] `frontend/renderer/components/chat/ChatView.tsx`（revokedPaths bug）
- [x] `frontend/renderer/lib/api/http.ts`（HTTP 错误处理）
- [x] `frontend/renderer/hooks/useChatStream.ts`（批量审批队列）
- [x] `frontend/renderer/components/chat/ApprovalDialog.tsx`（队列消费）
- [x] `frontend/renderer/components/chat/PermissionToggle.tsx`（import 路径）
- [x] `frontend/renderer/stores/chat/index.ts`（import 路径）
- [x] `frontend/shared/api-types.ts`（PermissionMode 单一来源）

### 前端删除文件
- [x] `frontend/renderer/stores/permission.ts`

### 测试文件
- [x] `tests/python/unit/test_sandbox_path_guard.py`
- [x] `tests/python/unit/test_sandbox_store.py`
- [x] `tests/python/unit/test_sandbox_session.py`
- [x] `tests/python/unit/test_security_decision.py`
- [x] `tests/python/unit/test_security_state.py`
- [x] `tests/python/unit/test_security_dangerous_tools.py`
- [x] `tests/python/unit/test_security_command_filter.py`
- [x] `tests/python/unit/test_security_approval_flow.py`

### 文档
- [x] `AGENTS.md`（§11 文件地图 + §14.3 沙箱与安全 + §14.4 SSE/审批流 + §17 + §18）

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1.1 | path_guard.py: 路径归一化 + 修复 Linux bug | sandbox/path_guard.py | Linux 绝对路径不误判；Windows 不变 | ✅ |
| T1.2 | store.py: SQLite WAL + timeout | sandbox/store.py | 并发写不锁；WAL 生效 | ✅ |
| T1.3 | session_sandbox.py: 加锁 + DB-first + parent_thread_id | sandbox/session_sandbox.py | 并发安全；DB 一致；父授权继承 | ✅ |
| T1.4 | schemas.py: AuthorizeRequest/RevokeRequest | sandbox/schemas.py | schema 迁移 | ✅ |
| T1.5 | api.py: /api/sandbox/* + 审计日志 | sandbox/api.py | 端点正常；日志输出 | ✅ |
| T1.6 | sandbox/__init__.py 聚合导出 | sandbox/__init__.py | import 可用 | ✅ |
| T1.7 | sandbox/ 单元测试 | test_sandbox_*.py | 全部通过 | ✅ |
| T2.1 | decision.py: ApprovalDecision Enum 化 | security/approval/decision.py | 矛盾消除；property 兼容 | ✅ |
| T2.2 | state.py: TTL reaper + wait_for_resume | security/approval/state.py | reaper 清理；无竞态死锁 | ✅ |
| T2.3 | approval/__init__.py 聚合导出 | security/approval/__init__.py | import 可用 | ✅ |
| T2.4 | dangerous_tools.py: DANGEROUS_TOOLS + 清理 shell_exec | security/dangerous_tools.py | 无 shell_exec；compute 正确 | ✅ |
| T2.5 | command_filter.py: 命令过滤 + 脱敏 | security/command_filter.py | cli_execute 命令脱敏 | ✅ |
| T2.6 | security/__init__.py 聚合导出 | security/__init__.py | import 可用 | ✅ |
| T2.7 | security/ 单元测试 | test_security_*.py | 全部通过 | ✅ |
| T3.1 | approval_flow.py: 公共审批循环 + 6 bug 修复 | security/approval_flow.py | work/coding 一致；6 bug 修复 | ✅ |
| T3.2 | 重构 work_supervisor.py | agents/supervisor/work_supervisor.py | 行为不变；-200 行 | ✅ |
| T3.3 | 重构 coding.py + parent_thread_id | agents/expert/coding.py | 行为不变；Team 继承生效 | ✅ |
| T3.4 | scheduler.py 传 parent_thread_id | team/scheduler.py | Team 审批不断裂 | ✅ |
| T3.5 | graph.py await sandbox.authorize | router/graph.py | 授权正常 | ✅ |
| T3.6 | approval_flow 单元测试 | test_security_approval_flow.py | 全部通过 | ✅ |
| T4.1 | ChatView.tsx revokedPaths bug | ChatView.tsx | 撤销路径不被重新授权 | ✅ |
| T4.2 | http.ts HTTP 错误处理 | lib/api/http.ts | 4xx/5xx 正确抛错 | ✅ |
| T4.3 | useChatStream.ts 批量审批队列 | useChatStream.ts + ApprovalDialog.tsx | 逐条展示 | ✅ |
| T4.4 | 删除 permission.ts + 统一类型 | stores/permission.ts + api-types.ts | 无死代码；类型单一 | ✅ |
| T4.5 | 前端测试 | tests/renderer/ | 全部通过 | ✅ |
| T5.1 | 后端全量 import 迁移 | ~15 文件 | ruff 无报错；grep 无旧路径 | ✅ |
| T5.2 | 删除旧文件 | 5 处 | grep 无残留 | ✅ |
| T5.3 | api/__init__.py 路由注册 | api/__init__.py | 端点正常 | ✅ |
| T5.4 | main.py lifespan reaper | main.py | reaper 运行 | ✅ |
| T5.5 | schemas.py 移除迁移项 | api/schemas.py | 无重复 | ✅ |
| T6.1 | 端到端测试 | 三场景两模式 | 全部通过 | ✅ |
| T6.2 | 更新 AGENTS.md | AGENTS.md | 文档一致 | ✅ |
| T6.3 | 全量回归测试 | pytest + vitest + ruff + tsc | 全部通过 | ✅ |

## 规模判定
- 涉及文件数: ~35（13 新建 + 5 删除 + 15 修改 + 8 测试 + 1 文档）→ 规模: **L**
- 涉及模块数: 6（sandbox / security / deep / agents / router / frontend）
- 路径: 完整路径（L 级全流程：Worktree → TDD → 双轨 Review → 部署验证 → 归档）

## 完成总结

- **提交历史**（feature/sandbox-security-refactor 分支）：
  - `0d4f4d1` feat(sandbox): 新增 sandbox/ 包
  - `c2515aa` feat(security): 新增 security/ 包
  - `902de79` refactor(approval): 提取 approval_flow.py 公共审批循环
  - `9d909e7` fix(frontend): 修复 6 个前端权限控制 bug
  - `d3cfd35` refactor(migration): Phase 5 全量 import 迁移
  - `3ae0dfa` docs: 更新 AGENTS.md
  - `f7ec240` fix(review): 修复 Adversarial Review 发现的 5 个问题

- **合并到 master**：
  - `368e220` Merge feature/sandbox-security-refactor into master
  - `100a28a` fix(merge): 解决合并冲突 + 修复 project_config 迁移

- **测试结果**：
  - 后端：880 pytest passed, 3 skipped, 2 xfailed
  - 前端：402 vitest passed
  - TypeScript：tsc --noEmit 通过
  - Ruff：clean

- **Review 修复**：
  - CRITICAL #1: deep/agent.py parent_thread_id 传递
  - CRITICAL #2: team/scheduler.py parent_thread_id 传递
  - MEDIUM #6: 恢复 _handle_directory_extension 第二轮检查
  - LOW #8: state.py docstring 修正
  - LOW #9: main.py 注释更新
