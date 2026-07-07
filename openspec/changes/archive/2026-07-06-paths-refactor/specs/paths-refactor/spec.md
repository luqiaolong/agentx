## MODIFIED Requirements

### Requirement: 后端模块化包结构

后端 SHALL 按能力域组织为 `chat/`、`deep/`、`team/`、`subagents/dispatch.py` 四个独立模块，替代原 `paths/` 包。

#### Scenario: 路径 A 由 chat/ 承载

- **GIVEN** 用户消息被分类为 `CHAT`
- **WHEN** `run_router` 执行路径 A
- **THEN** 调用 `app.chat.run.run_chat_path()`
- **AND** 行为与原 `_run_chat_path` 完全一致

#### Scenario: 路径 B 由 subagents/dispatch.py 承载

- **GIVEN** 用户消息被分类为 `SINGLE_TOOL`
- **WHEN** `run_router` 执行路径 B
- **THEN** 调用 `app.subagents.dispatch.run_tool_path()`
- **AND** 行为与原 `_run_tool_path` 完全一致

#### Scenario: 路径 C 由 deep/ 承载

- **GIVEN** 用户消息被分类为 `DEEP_TASK`
- **WHEN** `run_router` 执行路径 C
- **THEN** 调用 `app.deep.agent.run_deep_path()`
- **AND** 行为与原 `paths/deep_path.run_deep_path` 完全一致

#### Scenario: 路径 D 由 team/ 承载

- **GIVEN** 用户消息以 `agent_mode=agent_team` 发送
- **WHEN** `run_router` 执行路径 D
- **THEN** 调用 `app.team.orchestrator.run_team_path()`
- **AND** 行为与原 `paths/team_path.run_team_path` 完全一致

---

### Requirement: SSE 事件构造统一

后端 SHALL 将所有 SSE 事件构造函数统一到 `utils/sse_events.py`，删除 `graph.py._sse()` 和 `deep_path.py` 局部版本。

#### Scenario: deep/ 和 team/ 使用统一 SSE 工具

- **GIVEN** `deep/agent.py` 需要构造 `todo_update` 事件
- **WHEN** 调用事件构造函数
- **THEN** 使用 `from app.utils.sse_events import make_todo_event`
- **AND** 不再使用 `deep_path._make_todo_event()`

#### Scenario: graph.py 不再有 _sse 函数

- **GIVEN** `graph.py` 需要构造 SSE 事件
- **WHEN** `run_router` yield done 事件
- **THEN** 使用 `from app.utils.sse_events import make_sse_event`
- **AND** `_sse()` 函数已被删除

---

### Requirement: resolve_system_prompt 统一

后端 SHALL 将 `resolve_system_prompt` 统一到 `utils/prompts.py`，删除 `graph.py` 中的重复版本。

#### Scenario: deep/agent.py 从 utils 导入

- **GIVEN** `deep/agent.py` 的 `build_deep_agent` 需要合并 system prompt
- **WHEN** 调用 `resolve_system_prompt`
- **THEN** 使用 `from app.utils.prompts import resolve_system_prompt`
- **AND** 不再延迟导入 `app.router.graph.resolve_system_prompt`

---

### Requirement: 画像抽取独立

后端 SHALL 将画像抽取逻辑从 `deep/agent.py` 拆到 `memory/profile_extractor.py`，使其可被任意路径复用。

#### Scenario: DeepAgent 路径 C 触发画像抽取

- **GIVEN** `profile_auto_extract=True` 且 DeepAgent 执行完成
- **WHEN** `run_deep_path` 结尾触发画像抽取
- **THEN** 调用 `from app.memory.profile_extractor import extract_and_save_profile`
- **AND** 抽取结果写入 `profile.json`，与重构前行为一致

#### Scenario: Team 路径 D 可复用画像抽取

- **GIVEN** `profile_auto_extract=True` 且 AgentTeam 执行完成
- **WHEN** 未来 Team 路径需要触发画像抽取
- **THEN** 可直接调用 `extract_and_save_profile`，无需依赖 `deep/agent.py`

---

### Requirement: paths/ 包删除

后端 SHALL 删除 `backend/app/paths/` 整个包，所有原有功能已迁移到新的能力域模块。

#### Scenario: 不存在 paths 包

- **WHEN** 尝试 `from app.paths import ...`
- **THEN** 抛出 `ModuleNotFoundError`

#### Scenario: 功能等价

- **WHEN** 运行全量单元测试
- **THEN** 所有测试通过，无行为变更

---

### Requirement: router/graph.py 精简

`router/graph.py` SHALL 仅保留分类调度逻辑，删除内嵌的路径实现代码。

#### Scenario: graph.py 不定义路径实现函数

- **GIVEN** 重构后的 `graph.py`
- **WHEN** 检查文件内容
- **THEN** 不**定义** `_run_chat_path`、`_run_tool_path`、`_run_deep_path`、`_convert_subagent_event`、`_sse`、`_select_subagent`、`_llm_select_subagent`、`_keyword_select_subagent`、`_extract_keywords_from_trigger`、`resolve_system_prompt` 等路径实现函数（`def` 声明不存在）
- **AND** 仅定义 `classify_node`、`chat_node`、`tool_node`、`deep_node`、`route_conditional`、`build_router_graph`、`run_router`、`_parse_skill_tag`、`_load_history_from_checkpointer` 等调度逻辑

#### Scenario: graph.py 不再 import 仅路径使用的符号

- **GIVEN** 重构后的 `graph.py`
- **WHEN** 检查 import 语句
- **THEN** 不包含 `from app.llm import get_chat_model`（仅 `_run_chat_path`/`_llm_select_subagent` 使用，已迁出）
- **AND** 不包含 `from app.subagents import run_code_agent, ...`（仅 `_run_tool_path` 使用，已迁出）
- **AND** 不包含 `from app.utils.text import ThinkFilter, ...`（仅路径实现使用，已迁出）
- **AND** 仍包含 `from app.memory.skills_loader import get_skills`（`_parse_skill_tag` 使用）

#### Scenario: graph.py 行数精简

- **GIVEN** 重构后的 `graph.py`
- **WHEN** 统计代码行数
- **THEN** 行数不超过 250 行

---

### Requirement: 依赖方向单向无环

后端模块间 SHALL 遵循单向依赖规则，禁止反向依赖。

#### Scenario: utils 不依赖路径模块

- **GIVEN** `utils/sse_events.py` 和 `utils/prompts.py`
- **WHEN** 检查 import
- **THEN** 不包含 `from app.chat`、`from app.deep`、`from app.team`、`from app.router` 的 import

#### Scenario: deep 不依赖 team

- **GIVEN** `deep/agent.py`
- **WHEN** 检查 import
- **THEN** 不包含 `from app.team` 的 import

#### Scenario: team 可依赖 deep

- **GIVEN** `team/orchestrator.py`
- **WHEN** deep 子任务执行
- **THEN** 可 import `from app.deep import run_deep_path`
