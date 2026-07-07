# Tasks: backend/app 包结构重构

## 预期修改文件

### 新增文件
- [ ] `backend/app/approval/__init__.py`
- [ ] `backend/app/approval/state.py`
- [ ] `backend/app/approval/decision.py`
- [ ] `backend/app/api/__init__.py`
- [ ] `backend/app/api/schemas.py`
- [ ] `backend/app/api/health.py`
- [ ] `backend/app/api/sandbox.py`
- [ ] `backend/app/api/chat.py`
- [ ] `backend/app/api/memory.py`
- [ ] `backend/app/api/mcp.py`
- [ ] `backend/app/api/skills.py`
- [ ] `backend/app/api/workspace.py`
- [ ] `backend/app/api/config_reload.py`
- [ ] `backend/app/api/models_test.py`
- [ ] `backend/app/config/__init__.py`
- [ ] `backend/app/config/settings.py`
- [ ] `backend/app/config/subagents.py`
- [ ] `backend/app/config/prompts/__init__.py`
- [ ] `backend/app/config/prompts/builtin.py`
- [ ] `backend/app/config/prompts/team.py`
- [ ] `backend/app/deep/tools.py`
- [ ] `backend/app/deep/streaming.py`
- [ ] `backend/app/deep/approval.py`
- [ ] `backend/app/deep/recovery.py`
- [ ] `backend/app/team/planner.py`
- [ ] `backend/app/team/scheduler.py`
- [ ] `backend/app/team/blackboard.py`
- [ ] `backend/app/team/aggregator.py`
- [ ] `backend/app/subagents/base.py`
- [ ] `backend/app/utils/paths.py`

### 修改文件
- [ ] `backend/app/main.py`（1019 → ~150 行）
- [ ] `backend/app/config.py` → 删除（拆为 `config/` 包）
- [ ] `backend/app/deep/agent.py`（813 → ~200 行）
- [ ] `backend/app/deep/__init__.py`
- [ ] `backend/app/team/orchestrator.py`（681 → ~200 行）
- [ ] `backend/app/team/__init__.py`
- [ ] `backend/app/subagents/code_agent.py`
- [ ] `backend/app/subagents/rag_agent.py`
- [ ] `backend/app/subagents/web_agent.py`
- [ ] `backend/app/subagents/custom_agent.py`
- [ ] `backend/app/subagents/__init__.py`
- [ ] `backend/app/utils/security.py`（移除 ApprovalDecision）
- [ ] `backend/app/utils/sse_events.py`（合并 make_team_event）
- [ ] `backend/app/tools/filesystem.py`（路径归一化改用 utils/paths）
- [ ] `backend/app/router/graph.py`（仅 import 路径更新，无逻辑变更）
- [ ] `AGENTS.md` §11 文件地图更新

### 删除文件
- [ ] `backend/app/config.py`（迁移为 `config/` 包后删除原文件）

## 规模判定
- 涉及文件数: 45+ → 规模: **L**
- 涉及模块数: 9（main/config/deep/team/subagents/utils/approval/api/tools）

---

## Phase 1: 提取 approval 模块（解耦 deep ↔ main 反射）

- [ ] T1.1 新建 `backend/app/approval/decision.py`
  - 从 `utils/security.py` 移入 `ApprovalDecision` dataclass
  - `__all__ = ["ApprovalDecision"]`
- [ ] T1.2 新建 `backend/app/approval/state.py`
  - 从 `main.py` 移入 `_pending_approvals` + `_abort_flags` 模块级 dict
  - 实现函数：`submit_approval` / `pop_approval` / `set_abort` / `is_aborted` / `clear_abort`
  - `__all__` 包含全部 5 个函数
- [ ] T1.3 新建 `backend/app/approval/__init__.py`
  - 聚合导出：`from .decision import ApprovalDecision` + `from .state import *`
  - `__all__` 包含 `ApprovalDecision` + 5 个函数
- [ ] T1.4 修改 `utils/security.py`
  - 删除 `ApprovalDecision` 定义
  - 顶部加 `from app.approval.decision import ApprovalDecision`（向后兼容 re-export）
  - `__all__` 保留 `ApprovalDecision`
- [ ] T1.5 修改 `main.py`
  - 删除 `_pending_approvals` + `_abort_flags` 模块级 dict 声明
  - `chat_approve` 函数改为 `from app.approval import submit_approval` + 调用 `submit_approval(...)`
  - `chat_abort` 函数改为 `from app.approval import set_abort` + 调用 `set_abort(...)`
  - `_event_generator` 中止检查改为 `from app.approval import is_aborted, clear_abort`
- [ ] T1.6 修改 `deep/agent.py`
  - `_await_approval` 函数：删除 `from app import main` + `getattr(main, "_pending_approvals")` + `getattr(main, "_abort_flags")`
  - 改为 `from app.approval import pop_approval, is_aborted` + 调用新函数
  - `wait_for_approval` 函数同步改造
- [ ] T1.7 验证：`uv run pytest tests/python/unit -m "not integration"`

## Phase 2: 拆分 config.py → config/ 包

- [ ] T2.1 新建 `backend/app/config/prompts/builtin.py`
  - 移入 `_DEFAULT_CODE_SYSTEM_PROMPT` / `_DEFAULT_RAG_SYSTEM_PROMPT` / `_DEFAULT_WEB_SYSTEM_PROMPT`
  - 移入 `_DEFAULT_CODE_TRIGGER_DESCRIPTION` / `_DEFAULT_RAG_TRIGGER_DESCRIPTION` / `_DEFAULT_WEB_TRIGGER_DESCRIPTION`
  - 移入 `_DEFAULT_CODE_TOOLS` / `_DEFAULT_RAG_TOOLS` / `_DEFAULT_WEB_TOOLS`
  - `__all__` 导出全部常量
- [ ] T2.2 新建 `backend/app/config/prompts/team.py`
  - 移入 7 个 `_DEFAULT_*_SYSTEM_PROMPT`（frontend_dev/backend_dev/tester/architect/devops/ui_designer/product_manager）
  - 移入 7 个 `_DEFAULT_*_TRIGGER_DESCRIPTION`
  - 移入 `_DEFAULT_TEAM_TOOLS`
  - `__all__` 导出全部
- [ ] T2.3 新建 `backend/app/config/prompts/__init__.py`
  - 聚合导出 `from .builtin import *` + `from .team import *`
- [ ] T2.4 新建 `backend/app/config/subagents.py`
  - 移入 `SubagentSettings` / `CustomSubagentEntry` 模型
  - 移入 `BUILTIN_SUBAGENT_KEYS` / `BUILTIN_TEAM_KEYS` / `FORBIDDEN_SUBAGENT_TOOLS` / `_ALL_TOOLS` 常量
  - 移入 `_default_subagents` / `_default_team_subagents` / `_sanitize_custom_tools` / `_parse_custom_subagents` 函数
  - `_default_subagents` 改为 `from app.config.prompts.builtin import *` 读取常量
  - `_default_team_subagents` 改为 `from app.config.prompts.team import *` 读取常量
- [ ] T2.5 新建 `backend/app/config/settings.py`
  - 移入 `PROJECT_ROOT` / `BACKEND_ROOT` / `DATA_DIR` / `WORKSPACE_DIR` / `UPLOADS_DIR` 路径常量
  - 移入 `Settings` 类 + `get_settings` + `reload_settings`
  - `Settings.subagents` property 改为 `from app.config.subagents import _default_subagents, SubagentSettings`
  - `Settings.team_subagents` property 改为 `from app.config.subagents import _default_team_subagents`
  - `Settings.custom_subagents` property 改为 `from app.config.subagents import _parse_custom_subagents`
  - `Settings.tools_enabled` property 保持（依赖本模块 `_default_tools_enabled`）
- [ ] T2.6 新建 `backend/app/config/__init__.py`
  - `from .settings import Settings, get_settings, reload_settings, PROJECT_ROOT, BACKEND_ROOT, DATA_DIR, WORKSPACE_DIR, UPLOADS_DIR`
  - `from .subagents import SubagentSettings, CustomSubagentEntry, BUILTIN_SUBAGENT_KEYS, BUILTIN_TEAM_KEYS, FORBIDDEN_SUBAGENT_TOOLS`
  - `__all__` 聚合全部公共符号
- [ ] T2.7 删除 `backend/app/config.py`
- [ ] T2.8 验证：`uv run pytest tests/python/unit -m "not integration"` + `uv run ruff check backend/`

## Phase 3: 拆分 deep/agent.py → 5 文件

- [ ] T3.1 新建 `backend/app/deep/tools.py`
  - 移入 `_make_deep_tools` + `_load_mcp_tools`
  - 移入 `DANGEROUS_TOOLS` + `_TOOL_NAME_MAP` 常量
  - `_make_deep_tools` 改为 `from app.subagents.base import make_fs_tools, make_rag_tools, make_web_tools`（依赖 Phase 5，先用现有 import，Phase 5 完成后回填）
  - `__all__ = ["DANGEROUS_TOOLS", "_make_deep_tools", "_load_mcp_tools"]`
- [ ] T3.2 新建 `backend/app/deep/streaming.py`
  - 移入 `_stream_agent_events` 函数
  - import: `from app.utils.sse_events import make_sse_event, make_tool_call_event, make_tool_result_event, make_todo_event`
  - import: `from app.utils.text import strip_think, split_think, strip_tool_call_xml`
  - `__all__ = ["_stream_agent_events"]`
- [ ] T3.3 新建 `backend/app/deep/approval.py`
  - 移入：`_await_approval` + `wait_for_approval` + `_make_approval_event` + `_handle_directory_extension` + `_redact_args` + `_extract_paths_from_tool_call` + `_is_read_only_fs_tool` + `_READ_ONLY_FS_TOOLS` + `_ExtensionResult` + `_APPROVAL_POLL_INTERVAL`
  - 改为 `from app.approval import pop_approval, is_aborted`（依赖 Phase 1）
  - `__all__ = ["wait_for_approval", "_await_approval", "_make_approval_event", "_handle_directory_extension"]`
- [ ] T3.4 新建 `backend/app/deep/recovery.py`
  - 移入：`_inject_tool_error_messages` + `_sanitize_message_history` + `_collect_unpaired_tool_call_ids` + `_to_serializable`
  - `__all__ = ["_inject_tool_error_messages", "_sanitize_message_history"]`
- [ ] T3.5 修改 `backend/app/deep/agent.py`（813 → ~200 行）
  - 仅保留：`build_deep_agent` + `run_deep_path`
  - 顶部 import：`from app.deep.tools import _make_deep_tools, _load_mcp_tools, DANGEROUS_TOOLS, _TOOL_NAME_MAP`
  - 顶部 import：`from app.deep.streaming import _stream_agent_events`
  - 顶部 import：`from app.deep.approval import _await_approval, _make_approval_event, _handle_directory_extension`
  - 顶部 import：`from app.deep.recovery import _inject_tool_error_messages, _sanitize_message_history`
  - 保留 `_DEEP_SYSTEM_PROMPT` + `_extract_tasks` 在 agent.py（与 run_deep_path 主流程紧耦合）
  - `__all__` 保持 `["DANGEROUS_TOOLS", "build_deep_agent", "run_deep_path", "wait_for_approval"]`
  - `wait_for_approval` 改为 `from app.deep.approval import wait_for_approval`（re-export）
- [ ] T3.6 修改 `backend/app/deep/__init__.py`
  - 导出 `run_deep_path` + `build_deep_agent` + `DANGEROUS_TOOLS` + `wait_for_approval`
- [ ] T3.7 验证：`uv run pytest tests/python/unit -m "not integration"`

## Phase 4: 拆分 team/orchestrator.py → 5 文件

- [ ] T4.1 新建 `backend/app/team/blackboard.py`
  - 移入 `Blackboard` + `TeamPlanTask` + `TeamSubtaskResult` + `_serialize_blackboard`
  - `__all__ = ["Blackboard", "TeamPlanTask", "TeamSubtaskResult"]`
- [ ] T4.2 新建 `backend/app/team/planner.py`
  - 移入：`_ORCHESTRATOR_PROMPT` + `_BASE_EXPERTS` + `_TEAM_EXPERTS` + `_build_orchestrator_prompt` + `_build_project_context` + `_parse_plan` + `_try_parse_json` + `_extract_codeblock` + `_extract_first_json_object` + `_looks_like_dangerous_task` + `_validate_task`
  - `__all__` 导出 `_build_orchestrator_prompt` + `_parse_plan` + `_validate_task` + `_build_project_context`
- [ ] T4.3 新建 `backend/app/team/scheduler.py`
  - 移入：`_run_subtask` + `_collect_event` + `_SUBTASK_DONE_EVENT` + `_PASSTHROUGH_EVENTS`
  - 移入队列驱动逻辑（提取为独立函数 `_run_subtask_runner`）
  - `__all__ = ["_run_subtask", "_run_subtask_runner"]`
- [ ] T4.4 新建 `backend/app/team/aggregator.py`
  - 移入：`_AGGREGATOR_PROMPT` + `_run_aggregator` + `_quality_gate` + `_build_summary` + `_should_downgrade_to_single` + `_SIMPLE_TASK_KEYWORDS`
  - `__all__ = ["_run_aggregator", "_should_downgrade_to_single"]`
- [ ] T4.5 修改 `backend/app/team/orchestrator.py`（681 → ~200 行）
  - 仅保留：`run_team_path` 主入口
  - 顶部 import：`from app.team.blackboard import Blackboard, TeamPlanTask, TeamSubtaskResult`
  - 顶部 import：`from app.team.planner import _build_orchestrator_prompt, _parse_plan, _validate_task, _build_project_context`
  - 顶部 import：`from app.team.scheduler import _run_subtask, _run_subtask_runner`
  - 顶部 import：`from app.team.aggregator import _run_aggregator, _should_downgrade_to_single`
  - `__all__` 保持 `["run_team_path", "Blackboard", "TeamPlanTask", "TeamSubtaskResult"]`
- [ ] T4.6 修改 `backend/app/team/__init__.py`
  - 保持导出 `run_team_path` + `Blackboard` + `TeamPlanTask` + `TeamSubtaskResult`
- [ ] T4.7 验证：`uv run pytest tests/python/unit -m "not integration"`

## Phase 5: subagents 抽基类

- [ ] T5.1 新建 `backend/app/subagents/base.py`
  - 移入公开版工具构造函数（去掉下划线前缀）：
    - `make_fs_tools(thread_id)` — 从 `code_agent._make_fs_tools` 提取
    - `make_rag_tools(thread_id)` — 从 `rag_agent._make_rag_tools` 提取
    - `make_web_tools(thread_id)` — 从 `web_agent._make_web_tools` 提取
  - 移入 `extract_text(chunk)` 通用函数（从 `*_agent._extract_text` 合并）
  - 移入 `THINK_PROMPT_SUFFIX` 常量
  - `__all__ = ["make_fs_tools", "make_rag_tools", "make_web_tools", "extract_text", "THINK_PROMPT_SUFFIX"]`
- [ ] T5.2 修改 `backend/app/subagents/code_agent.py`
  - 删除 `_make_fs_tools` 定义 + `_extract_text` + `_THINK_PROMPT_SUFFIX`
  - 改为 `from app.subagents.base import make_fs_tools, extract_text, THINK_PROMPT_SUFFIX`
  - 内部调用 `_make_fs_tools` → `make_fs_tools`
  - 保留 `run_code_agent` 公共签名不变
  - 保留 `_make_fs_tools` 作为向后兼容别名（`_make_fs_tools = make_fs_tools`）— DeepAgent 当前 import 此名，Phase 3.1 已改为 `make_fs_tools`，此处别名可删除
- [ ] T5.3 修改 `backend/app/subagents/rag_agent.py`
  - 删除 `_make_rag_tools` + `_extract_text` + `_THINK_PROMPT_SUFFIX`
  - 改为 `from app.subagents.base import make_rag_tools, extract_text, THINK_PROMPT_SUFFIX`
- [ ] T5.4 修改 `backend/app/subagents/web_agent.py`
  - 删除 `_make_web_tools` + `_extract_text` + `_THINK_PROMPT_SUFFIX`
  - 改为 `from app.subagents.base import make_web_tools, extract_text, THINK_PROMPT_SUFFIX`
- [ ] T5.5 修改 `backend/app/deep/tools.py`（Phase 3.1 已创建）
  - 改为 `from app.subagents.base import make_fs_tools, make_rag_tools, make_web_tools`
  - 删除原 `from app.subagents.code_agent import _make_fs_tools` 等私有 import
- [ ] T5.6 验证：`uv run pytest tests/python/unit -m "not integration"`

## Phase 6: 拆分 main.py → api/ 包

- [ ] T6.1 新建 `backend/app/api/schemas.py`
  - 移入 15 个 Pydantic 模型：`AuthorizeRequest` / `RevokeRequest` / `ApproveRequest` / `AbortRequest` / `ChatRequest` / `SkillSaveRequest` / `ProfileEntryRequest` / `ProfileUpdateRequest` / `ExtractRequest` / `McpServerTestRequest` / `ConfigReloadRequest` / `ModelTestRequest` / `ModelTestResponse` / `CompactRequest`
  - `__all__` 导出全部
- [ ] T6.2 新建 `backend/app/api/health.py`
  - 移入 `root()` + `health()` + `_health_with_timeout()`
  - 导出 `register_health_routes(app)` 函数
- [ ] T6.3 新建 `backend/app/api/sandbox.py`
  - 移入 `sandbox_authorize` + `sandbox_revoke` + `sandbox_authorized`
  - 导出 `register_sandbox_routes(app)`
- [ ] T6.4 新建 `backend/app/api/chat.py`
  - 移入 `chat` + `chat_approve` + `chat_abort` + `chat_compact` + `_event_generator` + `_clear_thread_state`
  - 改为 `from app.approval import submit_approval, set_abort, is_aborted, clear_abort`
  - 导出 `register_chat_routes(app)`
- [ ] T6.5 新建 `backend/app/api/memory.py`
  - 移入 skills CRUD（`memory_skills_list` + `memory_skills_get` + `memory_skills_save` + `memory_skills_delete`）
  - 移入 profile CRUD（`memory_profile_list` + `memory_profile_add` + `memory_profile_update` + `memory_profile_delete` + `memory_profile_extract`）
  - 移入 checkpointer（`memory_checkpointer_status` + `memory_checkpointer_delete`）
  - 导出 `register_memory_routes(app)`
- [ ] T6.6 新建 `backend/app/api/mcp.py`
  - 移入 `mcp_servers_list` + `mcp_tools_list` + `mcp_servers_test` + `mcp_refresh`
  - 导出 `register_mcp_routes(app)`
- [ ] T6.7 新建 `backend/app/api/skills.py`
  - 移入 `list_skills` + `skills_reload`
  - 导出 `register_skills_routes(app)`
- [ ] T6.8 新建 `backend/app/api/workspace.py`
  - 移入 `workspace_list`
  - 导出 `register_workspace_routes(app)`
- [ ] T6.9 新建 `backend/app/api/config_reload.py`
  - 移入 `config_reload`
  - 导出 `register_config_reload_routes(app)`
- [ ] T6.10 新建 `backend/app/api/models_test.py`
  - 移入 `models_test`
  - 导出 `register_models_test_routes(app)`
- [ ] T6.11 新建 `backend/app/api/__init__.py`
  - 实现 `register_routes(app)` 函数，调用全部 10 个 `register_*_routes(app)`
- [ ] T6.12 修改 `backend/app/main.py`（1019 → ~150 行）
  - 仅保留：`lifespan` + `app = FastAPI(...)` + CORS 中间件 + `UTF8JSONBodyMiddleware` + `register_routes(app)` 调用
  - 删除全部 15 个 Pydantic 模型定义
  - 删除全部 REST 端点函数
  - 删除 `_pending_approvals` + `_abort_flags`（Phase 1 已迁移，确认无残留）
  - 删除 `_event_generator` + `_clear_thread_state`（已迁到 `api/chat.py`）
- [ ] T6.13 验证：`uv run pytest tests/python/unit -m "not integration"` + 端点冒烟测试

## Phase 7: utils 重复消除

- [ ] T7.1 新建 `backend/app/utils/paths.py`
  - 实现 `normalize_path(path: str | Path) -> Path`：统一路径归一化（resolve + case normalization）
  - `__all__ = ["normalize_path"]`
- [ ] T7.2 修改 `backend/app/utils/security.py`
  - 删除 `_normalize` 实现
  - 改为 `from app.utils.paths import normalize_path`
  - 内部调用 `_normalize` → `normalize_path`
- [ ] T7.3 修改 `backend/app/tools/filesystem.py`
  - 删除 `_resolve` 实现
  - 改为 `from app.utils.paths import normalize_path`
  - 内部调用 `_resolve` → `normalize_path`
- [ ] T7.4 修改 `backend/app/utils/sse_events.py`
  - 合并 `make_team_event` 逻辑到 `make_sse_event`（统一事件白名单，含 `_subtask_done`）
  - `make_team_event` 改为 `make_team_event = make_sse_event`（向后兼容别名）或直接删除（若无外部调用）
  - `make_approval_event` 改为调用 `make_sse_event("approval_request", data)`
- [ ] T7.5 验证：`uv run pytest tests/python/unit -m "not integration"` + `uv run ruff check backend/`

## Phase 8: 文档更新 + 最终验证

- [ ] T8.1 更新 `AGENTS.md` §11 文件地图
  - `main.py` 描述更新：仅 lifespan + app + 中间件
  - 新增 `api/` 包描述
  - 新增 `approval/` 包描述
  - `config.py` → `config/` 包描述
  - `deep/` 补充 `tools.py` / `streaming.py` / `approval.py` / `recovery.py`
  - `team/` 补充 `planner.py` / `scheduler.py` / `blackboard.py` / `aggregator.py`
  - `subagents/` 补充 `base.py`
  - `utils/` 补充 `paths.py`
- [ ] T8.2 更新 `AGENTS.md` §14.5 路径导入循环说明
  - 补充：approval 模块解耦后 deep 不再反射访问 main
- [ ] T8.3 全量测试：`uv run pytest tests/python/unit -m "not integration"`
- [ ] T8.4 风格检查：`uv run ruff check backend/`
- [ ] T8.5 全量 grep 验证无残留反射访问
  - `grep -r "getattr(main" backend/` 应为空
  - `grep -r "_pending_approvals" backend/app/main.py` 应为空
  - `grep -r "_abort_flags" backend/app/main.py` 应为空
  - `grep -r "from app.paths" backend/` 应为空（保持 §14.5 约束）
