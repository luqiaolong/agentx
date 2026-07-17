# Design: Coding 场景按需规划与子代理委派

## 架构概览

```
Router (agent_mode=coding)
    ↓
run_coding_expert
    ↓
[新增] ComplexityClassifier.classify(message, history_count)
    ↓
    ├── 简单 → 当前路径（默认 TodoListMiddleware，LLM 自决是否用 todo）
    └── 复杂 → 增强 DeepAgent 构建：
                ├── HarnessProfile.excluded_middleware={"TodoListMiddleware"}
                ├── middleware=[AggressiveTodoMiddleware]（强制 write_todos）
                ├── subagents 扩展：rag/web/custom + 团队角色
                └── LLM 自主调用 task 工具并行委派（LangGraph 原生并行）
```

## 关键决策

### D1: 为何不新建 TodoListMiddleware 子类

`TodoListMiddleware` 的 `wrap_model_call` 在每次模型调用时注入 `WRITE_TODOS_SYSTEM_PROMPT`。
子类化需重写 `wrap_model_call`，维护成本高且依赖 langchain 内部实现。

**选择**：构造时传入自定义 `system_prompt` 和 `tool_description`。`TodoListMiddleware.__init__`
原生支持这两个 kwargs（见 `langchain/agents/middleware/todo.py:203-218`）。

### D2: 为何通过 excluded_middleware 排除默认实例

`create_deep_agent` 在 `graph.py:774` 硬编码 `TodoListMiddleware()`（默认 prompt）。
无法通过参数自定义。唯一注入自定义实例的方式：

1. `HarnessProfile(excluded_middleware=frozenset({"TodoListMiddleware"}))` 排除默认
2. `create_deep_agent(middleware=[my_todo_mw])` 注入自定义

`TodoListMiddleware` 不在 `_REQUIRED_MIDDLEWARE`（仅 `FilesystemMiddleware` + `SubAgentMiddleware`
受保护），可安全排除。用户 middleware 在 position 8 注入（晚于默认 position 1），但功能正常。

### D3: 为何不使用 PlanMiddleware / 自定义 plan 工具

deepagents 库**无 PlanMiddleware**。`write_todos` 即原生规划机制，`Todo` schema 为
`{content, status}`（无 depends_on / DAG）。自建 plan 工具会：

- 重复造轮子（write_todos 已够用）
- 前端 `todo_update` SSE 事件已绑定 `state.todos`，自定义需新增事件类型
- LLM 已被训练使用 `write_todos`，自定义工具需额外 prompt 引导

**选择**：复用 `write_todos`，仅改 prompt 让 LLM 更积极地调用。

### D4: 团队角色作为 inline SubAgent 而非 CompiledSubAgent

deepagents 支持两种子代理声明：

- `SubAgent`（TypedDict）：inline，由 `SubAgentMiddleware` 自动编译为子图
- `CompiledSubAgent`：预编译的 Runnable

**选择 inline SubAgent**：

- coding 模式当前已用 inline（`_build_subagents` 返回 `list[SubAgent]`）
- 团队角色复用 `settings.team_subagents` 配置，无需预编译
- inline 子代理自动获得 `TodoListMiddleware`（子代理有自己的 todo list）

### D5: ComplexityClassifier 缓存策略

与 `DangerousTaskClassifier` 一致：

- **key**：`SHA256(message)[:16]`
- **存储**：实例级 dict（非持久化）
- **生命周期**：随 `ComplexityClassifier` 实例，每次 `run_coding_expert` 新建实例
- **效果**：同一次 `run_coding_expert` 内不会重复调用 LLM（但跨请求不缓存）

**不持久化缓存的理由**：复杂度判断依赖 `history_count`，跨请求 history 变化后判断可能不同。

### D6: 启发式降级信号选择

| 信号 | 阈值 | 理由 |
|---|---|---|
| 消息长度 | > 200 字 | 复杂任务通常描述详细 |
| 多领域关键词共现 | 2+ 组 | 前端+后端、重构+测试等明显需多角色 |
| 多步骤信号 | 含特定模式 | "首先...然后...最后"等显式步骤 |
| 历史长度 | > 10 条 | 长对话通常是复杂任务的延续 |

阈值均可配置（`settings.coding_complexity_*`），适应不同项目。

## 数据流

### 复杂任务完整流程

```
1. 用户消息 "重构 auth 模块，前端加登录页，后端加 JWT，写测试"
2. run_coding_expert 入口
3. ComplexityClassifier.classify(message, history_count=15)
   → LLM 判断 is_complex=True, suggested_subagents=["frontend_dev", "backend_dev", "tester"]
4. build_coding_expert(force_todo=True, include_team_roles=True)
   → create_agent(force_todo=True, extra_subagents=[团队角色...])
   → HarnessProfile(excluded_middleware={"TodoListMiddleware"})
   → middleware=[AggressiveTodoMiddleware, ReadonlyLoopGuardMiddleware, ...]
   → subagents=[rag, web, frontend_dev, backend_dev, tester, ...]
5. LLM 调用 write_todos → todo_update SSE → 前端展示步骤
6. LLM 调用 task(subagent_type="frontend_dev", description="...") × 3（并行）
   → delegation SSE × 3 → 前端展示委派卡片
7. 子代理执行完毕 → 结果回传 → LLM 汇总
8. 最终回答 → token SSE → 前端
```

### 简单任务流程（零变化）

```
1. 用户消息 "hi"
2. run_coding_expert 入口
3. ComplexityClassifier.classify("hi", 0) → is_complex=False
4. build_coding_expert()（当前路径，force_todo=False, include_team_roles=False）
5. 当前行为不变
```

## 组件设计

### ComplexityClassifier

```python
# backend/app/team/complexity_classifier.py

class ComplexityResult(BaseModel):
    is_complex: bool
    reason: str
    suggested_subagents: list[str] = Field(default_factory=list)

class ComplexityClassifier:
    def __init__(self, chat_model: Any | None = None):
        self._chat_model = chat_model
        self._structured = None
        if chat_model is not None:
            try:
                self._structured = make_structured_llm(chat_model, ComplexityResult)
            except Exception:
                self._structured = None
        self._cache: dict[str, ComplexityResult] = {}

    async def classify(self, message: str, history_count: int = 0) -> ComplexityResult:
        cache_key = self._cache_key(message, history_count)
        if cached := self._cache.get(cache_key):
            return cached
        if self._structured is not None:
            try:
                result = await self._structured.ainvoke(self._build_prompt(message, history_count))
                if isinstance(result, ComplexityResult):
                    self._cache[cache_key] = result
                    return result
            except Exception:
                logger.warning("ComplexityClassifier LLM failed, fallback to heuristic")
        result = self._heuristic_fallback(message, history_count)
        self._cache[cache_key] = result
        return result
```

### AggressiveTodoMiddleware

```python
# backend/app/deepagent/factory.py

_AGGRESSIVE_TODO_SYSTEM_PROMPT = """## 任务规划（强制）

你必须先调用 `write_todos` 工具拆解任务为步骤清单，再开始执行。
执行过程中及时更新每个 todo 的状态（pending → in_progress → completed）。
每完成一步立即标记 completed，不要批量更新。

## 子代理委派

对于可委派的子任务，通过 `task` 工具并行调用子代理：
- 一个 AIMessage 里可以放多个 task tool_calls 实现并行
- 子代理类型见 task 工具描述的 Available agent types 列表
- 独立子任务优先并行，有依赖的串行
- 子代理返回后，汇总结果并更新 todo 状态
"""

_AGGRESSIVE_TODO_TOOL_DESCRIPTION = """创建或更新任务清单。复杂任务必须先调用此工具拆解步骤。

参数 todos 为完整清单（覆盖式更新），每项含 content 和 status：
- content: 步骤描述
- status: pending / in_progress / completed

首次调用时所有 status 应为 pending。执行中动态更新。
"""

def _build_aggressive_todo_middleware() -> TodoListMiddleware:
    return TodoListMiddleware(
        system_prompt=_AGGRESSIVE_TODO_SYSTEM_PROMPT,
        tool_description=_AGGRESSIVE_TODO_TOOL_DESCRIPTION,
    )
```

### create_agent 改造

```python
# backend/app/deepagent/factory.py

def create_agent(
    model, tools, system_prompt, ...,
    force_todo: bool = False,  # 新增
) -> Any:
    middleware = []
    # ... 现有 middleware 装配 ...

    if force_todo:
        # 排除默认 TodoListMiddleware，注入自定义
        excluded_middleware = frozenset({"TodoListMiddleware"})
        middleware.append(_build_aggressive_todo_middleware())
    else:
        excluded_middleware = frozenset()

    # HarnessProfile 注册
    ensure_harness_profile(excluded_tools, excluded_middleware=excluded_middleware)

    return create_deep_agent(
        ...,
        middleware=middleware,
    )
```

### _build_subagents 团队角色注入

```python
# backend/app/scenarios/coding/agent.py

def _build_subagents(
    thread_id: str,
    workspace_path: str | None = None,
    include_team_roles: bool = False,  # 新增
) -> list[SubAgent]:
    subagents = []
    # 现有：rag/web/custom（不变）
    subagents.extend(_build_builtin_and_custom_subagents(...))

    if include_team_roles:
        subagents.extend(_build_team_role_subagents(thread_id, workspace_path))

    return subagents

def _build_team_role_subagents(thread_id, workspace_path) -> list[SubAgent]:
    """从 settings.team_subagents 构造团队角色 SubAgent 声明。"""
    settings = get_settings()
    if not settings.coding_complexity_team_roles_enabled:
        return []
    subagents = []
    for key, cfg in settings.team_subagents.items():
        if not cfg.enabled:
            continue
        tools = _make_tools_for_team_role(cfg.tools, thread_id, workspace_path)
        subagents.append(SubAgent(
            name=key,
            description=cfg.trigger_description or key,
            system_prompt=cfg.system_prompt + THINK_PROMPT_SUFFIX,
            tools=tools,
        ))
    return subagents
```

## 错误处理

| 失败点 | 处理 |
|---|---|
| ComplexityClassifier LLM 超时 | 降级到启发式 |
| ComplexityClassifier 整体异常 | warning 日志 + 走简单路径 |
| AggressiveTodoMiddleware 构造异常 | 降级到默认 TodoListMiddleware |
| 团队角色子代理构造失败 | 跳过该角色 + warning，不阻塞 |
| LLM 仍不调用 write_todos | 等价当前行为（无 todo 但任务正常执行） |

## 测试策略

### 单元测试

1. `test_complexity_classifier.py`：
   - LLM 路径（mock LLM 返回 is_complex=True/False）
   - 启发式各信号（长度/多领域/多步骤/历史长度）
   - 缓存命中
   - LLM 异常降级

2. `test_factory_force_todo.py`：
   - `force_todo=True` → agent 含 AggressiveTodoMiddleware
   - `force_todo=False` → agent 含默认 TodoListMiddleware
   - prompt 内容断言（含"强制"文本，不含"劝退"文本）

3. `test_coding_subagents_team_roles.py`：
   - `include_team_roles=True` → 含团队角色
   - `include_team_roles=False` → 不含团队角色
   - `coding_complexity_team_roles_enabled=False` → 不含团队角色

### 集成测试

4. `test_run_coding_expert_complex.py`：
   - mock ComplexityClassifier 返回 is_complex=True → build_coding_expert 收到 force_todo=True
   - mock 返回 is_complex=False → build_coding_expert 收到 force_todo=False
   - `coding_complexity_enabled=False` → 跳过分类
   - 分类异常 → 降级到简单路径

## 风险与缓解

| 风险 | 级别 | 缓解 |
|---|---|---|
| LLM 仍不调用 write_todos | MEDIUM | prompt 强化引导；非代码强制，可接受 |
| 团队角色增加 token 消耗 | LOW | 仅复杂任务注入；子代理上下文隔离 |
| ComplexityClassifier 延迟 | LOW | 缓存 + 启发式降级；~100ms 可接受 |
| excluded_middleware 兼容性 | LOW | deepagents 官方支持；有单测覆盖 |
