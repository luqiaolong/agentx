# Proposal: backend/app 包结构重构

## 背景

`2026-07-06-paths-refactor` 完成了路径层的模块化拆分，但 `backend/app/` 仍存在以下结构性问题：

1. **巨型文件未拆**：
   - `main.py` 1019 行（lifespan + 中间件 + 15 个 Pydantic 模型 + 6 类 REST 端点全堆一个文件）
   - `deep/agent.py` 813 行（DeepAgent 构建 + 工具集 + 审批流 + SSE 流 + checkpoint 修复 + 画像抽取触发 7 类职责）
   - `team/orchestrator.py` 681 行（Orchestrator + 计划解析 + 子任务调度 + Blackboard + Aggregator + 质量门）
   - `config.py` 517 行（Settings + 7 个专家角色 prompt + SubagentSettings + CustomSubagentEntry + _parse_custom_subagents）

2. **跨模块私有状态反射访问**：`deep/agent.py` 通过 `getattr(main, "_pending_approvals")` 反向访问 `main.py` 的模块级私有 dict，产生运行时反向依赖，无法独立测试 deep 模块。

3. **跨模块重复代码**：
   - 路径归一化在 `utils/security.py:_normalize` 与 `tools/filesystem.py:_resolve` 重复实现
   - SSE 事件构造 `make_sse_event` 与 `make_team_event` 逻辑几乎一致
   - subagents 三个 agent 文件结构高度相似（_make_*_tools + run_*_agent + 事件转换）
   - `deep/agent.py` 跨包 import `subagents/code_agent._make_fs_tools` 私有函数

4. **职责边界泄漏**：
   - `utils/security.py` 混杂沙箱授权 + `ApprovalDecision`（应属审批层）
   - `team/orchestrator.py` 在 `_run_subtask` 内部直接 import 子代理实现细节
   - 7 个专家角色 prompt 硬编码在 `config.py`

## 目标

按能力域拆分巨型文件，提取 approval 模块解耦 deep ↔ main 反射依赖，消除重复代码，理清职责边界。**不改任何运行时行为**（功能 100% 等价）。

## 范围

- 纯后端 Python 代码重构，不改前端、不改 Tauri
- 不改 SSE 事件契约（AGENTS.md §13）
- 不改 API 端点签名
- 不改 Router 四路径公共签名（`run_router` / `run_chat_path` / `run_tool_path` / `run_deep_path` / `run_team_path`）
- **不重新创建** `backend/app/paths/` 目录（AGENTS.md §14.5 硬约束）

## 预期收益

| 文件 | 重构前 | 重构后 |
|---|---|---|
| `main.py` | 1019 行 | ~150 行（仅 lifespan + app + 中间件） |
| `deep/agent.py` | 813 行 | ~200 行（仅编排） |
| `team/orchestrator.py` | 681 行 | ~200 行（仅编排） |
| `config.py` | 517 行 | ~200 行（仅 Settings） |

- `deep` 模块可独立单元测试（不再反射访问 `main._pending_approvals`）
- 路径归一化、SSE 构造、subagent 工具构造消除重复
- 专家角色 prompt 外置，`config.py` 纯配置

## 风险

| 风险 | 缓解 |
|---|---|
| import 路径全量变更导致测试断裂 | 分批拆分，每批跑 `uv run pytest tests/python/unit -m "not integration"` |
| 循环导入（approval ↔ deep ↔ main） | approval 模块仅含数据类 + 状态 dict + get/set 函数，不依赖任何业务模块 |
| 反射访问遗漏导致运行时 AttributeError | 全量 grep `getattr(main` + `_pending_approvals` + `_abort_flags` 确保无遗漏 |
| subagents 抽基类破坏现有公共 API | 保持 `run_code_agent` / `run_rag_agent` / `run_web_agent` / `run_custom_agent` 签名不变 |
| 拆分后 `__init__.py` 聚合导出不全 | 每个新包 `__init__.py` 显式 `__all__` + 对照原模块导出清单 |

## 约束（AGENTS.md 既有约定）

- ✅ 保持 `graph.py` 顶层单向 import 四条路径，路径间延迟 import 防循环（§14.5）
- ✅ 保持 `run_router` / `run_chat_path` / `run_tool_path` / `run_deep_path` / `run_team_path` 公共签名不变
- ✅ 不改 SSE 事件契约（§13），仅重构构造函数内部
- ✅ 保持 `deep/agent.py` 用 `TYPE_CHECKING` 延迟导入 `RouterState`
- ✅ 不重新创建 `backend/app/paths/` 目录
- ✅ 重构分批进行，每批跑单元测试 + ruff check 验证
