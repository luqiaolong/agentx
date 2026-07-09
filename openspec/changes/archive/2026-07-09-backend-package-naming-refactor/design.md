# Design: 后端包名与目录结构重命名

> 配套 proposal.md，详述技术决策、备选方案与影响面。按 P0→P3 优先级组织。
> 遵循项目硬约束：**开发阶段无需考虑灰度兼容，直接推倒重来，禁止写兼容层/shim/`@Deprecated`**。

---

## P0 — 零风险重命名/提取

### D1. 重命名两个 `CLI_TOOL_NAME` 消歧

**现状**：

- [tools/cli.py:31](file:///d:/java/agentprojects/agentx/backend/app/tools/cli.py#L31) `CLI_TOOL_NAME: str = "cli_execute"`
  — LangChain `@tool` 装饰器注册的工具名（LLM-facing，子代理/自定义子代理使用）
- [security/dangerous_tools.py:24](file:///d:/java/agentprojects/agentx/backend/app/security/dangerous_tools.py#L24) `CLI_TOOL_NAME = "execute"`
  — deepagents `LocalShellBackend` 内置工具名（deepagents-facing，`SafeLocalShellBackend` 继承）
- [security/command_filter.py:145](file:///d:/java/agentprojects/agentx/backend/app/security/command_filter.py#L145) `_CLI_TOOL_NAMES = frozenset({"execute", "cli_execute"})`
  — 被迫维护双名集合，证明命名冲突已实际发生

**风险**：两常量同名同语义（"CLI 工具名"）但不同值，未来若有人 `from app.security.dangerous_tools import CLI_TOOL_NAME`
误用为 LLM 工具名过滤，会漏匹配 `"cli_execute"`，反之亦然。

**备选方案**：

- A. **重命名消歧**（推荐）：`LLM_CLI_TOOL_NAME`（tools/cli.py）+ `SHELL_CLI_TOOL_NAME`（dangerous_tools.py）。
  语义清晰，调用方一眼区分。
- B. **统一为单一常量**：删除其中一个，强制所有 CLI 走单一工具名。
  不可行——`cli_execute`（LangChain `@tool`）与 `execute`（deepagents `LocalShellBackend`）
  是两套独立的工具暴露机制，无法合并。
- C. **保留双名 + 加注释**：治标不治本，未来仍可能误用。

**决策**：方案 A。

```python
# tools/cli.py
LLM_CLI_TOOL_NAME: str = "cli_execute"

# security/dangerous_tools.py
SHELL_CLI_TOOL_NAME = "execute"  # deepagents LocalShellBackend 内置工具名
DANGEROUS_TOOLS: frozenset[str] = frozenset({
    "edit_file", "write_file", SHELL_CLI_TOOL_NAME, ...
})

# security/command_filter.py（_CLI_TOOL_NAMES 保留，用于运行时双名过滤）
_CLI_TOOL_NAMES = frozenset({LLM_CLI_TOOL_NAME, SHELL_CLI_TOOL_NAME})
# 或保留字面量 frozenset({"execute", "cli_execute"}) 以解耦 import
```

**影响面**：

- `tools/cli.py` 内部使用 `CLI_TOOL_NAME` 1 处（L31 定义）+ `__all__` 1 处；
- `dangerous_tools.py` 内部使用 `CLI_TOOL_NAME` 2 处（L24 定义 + L31 引用）；
- `command_filter.py` 不直接 import 上述常量，用字面量集合，零影响；
- 全项目 grep `CLI_TOOL_NAME` 命中 8 行（已确认），全部在上述 3 文件内。

### D2. 提取 `_effective_blocklist`/`_is_command_blocked` 重复

**现状**：

- [tools/cli.py:38-49](file:///d:/java/agentprojects/agentx/backend/app/tools/cli.py#L38) `_effective_blocklist()` + `_is_command_blocked(command)` — **合并用户配置**
- [deep/safe_shell_backend.py:26-39](file:///d:/java/agentprojects/agentx/backend/app/deep/safe_shell_backend.py#L26) 同名函数，**逐字相同** — **合并用户配置**
- [security/command_filter.py:70-76](file:///d:/java/agentprojects/agentx/backend/app/security/command_filter.py#L70) `is_command_blocked(command)` — **已存在**，但**仅检查 `DEFAULT_BLOCKLIST`，不合并用户配置**（docstring 明确说明）

```python
# cli.py / safe_shell_backend.py 的版本（合并用户配置）
def _effective_blocklist() -> frozenset[str]:
    cfg = get_settings().cli_tool_blocklist
    if not cfg:
        return DEFAULT_BLOCKLIST
    user_blocked = frozenset(
        cmd.strip().lower() for cmd in cfg if isinstance(cmd, str) and cmd.strip()
    )
    return DEFAULT_BLOCKLIST | user_blocked

def _is_command_blocked(command: str) -> bool:
    return command.strip().lower() in _effective_blocklist()

# command_filter.py 的版本（不合并用户配置）
def is_command_blocked(command: str) -> bool:
    """注意：本函数仅检查默认黑名单 DEFAULT_BLOCKLIST，不合并用户配置。"""
    return command.strip().lower() in DEFAULT_BLOCKLIST
```

**风险**：

1. 维护需双向同步 cli.py 与 safe_shell_backend.py 的副本；
2. `command_filter.is_command_blocked` 与 cli.py/safe_shell_backend.py 的 `_is_command_blocked` **同名不同行为**，
   未来若有人误用 `command_filter.is_command_blocked` 替代合并版本，会漏过滤用户配置的黑名单命令；
3. `command_filter.is_command_blocked` 的 docstring 已自认"仅检查默认黑名单"，但当前**无任何调用方**
   依赖此"默认 only"行为（grep 确认 cli.py/safe_shell_backend.py 都自带副本而非调用它）。

**设计**：

1. 在 `security/command_filter.py` 新增 `effective_blocklist()` 公开函数（合并用户配置逻辑）：
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
   需要 `from app.config import get_settings` import（command_filter.py 当前无此 import，需新增）。

2. **修改** `security/command_filter.py::is_command_blocked` 使其调用 `effective_blocklist()`：
   ```python
   def is_command_blocked(command: str) -> bool:
       """命令名是否在黑名单内（含用户配置合并，不区分大小写）。"""
       return command.strip().lower() in effective_blocklist()
   ```
   更新 docstring 删除"仅检查默认黑名单"说明。这是**行为增强**（原来不合并用户配置，现在合并），
   但因当前无调用方依赖旧行为，零回归风险。

3. `tools/cli.py` 删除 L38-49 的 `_effective_blocklist` + `_is_command_blocked`，
   改为 `from app.security.command_filter import is_command_blocked`，
   调用处 `_is_command_blocked(cmd_name)` → `is_command_blocked(cmd_name)`。

4. `deep/safe_shell_backend.py` 同上。

**影响面**：

- `security/command_filter.py`：新增 `effective_blocklist()` + 修改 `is_command_blocked()` 行为 + 新增 `get_settings` import；
- `tools/cli.py`：删除 L38-49，改 import，调用处更名（约 1 处）；
- `deep/safe_shell_backend.py`：同上；
- **行为变化**：`command_filter.is_command_blocked` 现在合并用户配置（增强），但无调用方依赖旧行为；
- 无外部 API 变化（`is_command_blocked` 已在 `__all__` 中导出，签名不变）。

### D3. 提升 `utils/sse_events.py` → `sse/events.py`

**现状**：[utils/sse_events.py](file:///d:/java/agentprojects/agentx/backend/app/utils/sse_events.py)
定义 9 个 SSE 事件工厂函数：

```python
make_sse_event, make_todo_event, make_tool_call_event, make_tool_result_event,
make_team_event, make_approval_event, make_error_event  # 等
```

被 `api/chat.py`、`agents/supervisor/work_supervisor.py`、`deep/execution.py`、`deep/streaming.py`、
`team/orchestrator.py`、`router/graph.py`、`security/approval_flow.py` 等 7+ 个核心模块依赖。
**这是 SSE 事件契约的实现层**（对应 AGENTS.md §13），不是通用工具函数。

**问题**：

- 放在 `utils/` 降低其重要性认知，新人可能误以为可以随意修改；
- `utils/` 是 catch-all 目录，与 `chunks.py`/`text.py`/`paths.py` 等纯函数混放；
- AGENTS.md §13 明确指出 SSE 事件契约是核心协议，修改需同步 3 处（前后端 + 文档），
  协议实现层应独立成包。

**设计**：新建 `backend/app/sse/` 包，迁入 `utils/sse_events.py` → `sse/events.py`：

```
backend/app/sse/
├── __init__.py    # re-export events.py 的公共函数
└── events.py      # ← utils/sse_events.py 迁入
```

所有 `from app.utils.sse_events import` 改为 `from app.sse.events import`（或 `from app.sse import`）。

**备选方案**：

- A. **新建 `sse/` 包**（推荐）—— 独立协议层，语义清晰；
- B. **迁入 `api/`** —— SSE 是 API 层契约，但 `api/` 目前只放 REST 路由，混放会模糊职责；
- C. **保留 `utils/`** —— 治标不治本，问题持续存在。

**决策**：方案 A。

**影响面**：全项目 grep `from app.utils.sse_events` 命中约 10+ 处，全部更新 import 路径。
函数签名/行为不变，零运行时影响。

---

## P1 — 目录语义清晰化

### D4. `app/deep/` → `app/deepagent/` + 内部文件重命名

**现状**：`app/deep/` 含 6 文件，包名"deep"语义最模糊：

| 文件 | 实际职责 | 命名问题 |
|---|---|---|
| `agent.py` | `build_deep_agent` / `run_deep_path` | "agent" 过于通用 |
| `execution.py` | 带审批的 agent 执行循环 | "execution" 过于宽泛，与 `agent.py` 边界靠 docstring 区分 |
| `harness.py` | `deepagents.create_deep_agent` 工厂 | "harness" 是框架术语非业务术语 |
| `safe_shell_backend.py` | `SafeLocalShellBackend` | 类名多了 `Local`，与文件名不完全对应 |
| `streaming.py` | astream_events → SSE 转换 | 命名尚可 |
| `tools.py` | DeepAgent 工具组装 + MCP 加载 | 与 `app/tools/` 目录撞名 |

**设计**：目录 + 文件双重重命名：

```
app/deep/                          →  app/deepagent/
├── agent.py                       →  agent.py              # 不变
├── execution.py                   →  approval_runner.py    # 语义化
├── harness.py                     →  factory.py            # 语义化
├── safe_shell_backend.py          →  safe_shell_backend.py # 不变
├── streaming.py                   →  streaming.py          # 不变
└── tools.py                       →  tool_assembly.py      # 避免与 app/tools/ 撞名
```

**重命名理由**：

- `deep` → `deepagent`：包名直接表达"deepagents 框架的执行体"，消除"deep"歧义；
- `execution.py` → `approval_runner.py`：实际职责是"带审批的 agent 执行循环"，`approval_runner` 精确表达；
- `harness.py` → `factory.py`：实际是 `create_deep_agent` 的工厂封装，`factory` 是 Python 惯例；
- `tools.py` → `tool_assembly.py`：避免与 `app/tools/` 目录撞名，`assembly` 表达"组装"语义。

**备选方案**：

- A. **只改目录名 `deep` → `deepagent`，文件名不动**（最小改动）—— 但 `tools.py` 与 `app/tools/` 撞名问题未解决；
- B. **目录 + 文件双重重命名**（推荐）—— 一次性解决所有命名问题；
- C. **合并到 `app/execution/`** —— 但 `execution` 过于通用，且 deepagents 是框架名，应保留在包名中。

**决策**：方案 B。

**影响面**：全项目 grep `from app.deep.` 命中约 15+ 处，全部更新 import 路径。
`deep/__init__.py` 若有 re-export 同步迁移。函数/类签名不变。

### D5. 合并 `security/approval_flow.py` → `security/approval/flow.py`

**现状**：

- `security/approval_flow.py`（文件）— 公共审批执行流辅助函数（`_extract_paths_from_tool_call`/`_make_approval_event`/`_await_approval`/`_handle_directory_extension`）
- `security/approval/`（目录）— 决策类型 + 跨请求状态管理
  - `approval/__init__.py` re-export `state`/`decision` 的公共 API
  - `approval/decision.py` `ApprovalDecision` 枚举 + `ApprovalResult`
  - `approval/state.py` `submit_approval`/`pop_approval`/`set_abort`/`set_pause` 等

文件与目录职责高度重叠：`approval_flow.py` 的审批"流"逻辑与 `approval/` 的审批"状态/决策"
本属同一领域，分两处维护人为割裂。

**设计**：`approval_flow.py` 整文件迁入 `approval/flow.py`：

```
security/
├── approval/
│   ├── __init__.py    # re-export flow + state + decision 的公共 API
│   ├── decision.py    # 不变
│   ├── state.py       # 不变
│   └── flow.py        # ← approval_flow.py 迁入
├── command_filter.py
└── dangerous_tools.py
```

`approval/__init__.py` 增加 re-export `flow.py` 的公共函数（`_extract_paths_from_tool_call` 等
若需被外部调用则去 `_` 前缀，否则保留私有）。

**备选方案**：

- A. **`approval_flow.py` → `approval/flow.py`**（推荐）—— 同领域聚合；
- B. **`approval/` 目录拍平为 `security/approval_*.py` 文件**—— 倒退，破坏已有的状态/决策分离；
- C. **保留现状**—— 文件/目录职责重叠持续存在。

**决策**：方案 A。

**影响面**：全项目 grep `from app.security.approval_flow` 命中约 5+ 处，全部更新为
`from app.security.approval.flow`（或通过 `__init__.py` re-export 用 `from app.security.approval import`）。

### D6. 合并 `observability/langsmith_dual.py` → `langsmith.py` + 统一 trace_id

**现状**：

- `langsmith.py` — `redact`/`trace_span`/`mark_redacted`/`gen_trace_id`（trace_id 生成）+ `_langsmith_available`
- `langsmith_dual.py` — `DualTraceContext` + `dual_trace`（双写 trace：本地 SQLite + LangSmith remote）
- `trace.py` — `new_trace_id`/`current_trace_id`/`bind_trace`（trace_id 基础设施）

**问题**：

1. `langsmith.py::gen_trace_id` 与 `trace.py::new_trace_id` 两套 trace_id 生成逻辑，职责重叠；
2. `langsmith_dual.py` 与 `langsmith.py` 都涉及 LangSmith 追踪，文件分裂人为割裂；
3. `langsmith_dual.py` 仅 1 个公开类 + 1 个公开函数，独立文件过重。

**设计**：

```
observability/
├── trace.py          # trace_id 唯一来源（new_trace_id/current_trace_id/bind_trace）
├── langsmith.py      # ← 合并 langsmith_dual.py：redact/trace_span/mark_redacted/dual_trace/DualTraceContext
├── observation.py    # 不变
├── feedback.py       # 不变
└── logger.py         # 不变
```

具体动作：

1. `langsmith.py::gen_trace_id` 删除，所有调用改用 `trace.py::new_trace_id`；
2. `langsmith_dual.py` 整文件内容并入 `langsmith.py` 末尾（保留 `DualTraceContext` 类与 `dual_trace` 函数签名不变）；
3. `langsmith_dual.py` 文件删除；
4. 所有 `from app.observability.langsmith_dual import` 改为 `from app.observability.langsmith import`；
5. 所有 `from app.observability.langsmith import gen_trace_id` 改为 `from app.observability.trace import new_trace_id`。

**备选方案**：

- A. **合并 dual 进 langsmith + 统一 trace_id**（推荐）—— 消除双源 + 文件分裂；
- B. **只合并文件，保留双 trace_id 函数**—— trace_id 双源问题未解决；
- C. **保留现状**—— 持续维护成本。

**决策**：方案 A。

**影响面**：

- `langsmith_dual.py` 删除（约 100-150 行）；
- `langsmith.py` 删除 `gen_trace_id`（约 5-10 行）+ 新增 dual 部分（约 100-150 行）；
- `trace.py` 不变（已是 trace_id 唯一来源）；
- import 调用处更新约 5+ 处。

---

## P2 — 场景化执行体重构

### D7. `app/agents/` → `app/scenarios/` + 子目录改名

**现状**：`app/agents/` 含 3 子包：

| 子包 | 文件 | 实际职责 |
|---|---|---|
| `supervisor/` | `work_supervisor.py` + `mention.py` | work 场景 Supervisor（全能 agent） |
| `expert/` | `coding.py` | coding 场景 Expert（代码专家） |
| `team/` | `coding_team.py` | coding_team 场景入口（薄封装 `team/run_team_path`） |

**问题**：

1. `agents/`（复数，场景化执行体，带审批）vs `subagents/`（复数，无中断 ReAct 子图）
   需读文档才能区分层级语义；
2. `supervisor/` 目录名与 `work_supervisor.py` 文件名冗余（"supervisor" 出现两次）；
3. `expert/coding.py` 文件名 `coding` 与导出函数 `build_coding_expert`/`run_coding_expert`
   的"Expert"职责名不直接对应；
4. `team/coding_team.py` 文件名与目录名 `team/` 冗余（"team" 出现两次）。

**设计**：目录 + 文件双重重命名，对齐 AGENTS.md §12 的 `agent_mode` 值
（`"work"`/`"coding"`/`"coding_team"`）：

```
app/agents/                          →  app/scenarios/
├── supervisor/                      →  work/
│   ├── __init__.py                  →  __init__.py
│   ├── work_supervisor.py           →  agent.py        # 消除 "supervisor" 冗余
│   └── mention.py                   →  mention.py      # 不变
├── expert/                          →  coding/
│   ├── __init__.py                  →  __init__.py
│   └── coding.py                    →  agent.py        # 消除 "coding" 冗余
└── team/                            →  coding_team/
    ├── __init__.py                  →  __init__.py
    └── coding_team.py               →  agent.py        # 消除 "team" 冗余
```

**重命名理由**：

- `agents/` → `scenarios/`：直接表达"场景化执行体"，与 `subagents/`（子代理）层级区分清晰；
- `supervisor/` → `work/`：对齐 `agent_mode="work"`，子目录名即场景名；
- `expert/` → `coding/`：对齐 `agent_mode="coding"`，子目录名即场景名；
- `team/` → `coding_team/`：对齐 `agent_mode="coding_team"`，子目录名即场景名；
- `work_supervisor.py`/`coding.py`/`coding_team.py` → 统一 `agent.py`：每个场景子包的入口文件
  统一命名，消除"目录名 + 文件名"双重场景名冗余。

**`coding_team/agent.py` 薄封装决策**：

经评估，**保留** `coding_team/agent.py` 薄封装（78 行，直接委托 `team/run_team_path`），
理由：

1. **签名对齐**：`run_coding_team` 与 `run_work_supervisor`/`run_coding_expert` 签名对齐
   （无 state 参数），是场景入口统一形态；
2. **降级策略差异**：`coding_team` 降级时不回退 CHAT 路径，改为建议用户切换 work 模式，
   这是场景级策略，不应耦合到 `team/` 框架层；
3. **source 标识**：`coding_team` 场景的 SSE 事件 `source="coding_team"`，与 `team/` 框架层
   的 `source="team_*"` 区分；
4. **未来扩展**：若新增 `work_team` 场景，`scenarios/work_team/agent.py` 可复用 `team/` 框架
   而不影响 `coding_team`。

但需在 `coding_team/agent.py` docstring 明确："本文件是场景入口薄封装，框架实现见 `app.team.orchestrator`"。

**备选方案**：

- A. **目录 + 文件双重重命名**（推荐）—— 一次性解决命名歧义与冗余；
- B. **只改 `agents/` → `scenarios/`，子目录不动**—— `supervisor/work_supervisor.py` 冗余未解决；
- C. **删除 `coding_team/agent.py` 薄封装，调用方直接调 `team/run_team_path`**—— 破坏场景入口统一形态；
- D. **重命名 `agents/` → `executors/`**—— `executors` 与 `execution` 词根重复，且 `scenarios` 更贴合 AGENTS.md §12 的"场景"术语。

**决策**：方案 A，保留 `coding_team/agent.py` 薄封装。

**影响面**：

- 全项目 grep `from app.agents.` 命中约 8+ 处，全部更新 import 路径；
- `router/graph.py` 的 `run_work_supervisor`/`run_coding_expert`/`run_coding_team` import 更新；
- 函数签名/行为不变。

---

## P3 — 清理

### D8. 合并 `utils/paths.py` 与 `sandbox/path_guard.py`

**现状**：

- [utils/paths.py](file:///d:/java/agentprojects/agentx/backend/app/utils/paths.py) `normalize_path(path)` — 通用路径归一化
- [sandbox/path_guard.py](file:///d:/java/agentprojects/agentx/backend/app/sandbox/path_guard.py) `is_under(path, base)` + `is_critical(path)` + `CRITICAL_DIRS`/`DEFAULT_WHITELIST` 常量 + `_build_critical_dirs`

两者都做路径归一化，`normalize_path` 与 `path_guard` 内部的归一化逻辑边界模糊。

**设计**：`utils/paths.py::normalize_path` 迁入 `sandbox/path_guard.py`：

```python
# sandbox/path_guard.py
def normalize_path(path: str | Path) -> Path:
    """路径归一化：resolve + expanduser + case normalization。"""
    ...

def is_under(path: str | Path, base: str | Path) -> bool: ...
def is_critical(path: str | Path) -> bool: ...
```

`utils/paths.py` 文件删除。所有 `from app.utils.paths import normalize_path` 改为
`from app.sandbox.path_guard import normalize_path`。

**备选方案**：

- A. **`normalize_path` 迁入 `path_guard.py`**（推荐）—— 路径相关函数聚合到 sandbox 包；
- B. **`path_guard` 的归一化逻辑迁入 `utils/paths.py`**—— 但 `is_under`/`is_critical` 强依赖
  `CRITICAL_DIRS`/`DEFAULT_WHITELIST` 常量，迁移会打散 sandbox 包内聚；
- C. **保留双份**—— 持续边界模糊。

**决策**：方案 A。

**影响面**：全项目 grep `from app.utils.paths import` 命中约 3-5 处（`tools/cli.py`、
`workspace/api.py` 等），全部更新 import 路径。

### D9. （已否决）重命名 `api/observation.py` → `api/feedback.py`

**现状**：

- [api/observation.py](file:///d:/java/agentprojects/agentx/backend/app/api/observation.py) — `register_observation_routes` 注册 5 个端点：
  - `POST /api/observation/feedback`
  - `GET /api/observation/feedback`
  - `GET /api/observation/runs`
  - `GET /api/observation/runs/{run_id}`
  - `GET /api/observation/runs/{run_id}/events`
- [observability/observation.py](file:///d:/java/agentprojects/agentx/backend/app/observability/observation.py) — `ObservationSink`/`SqliteObservationSink`/`ObservationCallback`（观测中心实现）

**原计划**：重命名 `api/observation.py` → `api/feedback.py` 避免与 `observability/observation.py` 撞名。

**否决理由**：

1. `api/observation.py` 注册 5 个端点，仅 2 个是 feedback，3 个是 runs 相关；
2. 文件名 `observation` 与路由前缀 `/api/observation/*` 语义对齐，重命名会破坏一致性；
3. 与 `observability/observation.py` 的撞名是 Python 常见的跨包同名模式（如 `models.py`/`schemas.py`/`base.py`），
   包名前缀已区分（`app.api.observation` vs `app.observability.observation`）；
4. 跨包搜索 `observation.py` 命中 2 文件，但 IDE 通常显示完整包路径，混淆风险低。

**决策**：保留现状，不重命名。本提案 P3 仅保留 D8（合并 `utils/paths.py`）。

---

## 备选方案（已否决）

1. **保留 `app/deep/` 不改目录名，仅改文件名**：
   - 否决理由：`deep` 本身语义模糊，仅改文件名治标不治本；
   - 项目硬约束"开发阶段无需考虑灰度兼容，直接推倒重来"支持一次性彻底重构。

2. **`agents/` → `executors/` 而非 `scenarios/`**：
   - 否决理由：`executors` 与 `deepagent/approval_runner.py`（原 `execution.py`）词根重复，
     且 AGENTS.md §12 明确使用"场景"术语（`agent_mode` = 场景标识），`scenarios` 更贴合。

3. **删除 `coding_team/agent.py` 薄封装**：
   - 否决理由：破坏场景入口统一形态（`run_work_supervisor`/`run_coding_expert`/`run_coding_team`
     三者签名对齐），且 `coding_team` 有场景级降级策略与 source 标识，不应耦合到框架层。

4. **合并 `app/config/` 与 `app/workspace/config/`**：
   - 否决理由：两者职责清晰（全局 Settings vs 项目级 `.agentx/` 配置），前缀已区分，
     合并无收益且增加耦合。本提案明确列为非目标。

5. **重命名 `app/router/` → `app/dispatcher/`**：
   - 否决理由：项目内 docstring 已澄清 `router` 是 LangGraph 场景分发器，重命名收益有限，
     且 `router` 在 LangGraph 生态中是常见术语（如 `RouterChain`）。本提案明确列为非目标。

6. **`api/memory.py` → `api/profile.py`**（承载 skills+profile+checkpointer 三类）：
   - 否决理由：`memory.py` 端点路由前缀是 `/api/memory/*`，改文件名可能误导调用方期待
     改路由前缀；且 `memory` 在 AGENTS.md §13 SSE 契约中作为 `source` 取值出现，
     改名会引入语义混淆。本提案明确列为非目标。

---

## 影响面汇总

| P 级 | 文件数（变更/新建/删除） | 预计行数变化 | 风险 | 测试影响 |
|---|---|---|---|---|
| P0 | 变更 ~5 + 新建 2 + 删除 1 | ±0（纯重命名/提取） | 低 | 无 |
| P1 | 变更 ~20 + 新建 0 + 删除 3 | ±0（纯重命名/合并） | 中 | import 路径更新 |
| P2 | 变更 ~10 + 新建 0 + 删除 0 | ±0（纯重命名） | 中 | import 路径更新 |
| P3 | 变更 ~5 + 新建 0 + 删除 2 | ±0（纯合并/重命名） | 低 | import 路径更新 |
| **合计** | ~40 文件 | **±0** | — | 无行为变化，仅 import 路径 |

**关键性质**：本提案是**纯命名/结构重构**，零行为变化，零 API 契约变化，
零前端影响。所有变更为文件移动 + import 路径更新 + 常量重命名。
