# 任务追踪 — agentx-project-config

## 预期修改文件

### 新建文件（后端）
- [x] `backend/app/project_config/__init__.py`
- [x] `backend/app/project_config/templates.py`
- [x] `backend/app/project_config/generator.py`
- [x] `backend/app/project_config/loader.py`
- [x] `backend/app/project_config/merger.py`
- [x] `backend/app/api/project_config.py`
- [x] `tests/python/unit/test_project_config.py`
- [x] `tests/python/unit/test_project_config_api.py`

### 新建文件（前端）
- [x] `frontend/renderer/lib/api/projectConfig.ts`
- [x] `frontend/renderer/components/workspace/ProjectConfigBadge.tsx`
- [x] `tests/renderer/project-config-badge.test.tsx`

### 修改文件（后端）
- [x] `backend/app/api/__init__.py`（注册新路由）
- [x] `backend/app/api/schemas.py`（新增 ProjectConfigInitRequest）
- [x] `backend/app/router/graph.py`（加载项目配置 + 注入上下文）

### 修改文件（前端）
- [x] `frontend/renderer/components/chat/ChatComposer.tsx`（授权后触发生成）
- [x] `frontend/renderer/components/workspace/WorkspacePanel.tsx`（显示徽章）
- [x] `frontend/shared/api-types.ts`（新增类型）

### 修改文件（文档）
- [x] `AGENTS.md`（§11 文件地图 + §16 配置入口 + §17 修改前必读清单）

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | 模板与生成器 | `project_config/templates.py` + `project_config/generator.py` | 幂等生成，第二次调用 created=[] | ✅ |
| T2 | 加载器 | `project_config/loader.py` | 缺失文件降级，JSON 错误不崩溃 | ✅ |
| T3 | 合并器 | `project_config/merger.py` | 无项目配置时等价于原 Settings | ✅ |
| T4 | API 端点 | `api/project_config.py` + `api/__init__.py` + `api/schemas.py` | 未授权路径 400，已授权 200 | ✅ |
| T5 | router 集成 | `router/graph.py` | 有 .agentx/ 时 AI 遵循规则 | ✅ |
| T6 | 前端 API client | `lib/api/projectConfig.ts` + `shared/api-types.ts` | 类型安全，调用正确 | ✅ |
| T7 | ChatComposer 集成 | `components/chat/ChatComposer.tsx` | 授权后自动生成 .agentx/ | ✅ |
| T8 | ProjectConfigBadge | `components/workspace/ProjectConfigBadge.tsx` + `WorkspacePanel.tsx` | 状态展示 + 点击生成 | ✅ |
| T9 | 后端单元测试 | `tests/python/unit/test_project_config*.py` | 全部通过 | ✅ |
| T10 | 前端测试 | `tests/renderer/project-config-badge.test.tsx` | 全部通过 | ✅ |
| T11 | 文档更新 | `AGENTS.md` | 文档与代码一致 | ✅ |
| T12 | 端到端验证 | 手动 | 全部场景通过 | ⬜ |

## 规模判定

- 涉及文件数: 18（8 新建 + 7 修改 + 3 测试）→ **L 规模**
- 涉及模块数: 3（后端 project_config + 后端 API/router + 前端）
- 跨层: 是（Python 后端 + TypeScript 前端 + Rust 无改动）
- 流程: 完整 OpenSpec + Worktree + TDD + 双轨 Review + 部署验证 + 归档

## Phase 3 Review 结果

双轨 Review（L 级）发现 3 CRITICAL + 6 HIGH issues，已在 Phase 4 全部修复：

- C1 API critical dir 校验漏洞 → `_is_critical_path` 双向检查
- C2 绕过 SessionSandbox → 增加 thread_id + sandbox.check_read
- C3 merge_configs 死代码 → router 调用 merge_configs 注入上下文
- H1 README.md 被当 rule → _RULES_SKIP_NAMES 排除
- H2 async 热路径同步 IO → asyncio.to_thread 包装
- H3 symlink 攻击 → is_symlink() 检查
- H4 无大小上限 → 32KB 截断
- H5 前端 fetch 不检查 r.ok → 改用 apiPost/apiGet
- H6 subagents 浅合并 → _deep_merge 递归合并 + ValidationError 降级
