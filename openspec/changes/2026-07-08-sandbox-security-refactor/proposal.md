# Proposal: 沙箱与安全权限包重构

## Why

当前 AgentX 的沙箱授权、审批流、危险工具分类、命令过滤等安全相关代码分散在 7 个位置：
`utils/security.py`、`api/sandbox.py`、`memory/sandbox_store.py`、`approval/`、
`deep/approval.py`、`deep/tools.py`、`tools/cli.py`。这导致：

1. **职责混乱**：`utils/security.py` 既做路径归一化（纯函数）又做会话级授权状态管理（有状态单例）
2. **重复代码**：`work_supervisor.py` 与 `coding.py` 的审批循环逻辑几乎完全相同（~200 行）
3. **16 个已确认 Bug**：从并发安全、跨平台路径、审批流断裂到内存泄漏，覆盖严重/中/低各级别
4. **边界模糊**：危险工具分类（`deep/tools.py`）、命令过滤（`tools/cli.py`）、
   审批决策（`approval/`）、沙箱校验（`utils/security.py`）四者本应同属"安全域"，
   却分散在 4 个顶级包

本次重构抽取 `sandbox/` 与 `security/` 两个顶级包，与 `deep/`、`team/`、`tools/` 平行，
同时修复全部已识别 Bug，建立清晰的安全域边界。

## What Changes

### 新建 `backend/app/sandbox/` 包（沙箱路径授权）

- `session_sandbox.py`：`SessionSandbox`（从 `utils/security.py` 迁移）+ `asyncio.Lock` 并发保护
- `store.py`：`SandboxStore`（从 `memory/sandbox_store.py` 迁移）+ WAL 模式 + 显式 timeout
- `api.py`：`/api/sandbox/*` 路由（从 `api/sandbox.py` 迁移）+ 审计日志
- `schemas.py`：`AuthorizeRequest`/`RevokeRequest`（从 `api/schemas.py` 提取）
- `path_guard.py`：路径归一化 + 关键目录保护（从 `utils/security.py` 提取）+ 修复 Linux `Path("/")` bug

### 新建 `backend/app/security/` 包（安全策略与审批）

- `approval/decision.py`：`ApprovalDecision` Enum 化（消除 `approved`/`decision` 矛盾）
- `approval/state.py`：审批状态机 + TTL reaper + 修复 pause check-then-wait 竞态
- `dangerous_tools.py`：`DANGEROUS_TOOLS` + `runtime_dangerous` 计算（从 `deep/tools.py` 提取）+ 清理 `shell_exec` 死代码
- `command_filter.py`：CLI 命令黑名单 + 元字符过滤 + 参数脱敏（从 `tools/cli.py` + `deep/approval.py` 提取）
- `approval_flow.py`：审批执行流公共逻辑（消除 `work_supervisor.py` / `coding.py` 重复）

### Bug 修复（后端 16 项）

| 级别 | Bug | 修复 |
|---|---|---|
| 严重 | Linux 下 `Path("/")` 导致所有绝对路径被判关键目录 | `path_guard.py` 对 `/` 仅拒绝本身，不拒绝后代 |
| 严重 | `SessionSandbox` 无锁并发不安全 | `session_sandbox.py` 加 `asyncio.Lock` 保护所有数据结构 |
| 严重 | `/api/sandbox/*` 端点无审计日志 | `api.py` 加审计日志（本地桌面应用 localhost only，无需 token 鉴权） |
| 严重 | `approval/state.py` 模块级 dict 多进程失效 | 保留单进程实现 + 文档标注单 worker 部署约束；加 TTL reaper 防泄漏 |
| 严重 | `directory_extension` 工作区免审批死代码 | `approval_flow.py` 修复 `is_path_authorized` 逻辑 |
| 高 | Team 模式子任务 thread_id 不继承 workspace 授权 | `approval_flow.py` 加 parent_thread_id 映射，子任务继承父授权 |
| 高 | `is_path_authorized` 未传 `base=workspace_path` | 所有审批检查传 `base=workspace_path` |
| 高 | `cli_execute` 无 cwd 自动免审批 | workspace 授权仅放行 fs 工具，CLI 仍需审批命令内容 |
| 中 | `cli_execute` 命令参数未脱敏 | `command_filter.redact_args` 统一脱敏 |
| 中 | 内存/DB 双写无事务 | `store.py` 先写 DB 再改内存，DB 失败则抛异常不更新内存 |
| 中 | SQLite 未启用 WAL | `store.py` 加 `PRAGMA journal_mode=WAL` + `timeout=30` |
| 中 | 五个 dict 无 TTL/reaper | `state.py` 加后台 reaper 协程清理 30 分钟未活动的 thread_id |
| 中 | pause check-then-wait 竞态 | `state.py` 提供 `wait_for_resume()` 原子原语 |
| 中 | `ApprovalDecision` approved/decision 矛盾 | `decision.py` 改为单一 Enum 字段 |
| 中 | `shell_exec` 死代码 | 从 `DANGEROUS_TOOLS` / `FORBIDDEN_SUBAGENT_TOOLS` 移除 |
| 中 | `approval_max_wait=0` 无限阻塞 | 加绝对上限 3600s 兜底 |

### Bug 修复（前端 6 项）

| 级别 | Bug | 修复 |
|---|---|---|
| 高 | `revokedPaths` 恒为空（`storeState` 应为 `session`） | `ChatView.tsx` 改为 `session?.manuallyRevokedPaths` |
| 中 | HTTP 错误静默吞没（不检查 `r.ok`） | `http.ts` 的 `sandbox.authorize` / `approve.submit` 加 `r.ok` 检查并抛错 |
| 中 | 批量 directory_extension 只显示最后一条 | `useChatStream.ts` 改为队列，`ApprovalDialog` 逐条展示 |
| 低 | `usePermissionStore` 死代码 | 删除 `stores/permission.ts`，类型统一到 `shared/api-types.ts` |
| 低 | `PermissionMode` 三处重复定义 | 统一到 `shared/api-types.ts` |
| 中 | `full_trust` 仍可能弹审批框 | 后端 `approval_flow.py` 在 full_trust 模式跳过 directory_extension 预检查 |

### 删除的旧文件

- `backend/app/utils/security.py`（迁移到 `sandbox/` + `security/path_guard.py`）
- `backend/app/api/sandbox.py`（迁移到 `sandbox/api.py`）
- `backend/app/memory/sandbox_store.py`（迁移到 `sandbox/store.py`）
- `backend/app/approval/`（迁移到 `security/approval/`）
- `backend/app/deep/approval.py`（公共逻辑迁移到 `security/approval_flow.py`，DeepAgent 专用部分保留为薄封装）
- `frontend/renderer/stores/permission.ts`（死代码）

## Capabilities

### New Capabilities

- `sandbox-package`：沙箱路径授权包，提供会话级目录授权管理（CRUD + 持久化 + 并发安全）
- `security-package`：安全策略包，提供危险工具分类、命令过滤、审批状态机、审批执行流

### Modified Capabilities

- `dangerous-operation-approval`：审批流从 `deep/` 迁移到 `security/`，修复 6 个严重 bug
- `filesystem-sandbox`：沙箱从 `utils/` 迁移到 `sandbox/`，修复并发安全 + Linux 路径 bug

## Impact

- **后端**：
  - 新建 `sandbox/`（5 文件）+ `security/`（6 文件）共 11 个新文件
  - 删除 5 个旧文件（`utils/security.py` / `api/sandbox.py` / `memory/sandbox_store.py` / `approval/` / `deep/approval.py`）
  - 修改 ~15 个文件的 import 路径（`deep/agent.py` / `agents/supervisor/*` / `agents/expert/*` / `agents/team/*` / `router/graph.py` / `tools/filesystem.py` / `tools/cli.py` 等）
- **前端**：
  - 修改 4 文件（`ChatView.tsx` / `http.ts` / `useChatStream.ts` / `ApprovalDialog.tsx`）
  - 删除 1 文件（`stores/permission.ts`）
  - 统一类型到 `shared/api-types.ts`
- **API**：无新增端点，`/api/sandbox/*` 路由行为不变（仅迁移 + 加审计日志）
- **测试**：新增 `sandbox/` + `security/` 包单元测试；现有测试更新 import
- **文档**：更新 `AGENTS.md` §11 文件地图 + §14.3 沙箱与安全章节

## Future Extensibility

- `security/approval/state.py` 未来可替换为 Redis 后端实现多进程审批（当前单进程 + 文档标注约束）
- `sandbox/` 未来可扩展网络沙箱（限制 agent 可访问的域名/IP）
- `security/command_filter.py` 未来可扩展为可配置策略（用户自定义命令白名单/黑名单）
