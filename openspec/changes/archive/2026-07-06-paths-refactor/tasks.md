# Tasks: paths/ 包重构为按能力域模块化结构

## Phase 1: 统一公共工具（消除重复）

- [x] 修改 `utils/sse_events.py`
  - 新增 `make_approval_event(data: dict) -> dict`：仅做 JSON 封装（`{"event": "approval_request", "data": json.dumps(data, ensure_ascii=False)}`）
  - 确认 `make_sse_event` 已覆盖 `_sse` 全部逻辑（对比 event 类型列表 — 已覆盖，`make_sse_event` 是 `_sse` 的超集）
  - **不要修改 error 事件处理**（D11 已确认 `make_sse_event` 对 string passthrough + dict JSON 的行为与所有调用方一致）
  - 更新 `__all__` 加入 `make_approval_event`
- [x] 验证 `utils/prompts.py` 的 `resolve_system_prompt` 与 `graph.py` 版本完全一致（已确认一致）

## Phase 2: 拆 graph.py 内联代码到 chat/ 和 subagents/

- [x] 新建 `backend/app/chat/__init__.py`
  - 导出 `run_chat_path`
- [x] 新建 `backend/app/chat/run.py`
  - 从 graph.py 拷贝 `_run_chat_path()`，改名为 `run_chat_path()`
  - import 改为：
    - `from app.utils.prompts import resolve_system_prompt`
    - `from app.llm import get_chat_model`
    - `from app.config import get_settings`
    - `from app.utils.text import ThinkFilter, extract_chunk_text`
    - `from app.observability.logger import logger`
  - SSE 构造改为 `from app.utils.sse_events import make_sse_event`
  - 所有 `_sse(...)` 调用改为 `make_sse_event(...)`
- [x] 新建 `backend/app/subagents/dispatch.py`
  - 从 graph.py 拷贝以下函数/常量（去掉前导下划线改为公开名）：
    - `_SUBAGENT_ROUTER_PROMPT` → `SUBAGENT_ROUTER_PROMPT`
    - `_extract_keywords_from_trigger()` → `extract_keywords_from_trigger()`
    - `_llm_select_subagent()` → `llm_select_subagent()`
    - `_keyword_select_subagent()` → `keyword_select_subagent()`
    - `_select_subagent()` → `select_subagent()`
    - `_run_tool_path()` → `run_tool_path()`
    - `_convert_subagent_event()` → `convert_subagent_event()`
  - import：
    - `from app.config import get_settings`
    - `from app.llm import get_chat_model`
    - `from app.subagents import run_code_agent, run_custom_agent, run_rag_agent, run_web_agent`
    - `from app.utils.text import ThinkFilter, strip_think`
    - `from app.utils.sse_events import make_sse_event`
    - `from app.observability.logger import logger`
  - 回退路径 A：`from app.chat.run import run_chat_path`（延迟 import 保持不变 — 虽然 chat 不依赖 subagents 无循环，但保持与原代码一致的延迟 import 模式）
  - 所有 `_sse(...)` 调用改为 `make_sse_event(...)`
- [x] 修改 `backend/app/subagents/__init__.py`
  - 新增导出 `run_tool_path`（从 `dispatch` 模块）

## Phase 3: 搬迁 deep_path.py → deep/agent.py

- [x] 新建 `backend/app/deep/__init__.py`
  - 导出 `run_deep_path`, `build_deep_agent`, `DANGEROUS_TOOLS`, `wait_for_approval`
- [x] 新建 `backend/app/deep/agent.py`
  - 从 `paths/deep_path.py` 完整拷贝内容
  - 删除画像抽取相关代码：
    - `_extract_last_assistant_reply()` 函数
    - `_extract_profile_via_llm()` 函数
    - `_extract_tasks: set[asyncio.Task]` 声明
    - `run_deep_path` 结尾 T10 画像抽取 block（改为调用 `memory/profile_extractor`，见 Phase 5）
  - 替换局部 SSE 函数为显式 import（**不要用 `import *`**）：
    - `from app.utils.sse_events import make_sse_event, make_todo_event, make_tool_call_event, make_tool_result_event, make_team_event, make_approval_event`
    - 删除 deep_path.py 内部定义的 `_make_todo_event`, `_make_tool_call_event`, `_make_tool_result_event`
    - `_make_approval_event` 保留在 `deep/agent.py`（含 redaction + preview 业务逻辑），但内部 JSON 封装改为调 `make_approval_event(data)`（D10 策略）
  - `resolve_system_prompt` 改为 `from app.utils.prompts import resolve_system_prompt`（删除延迟 import `from app.router.graph import resolve_system_prompt`）
  - `__all__` 更新为 `["DANGEROUS_TOOLS", "build_deep_agent", "run_deep_path", "wait_for_approval"]`

## Phase 4: 搬迁 team_path.py → team/orchestrator.py

- [x] 新建 `backend/app/team/__init__.py`
  - 导出 `run_team_path`, `Blackboard`, `TeamPlanTask`, `TeamSubtaskResult`
- [x] 新建 `backend/app/team/orchestrator.py`
  - 从 `paths/team_path.py` 完整拷贝内容
  - import 路径更新：
    - `from app.paths.deep_path import run_deep_path` → `from app.deep.agent import run_deep_path`
    - `from app.router.graph import _run_chat_path` → `from app.chat.run import run_chat_path`（保持延迟 import，在函数内部）
    - `RouterState` TYPE_CHECKING 保持不变
  - 替换局部 SSE 函数为显式 import：
    - `from app.utils.sse_events import make_sse_event, make_team_event`
    - 删除 team_path.py 内部定义的 `_make_team_event`
    - 所有 `_make_team_event(...)` 调用改为 `make_team_event(...)`
    - `_SUBTASK_DONE_EVENT` 常量保留在 `team/orchestrator.py`
  - `__all__` 更新为 `["run_team_path", "Blackboard", "TeamPlanTask", "TeamSubtaskResult"]`

## Phase 5: 画像抽取独立到 memory/profile_extractor.py

- [x] 新建 `backend/app/memory/profile_extractor.py`
  - 从 `deep_path.py` 提取以下函数（去掉前导下划线）：
    - `_extract_last_assistant_reply()` → `extract_last_assistant_reply()`
    - `_extract_profile_via_llm()` → `extract_profile_via_llm()`
    - `_extract_tasks` 集合管理
  - import：
    - `from app.llm import get_chat_model`
    - `from app.observability.logger import logger`
    - `from langchain_core.messages import AIMessage`（在函数内部延迟 import）
  - `__all__` = `["extract_profile_via_llm", "extract_last_assistant_reply"]`
- [x] 修改 `deep/agent.py` 的 `run_deep_path` 结尾 T10 部分
  - 改为：
    ```python
    if get_settings().profile_auto_extract:
        from app.memory.profile_extractor import extract_last_assistant_reply, extract_profile_via_llm
        async def _do_extract() -> None:
            try:
                assistant_reply = await extract_last_assistant_reply(agent, config)
                if assistant_reply:
                    from app.memory.profile_store import upsert_from_llm
                    entries = await extract_profile_via_llm(message, assistant_reply)
                    upsert_from_llm(entries)
                    logger.info("profile auto extracted", count=len(entries))
            except Exception as exc:
                logger.warning("profile auto extract failed", error=str(exc))
        task = asyncio.create_task(_do_extract())
        _extract_tasks.add(task)
        task.add_done_callback(_extract_tasks.discard)
    ```
  - `_extract_tasks` 集合保留在 `deep/agent.py`（或移到 `profile_extractor.py`，二选一 — 建议保留在 `deep/agent.py` 因为引用在此）
- [x] 不修改 `memory/__init__.py`（`profile_extractor` 通过完整路径 import）

## Phase 6: 精简 graph.py + 更新 import

- [x] 修改 `router/graph.py`
  - **删除函数**：`resolve_system_prompt()`、`_sse()`、`_extract_keywords_from_trigger()`、
    `_llm_select_subagent()`、`_keyword_select_subagent()`、`_select_subagent()`、
    `_run_chat_path()`、`_run_tool_path()`、`_run_deep_path()`、`_convert_subagent_event()`
  - **删除常量**：`_SUBAGENT_ROUTER_PROMPT`
  - **删除 import**（仅被迁出函数使用）：
    - `from app.llm import get_chat_model`
    - `from app.subagents import run_code_agent, run_custom_agent, run_rag_agent, run_web_agent`
    - `from app.utils.text import ThinkFilter, extract_chunk_text as _extract_chunk_text, strip_think`
    - `import json`（检查是否仍被使用 — `_parse_skill_tag` 不用 json，`run_router` 不用 json，若仅 `_sse` 用则删除）
  - **更新 import**：
    - `from app.paths.deep_path import run_deep_path` → `from app.deep.agent import run_deep_path`
    - `from app.paths.team_path import run_team_path` → `from app.team.orchestrator import run_team_path`
    - 新增：`from app.chat.run import run_chat_path`
    - 新增：`from app.subagents.dispatch import run_tool_path`
  - **更新 `run_router()` 内部调用**（不用别名，直接用新函数名）：
    - CHAT 分支：`_run_chat_path(...)` → `run_chat_path(...)`
    - SINGLE_TOOL 分支：`_run_tool_path(...)` → `run_tool_path(...)`
    - DEEP_TASK 分支：`_run_deep_path(state, ...)` → `run_deep_path(state, ...)`（直接调，不再包一层）
    - 删除 `_run_deep_path` 的 try/except 包装（`run_deep_path` 自身已有异常处理；如需保留 error SSE，在 `run_router` 内 try/except 即可）
    - done 事件：`_sse("done", "{}")` → `make_sse_event("done", "{}")`（需新增 `from app.utils.sse_events import make_sse_event`）
  - **保留**：图构建、节点函数、`_parse_skill_tag`、`_load_history_from_checkpointer`
  - **更新 `__all__`**：`["build_router_graph", "run_router", "_parse_skill_tag"]`（移除 `resolve_system_prompt`）

## Phase 7: 更新 main.py import

- [x] 修改 `backend/app/main.py:870`
  - `from app.paths.deep_path import _extract_profile_via_llm` → `from app.memory.profile_extractor import extract_profile_via_llm`
  - 调用处 `_extract_profile_via_llm(req.message, req.assistant_reply)` → `extract_profile_via_llm(req.message, req.assistant_reply)`

## Phase 8: 删除 paths/ 包

- [x] 删除 `backend/app/paths/__init__.py`
- [x] 删除 `backend/app/paths/deep_path.py`
- [x] 删除 `backend/app/paths/team_path.py`
- [x] 删除 `backend/app/paths/` 目录

## Phase 9: 更新测试

### 9.1 直接 import 变更

- [x] `tests/python/unit/test_sse_contract.py`
  - `from app.router.graph import _sse` → `from app.utils.sse_events import make_sse_event`（调用处 `_sse` → `make_sse_event`）
  - `from app.router.graph import _convert_subagent_event` → `from app.subagents.dispatch import convert_subagent_event`
  - `from app.router.graph import _select_subagent` → `from app.subagents.dispatch import select_subagent`
  - `from app.paths.deep_path import _make_approval_event` → `from app.deep.agent import _make_approval_event`
  - `from app.paths.deep_path import _make_todo_event` → `from app.utils.sse_events import make_todo_event`（调用处 `_make_todo_event` → `make_todo_event`）
- [x] `tests/python/unit/test_router_graph.py:23`
  - `from app.router.graph import build_router_graph, resolve_system_prompt, run_router` → `from app.router.graph import build_router_graph, run_router` + `from app.utils.prompts import resolve_system_prompt`
- [x] `tests/python/unit/test_custom_subagents.py:26`
  - `from app.router.graph import _select_subagent` → `from app.subagents.dispatch import select_subagent`
- [x] `tests/python/unit/test_subagents_config.py:20`
  - `from app.router.graph import _select_subagent` → `from app.subagents.dispatch import select_subagent`
- [x] `tests/python/unit/test_tools_config.py:17`
  - `from app.paths.deep_path import (...)` → `from app.deep.agent import (...)`
- [x] `tests/python/unit/test_memory_profile.py`（4 处：L322, L344, L360, L376）
  - `from app.paths.deep_path import _extract_profile_via_llm` → `from app.memory.profile_extractor import extract_profile_via_llm`
  - 调用处 `_extract_profile_via_llm(...)` → `extract_profile_via_llm(...)`
- [x] `tests/python/unit/test_team_path.py:21`
  - `from app.paths.team_path import (...)` → `from app.team.orchestrator import (...)`（同函数名）
- [x] `tests/python/unit/test_skills_api.py:15` — 不变（`_parse_skill_tag` 保留在 graph.py）

### 9.2 monkeypatch 目标变更

- [x] `tests/python/unit/test_router_graph.py`
  - `app.router.graph.get_chat_model`（L159, L220, L250, L276, L307, L347, L386）→ `app.subagents.dispatch.get_chat_model`
  - `app.router.graph.run_code_agent`（L177, L225, L283, L314, L360, L395）→ `app.subagents.dispatch.run_code_agent`
  - `app.router.graph.run_web_agent`（L255, L359）→ `app.subagents.dispatch.run_web_agent`
  - `app.router.graph.classify_message`（多处）→ 不变（graph.py 仍 import `classify_message`）
  - `app.router.graph.run_deep_path`（L435, L473）→ 不变（graph.py 顶层 import `run_deep_path` from `app.deep.agent`）
- [x] `tests/python/unit/test_sse_contract.py`
  - `app.router.graph.get_settings`（L432, L450, L464）→ `app.subagents.dispatch.get_settings`
- [x] `tests/python/unit/test_custom_subagents.py`
  - `app.router.graph.get_settings`（L249, L274, L301, L327, L361）→ `app.subagents.dispatch.get_settings`
- [x] `tests/python/unit/test_subagents_config.py`
  - `app.router.graph.get_settings`（L275, L295, L311, L326, L353, L370, L386, L402）→ `app.subagents.dispatch.get_settings`
- [x] `tests/python/unit/test_skills_api.py`
  - `app.router.graph.get_skills`（L157, L170, L183, L201, L212, L224）→ 不变（graph.py 仍 import `get_skills`）
- [x] `tests/python/unit/test_team_path.py`
  - `app.paths.team_path.get_chat_model`（L316, L365, L393, L424, L444, L479, L517）→ `app.team.orchestrator.get_chat_model`
  - `app.paths.team_path.run_code_agent`（L324, L372, L403, L484）→ `app.team.orchestrator.run_code_agent`
  - `app.paths.team_path.run_rag_agent`（L325, L404）→ `app.team.orchestrator.run_rag_agent`
  - `app.paths.team_path.run_deep_path`（L451）→ `app.team.orchestrator.run_deep_path`
- [x] `tests/python/unit/test_memory_profile.py`
  - `app.paths.deep_path.get_chat_model`（L332, L350, L366, L385）→ `app.memory.profile_extractor.get_chat_model`
- [x] `tests/python/unit/test_chat_control_api.py:157` — 不变（`app.router.graph.classify_message` 仍有效）

### 9.3 运行测试

- [x] 运行全量测试：`uv run pytest tests/python/unit -m "not integration"`
- [x] 确认测试全部通过（423+ 测试用例）

## Phase 10: 文档更新

- [x] 更新 `AGENTS.md` §11 文件地图
  - `paths/` → `chat/` + `deep/` + `team/` + `subagents/dispatch.py`
  - `memory/` 补充 `profile_extractor.py`
  - 当前 §11 提到的 `chat_path.py` 和 `tool_path.py` 本就不存在（文档已过期），借此机会修正
- [x] 更新 `AGENTS.md` §12 Router 三路径表格中的文件链接
  - 路径 A → `chat/run.py`
  - 路径 B → `subagents/dispatch.py`
  - 路径 C → `deep/agent.py`
  - 新增路径 D → `team/orchestrator.py`
- [x] 更新 `AGENTS.md` §14.5 路径导入循环说明（循环已消除，deep 不再依赖 router.graph）

## 最终验证

代码已 100% 落地，所有任务由 subagent 验证通过。本 tasks.md 的勾选状态为事后回填
（paths/ 包已删除，按能力域拆分为 chat/ / deep/ / team/ / subagents/dispatch.py，
测试 import 与 monkeypatch 目标已全部更新，423+ 单测全绿）。
归档时统一打勾，以反映真实交付状态。
