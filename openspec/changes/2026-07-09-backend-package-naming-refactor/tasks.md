# Tasks: 后端包名与目录结构重命名

> 按 P0→P3 分批，每个任务可独立提交。每个任务标注验证命令。
> 遵循项目硬约束：**直接推倒重来，删除旧代码，禁止写兼容层/shim/`@Deprecated`**。

---

## P0 — 零风险重命名/提取

### [ ] T-P0-1: 重命名两个 `CLI_TOOL_NAME` 消歧

- `backend/app/tools/cli.py:31` `CLI_TOOL_NAME` → `LLM_CLI_TOOL_NAME`
  - 更新 `__all__ = ["CLI_TOOL_NAME", "cli_execute"]` → `__all__ = ["LLM_CLI_TOOL_NAME", "cli_execute"]`
  - `@tool` 装饰器名不变（`cli_execute` 函数名是 LangChain `@tool` 注册名，与常量值 `"cli_execute"` 一致，无需改）
- `backend/app/security/dangerous_tools.py:24` `CLI_TOOL_NAME` → `SHELL_CLI_TOOL_NAME`
  - 更新 L31 引用 `CLI_TOOL_NAME` → `SHELL_CLI_TOOL_NAME`
- `backend/app/security/command_filter.py:145` `_CLI_TOOL_NAMES = frozenset({"execute", "cli_execute"})`
  - 保留字面量集合（解耦 import），但加注释说明两个值的来源
- 全项目 grep `CLI_TOOL_NAME` 确认无遗漏引用（除上述 3 文件外应无其他命中）
- 验证：
  - `grep -rn "^CLI_TOOL_NAME\b" backend/app/` 返回空
  - `grep -rn "LLM_CLI_TOOL_NAME\|SHELL_CLI_TOOL_NAME" backend/app/` 命中 3 文件
  - `uv run ruff check backend/app/tools/cli.py backend/app/security/`
  - `uv run pytest tests/python/unit -m "not integration" -k "cli or security or dangerous"`

### [ ] T-P0-2: 提取 `_effective_blocklist`/`_is_command_blocked` 重复 + 修改 `command_filter.is_command_blocked` 行为

**注意**：`security/command_filter.py:70` 已存在 `is_command_blocked`，但当前**仅检查 `DEFAULT_BLOCKLIST` 不合并用户配置**；
`cli.py`/`safe_shell_backend.py` 的 `_is_command_blocked` **合并用户配置**。本任务将合并逻辑统一到 `command_filter.py`。

- `backend/app/security/command_filter.py`：
  - 新增 `from app.config import get_settings` import（当前无）
  - 新增公开函数 `effective_blocklist()`：
    ```python
    def effective_blocklist() -> frozenset[str]:
        """合并默认黑名单与用户配置黑名单。"""
        cfg = get_settings().cli_tool_blocklist
        if not cfg:
            return DEFAULT_BLOCKLIST
        user_blocked = frozenset(
            cmd.strip().lower() for cmd in cfg if isinstance(cmd, str) and cmd.strip()
        )
        return DEFAULT_BLOCKLIST | user_blocked
    ```
  - **修改**现有 `is_command_blocked(command)` 使其调用 `effective_blocklist()`：
    ```python
    def is_command_blocked(command: str) -> bool:
        """命令名是否在黑名单内（含用户配置合并，不区分大小写）。"""
        return command.strip().lower() in effective_blocklist()
    ```
    更新 docstring，删除"仅检查默认黑名单"说明
  - 将 `effective_blocklist` 加入 `__all__`
- `backend/app/tools/cli.py`：
  - 删除 L38-49 的 `_effective_blocklist` + `_is_command_blocked`
  - 改 import：若已有 `from app.security.command_filter import DEFAULT_BLOCKLIST, has_forbidden_args`，改为 `from app.security.command_filter import has_forbidden_args, is_command_blocked`（移除 `DEFAULT_BLOCKLIST` 若不再直接使用）
  - 调用处 `_is_command_blocked(cmd_name)` → `is_command_blocked(cmd_name)`
- `backend/app/deep/safe_shell_backend.py`：
  - 删除 L26-39 的 `_effective_blocklist` + `_is_command_blocked`
  - 改 import：`from app.security.command_filter import has_forbidden_args, is_command_blocked`（移除 `DEFAULT_BLOCKLIST` 若不再直接使用）
  - 调用处 `_is_command_blocked(cmd_name)` → `is_command_blocked(cmd_name)`
- 验证：
  - `grep -rn "_effective_blocklist\|_is_command_blocked" backend/app/tools/cli.py backend/app/deep/safe_shell_backend.py` 返回空
  - `grep -rn "^def effective_blocklist\|^def is_command_blocked" backend/app/security/command_filter.py` 命中 2 个公开函数
  - `grep -n "get_settings" backend/app/security/command_filter.py` 命中 import
  - `uv run ruff check backend/app/tools/cli.py backend/app/deep/safe_shell_backend.py backend/app/security/command_filter.py`
  - `uv run pytest tests/python/unit -m "not integration" -k "cli or shell_backend or command_filter or security"`

### [ ] T-P0-3: 提升 `utils/sse_events.py` → `sse/events.py`

- 新建 `backend/app/sse/__init__.py`（空文件 + docstring "SSE 事件协议层。"）
- `backend/app/utils/sse_events.py` 整文件迁移到 `backend/app/sse/events.py`
  - 文件内容不变（仅修改 import 路径，无内部依赖变化）
- `backend/app/sse/__init__.py` 添加 re-export：
  ```python
  from app.sse.events import (  # noqa: F401
      make_sse_event,
      make_todo_event,
      make_tool_call_event,
      make_tool_result_event,
      make_team_event,
      make_approval_event,
      make_error_event,
  )
  ```
  （具体 re-export 列表按 `events.py` 实际 `__all__` 确定）
- 全项目更新 import：
  - `grep -rln "from app.utils.sse_events" backend/ tests/` 列出所有调用文件
  - 每个文件 `from app.utils.sse_events import X` → `from app.sse.events import X`
    （或 `from app.sse import X` 走 `__init__.py` re-export）
- 删除 `backend/app/utils/sse_events.py`
- 验证：
  - `test ! -f backend/app/utils/sse_events.py` 文件不存在
  - `test -f backend/app/sse/events.py` 文件存在
  - `grep -rn "from app.utils.sse_events" backend/ tests/` 返回空
  - `grep -rn "from app.sse.events\|from app.sse import" backend/ tests/` 命中原调用点
  - `uv run ruff check backend/app/sse/ backend/app/utils/`
  - `uv run pytest tests/python/unit -m "not integration"`

---

## P1 — 目录语义清晰化

### [ ] T-P1-1: `app/deep/` → `app/deepagent/` + 内部文件重命名

- 目录重命名：`backend/app/deep/` → `backend/app/deepagent/`
- 文件重命名（在 `deepagent/` 内）：
  - `harness.py` → `factory.py`
  - `execution.py` → `approval_runner.py`
  - `tools.py` → `tool_assembly.py`
  - `agent.py` / `safe_shell_backend.py` / `streaming.py` / `__init__.py` 不变
- 全项目更新 import：
  - `grep -rln "from app.deep\." backend/ tests/` 列出所有调用文件
  - 每个文件：
    - `from app.deep.agent import` → `from app.deepagent.agent import`
    - `from app.deep.harness import` → `from app.deepagent.factory import`
    - `from app.deep.execution import` → `from app.deepagent.approval_runner import`
    - `from app.deep.tools import` → `from app.deepagent.tool_assembly import`
    - `from app.deep.safe_shell_backend import` → `from app.deepagent.safe_shell_backend import`
    - `from app.deep.streaming import` → `from app.deepagent.streaming import`
- 检查 `deepagent/__init__.py` 是否有 re-export，有则同步更新（`from app.deep.harness` → `from app.deepagent.factory` 等）
- 检查 `backend/app/main.py` / `backend/app/router/graph.py` 等入口文件的 import
- 验证：
  - `test ! -d backend/app/deep` 目录不存在
  - `test -d backend/app/deepagent` 目录存在
  - `ls backend/app/deepagent/` 含 `agent.py`/`factory.py`/`approval_runner.py`/`safe_shell_backend.py`/`streaming.py`/`tool_assembly.py`/`__init__.py` 7 个文件
  - `grep -rn "from app.deep\." backend/ tests/` 返回空
  - `grep -rn "from app.deepagent\." backend/ tests/` 命中原调用点
  - `uv run ruff check backend/app/deepagent/`
  - `uv run pytest tests/python/unit -m "not integration"`

### [ ] T-P1-2: 合并 `security/approval_flow.py` → `security/approval/flow.py`

- `backend/app/security/approval_flow.py` 整文件迁移到 `backend/app/security/approval/flow.py`
  - 文件内容不变（仅修改 import 路径，无内部依赖变化）
- `backend/app/security/approval/__init__.py` 增加 re-export `flow.py` 的公共 API：
  - 确认 `flow.py` 中哪些函数需被外部调用（如 `_extract_paths_from_tool_call`/`_make_approval_event`/`_await_approval`/`_handle_directory_extension`）
  - 需被外部调用的去 `_` 前缀（提升为公共 API），仅在 `flow.py` 内部使用的保留 `_` 前缀
  - `__init__.py` re-export 公共 API
- 全项目更新 import：
  - `grep -rln "from app.security.approval_flow" backend/ tests/` 列出所有调用文件
  - 每个文件 `from app.security.approval_flow import X` → `from app.security.approval.flow import X`
    （或 `from app.security.approval import X` 走 `__init__.py` re-export）
- 删除 `backend/app/security/approval_flow.py`
- 验证：
  - `test ! -f backend/app/security/approval_flow.py` 文件不存在
  - `test -f backend/app/security/approval/flow.py` 文件存在
  - `grep -rn "from app.security.approval_flow" backend/ tests/` 返回空
  - `grep -rn "from app.security.approval.flow\|from app.security.approval import" backend/ tests/` 命中原调用点
  - `uv run ruff check backend/app/security/`
  - `uv run pytest tests/python/unit -m "not integration" -k "approval or security"`

### [ ] T-P1-3: 合并 `observability/langsmith_dual.py` → `langsmith.py` + 统一 trace_id

- `backend/app/observability/langsmith_dual.py` 整文件内容（`DualTraceContext` 类 + `dual_trace` 函数 + 私有助手）
  迁入 `backend/app/observability/langsmith.py` 末尾
  - 保留 `DualTraceContext`/`dual_trace` 公共签名不变
  - 合并时去重 import（`langsmith.py` 已有的 import 不重复）
- `backend/app/observability/langsmith.py` 删除 `gen_trace_id` 函数
  - **唯一调用点**：`langsmith_dual.py:80` `trace_id = run_id or gen_trace_id()`（合并后成为 `langsmith.py` 内部调用）
  - 合并时在 `langsmith.py` 顶部新增 `from app.observability.trace import new_trace_id`
  - 原 `langsmith_dual.py:80` 的 `gen_trace_id()` 调用改为 `new_trace_id()`
  - 删除 `langsmith.py:135` 的 `gen_trace_id` 函数定义
  - 确认 `new_trace_id` 与 `gen_trace_id` 签名一致（都返回 `str`，无参数）
- 删除 `backend/app/observability/langsmith_dual.py`
- 全项目更新 import：
  - `grep -rln "from app.observability.langsmith_dual" backend/ tests/` 列出所有调用文件
  - 每个文件 `from app.observability.langsmith_dual import X` → `from app.observability.langsmith import X`
  - `grep -rln "gen_trace_id" backend/ tests/` 列出所有调用文件
  - 每个文件 `from app.observability.langsmith import gen_trace_id` → `from app.observability.trace import new_trace_id`
    并修改调用 `gen_trace_id()` → `new_trace_id()`
- 验证：
  - `test ! -f backend/app/observability/langsmith_dual.py` 文件不存在
  - `grep -rn "from app.observability.langsmith_dual" backend/ tests/` 返回空
  - `grep -rn "gen_trace_id" backend/app/observability/langsmith.py` 返回空
  - `grep -rn "DualTraceContext\|dual_trace" backend/app/observability/langsmith.py` 命中合并后的类/函数
  - `grep -rn "new_trace_id" backend/app/observability/trace.py` 命中（trace_id 唯一来源）
  - `uv run ruff check backend/app/observability/`
  - `uv run pytest tests/python/unit -m "not integration" -k "langsmith or trace or observability"`

---

## P2 — 场景化执行体重构

### [ ] T-P2-1: `app/agents/` → `app/scenarios/` + 子目录 + 文件重命名

- 目录重命名：`backend/app/agents/` → `backend/app/scenarios/`
- 子目录重命名（在 `scenarios/` 内）：
  - `supervisor/` → `work/`
  - `expert/` → `coding/`
  - `team/` → `coding_team/`
- 文件重命名：
  - `work/work_supervisor.py` → `work/agent.py`
  - `coding/coding.py` → `coding/agent.py`
  - `coding_team/coding_team.py` → `coding_team/agent.py`
  - `work/mention.py` / 各 `__init__.py` 不变
- 更新 `coding_team/agent.py` docstring，明确"本文件是场景入口薄封装，框架实现见 `app.team.orchestrator`"
- 全项目更新 import：
  - `grep -rln "from app.agents\." backend/ tests/` 列出所有调用文件
  - 每个文件：
    - `from app.agents.supervisor.work_supervisor import` → `from app.scenarios.work.agent import`
    - `from app.agents.supervisor.mention import` → `from app.scenarios.work.mention import`
    - `from app.agents.expert.coding import` → `from app.scenarios.coding.agent import`
    - `from app.agents.team.coding_team import` → `from app.scenarios.coding_team.agent import`
- 检查 `backend/app/router/graph.py` 的 `run_work_supervisor`/`run_coding_expert`/`run_coding_team` import 更新
- 检查 `backend/app/main.py` 等入口文件的 import
- 检查 `scenarios/__init__.py` 与各子包 `__init__.py` 是否有 re-export，有则同步更新
- 验证：
  - `test ! -d backend/app/agents` 目录不存在
  - `test -d backend/app/scenarios` 目录存在
  - `ls backend/app/scenarios/` 含 `work/`/`coding/`/`coding_team/` 3 个子目录
  - `ls backend/app/scenarios/work/ backend/app/scenarios/coding/ backend/app/scenarios/coding_team/`
    每个含 `agent.py` + `__init__.py`（work 另有 `mention.py`）
  - `grep -rn "from app.agents\." backend/ tests/` 返回空
  - `grep -rn "from app.scenarios\." backend/ tests/` 命中原调用点
  - `uv run ruff check backend/app/scenarios/`
  - `uv run pytest tests/python/unit -m "not integration"`

---

## P3 — 清理

### [ ] T-P3-1: 合并 `utils/paths.py` 与 `sandbox/path_guard.py`

- `backend/app/utils/paths.py::normalize_path` 函数迁移到 `backend/app/sandbox/path_guard.py`
  - 在 `path_guard.py` 末尾新增 `normalize_path` 函数（保持签名与行为不变）
  - 注意 import 去重（`pathlib.Path` 等若已有则不重复）
- 全项目更新 import：
  - `grep -rln "from app.utils.paths" backend/ tests/` 列出所有调用文件
  - 每个文件 `from app.utils.paths import normalize_path` → `from app.sandbox.path_guard import normalize_path`
- 删除 `backend/app/utils/paths.py`
- 验证：
  - `test ! -f backend/app/utils/paths.py` 文件不存在
  - `grep -rn "def normalize_path" backend/app/sandbox/path_guard.py` 命中
  - `grep -rn "from app.utils.paths" backend/ tests/` 返回空
  - `grep -rn "from app.sandbox.path_guard import normalize_path" backend/ tests/` 命中原调用点
  - `uv run ruff check backend/app/sandbox/ backend/app/utils/`
  - `uv run pytest tests/python/unit -m "not integration" -k "path or sandbox"`

---

## 全局验证

### [ ] T-GLOBAL-1: 全量单元测试

- 命令：`uv run pytest tests/python/unit -m "not integration"`
- 预期：全部通过（已知 6 个失败保持不变或减少）

### [ ] T-GLOBAL-2: 全量风格检查

- 命令：`uv run ruff check backend/`
- 预期：无错误

### [ ] T-GLOBAL-3: 残留 import 检查

- 命令（PowerShell 或 git bash）：
  - `grep -rn "from app.deep\." backend/ tests/` 返回空
  - `grep -rn "from app.agents\." backend/ tests/` 返回空
  - `grep -rn "from app.utils.sse_events" backend/ tests/` 返回空
  - `grep -rn "from app.utils.paths" backend/ tests/` 返回空
  - `grep -rn "from app.security.approval_flow" backend/ tests/` 返回空
  - `grep -rn "from app.observability.langsmith_dual" backend/ tests/` 返回空
  - `grep -rn "^CLI_TOOL_NAME\b" backend/app/` 返回空
- 预期：全部返回空

### [ ] T-GLOBAL-4: 前端无回归（仅验证，不改前端）

- 命令：`pnpm typecheck`
- 预期：无新增错误（本提案不动前端，仅验证 import 路径未影响类型契约）

### [ ] T-GLOBAL-5: AGENTS.md §11 文件地图同步

- 检查 `docs/agents/01-architecture-file-map.md` 是否引用旧路径（`app/deep/`/`app/agents/` 等）
- 若有引用，更新为新路径（`app/deepagent/`/`app/scenarios/` 等）
- 验证：`grep -rn "app/deep/\|app/agents/" docs/agents/` 仅在历史 ADR 或归档提案中出现
