# Tasks: backend/app 包结构重构

## 预期修改文件

### 新增文件 ✅
- [x] `backend/app/approval/__init__.py`
- [x] `backend/app/approval/state.py`
- [x] `backend/app/approval/decision.py`
- [x] `backend/app/api/__init__.py`
- [x] `backend/app/api/schemas.py`
- [x] `backend/app/api/health.py`
- [x] `backend/app/api/sandbox.py`
- [x] `backend/app/api/chat.py`
- [x] `backend/app/api/memory.py`
- [x] `backend/app/api/mcp.py`
- [x] `backend/app/api/skills.py`
- [x] `backend/app/api/workspace.py`
- [x] `backend/app/api/config_reload.py`
- [x] `backend/app/api/models_test.py`
- [x] `backend/app/config/__init__.py`
- [x] `backend/app/config/settings.py`
- [x] `backend/app/config/subagents.py`
- [x] `backend/app/config/prompts/__init__.py`
- [x] `backend/app/config/prompts/builtin.py`
- [x] `backend/app/config/prompts/team.py`
- [x] `backend/app/deep/tools.py`
- [x] `backend/app/deep/streaming.py`
- [x] `backend/app/deep/approval.py`
- [x] `backend/app/deep/recovery.py`
- [x] `backend/app/team/planner.py`
- [x] `backend/app/team/scheduler.py`
- [x] `backend/app/team/blackboard.py`
- [x] `backend/app/team/aggregator.py`
- [x] `backend/app/subagents/base.py`
- [x] `backend/app/utils/paths.py`

### 修改文件 ✅
- [x] `backend/app/main.py`（1019 → 198 行）
- [x] `backend/app/config.py` → 删除（拆为 `config/` 包）
- [x] `backend/app/deep/agent.py`（813 → ~200 行）
- [x] `backend/app/deep/__init__.py`
- [x] `backend/app/team/orchestrator.py`（681 → ~200 行）
- [x] `backend/app/team/__init__.py`
- [x] `backend/app/subagents/code_agent.py`
- [x] `backend/app/subagents/rag_agent.py`
- [x] `backend/app/subagents/web_agent.py`
- [x] `backend/app/subagents/custom_agent.py`
- [x] `backend/app/subagents/__init__.py`
- [x] `backend/app/utils/security.py`（移除 ApprovalDecision）
- [x] `backend/app/utils/sse_events.py`（合并 make_team_event）
- [x] `backend/app/tools/filesystem.py`（路径归一化改用 utils/paths）
- [x] `backend/app/router/graph.py`（仅 import 路径更新，无逻辑变更）
- [x] `AGENTS.md` §11 文件地图更新

### 删除文件 ✅
- [x] `backend/app/config.py`（迁移为 `config/` 包后删除原文件）

## 规模判定
- 涉及文件数: 45+ → 规模: **L**
- 涉及模块数: 9（main/config/deep/team/subagents/utils/approval/api/tools）

---

## Phase 1-7: 已完成 ✅

所有 Phase 1-7 的任务（approval 模块提取 / config 包拆分 / deep 5 文件拆分 / team 5 文件拆分 / subagents 基类 / main.py 拆 api 包 / utils 重复消除）均已实现，新文件全部存在，旧文件已删除/精简。

## Phase 8: 文档更新 + 最终验证 ✅

- [x] T8.1 更新 `AGENTS.md` §11 文件地图（api/approval/config/deep/team/subagents.base/utils.paths）
- [x] T8.2 §14.5 路径导入循环说明（approval 模块解耦后 deep 不再反射访问 main）
- [x] T8.3 全量测试：`uv run pytest tests/python/unit -m "not integration"` → **435 passed, 1 skipped, 2 xfailed**
- [x] T8.4 风格检查：`uv run ruff check backend/` → **All checks passed!**
- [x] T8.5 全量 grep 验证无残留反射访问
  - `getattr(main` → 无匹配 ✅
  - `main.py` 中 `_pending_approvals` → 无匹配 ✅
  - `main.py` 中 `_abort_flags` → 无匹配 ✅
  - `from app.paths` → 无匹配 ✅

## 最终验证
- pytest: 435 passed, 1 skipped, 2 xfailed ✅
- ruff: All checks passed ✅
- main.py: 1019 → 198 行 ✅
- config.py: 已删除 ✅
- 无残留反射访问 ✅
