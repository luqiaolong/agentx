# Spec: 后端包名与目录结构重命名

> 需求规格（When/Then 形式）。每个需求映射到 proposal.md 的 P 级目标。
> 本提案是纯命名/结构重构，零行为变化，零 API 契约变化。

---

## P0 — 零风险重命名/提取

### REQ-P0-1: 重命名两个 `CLI_TOOL_NAME` 消歧

**When** `grep -rn "^CLI_TOOL_NAME\b" backend/app/`
**Then** 返回空（无同名常量）

**When** `grep -rn "LLM_CLI_TOOL_NAME" backend/app/tools/cli.py`
**Then** 命中定义行 `LLM_CLI_TOOL_NAME: str = "cli_execute"` 与 `__all__` 导出

**When** `grep -rn "SHELL_CLI_TOOL_NAME" backend/app/security/dangerous_tools.py`
**Then** 命中定义行 `SHELL_CLI_TOOL_NAME = "execute"` 与 `DANGEROUS_TOOLS` 集合引用

**When** `uv run ruff check backend/app/tools/cli.py backend/app/security/dangerous_tools.py`
**Then** 无 F401 未使用导入错误

**When** `uv run pytest tests/python/unit -m "not integration" -k "cli or security or dangerous"`
**Then** 全部通过

### REQ-P0-2: 提取 `_effective_blocklist`/`_is_command_blocked` 重复 + 修改 `command_filter.is_command_blocked` 行为

**When** `grep -rn "_effective_blocklist\|_is_command_blocked" backend/app/tools/cli.py backend/app/deep/safe_shell_backend.py`
**Then** 返回空（私有副本已删除）

**When** `grep -rn "^def effective_blocklist\|^def is_command_blocked" backend/app/security/command_filter.py`
**Then** 命中 2 个公开函数定义（`effective_blocklist` 新增，`is_command_blocked` 已存在但行为已修改）

**When** `grep -n "from app.config import get_settings" backend/app/security/command_filter.py`
**Then** 命中（新增的 import）

**When** `grep -n "effective_blocklist" backend/app/security/command_filter.py`
**Then** `is_command_blocked` 函数体内调用 `effective_blocklist()`（而非直接用 `DEFAULT_BLOCKLIST`）

**When** `grep -rn "from app.security.command_filter import.*is_command_blocked" backend/app/tools/cli.py backend/app/deep/safe_shell_backend.py`
**Then** 命中 import 语句

**When** `command_filter.is_command_blocked("rm")` 调用（默认黑名单中的命令）
**Then** 返回 `True`（行为不变）

**When** `command_filter.is_command_blocked("custom_cmd")` 调用，且 `settings.cli_tool_blocklist` 含 `"custom_cmd"`
**Then** 返回 `True`（行为增强：现在合并用户配置，原来不合并）

**When** `uv run ruff check backend/app/tools/cli.py backend/app/deep/safe_shell_backend.py backend/app/security/command_filter.py`
**Then** 无错误

**When** `uv run pytest tests/python/unit -m "not integration" -k "cli or shell_backend or command_filter or security"`
**Then** 全部通过

### REQ-P0-3: 提升 `utils/sse_events.py` → `sse/events.py`

**When** 文件存在性检查 `backend/app/utils/sse_events.py`
**Then** 文件不存在

**When** 文件存在性检查 `backend/app/sse/events.py` 与 `backend/app/sse/__init__.py`
**Then** 两个文件均存在

**When** `grep -rn "from app.utils.sse_events" backend/ tests/`
**Then** 返回空

**When** `grep -rn "from app.sse.events\|from app.sse import" backend/ tests/`
**Then** 命中原 `from app.utils.sse_events` 调用点对应的文件

**When** SSE 事件工厂函数调用（如 `make_sse_event("token", ...)`）
**Then** 行为不变（函数签名与返回值与迁移前一致）

**When** `uv run ruff check backend/app/sse/ backend/app/utils/`
**Then** 无错误

**When** `uv run pytest tests/python/unit -m "not integration"`
**Then** 全部通过

---

## P1 — 目录语义清晰化

### REQ-P1-1: `app/deep/` → `app/deepagent/` + 内部文件重命名

**When** 目录存在性检查 `backend/app/deep/`
**Then** 目录不存在

**When** 目录存在性检查 `backend/app/deepagent/`
**Then** 目录存在

**When** `ls backend/app/deepagent/`
**Then** 含 7 个文件：`agent.py` / `factory.py` / `approval_runner.py` / `safe_shell_backend.py` / `streaming.py` / `tool_assembly.py` / `__init__.py`

**When** `grep -rn "from app.deep\." backend/ tests/`
**Then** 返回空

**When** `grep -rn "from app.deepagent.agent import\|from app.deepagent.factory import\|from app.deepagent.approval_runner import\|from app.deepagent.tool_assembly import\|from app.deepagent.safe_shell_backend import\|from app.deepagent.streaming import" backend/ tests/`
**Then** 命中原 `from app.deep.*` 调用点对应的文件

**When** DeepAgent 执行流调用 `build_deep_agent` / `run_deep_path` / `create_agent` / `run_agent_with_approval`
**Then** 行为不变（函数签名与返回值与迁移前一致）

**When** `uv run ruff check backend/app/deepagent/`
**Then** 无错误

**When** `uv run pytest tests/python/unit -m "not integration"`
**Then** 全部通过

### REQ-P1-2: 合并 `security/approval_flow.py` → `security/approval/flow.py`

**When** 文件存在性检查 `backend/app/security/approval_flow.py`
**Then** 文件不存在

**When** 文件存在性检查 `backend/app/security/approval/flow.py`
**Then** 文件存在

**When** `grep -rn "from app.security.approval_flow" backend/ tests/`
**Then** 返回空

**When** `grep -rn "from app.security.approval.flow\|from app.security.approval import" backend/ tests/`
**Then** 命中原 `from app.security.approval_flow` 调用点对应的文件

**When** 审批流调用 `_extract_paths_from_tool_call` / `_make_approval_event` / `_await_approval` / `_handle_directory_extension`
**Then** 行为不变（函数签名与返回值与迁移前一致；若去 `_` 前缀则调用处同步更新）

**When** `uv run ruff check backend/app/security/`
**Then** 无错误

**When** `uv run pytest tests/python/unit -m "not integration" -k "approval or security"`
**Then** 全部通过

### REQ-P1-3: 合并 `observability/langsmith_dual.py` → `langsmith.py` + 统一 trace_id

**When** 文件存在性检查 `backend/app/observability/langsmith_dual.py`
**Then** 文件不存在

**When** `grep -rn "from app.observability.langsmith_dual" backend/ tests/`
**Then** 返回空

**When** `grep -rn "DualTraceContext\|def dual_trace" backend/app/observability/langsmith.py`
**Then** 命中合并后的类与函数定义

**When** `grep -rn "gen_trace_id" backend/app/observability/langsmith.py`
**Then** 返回空（已删除，统一到 `trace.py`）

**When** `grep -rn "new_trace_id" backend/app/observability/trace.py`
**Then** 命中（trace_id 唯一来源）

**When** `grep -rln "gen_trace_id" backend/ tests/`
**Then** 返回空（所有调用已改为 `new_trace_id`）

**When** trace 调用 `dual_trace(...)` 或 `DualTraceContext(...)`
**Then** 行为不变（签名与返回值与迁移前一致）

**When** `uv run ruff check backend/app/observability/`
**Then** 无错误

**When** `uv run pytest tests/python/unit -m "not integration" -k "langsmith or trace or observability"`
**Then** 全部通过

---

## P2 — 场景化执行体重构

### REQ-P2-1: `app/agents/` → `app/scenarios/` + 子目录 + 文件重命名

**When** 目录存在性检查 `backend/app/agents/`
**Then** 目录不存在

**When** 目录存在性检查 `backend/app/scenarios/`
**Then** 目录存在

**When** `ls backend/app/scenarios/`
**Then** 含 3 个子目录：`work/` / `coding/` / `coding_team/`

**When** `ls backend/app/scenarios/work/`
**Then** 含 `agent.py` / `mention.py` / `__init__.py`

**When** `ls backend/app/scenarios/coding/`
**Then** 含 `agent.py` / `__init__.py`

**When** `ls backend/app/scenarios/coding_team/`
**Then** 含 `agent.py` / `__init__.py`

**When** `grep -rn "from app.agents\." backend/ tests/`
**Then** 返回空

**When** `grep -rn "from app.scenarios.work.agent import\|from app.scenarios.work.mention import\|from app.scenarios.coding.agent import\|from app.scenarios.coding_team.agent import" backend/ tests/`
**Then** 命中原 `from app.agents.*` 调用点对应的文件

**When** Router 调用 `run_work_supervisor` / `run_coding_expert` / `run_coding_team`
**Then** 行为不变（函数签名与返回值与迁移前一致）

**When** `uv run ruff check backend/app/scenarios/`
**Then** 无错误

**When** `uv run pytest tests/python/unit -m "not integration"`
**Then** 全部通过

---

## P3 — 清理

### REQ-P3-1: 合并 `utils/paths.py` 与 `sandbox/path_guard.py`

**When** 文件存在性检查 `backend/app/utils/paths.py`
**Then** 文件不存在

**When** `grep -rn "def normalize_path" backend/app/sandbox/path_guard.py`
**Then** 命中函数定义

**When** `grep -rn "from app.utils.paths" backend/ tests/`
**Then** 返回空

**When** `grep -rn "from app.sandbox.path_guard import normalize_path" backend/ tests/`
**Then** 命中原 `from app.utils.paths import normalize_path` 调用点对应的文件

**When** 路径归一化调用 `normalize_path(...)`
**Then** 行为不变（函数签名与返回值与迁移前一致）

**When** `uv run ruff check backend/app/sandbox/ backend/app/utils/`
**Then** 无错误

**When** `uv run pytest tests/python/unit -m "not integration" -k "path or sandbox"`
**Then** 全部通过

---

## 全局验收

### REQ-GLOBAL-1: 全量单元测试通过

**When** `uv run pytest tests/python/unit -m "not integration"`
**Then** 全部通过（已知 6 个失败保持不变或减少）

### REQ-GLOBAL-2: 全量风格检查通过

**When** `uv run ruff check backend/`
**Then** 无错误

### REQ-GLOBAL-3: 残留 import 检查

**When** 执行以下 grep 命令：
- `grep -rn "from app.deep\." backend/ tests/`
- `grep -rn "from app.agents\." backend/ tests/`
- `grep -rn "from app.utils.sse_events" backend/ tests/`
- `grep -rn "from app.utils.paths" backend/ tests/`
- `grep -rn "from app.security.approval_flow" backend/ tests/`
- `grep -rn "from app.observability.langsmith_dual" backend/ tests/`
- `grep -rn "^CLI_TOOL_NAME\b" backend/app/`

**Then** 全部返回空

### REQ-GLOBAL-4: 前端无回归

**When** `pnpm typecheck`
**Then** 无新增错误（本提案不动前端，仅验证 import 路径未影响类型契约）

### REQ-GLOBAL-5: AGENTS.md 文件地图同步

**When** `grep -rn "app/deep/\|app/agents/" docs/agents/`
**Then** 仅在历史 ADR 或归档提案中出现（当前文档应已更新为新路径）

**When** 检查 `docs/agents/01-architecture-file-map.md` 的目录树
**Then** 含 `app/deepagent/` / `app/scenarios/` / `app/sse/` 等新路径，不含 `app/deep/` / `app/agents/` 旧路径
