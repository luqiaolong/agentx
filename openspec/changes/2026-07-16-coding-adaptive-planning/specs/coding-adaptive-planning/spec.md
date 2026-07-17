# Spec: Coding 场景按需规划与子代理委派

## Purpose

在 `agent_mode=coding` 场景入口增加复杂度分类层，复杂任务自动启用 deepagents 原生
`write_todos` 强制规划 + 团队角色子代理委派，简单任务保持当前直执行路径不变。

## Requirements

### REQ-CP-1: ComplexityClassifier 必须支持缓存 → LLM → 启发式降级

`ComplexityClassifier` 类（新文件 `backend/app/team/complexity_classifier.py`）必须按
**缓存 → LLM 结构化输出 → 启发式降级** 顺序尝试，与 `DangerousTaskClassifier` 一致。

- **缓存 key**：消息内容 SHA256 前 16 字符
- **LLM 路径**：`make_structured_llm(chat_model, ComplexityResult)` 结构化输出
- **启发式降级**：LLM 不可用 / 超时 / 返回无效时自动降级
- **构造函数**：`__init__(self, chat_model: Any | None = None)`
- **主入口**：`async def classify(self, message: str, history_count: int = 0) -> ComplexityResult`

#### Scenario

- **Given** `chat_model=None`（LLM 不可用）
- **When** 调用 `classify("重构 backend/app/team 整个模块", history_count=15)`
- **Then** 走启发式降级路径，返回 `ComplexityResult(is_complex=True, reason="匹配多领域/多步骤信号", suggested_subagents=[...])`

#### Scenario（缓存命中）

- **Given** 同一 `message` 第二次调用
- **When** `classify(message, history_count=5)`
- **Then** 直接返回缓存的 `ComplexityResult`，不调用 LLM

### REQ-CP-2: ComplexityResult 结构固定

`ComplexityResult`（pydantic BaseModel）必须含以下字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `is_complex` | `bool` | ✓ | 是否为复杂任务 |
| `reason` | `str` | ✓ | 判断理由（≤50 字中文） |
| `suggested_subagents` | `list[str]` | ✓ | 建议启用的子代理类型列表（如 `["frontend_dev", "backend_dev"]`） |

#### Scenario

- **Given** LLM 判断任务涉及前端 + 后端
- **When** 返回结果
- **Then** `is_complex=True`, `reason` 含"多领域", `suggested_subagents` 含 `"frontend_dev"` 和 `"backend_dev"`

### REQ-CP-3: 启发式降级信号定义

启发式降级路径必须检测以下信号，**任一命中即 `is_complex=True`**：

1. **消息长度**：`len(message) > settings.coding_complexity_message_length`（默认 200）
2. **多领域关键词共现**：同时出现 2+ 个领域关键词组。关键词组定义（任一组内任一关键词命中即该组命中）：
   - 前端组：`["前端", "frontend", "react", "vue", "angular", "css", "html", "UI 组件"]`
   - 后端组：`["后端", "backend", "api", "数据库", "database", "microservice", "微服务"]`
   - 测试组：`["测试", "test", "pytest", "jest", "coverage", "覆盖率", "e2e"]`
   - 重构组：`["重构", "refactor", "重写", "优化结构"]`
   - 架构组：`["架构", "architecture", "设计模式", "ddd", "分层"]`
   - DevOps 组：`["部署", "deploy", "ci/cd", "docker", "k8s", "监控"]`
3. **明确多步骤信号**：含以下任一模式：
   - `"首先"` 且 `"然后"` 共现
   - `"第一步"` / `"第1步"` / `"step 1"`（大小写不敏感）
   - `"多个文件"` / `"分步骤"` / `"逐步"`
4. **历史长度**：`history_count > settings.coding_complexity_history_count`（默认 10）

#### Scenario

- **Given** `message = "首先重构 auth 模块，然后更新测试，最后修改文档"`（含多步骤信号）
- **When** 启发式降级
- **Then** `is_complex=True`, `reason` 含"多步骤信号"

#### Scenario（简单消息）

- **Given** `message = "hi"`（长度 2，无信号，history_count=0）
- **When** 启发式降级
- **Then** `is_complex=False`, `reason="无复杂信号"`

### REQ-CP-4: AggressiveTodoMiddleware 必须替换默认 TodoListMiddleware

当 `force_todo=True` 时，`create_agent` 必须：

1. 通过 `HarnessProfile.excluded_middleware=frozenset({"TodoListMiddleware"})` 排除默认实例
2. 通过 `middleware=[_build_aggressive_todo_middleware()]` 注入自定义实例
3. 自定义实例使用 `_AGGRESSIVE_TODO_SYSTEM_PROMPT`（强制 write_todos）和 `_AGGRESSIVE_TODO_TOOL_DESCRIPTION`

`_AGGRESSIVE_TODO_SYSTEM_PROMPT` 必须包含：
- 强制要求复杂任务先调用 `write_todos` 拆解步骤
- 指导通过 `task` 工具并行委派子代理（一个 AIMessage 多个 tool_calls）
- 不含"简单任务不要用"的劝退文本

#### Scenario

- **Given** `force_todo=True`
- **When** `create_agent(...)` 构建完成
- **Then** agent 的 middleware 栈含 AggressiveTodoMiddleware 实例，不含默认 TodoListMiddleware 实例

#### Scenario（force_todo=False）

- **Given** `force_todo=False`（默认）
- **When** `create_agent(...)` 构建完成
- **Then** agent 的 middleware 栈含默认 TodoListMiddleware 实例（当前行为不变）

### REQ-CP-5: coding 模式 _build_subagents + build_coding_expert 支持团队角色注入

`_build_subagents` 必须新增 `include_team_roles: bool = False` 参数：

- `include_team_roles=False`（默认）：返回 rag/web/custom（当前行为不变）
- `include_team_roles=True`：额外追加团队角色 SubAgent 声明

`build_coding_expert` 必须新增 `include_team_roles: bool = False` + `force_todo: bool = False`
参数，并透传到 `create_agent(force_todo=...)` 与 `_build_subagents(include_team_roles=...)`。

团队角色来源：`settings.team_subagents` 中 `enabled=True` 的角色。每个角色转换为 deepagents
`SubAgent` 声明，`name` 为角色键名，`description` 为 `trigger_description`，`system_prompt`
为角色配置的 `system_prompt`，`tools` 为角色配置的 `tools`（经 `make_deep_tools` 构造）。

#### Scenario

- **Given** `include_team_roles=True` 且 `settings.team_subagents` 含 `frontend_dev`（enabled=True）
- **When** `_build_subagents(thread_id, workspace_path, include_team_roles=True)`
- **Then** 返回列表含 `SubAgent(name="frontend_dev", description=..., system_prompt=..., tools=[...])`

### REQ-CP-6: run_coding_expert 入口必须执行复杂度分类

`run_coding_expert` 在构建 agent 前必须：

1. 检查 `settings.coding_complexity_enabled`，False 时走当前路径（跳过分类）
2. True 时构造 `ComplexityClassifier`（复用 `chat_model`）
3. 调用 `classifier.classify(message, history_count)`
4. `result.is_complex=True` → 构建 agent 时 `force_todo=True` + `include_team_roles=True`
5. `result.is_complex=False` → 当前路径不变
6. 分类异常 → warning 日志 + 降级到简单路径

#### Scenario

- **Given** `coding_complexity_enabled=True` 且分类返回 `is_complex=True`
- **When** `run_coding_expert` 执行
- **Then** `build_coding_expert(force_todo=True, include_team_roles=True)` 被调用

#### Scenario（特性关闭）

- **Given** `coding_complexity_enabled=False`
- **When** `run_coding_expert` 执行
- **Then** 跳过分类，走当前路径（`force_todo=False, include_team_roles=False`）

#### Scenario（分类异常）

- **Given** `ComplexityClassifier.classify` 抛异常
- **When** `run_coding_expert` 执行
- **Then** 记录 warning 日志，降级到简单路径（不阻塞执行）

### REQ-CP-7: ensure_harness_profile 支持 excluded_middleware

`ensure_harness_profile` 必须新增 `excluded_middleware: frozenset[str] | None = None` 参数：

- `excluded_middleware=None` 或空（默认）：当前行为不变
- `excluded_middleware=frozenset({"TodoListMiddleware"})`：注册的 `HarnessProfile` 含 `excluded_middleware` 字段

**profile key 必须包含 `excluded_middleware`**，避免不同 `excluded_middleware` 配置复用同一 profile
导致 middleware 未被排除。key 生成方式：`excluded_tools` hash + `excluded_middleware` hash 组合。

#### Scenario

- **Given** `excluded_middleware=frozenset({"TodoListMiddleware"})`
- **When** `ensure_harness_profile(excluded_tools=None, excluded_middleware=excluded_middleware)`
- **Then** 注册的 `HarnessProfile.excluded_middleware` 含 `"TodoListMiddleware"`，profile key 与默认 profile 不同

### REQ-CP-8: 配置开关

`settings.py` 必须新增以下字段，均带默认值：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `coding_complexity_enabled` | `bool` | `True` | 总开关 |
| `coding_complexity_message_length` | `int` | `200` | 启发式消息长度阈值 |
| `coding_complexity_history_count` | `int` | `10` | 启发式历史长度阈值 |
| `coding_complexity_team_roles_enabled` | `bool` | `True` | 团队角色注入开关 |

#### Scenario

- **Given** 未配置任何 `coding_complexity_*` 环境变量
- **When** `get_settings()` 加载
- **Then** 4 个字段均为默认值

### REQ-CP-9: 简单任务路径零回归

当 `is_complex=False` 或 `coding_complexity_enabled=False` 时，coding 模式的行为必须与
变更前完全一致：

- agent 构建参数不变（`force_todo=False, include_team_roles=False`）
- middleware 栈不变（默认 TodoListMiddleware）
- 子代理列表不变（rag/web/custom）
- SSE 事件流不变

#### Scenario

- **Given** `message = "hello"`（简单消息）
- **When** `run_coding_expert` 执行
- **Then** 构建的 agent 与变更前版本行为一致（相同 middleware / tools / subagents）
