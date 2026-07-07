# Design: backend/app 包结构重构

## 目标包结构

```
backend/app/
├── main.py                    ← ~150 行（仅 lifespan + app + 中间件 + register_routes）
├── config/                    ← 拆分 config.py (517 行)
│   ├── __init__.py            ← 聚合导出 get_settings/reload_settings/Settings
│   ├── settings.py            ← Settings + get_settings + reload_settings + 路径常量
│   ├── subagents.py           ← SubagentSettings + CustomSubagentEntry + _parse_custom_subagents
│   └── prompts/               ← 专家角色 prompt 外置
│       ├── __init__.py
│       ├── builtin.py         ← code/rag/web 的 _DEFAULT_*_SYSTEM_PROMPT + trigger_description
│       └── team.py            ← 7 个团队角色 _DEFAULT_*_SYSTEM_PROMPT + trigger_description
│
├── api/                       ← 新增：从 main.py 拆出所有 REST 端点
│   ├── __init__.py            ← register_routes(app) 统一注册
│   ├── schemas.py             ← 15 个 Pydantic 请求体模型
│   ├── health.py              ← GET / + /api/health
│   ├── sandbox.py             ← /api/sandbox/*
│   ├── chat.py                ← /api/chat + /api/chat/approve + /api/chat/abort + /api/chat/compact
│   ├── memory.py              ← /api/memory/*
│   ├── mcp.py                 ← /api/mcp/*
│   ├── skills.py              ← /api/skills + /api/skills/reload
│   ├── workspace.py           ← /api/workspace/list
│   ├── config_reload.py       ← POST /api/config/reload
│   └── models_test.py         ← POST /api/models/test
│
├── approval/                  ← 新增：审批状态从 main.py 提取（解耦 deep 依赖）
│   ├── __init__.py            ← 聚合导出
│   ├── state.py               ← _pending_approvals + _abort_flags + get/clear/set 函数
│   └── decision.py            ← ApprovalDecision dataclass（从 security.py 移入）
│
├── router/                    ← 保持不变（486 行，结构合理）
├── chat/                      ← 保持不变
│
├── deep/                      ← 拆分 agent.py (813 行)
│   ├── __init__.py
│   ├── agent.py               ← build_deep_agent + run_deep_path（~200 行，仅编排）
│   ├── tools.py               ← _make_deep_tools + _load_mcp_tools
│   ├── streaming.py           ← _stream_agent_events + SSE 事件映射
│   ├── approval.py            ← _await_approval + _make_approval_event + _handle_directory_extension
│   └── recovery.py            ← _inject_tool_error_messages + _sanitize_message_history
│
├── team/                      ← 拆分 orchestrator.py (681 行)
│   ├── __init__.py
│   ├── orchestrator.py        ← run_team_path 主入口（~200 行）
│   ├── planner.py             ← _build_orchestrator_prompt + _parse_plan + _validate_task
│   ├── scheduler.py           ← _run_subtask + 队列驱动并行
│   ├── blackboard.py          ← Blackboard + TeamPlanTask + TeamSubtaskResult
│   └── aggregator.py          ← _run_aggregator + _quality_gate + _build_summary
│
├── subagents/                 ← 抽基类消除重复
│   ├── __init__.py
│   ├── base.py                ← 新增：make_fs_tools + _extract_text + 通用事件转换
│   ├── dispatch.py            ← 保持
│   ├── code_agent.py          ← 继承 base，仅保留差异化
│   ├── rag_agent.py
│   ├── web_agent.py
│   └── custom_agent.py
│
├── tools/                     ← 保持，但提取路径归一化到 utils/paths.py
├── memory/                    ← 保持结构
├── mcp/                       ← 保持
├── embedding/                 ← 保持
├── vectorstore/               ← 保持
├── observability/             ← 保持
│
└── utils/
    ├── security.py            ← 仅保留 SessionSandbox 沙箱授权（移除 ApprovalDecision）
    ├── sse_events.py          ← 合并 make_team_event 到 make_sse_event
    ├── text.py                ← 保持
    ├── chunks.py              ← 保持
    ├── prompts.py             ← 保持
    └── paths.py               ← 新增：统一路径归一化（消除 security/filesystem 重复）
```

## 关键设计决策

### D1: approval 模块设计

**问题**：`deep/agent.py` 通过 `getattr(main, "_pending_approvals")` 反射访问 `main.py` 私有 dict。

**方案**：新增 `approval/` 包，将审批状态从 `main.py` 提取为独立模块。

```python
# approval/state.py
from app.approval.decision import ApprovalDecision

_pending_approvals: dict[str, ApprovalDecision] = {}
_abort_flags: dict[str, bool] = {}

def submit_approval(thread_id: str, decision: ApprovalDecision) -> None:
    _pending_approvals[thread_id] = decision

def pop_approval(thread_id: str) -> ApprovalDecision | None:
    return _pending_approvals.pop(thread_id, None)

def set_abort(thread_id: str) -> None:
    _abort_flags[thread_id] = True

def is_aborted(thread_id: str) -> bool:
    return _abort_flags.get(thread_id, False)

def clear_abort(thread_id: str) -> None:
    _abort_flags.pop(thread_id, None)
```

**迁移点**：
- `main.py:chat_approve` 改为 `from app.approval import submit_approval`
- `main.py:chat_abort` 改为 `from app.approval import set_abort`
- `main.py:_event_generator` 中止检查改为 `from app.approval import is_aborted, clear_abort`
- `deep/agent.py:_await_approval` 改为 `from app.approval import pop_approval, is_aborted`，**删除** `getattr(main, "_pending_approvals")` 反射

**收益**：deep 模块不再反向依赖 main，可独立单元测试。

### D2: ApprovalDecision 迁移

**问题**：`ApprovalDecision` dataclass 定义在 `utils/security.py`，但语义属于审批层，与沙箱无关。

**方案**：移到 `approval/decision.py`，`utils/security.py` 改为 `from app.approval.decision import ApprovalDecision`（保持向后兼容的 re-export）。

### D3: deep 模块拆分策略

**问题**：`deep/agent.py` 813 行混杂 7 类职责。

**方案**：按职责拆为 5 文件，`run_deep_path` 主入口保持在 `agent.py`，仅编排。

| 新文件 | 来源函数 | 行数估算 |
|---|---|---|
| `deep/agent.py` | `build_deep_agent` + `run_deep_path` | ~200 |
| `deep/tools.py` | `_make_deep_tools` + `_load_mcp_tools` | ~80 |
| `deep/streaming.py` | `_stream_agent_events` | ~100 |
| `deep/approval.py` | `_await_approval` + `wait_for_approval` + `_make_approval_event` + `_handle_directory_extension` + `_redact_args` + `_extract_paths_from_tool_call` + `_is_read_only_fs_tool` | ~250 |
| `deep/recovery.py` | `_inject_tool_error_messages` + `_sanitize_message_history` + `_collect_unpaired_tool_call_ids` + `_to_serializable` | ~150 |

**导入方向**：`agent.py` → `tools.py` + `streaming.py` + `approval.py` + `recovery.py`，无循环。

### D4: team 模块拆分策略

**问题**：`team/orchestrator.py` 681 行混杂 6 类职责。

**方案**：按职责拆为 5 文件。

| 新文件 | 来源函数 | 行数估算 |
|---|---|---|
| `team/orchestrator.py` | `run_team_path` + `_build_project_context` | ~200 |
| `team/planner.py` | `_build_orchestrator_prompt` + `_parse_plan` + `_try_parse_json` + `_extract_codeblock` + `_extract_first_json_object` + `_looks_like_dangerous_task` + `_validate_task` | ~200 |
| `team/scheduler.py` | `_run_subtask` + `_collect_event` + `_run_subtask_runner`（队列驱动逻辑） | ~200 |
| `team/blackboard.py` | `Blackboard` + `TeamPlanTask` + `TeamSubtaskResult` + `_serialize_blackboard` | ~50 |
| `team/aggregator.py` | `_run_aggregator` + `_quality_gate` + `_build_summary` + `_should_downgrade_to_single` | ~100 |

### D5: subagents 抽基类

**问题**：code/rag/web_agent.py 结构高度相似，且 `deep/agent.py` 跨包 import `subagents/code_agent._make_fs_tools` 私有函数。

**方案**：新增 `subagents/base.py`，提取公共函数：
- `make_fs_tools(thread_id)` — 公开名（去掉下划线），code_agent 与 deep 共用
- `make_rag_tools(thread_id)` — 从 rag_agent 提取
- `make_web_tools(thread_id)` — 从 web_agent 提取
- `extract_text(chunk)` — 统一 LLM chunk 文本提取
- `THINK_PROMPT_SUFFIX` 常量

`code_agent.py` / `rag_agent.py` / `web_agent.py` 改为 `from app.subagents.base import make_fs_tools, ...`，仅保留 `run_*_agent` 差异化逻辑。

`deep/tools.py` 改为 `from app.subagents.base import make_fs_tools, make_rag_tools, make_web_tools`，**消除**跨包私有 import。

### D6: config 拆分

**问题**：`config.py` 517 行混杂配置 + 模型 + 7 个专家角色 prompt（~80 行）。

**方案**：
- `config/settings.py`：`Settings` + `get_settings` + `reload_settings` + 路径常量
- `config/subagents.py`：`SubagentSettings` + `CustomSubagentEntry` + `_parse_custom_subagents` + `_sanitize_custom_tools` + 常量集合
- `config/prompts/builtin.py`：3 个内置子代理 prompt + trigger_description
- `config/prompts/team.py`：7 个团队角色 prompt + trigger_description

`config/__init__.py` 聚合导出：`from .settings import *` + `from .subagents import *`，保持 `from app.config import get_settings` 向后兼容。

### D7: main.py 拆分

**问题**：`main.py` 1019 行混杂 lifespan + 中间件 + 15 个 Pydantic 模型 + 6 类 REST 端点。

**方案**：
- `main.py`：仅保留 `lifespan` + `app = FastAPI(...)` + 中间件 + `register_routes(app)` 调用（~150 行）
- `api/schemas.py`：15 个 Pydantic 模型
- `api/<domain>.py`：按 10 个域拆分端点
- `api/__init__.py`：`register_routes(app)` 函数，统一注册所有 router

**端点拆分映射**：

| api 文件 | 端点 |
|---|---|
| `health.py` | `GET /` + `GET /api/health` |
| `sandbox.py` | `/api/sandbox/authorize` + `/api/sandbox/revoke` + `/api/sandbox/authorized/{thread_id}` |
| `chat.py` | `POST /api/chat` + `POST /api/chat/approve` + `POST /api/chat/abort` + `POST /api/chat/compact` |
| `memory.py` | `/api/memory/skills/*` + `/api/memory/profile/*` + `/api/memory/checkpointer/*` |
| `mcp.py` | `/api/mcp/servers` + `/api/mcp/tools` + `/api/mcp/servers/test` + `/api/mcp/refresh` |
| `skills.py` | `GET /api/skills` + `POST /api/skills/reload` |
| `workspace.py` | `GET /api/workspace/list` |
| `config_reload.py` | `POST /api/config/reload` |
| `models_test.py` | `POST /api/models/test` |

### D8: utils 重复消除

**路径归一化**：新增 `utils/paths.py`，提取 `security._normalize` 与 `filesystem._resolve` 的公共实现 `normalize_path(path: str) -> Path`，两处改为调用。

**SSE 构造**：`utils/sse_events.py` 合并 `make_team_event` 到 `make_sse_event`（统一事件白名单），`make_approval_event` 改为调用 `make_sse_event("approval_request", data)`。

## 迁移批次（按依赖顺序）

| 批次 | 动作 | 依赖 | 验证 |
|---|---|---|---|
| 1 | 提取 `approval/` 模块（D1+D2） | 无 | 单元测试 |
| 2 | 拆分 `config/`（D6） | 无 | 单元测试 |
| 3 | 拆分 `deep/`（D3） | 批次 1 | 单元测试 |
| 4 | 拆分 `team/`（D4） | 批次 3 | 单元测试 |
| 5 | subagents 抽基类（D5） | 无 | 单元测试 |
| 6 | 拆分 `main.py` → `api/`（D7） | 批次 1 | 单元测试 + 端点冒烟 |
| 7 | utils 重复消除（D8） | 无 | 单元测试 |
| 8 | 文档更新 | 全部 | ruff check |

每批完成后运行：
```bash
uv run pytest tests/python/unit -m "not integration"
uv run ruff check backend/
```

## 向后兼容

- `from app.config import get_settings` — 保持（`config/__init__.py` 聚合导出）
- `from app.utils.security import ApprovalDecision` — 保持（re-export from approval.decision）
- `from app.deep.agent import run_deep_path` — 保持（`deep/__init__.py` 聚合导出）
- `from app.team.orchestrator import run_team_path` — 保持
- `from app.subagents import run_code_agent` — 保持
- 所有 `run_*_path` / `run_*_agent` 公共签名 — 不变
- SSE 事件契约 — 不变
- API 端点签名 — 不变
