# Design: paths/ 包重构为按能力域模块化结构

## Context

当前代码结构（截至 2026-07-06）：

```
backend/app/
├── router/
│   ├── classifier.py          ← 分类器（不变）
│   ├── graph.py               ← 840+ 行：分类 + 调度 + 内嵌路径 A/B + SSE 事件构造
│   └── state.py               ← RouterState（不变）
├── paths/
│   ├── deep_path.py           ← 1055 行：路径 C 全部逻辑 + 画像抽取
│   └── team_path.py           ← 806 行：路径 D 全部逻辑
├── subagents/                 ← 子代理实现（不变）
├── utils/
│   ├── sse_events.py          ← SSE 事件构造（已有，但 graph.py/deep_path.py 未使用）
│   ├── prompts.py             ← resolve_system_prompt（已有，但 graph.py 另有一份）
│   └── ...
└── ...
```

关键依赖关系：

```
main.py → router/graph.py → paths/deep_path.py (run_deep_path)
                           → paths/team_path.py (run_team_path)
                           → subagents/ (路径 B 内联)

team_path.py → deep_path.py (deep 子任务复用)
deep_path.py → router.state (TYPE_CHECKING 延迟导入)
deep_path.py → router.graph.resolve_system_prompt (延迟导入)
team_path.py → router.graph._run_chat_path (降级时延迟导入)
```

## Goals / Non-Goals

**Goals:**

- 删除 `paths/` 包，按能力域重组为 `chat/`、`deep/`、`team/`
- 从 `graph.py` 拆出路径 A 实现到 `chat/`，路径 B 编排到 `subagents/dispatch.py`
- 统一 SSE 事件构造到 `utils/sse_events.py`，删除 `graph.py._sse()` 和 `deep_path.py` 局部版
- 统一 `resolve_system_prompt` 到 `utils/prompts.py`，删除 `graph.py` 版
- 画像抽取从 `deep_path.py` 拆到 `memory/profile_extractor.py`
- graph.py 精简到约 200 行（分类 + 调度 + @skill 解析）
- 功能 100% 等价，零行为变更

**Non-Goals:**

- 不改 SSE 事件契约（§13）
- 不改 API 端点签名
- 不改前端代码
- 不改 `subagents/` 内部子代理实现（code_agent/rag_agent/web_agent/custom_agent）
- 不改 `tools/`、`vectorstore/`、`embedding/`、`mcp/`、`observability/`

## Decisions

### D1. 目标包结构

```
backend/app/
├── main.py                          ← 更新 import 路径
├── config.py                        ← 不变
├── llm.py                           ← 不变
│
├── router/                          ← 精简：分类 + 调度
│   ├── __init__.py                  ← 导出 run_router
│   ├── classifier.py                ← 不变
│   ├── graph.py                     ← 精简至 ~200 行
│   └── state.py                     ← 不变
│
├── chat/                            ← 新建：路径 A
│   ├── __init__.py                  ← 导出 run_chat_path
│   └── run.py                       ← 从 graph.py 拆出 _run_chat_path
│
├── subagents/                       ← 扩展：路径 B 编排
│   ├── __init__.py                  ← 新增导出 run_tool_path
│   ├── dispatch.py                  ← 新增：路径 B 编排（从 graph.py 拆出）
│   ├── code_agent.py                ← 不变
│   ├── rag_agent.py                 ← 不变
│   ├── web_agent.py                 ← 不变
│   └── custom_agent.py              ← 不变
│
├── deep/                            ← 新建：路径 C
│   ├── __init__.py                  ← 导出 run_deep_path, build_deep_agent, DANGEROUS_TOOLS, wait_for_approval
│   └── agent.py                     ← 从 paths/deep_path.py 搬入，删除画像抽取
│
├── team/                            ← 新建：路径 D
│   ├── __init__.py                  ← 导出 run_team_path, Blackboard, TeamPlanTask, TeamSubtaskResult
│   └── orchestrator.py              ← 从 paths/team_path.py 搬入
│
├── memory/                          ← 扩展
│   ├── profile_extractor.py         ← 新增：从 deep_path.py 拆出画像抽取
│   └── ...（其余不变）
│
├── utils/                           ← 统一
│   ├── sse_events.py                ← 统一 SSE 事件构造（删除重复）
│   ├── prompts.py                   ← 统一 resolve_system_prompt（删除重复）
│   └── ...（其余不变）
│
├── tools/                           ← 不变
├── vectorstore/                     ← 不变
├── embedding/                       ← 不变
├── mcp/                             ← 不变
└── observability/                   ← 不变
```

### D2. graph.py 精简方案

从 graph.py 迁出的函数与目标位置：

| 函数 | 当前行号 | 目标 |
|---|---|---|
| `resolve_system_prompt()` | L43-63 | 删除，统一到 `utils/prompts.py` |
| `_sse()` | L340-362 | 删除，统一到 `utils/sse_events.py` |
| `_extract_keywords_from_trigger()` | L163-180 | `subagents/dispatch.py` |
| `_llm_select_subagent()` | L183-267 | `subagents/dispatch.py` |
| `_keyword_select_subagent()` | L270-327 | `subagents/dispatch.py` |
| `_select_subagent()` | L330-337 | `subagents/dispatch.py` |
| `_SUBAGENT_ROUTER_PROMPT` | L152-160 | `subagents/dispatch.py` |
| `_run_chat_path()` | L365-427 | `chat/run.py`（改名为 `run_chat_path`） |
| `_run_tool_path()` | L430-521 | `subagents/dispatch.py` (作为 `run_tool_path`) |
| `_run_deep_path()` | L524-553 | 删除（graph.py 直接调 `deep.run_deep_path`） |
| `_convert_subagent_event()` | L556-636 | `subagents/dispatch.py`（改名为 `convert_subagent_event`） |
| `_parse_skill_tag()` | L650-686 | 保留在 graph.py（属于路由调度逻辑） |
| `_SKILL_TAG_RE` / `_SKILL_CONTENT_MAX` | L644-647 | 保留在 graph.py |
| `run_router()` | L689-817 | 保留，改为调 `chat.run_chat_path` / `subagents.dispatch.run_tool_path` / `deep.run_deep_path` |
| `build_router_graph()` + 节点函数 | L71-143 | 保留在 graph.py |

精简后 graph.py 保留：图构建 + 节点函数 + `_parse_skill_tag` + `run_router` + `_load_history_from_checkpointer`。

**graph.py 需删除的 import**（仅被迁出函数使用）：

| import | 原使用者 | 迁移后归属 |
|---|---|---|
| `from app.llm import get_chat_model` | `_run_chat_path`, `_llm_select_subagent` | `chat/run.py`, `subagents/dispatch.py` |
| `from app.subagents import run_code_agent, run_custom_agent, run_rag_agent, run_web_agent` | `_run_tool_path` | `subagents/dispatch.py` |
| `from app.utils.text import ThinkFilter, extract_chunk_text as _extract_chunk_text, strip_think` | `_run_chat_path`, `_convert_subagent_event`, `_llm_select_subagent` | `chat/run.py`, `subagents/dispatch.py` |

**graph.py 保留的 import**（`run_router` / `_parse_skill_tag` 仍使用）：

- `from app.config import get_settings` — `run_router` 用
- `from app.memory.context import trim_messages_with_budget` — `run_router` 用
- `from app.memory.profile_store import build_profile_prompt` — `run_router` 用
- `from app.memory.skills_loader import get_skills` — `_parse_skill_tag` 用（test_skills_api.py monkeypatch `app.router.graph.get_skills` 依赖此 import）
- `from app.observability.langsmith import trace_span` — `run_router` 用
- `from app.observability.logger import logger` — `run_router` 用
- `from app.router.classifier import classify_message` — `run_router` 用
- `from app.router.state import RouterState` — 类型注解用

**graph.py 新增 import**（替代迁出的路径函数）：

```python
from app.chat.run import run_chat_path
from app.deep.agent import run_deep_path
from app.subagents.dispatch import run_tool_path
from app.team.orchestrator import run_team_path
```

`run_router` 内部调用更新：`_run_chat_path(...)` → `run_chat_path(...)`，`_run_tool_path(...)` → `run_tool_path(...)`，`_run_deep_path(...)` → `run_deep_path(state, ...)`。

`__all__` 更新为：`["build_router_graph", "run_router", "_parse_skill_tag"]`（移除 `resolve_system_prompt`）。

### D3. SSE 事件构造统一

当前 3 处重复：

| 位置 | 函数 | 统一到 |
|---|---|---|
| `graph.py._sse()` | 通用 SSE 事件 | `utils/sse_events.make_sse_event()` |
| `deep_path._make_todo_event()` | todo 事件 | `utils/sse_events.make_todo_event()` |
| `deep_path._make_tool_call_event()` | tool_call 事件 | `utils/sse_events.make_tool_call_event()` |
| `deep_path._make_tool_result_event()` | tool_result 事件 | `utils/sse_events.make_tool_result_event()` |
| `deep_path._make_approval_event()` | approval 事件 | `utils/sse_events.make_approval_event()`（新增） |
| `team_path._make_team_event()` | team 事件 | `utils/sse_events.make_team_event()` |

统一后 `deep/agent.py` 和 `team/orchestrator.py` 统一 import `from app.utils.sse_events import ...`。

### D4. resolve_system_prompt 统一

- 删除 `graph.py` 版本（L43-63）
- 保留 `utils/prompts.py` 版本作为唯一源头
- `deep/agent.py` 改为 `from app.utils.prompts import resolve_system_prompt`
- `chat/run.py` 同样 import `from app.utils.prompts import resolve_system_prompt`

### D5. 画像抽取独立

从 `paths/deep_path.py` 拆出以下函数到 `memory/profile_extractor.py`：

| 函数 | 说明 |
|---|---|
| `_extract_last_assistant_reply()` | 从 agent state 读最后一条 AIMessage |
| `_extract_profile_via_llm()` | 调 LLM 抽取画像条目 |
| `_extract_tasks: set[asyncio.Task]` | 防 GC 的 task 引用集合 |

新增公共入口 `extract_and_save_profile(message, agent, config)` 供 `deep/agent.py` 和 `team/orchestrator.py` 调用。

`deep/agent.py` 的 `run_deep_path` 结尾改为：

```python
if get_settings().profile_auto_extract:
    from app.memory.profile_extractor import extract_and_save_profile
    task = asyncio.create_task(extract_and_save_profile(message, agent, config))
    _extract_tasks.add(task)
    task.add_done_callback(_extract_tasks.discard)
```

### D6. 依赖方向（单向无环）

```
main.py
  → router/graph.py (分类 + 调度)
       → chat/run.py (路径 A)
       → subagents/dispatch.py (路径 B 编排)
            → subagents/code_agent.py, ... (子代理实现)
       → deep/agent.py (路径 C)
       → team/orchestrator.py (路径 D)
            → deep/agent.py (deep 子任务复用)
            → subagents/dispatch.py (非 deep 子任务)

utils/  ← 所有模块可依赖（SSE、prompt、text、security）
memory/ ← chat, deep, team 可依赖（画像、checkpointer）
tools/  ← subagents, deep 可依赖

禁止反向依赖：
- utils/ 不得 import chat/, deep/, team/, router/
- memory/ 不得 import chat/, deep/, team/ (profile_extractor 依赖 llm + observability，不依赖路径)
- deep/ 不得 import team/
- team/ 可 import deep/（deep 子任务复用）
```

循环导入处理：
- `deep/agent.py` 的 `RouterState` 仍用 `TYPE_CHECKING` 延迟导入 `router.state`
- `team/orchestrator.py` 降级到 chat 时延迟导入 `chat.run_chat_path`
- `deep/agent.py` 改为 `from app.utils.prompts import resolve_system_prompt`（不再依赖 router.graph）

### D7. import 路径变更清单

| 文件 | 旧 import | 新 import |
|---|---|---|
| `router/graph.py` | `from app.paths.deep_path import run_deep_path` | `from app.deep.agent import run_deep_path` |
| `router/graph.py` | `from app.paths.team_path import run_team_path` | `from app.team.orchestrator import run_team_path` |
| `main.py:870` | `from app.paths.deep_path import _extract_profile_via_llm` | `from app.memory.profile_extractor import extract_profile_via_llm` |
| `team/orchestrator.py` | `from app.paths.deep_path import run_deep_path` | `from app.deep.agent import run_deep_path` |
| `deep/agent.py` | `from app.router.graph import resolve_system_prompt` | `from app.utils.prompts import resolve_system_prompt` |
| `team/orchestrator.py` | `from app.router.graph import _run_chat_path` | `from app.chat.run import run_chat_path` |
| `subagents/dispatch.py` | （内联在 graph.py 中） | `from app.chat.run import run_chat_path`（回退路径 A） |
| `subagents/dispatch.py` | （内联在 graph.py 中） | `from app.utils.sse_events import make_sse_event` |

### D8. 测试 import 路径变更清单

**直接 import 变更：**

| 测试文件 | 旧 import | 新 import |
|---|---|---|
| `test_sse_contract.py` | `from app.router.graph import _sse` | `from app.utils.sse_events import make_sse_event` |
| `test_sse_contract.py` | `from app.router.graph import _convert_subagent_event` | `from app.subagents.dispatch import convert_subagent_event` |
| `test_sse_contract.py` | `from app.router.graph import _select_subagent` | `from app.subagents.dispatch import select_subagent` |
| `test_sse_contract.py` | `from app.paths.deep_path import _make_approval_event` | `from app.deep.agent import _make_approval_event` |
| `test_sse_contract.py` | `from app.paths.deep_path import _make_todo_event` | `from app.utils.sse_events import make_todo_event` |
| `test_router_graph.py` | `from app.router.graph import build_router_graph, resolve_system_prompt, run_router` | `from app.router.graph import build_router_graph, run_router` + `from app.utils.prompts import resolve_system_prompt` |
| `test_custom_subagents.py` | `from app.router.graph import _select_subagent` | `from app.subagents.dispatch import select_subagent` |
| `test_subagents_config.py` | `from app.router.graph import _select_subagent` | `from app.subagents.dispatch import select_subagent` |
| `test_tools_config.py` | `from app.paths.deep_path import (...)` | `from app.deep.agent import (...)` |
| `test_memory_profile.py` | `from app.paths.deep_path import _extract_profile_via_llm` | `from app.memory.profile_extractor import extract_profile_via_llm` |
| `test_team_path.py` | `from app.paths.team_path import (Blackboard, TeamPlanTask, _build_summary, _make_team_event, _parse_plan, _serialize_blackboard, _validate_task, run_team_path)` | `from app.team.orchestrator import (...)`（同函数名） |
| `test_skills_api.py` | `from app.router.graph import _parse_skill_tag` | 不变（`_parse_skill_tag` 保留在 graph.py） |
| `test_chat_control_api.py` | `patch("app.router.graph.classify_message", ...)` | 不变（`classify_message` 仍在 graph.py 顶层 import） |

**monkeypatch 目标变更：**

| 测试文件 | 旧 monkeypatch 目标 | 新 monkeypatch 目标 |
|---|---|---|
| `test_router_graph.py` | `app.router.graph.run_deep_path` | `app.router.graph.run_deep_path`（不变 — graph.py 顶层 import `run_deep_path`） |
| `test_router_graph.py` | `app.router.graph.get_chat_model` | `app.subagents.dispatch.get_chat_model` |
| `test_router_graph.py` | `app.router.graph.run_code_agent` | `app.subagents.dispatch.run_code_agent` |
| `test_router_graph.py` | `app.router.graph.run_web_agent` | `app.subagents.dispatch.run_web_agent` |
| `test_router_graph.py` | `app.router.graph.classify_message` | 不变（graph.py 仍 import `classify_message`） |
| `test_sse_contract.py` | `app.router.graph.get_settings` | `app.subagents.dispatch.get_settings` |
| `test_custom_subagents.py` | `app.router.graph.get_settings` (5 处) | `app.subagents.dispatch.get_settings` |
| `test_subagents_config.py` | `app.router.graph.get_settings` (8 处) | `app.subagents.dispatch.get_settings` |
| `test_skills_api.py` | `app.router.graph.get_skills` (6 处) | 不变（graph.py 仍 import `get_skills`） |
| `test_team_path.py` | `app.paths.team_path.get_chat_model` (多处) | `app.team.orchestrator.get_chat_model` |
| `test_team_path.py` | `app.paths.team_path.run_code_agent` | `app.team.orchestrator.run_code_agent` |
| `test_team_path.py` | `app.paths.team_path.run_rag_agent` | `app.team.orchestrator.run_rag_agent` |
| `test_team_path.py` | `app.paths.team_path.run_deep_path` | `app.team.orchestrator.run_deep_path` |
| `test_memory_profile.py` | `app.paths.deep_path.get_chat_model` (4 处) | `app.memory.profile_extractor.get_chat_model` |

### D9. scripts 目录变更

| 文件 | 旧 import | 新 import |
|---|---|---|
| `scripts/e2e_custom_subagent_factory.py:83` | `from app.router.graph import _llm_select_subagent, _keyword_select_subagent` | `from app.subagents.dispatch import llm_select_subagent, keyword_select_subagent` |

### D10. `_make_approval_event` 拆分策略

`deep_path._make_approval_event` 包含两类逻辑：
1. **事件构造**：`{"event": "approval_request", "data": json.dumps(...)}` → 纯 SSE 封装
2. **业务逻辑**：`_redact_args()`、preview 描述生成 → 属于 DeepAgent 业务

统一策略：
- `utils/sse_events.py` 新增 `make_approval_event(data: dict) -> dict`，只做 JSON 封装
- redaction 和 preview 生成逻辑保留在 `deep/agent.py` 内部（`_redact_args` 仍为内部函数）
- `deep/agent.py` 先构造 data dict（含 redaction + preview），再调 `make_approval_event(data)`

### D11. error 事件序列化行为（无需修改）

`graph.py._sse` 对 error 事件走 `str(data)`，`utils/sse_events.make_sse_event` 对 error 事件走 `json.dumps`（dict 输入时）或 passthrough（string 输入时）。

分析实际调用方：
- `graph.py._sse("error", f"LLM 不可用: {exc}")` — 输入为 string → `make_sse_event` 直接 passthrough，行为与 `_sse` 一致
- `deep_path.py` `{"event": "error", "data": f"...: {exc}"}` — 直接构造 dict，不走 `_sse`
- `team_path._make_team_event("error", {"message": "..."})` — 输入为 dict → `make_sse_event` JSON 序列化，与 `_make_team_event` 行为一致

结论：`make_sse_event` 已正确处理所有场景（string passthrough + dict JSON 序列化），**无需修改**。统一时直接用 `make_sse_event` 替换 `_sse` 即可，行为完全等价。

### D12. `_run_tool_path` 内部依赖

`_run_tool_path` 搬到 `subagents/dispatch.py` 后有两个内部依赖：
1. **回退路径 A**：调 `_run_chat_path()` → 需 import `from app.chat.run import run_chat_path`
2. **SSE 事件构造**：调 `_sse()` → 需 import `from app.utils.sse_events import make_sse_event`

这两个 import 不会造成循环依赖（chat 不依赖 subagents，utils 不依赖任何路径模块）。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 循环导入（team→chat 降级） | 延迟 import 保持不变 |
| deep_path 搬迁遗漏内部函数 | 全量搬迁，删除原文件后跑 pytest 确认 |
| `_sse` 统一后行为差异 | `make_sse_event` 已正确处理所有场景（string passthrough + dict JSON），与 `_sse` 对 string 输入行为一致，无差异（D11 已分析确认） |
| 画像抽取拆出后 deep_path 依赖断裂 | `extract_and_save_profile` 内部 import deep 模块，不反向依赖 |

## Migration Plan

1. 统一公共工具（SSE → `utils/sse_events.py`，prompt → `utils/prompts.py`）
2. 拆 graph.py 内联代码（`_run_chat_path` → `chat/`，路径 B → `subagents/dispatch.py`）
3. 搬迁 `paths/deep_path.py` → `deep/agent.py`，删除画像抽取
4. 搬迁 `paths/team_path.py` → `team/orchestrator.py`
5. 新建 `memory/profile_extractor.py`
6. 删除 `paths/` 包
7. 更新所有 import 路径
8. 更新测试 import 路径
9. 跑全量测试
10. 更新 AGENTS.md §11 文件地图

## Open Questions

| # | 问题 | 默认假设 |
|---|---|---|
| Q1 | `chat/` 包是否需要再拆（如 system prompt 管理） | 否，chat 逻辑只有 60 行，单文件足够 |
| Q2 | `subagents/dispatch.py` 是否改 `__init__.py` 导出名 | 是，`from app.subagents import run_tool_path` |
| Q3 | `_make_approval_event` 是否也统一到 sse_events | 是，deep 和 team 可能都需要审批事件 |
