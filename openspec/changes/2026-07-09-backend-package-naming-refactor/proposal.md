# Proposal: 后端包名与目录结构重命名

## 背景

2026-07-09 对 `backend/app/` 全量代码做包名/类名/目录结构审查，发现多处命名歧义、职责分散、
同名冲突与语义模糊问题。这些问题不影响运行时行为，但增加新人理解成本、提升误用风险，
且部分（如 `CLI_TOOL_NAME` 同名不同值）是潜在 bug 源头。

本提案承接已归档的 `2026-07-09-backend-code-quality-optimization`（聚焦代码质量/bug/框架合规），
**专注命名与目录结构**，按风险与收益分批重构。遵循项目硬约束：
**开发阶段无需考虑灰度兼容，直接推倒重来，禁止写兼容层/shim/`@Deprecated`**。

关键发现：

1. **同名常量值不一致（潜在 bug）**：`app/tools/cli.py:31` 与 `app/security/dangerous_tools.py:24`
   都定义 `CLI_TOOL_NAME`，值分别为 `"cli_execute"`（LangChain `@tool` 名）和 `"execute"`
   （deepagents `LocalShellBackend` 工具名）。`security/command_filter.py:145` 已被迫维护
   `_CLI_TOOL_NAMES = frozenset({"execute", "cli_execute"})` 兼容两者，证明命名冲突已实际发生。
2. **黑名单逻辑 100% 重复**：`tools/cli.py:_effective_blocklist` 与
   `deep/safe_shell_backend.py:_effective_blocklist` 逐字相同，维护需双向同步。
3. **核心契约被归类为 util**：`utils/sse_events.py` 定义 7 个 SSE 事件工厂函数
   （`make_sse_event`/`make_tool_call_event`/`make_approval_event` 等），被
   api/agents/deep/team/router 五个包广泛依赖，是核心协议层而非"工具函数"。
4. **包名 `deep` 语义模糊**：内部 6 文件（agent/execution/harness/safe_shell_backend/streaming/tools）
   职责跨度大，"deep" 无法传达"带审批的 deepagents 执行体"语义；`harness.py`/`execution.py`
   文件名也偏框架术语而非业务术语。
5. **审批职责 5 处分散**：`security/approval_flow.py`（文件）+ `security/approval/`（目录）+
   `utils/sse_events.py::make_approval_event` + `api/chat.py` 审批端点 + `cli/approval.py`。
   文件与目录职责高度重叠。
6. **trace_id 双源**：`observability/trace.py::new_trace_id` 与
   `observability/langsmith.py::gen_trace_id` 两套生成逻辑；`langsmith.py` 与
   `langsmith_dual.py` 职责重叠。
7. **agents/subagents 层级歧义**：`app/agents/`（场景化执行体，带审批）vs
   `app/subagents/`（无中断 ReAct 子图）需读文档才能区分；`agents/supervisor/work_supervisor.py`
   与目录名 `supervisor/` 冗余；`agents/team/coding_team.py` 是 78 行薄封装直接委托
   `team/run_team_path`，存在价值可疑。
8. **路径归一化双份**：`sandbox/path_guard.py` 与 `utils/paths.py` 都做路径归一化，
   边界模糊。
9. **API 文件撞名**：`api/observation.py`（反馈端点）与 `observability/observation.py`
   （观测中心实现）撞名；`api/memory.py` 名字偏窄（实际承载 skills+profile+checkpointer 三类）。

## 目标

1. **P0 消除潜在 bug 与重复**（零风险重命名/提取）：
   - 重命名两个 `CLI_TOOL_NAME` 消歧；
   - 提取 `_effective_blocklist`/`_is_command_blocked` 重复到 `security/command_filter.py`；
   - 提升 `utils/sse_events.py` → `sse/events.py`（核心协议层上提）。
2. **P1 目录语义清晰化**（高收益重命名）：
   - `app/deep/` → `app/deepagent/` + 内部文件改名
     （`harness.py` → `factory.py`，`execution.py` → `approval_runner.py`，`tools.py` → `tool_assembly.py`）；
   - 合并 `security/approval_flow.py` → `security/approval/flow.py`；
   - 合并 `observability/langsmith_dual.py` → `langsmith.py` + 统一 trace_id 到 `trace.py`。
3. **P2 场景化执行体重构**（中收益重命名）：
   - `app/agents/` → `app/scenarios/` + 子目录改名
     （`supervisor/` → `work/`，`expert/` → `coding/`，`team/` → `coding_team/`）；
   - 文件名重命名（`work_supervisor.py` → `work/agent.py`，`coding.py` → `coding/agent.py`，
     `coding_team.py` → `coding_team/agent.py`）。
4. **P3 清理**（低收益合并）：
   - 合并 `utils/paths.py` 与 `sandbox/path_guard.py`；
   - （已否决）重命名 `api/observation.py` → `api/feedback.py`：经核查 `api/observation.py` 注册 5 个端点
     （`/api/observation/feedback` × 2 + `/api/observation/runs` × 3），文件名 `observation` 与路由前缀对齐，
     重命名会破坏语义一致性。与 `observability/observation.py` 的撞名是 Python 常见的跨包同名模式（如 `models.py`），
     可接受。

## 非目标

- **不改 SSE 事件契约本身**（事件名/字段不变，仅改 `utils/sse_events.py` 的存放位置）。
- **不改 API 路由路径**（前端契约不变；`api/memory.py` → `api/profile.py` 涉及前端 API 路径变更，故不在本提案范围）。
- **不改前端代码**（除非 import 路径影响，但本提案只动后端 Python 包）。
- **不改 Tauri/Rust 层**。
- **不改 eval 框架结构**（已良好）。
- **不删除 `app/scenarios/coding_team/agent.py` 薄封装**（保留作为场景入口签名对齐 `run_work_supervisor`/`run_coding_expert`，但加明确 docstring 说明职责）。
- **不做 `team/` 框架层重构**（已在 `2026-07-09-backend-code-quality-optimization` P2 完成 LangGraph 迁移）。
- **不合并 `app/config/` 与 `app/workspace/config/`**（两者职责清晰：全局 vs 项目级，前缀已区分）。
- **不改 `app/router/` 名字**（项目内 docstring 已澄清，重命名 `dispatcher` 收益有限）。

## 涉及范围

### P0 零风险重命名/提取

- `backend/app/tools/cli.py` — `CLI_TOOL_NAME` → `LLM_CLI_TOOL_NAME`
- `backend/app/security/dangerous_tools.py` — `CLI_TOOL_NAME` → `SHELL_CLI_TOOL_NAME`
- `backend/app/security/command_filter.py` — 新增 `effective_blocklist()` / `is_command_blocked()` 公共函数
- `backend/app/tools/cli.py` — 删除本地 `_effective_blocklist`/`_is_command_blocked`，改用 `security.command_filter`
- `backend/app/deep/safe_shell_backend.py` — 删除本地 `_effective_blocklist`/`_is_command_blocked`，改用 `security.command_filter`
- `backend/app/sse/` — 新建包（从 `utils/sse_events.py` 迁入）
- `backend/app/utils/sse_events.py` — 整文件迁移到 `app/sse/events.py`
- 所有 `from app.utils.sse_events import` 调用处 — 改 `from app.sse.events import`

### P1 目录语义清晰化

- `backend/app/deep/` → `backend/app/deepagent/`（目录重命名）
  - `deep/agent.py` → `deepagent/agent.py`
  - `deep/harness.py` → `deepagent/factory.py`
  - `deep/execution.py` → `deepagent/approval_runner.py`
  - `deep/safe_shell_backend.py` → `deepagent/safe_shell_backend.py`
  - `deep/streaming.py` → `deepagent/streaming.py`
  - `deep/tools.py` → `deepagent/tool_assembly.py`
- `backend/app/security/approval_flow.py` → `backend/app/security/approval/flow.py`
- `backend/app/security/approval/__init__.py` — re-export `flow.py` 的公共 API
- `backend/app/observability/langsmith_dual.py` — 合并到 `langsmith.py`
- `backend/app/observability/langsmith.py` — 删除 `gen_trace_id`，统一用 `trace.py::new_trace_id`
- 所有上述路径的 import 调用处同步更新

### P2 场景化执行体重构

- `backend/app/agents/` → `backend/app/scenarios/`（目录重命名）
  - `agents/supervisor/` → `scenarios/work/`
    - `supervisor/work_supervisor.py` → `work/agent.py`
    - `supervisor/mention.py` → `work/mention.py`
    - `supervisor/__init__.py` → `work/__init__.py`
  - `agents/expert/` → `scenarios/coding/`
    - `expert/coding.py` → `coding/agent.py`
    - `expert/__init__.py` → `coding/__init__.py`
  - `agents/team/` → `scenarios/coding_team/`
    - `team/coding_team.py` → `coding_team/agent.py`
    - `team/__init__.py` → `coding_team/__init__.py`
- `backend/app/router/graph.py` — 更新 import（`run_work_supervisor`/`run_coding_expert`/`run_coding_team` 来源）
- 所有 `from app.agents.` import 调用处同步更新

### P3 清理

- `backend/app/utils/paths.py` — 合并到 `backend/app/sandbox/path_guard.py`（统一为 `normalize_path` + `is_under` + `is_critical`）
- 所有 `from app.utils.paths` import 调用处同步更新
- （已否决）`api/observation.py` 重命名：见上述 P3 决策说明

## 验收标准

1. **P0**：
   - `grep -rn "^CLI_TOOL_NAME" backend/app/` 返回空（无同名常量）；
   - `grep -rn "_effective_blocklist\|_is_command_blocked" backend/app/tools/cli.py backend/app/deep/safe_shell_backend.py` 返回空（已迁移到 `security/command_filter.py`）；
   - `backend/app/utils/sse_events.py` 文件不存在；`backend/app/sse/events.py` 存在；
   - `grep -rn "from app.utils.sse_events" backend/ tests/` 返回空；
   - `uv run ruff check backend/` 通过；`uv run pytest tests/python/unit -m "not integration"` 全绿。
2. **P1**：
   - `backend/app/deep/` 目录不存在；`backend/app/deepagent/` 存在且含 6 个文件（agent/factory/approval_runner/safe_shell_backend/streaming/tool_assembly）；
   - `backend/app/security/approval_flow.py` 不存在；`backend/app/security/approval/flow.py` 存在；
   - `backend/app/observability/langsmith_dual.py` 不存在；`langsmith.py` 含原 `dual_trace`/`DualTraceContext` 公共 API；
   - `grep -rn "gen_trace_id" backend/app/observability/langsmith.py` 返回空（已迁到 `trace.py`）；
   - `grep -rn "from app.deep\." backend/ tests/` 返回空；`grep -rn "from app.security.approval_flow" backend/ tests/` 返回空；
   - 全量单测 + ruff 通过。
3. **P2**：
   - `backend/app/agents/` 目录不存在；`backend/app/scenarios/` 存在且含 `work/`、`coding/`、`coding_team/` 三个子包；
   - 每个子包内有 `agent.py`（主入口）+ `__init__.py`（work 另有 `mention.py`）；
   - `grep -rn "from app.agents\." backend/ tests/` 返回空；
   - 全量单测 + ruff 通过。
4. **P3**：
   - `backend/app/utils/paths.py` 不存在；`normalize_path` 在 `sandbox/path_guard.py` 中定义；
   - `grep -rn "from app.utils.paths" backend/ tests/` 返回空；
   - 全量单测 + ruff 通过。
5. **全局**：
   - `uv run pytest tests/python/unit -m "not integration"` 全绿（已知 6 个失败保持不变或减少）；
   - `uv run ruff check backend/` 无错误；
   - `pnpm typecheck`（前端）无回归（本提案不动前端，仅验证 import 路径未影响类型契约）。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 大规模 import 路径变更漏改 | 每个 P 级独立提交；提交前 `grep -rn "from app.<旧路径>" backend/ tests/` 全空验证；ruff F401 捕获未用导入 |
| `deep` → `deepagent` 影响外部引用（如 Tauri/Rust 调用） | 已确认 Tauri/Rust 仅调 HTTP API，不直接 import Python 包；零影响 |
| `agents` → `scenarios` 影响配置 key | 配置 key 是 `agent_mode`（值 `"work"`/`"coding"`/`"coding_team"`），与 Python 包名无关；零影响 |
| `CLI_TOOL_NAME` 重命名影响外部 patch | 全项目内部使用，无外部 SDK 暴露；grep 确认所有引用点同步更新 |
| 合并 `langsmith_dual.py` 改变 trace 行为 | 合并时保留 `DualTraceContext` 类签名不变，仅迁移文件位置；测试覆盖 `dual_trace` 调用 |
| 路径归一化合并破坏 sandbox 边界 | `normalize_path` 保持纯函数语义；`is_under`/`is_critical` 仍只在 `path_guard.py`；测试覆盖边界 |

## 回滚策略

- 每个 P 级独立分支/提交，可单独 `git revert`。
- P0/P1/P2/P3 均为纯重命名/迁移，无行为变化，回滚即恢复原状。
- 所有旧路径在 git 历史中可追溯，必要时 cherry-pick 恢复。

## 与已有提案的关系

- **承接** `2026-07-09-backend-code-quality-optimization`（已归档）：前者聚焦 bug/框架合规/重复消除，
  本提案专注命名与目录结构，**不重复**前者的 P0-P4 范围。
- **不冲突** `2026-07-09-subagent-deepagents-unification`（仍在 changes/，未归档）：
  该提案聚焦子代理统一到 `create_deep_agent`，本提案只改包名/路径，不改子代理实现逻辑。
  若该提案先合并，本提案需同步更新 `subagents/base.py` 中的 `from app.deep.` import 路径。
