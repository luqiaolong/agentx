# Design: 后端代码质量优化

> 配套 proposal.md，详述技术决策、备选方案与影响面。按 P0→P4 优先级组织。

---

## P0 — 死代码与契约违规（零风险删除）

### D1. 删除 `deep/recovery.py` 整文件

**现状**：`_to_serializable`（L23-37）+ `_collect_unpaired_tool_call_ids`（L40-67）零调用（backend + tests 全 grep 无 `from app.deep.recovery import`）。模块 docstring 自认"保留以防万一"。

**设计**：直接删除整文件。`_collect_unpaired_tool_call_ids` 功能已由 `deepagents.PatchToolCallsMiddleware` 接管（docstring 自认）。`_to_serializable` 若未来需要，LangChain 提供 `BaseMessage.model_dump()`。

**影响面**：无（零调用）。删除后 `deep/__init__.py` 若有 re-export 需同步清理。

### D2. 删除 `resumed` SSE 事件（§18 红线）

**现状**：[execution.py:159](file:///d:/java/agentprojects/agentx/backend/app/deep/execution.py#L159) + [approval_flow.py:538](file:///d:/java/agentprojects/agentx/backend/app/security/approval_flow.py#L538) `yield make_sse_event("resumed", {})`。§13 契约只列 `paused`，前端 `useChatStream.ts` + `lib/api/chat.ts` 零匹配。

**备选方案**：
- A. 删除 `resumed` 事件（推荐）—— 前端无需感知恢复，下一条 `token` 事件即表示流恢复。
- B. 补全 §13 + 前端处理 —— 增加三处同步成本，无明确业务价值。

**决策**：方案 A。前端依赖 `done`/`error`/`token` 判断状态，`resumed` 是冗余信号。

### D3. 删除 `deep/agent.py` 死导入

**现状**：L17-38 有 6 个 `as _xxx` 别名导入（`_get_pending_tool_calls`/`_is_interrupted`/`_stream_agent_events`/`_await_approval`/`_handle_directory_extension`/`_make_approval_event`），全部未在 agent.py 中引用（实际仅 `run_agent_with_approval` 被使用）。

**设计**：删除 L19-20, 23, 34-38。保留 L18 `run_agent_with_approval`。

### D4. 删除 `subagents/base.py` 死代码

**现状**：
- L47-49 `_make_fs_tools = None` / `_make_rag_tools = None` / `_make_web_tools = None` 无意义的占位赋值（Python 无需前向声明）。
- L99-124 `make_cli_tools` 定义 + L25 `__all__` 导出 + L435 `_make_cli_tools = make_cli_tools` 别名 —— 零调用（`deep/tools.py:79` 明确 CLI 由 `SafeLocalShellBackend` 提供）。

**设计**：删除 L47-49 占位、L99-124 函数定义、L25 导出、L435 别名。

---

## P1 — Bug 修复

### B1. 修复 `milvus_client._on_skip` 闭包 bug

**现状**：[L332-345](file:///d:/java/agentprojects/agentx/backend/app/vectorstore/milvus_client.py#L332)
```python
def _on_skip(text):
    for i, t in enumerate(texts):
        if t is text or t == text:
            break
    meta = metadatas[i] if i < len(metadatas) else {}  # 循环外，i 可能未绑定/残留
```
- 若无匹配：`i` 未绑定（首次调用）或残留上次值（闭包复用）→ `NameError` 或错误元数据。
- `or t == text` 破坏"按对象身份匹配"的初衷（重复文本会误匹配）。

**设计**：
```python
def _on_skip(text):
    meta = {}
    for i, t in enumerate(texts):
        if t is text:  # 仅身份匹配
            meta = metadatas[i] if i < len(metadatas) else {}
            break
    # 使用 meta
```

### B2. 修复 `mcp/client.py` tool_count + 子进程泄漏

**现状**：
- L168-184 `list_servers` 在 per-server 循环内 `tool_count = sum(1 for t in self._tools)` 用全局工具列表 → 每个 server 报总数。
- L225-238 `_close_locked` 设 `self._client = None` 不关闭 stdio 子进程 → 泄漏。

**设计**：
- tool_count：缓存 per-server 工具列表 `self._server_tools: dict[str, list[BaseTool]]`，初始化时按 server 分组。
- 子进程关闭：`_close_locked` 遍历调用各 server 的 close hook（参照 `test_server` 的 `client.session(name)` 模式）。

### B3. 修复 `asyncio.run()` 在 LangChain 适配器同步方法中崩溃

**现状**：[tei_client.py:225,231](file:///d:/java/agentprojects/agentx/backend/app/embedding/tei_client.py#L225) + [milvus_client.py:685,707](file:///d:/java/agentprojects/agentx/backend/app/vectorstore/milvus_client.py#L685) 同步方法调 `asyncio.run(self.aembed_documents(texts))`，在 FastAPI async 上下文抛 `RuntimeError: cannot be called from a running event loop`。

**备选方案**：
- A. 检测运行中的循环，有则用 `asyncio.run_coroutine_threadsafe(coro, loop)` + 阻塞等待 —— 复杂，可能死锁。
- B. 同步方法改用 `loop.run_until_complete()` 仅当无运行循环时 —— 仍会在 async 上下文失败，但错误明确。
- C. **直接调用底层同步实现**（推荐）—— TEI/Milvus 底层客户端本就支持同步调用，绕过 async 包装。

**决策**：方案 C。`_do_embed` 改为同步 `httpx.post`；`milvus_client` 的 `add_texts`/`similarity_search` 同步分支直接调底层 `pymilvus`（已在 `asyncio.to_thread` 包装外）。

### B4. `sandbox/store.py` 同步 sqlite3 包 `asyncio.to_thread`

**现状**：[sandbox/store.py](file:///d:/java/agentprojects/agentx/backend/app/sandbox/store.py) 6 个方法同步 `sqlite3.connect` 在 `asyncio.Lock` 内调用，阻塞事件循环。与 `milvus_client.py`（用 `asyncio.to_thread`）不一致。

**设计**：
- 方案 A：`SessionSandbox` 调用处包 `await asyncio.to_thread(store.method, ...)`。
- 方案 B：`SandboxStore` 方法改 async + 内部 `to_thread`。

**决策**：方案 B（封装在 store 内，调用方无感知）。每个方法签名改 `async def`，内部 `await asyncio.to_thread(self._sync_method, ...)`。

---

## P1 — 重复消除

### R1. 合并 `rag_agent.py` + `web_agent.py`

**现状**：两文件各 79 行，95% 结构相同，仅 3 token 差异（config key / tool factory / source label）。

**设计**：在 `base.py` 新增：
```python
def build_builtin_subagent(name: str, thread_id: str, checkpointer=None):
    """name ∈ {"rag", "web"}"""
    cfg = get_settings().subagents[name]
    model = get_chat_model(temperature=cfg.temperature, streaming=True)
    factories = {"rag": make_rag_tools, "web": make_web_tools}
    tools = factories[name](thread_id)
    return create_agent(model, tools, system_prompt=cfg.system_prompt + THINK_PROMPT_SUFFIX,
                        checkpointer=checkpointer, name=f"{name}_agent")

async def run_builtin_subagent(name, thread_id, message, history=None, checkpointer=None):
    agent = build_builtin_subagent(name, thread_id, checkpointer)
    inputs = {"messages": [*history, {"role": "user", "content": message}]}
    config = {"configurable": {"thread_id": thread_id}}
    async for e in run_subagent_stream(agent, inputs, source=name, config=config):
        yield e
```
`rag_agent.py`/`web_agent.py` 保留为薄 re-export（向后兼容调用方）或直接删除（调用方改用 `run_builtin_subagent("rag", ...)`）。

### R2. 删除 `custom_agent.py` 重复工具定义

**现状**：[custom_agent.py:57-128](file:///d:/java/agentprojects/agentx/backend/app/subagents/custom_agent.py#L57) 逐字重复 base.py 的 5 个 `@tool`（read_file/list_dir/glob_files/grep_files/rag_retrieve/web_search）。

**设计**：`_make_custom_tools` 改为：
```python
def _make_custom_tools(cfg, thread_id, workspace_path):
    safe_names = set(cfg.tools)
    tools = []
    if safe_names & _FS_TOOL_NAMES:
        tools.extend(t for t in make_fs_tools(thread_id, workspace_path) if t.name in safe_names)
    if safe_names & _RAG_TOOL_NAMES:
        tools.extend(t for t in make_rag_tools(thread_id) if t.name in safe_names)
    if safe_names & _WEB_TOOL_NAMES:
        tools.extend(t for t in make_web_tools(thread_id) if t.name in safe_names)
    return tools
```
函数从 106 行降至 ~25 行。

### R3. 合并审批循环

**现状**：`execution.run_agent_with_approval`（246 行，生产）+ `approval_flow.run_approval_loop`（315 行，仅测试用，10 处调用）逻辑重复。

**设计**：
1. 删除 `approval_flow.run_approval_loop` 及其私有助手 `_inject_tool_error_messages_default`。
2. 迁移 `tests/python/unit/test_security_approval_flow.py` 10 处调用 → 改用 `run_agent_with_approval`。
3. 测试中 patch `app.deep.execution._is_interrupted` 而非本地副本。
4. [coding.py:183-192](file:///d:/java/agentprojects/agentx/backend/app/agents/expert/coding.py#L183) 的 `_is_interrupted` 本地副本删除，测试改 patch execution 模块。

---

## P2 — Team 路径迁移到 LangGraph（最大收益）

### T1. `run_team_path` 重写为 LangGraph `StateGraph`

**现状**：[orchestrator.py:83-310](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L83) 227 行手写状态机：拆任务 → 并行执行 → 汇总。

**设计**：
```python
from langgraph.graph import StateGraph, END

class TeamState(TypedDict):
    messages: list
    plan: list[TeamPlanTask]
    findings: Annotated[dict[str, str], merge_findings]  # reducer
    errors: Annotated[dict[str, str], merge_errors]
    workspace_path: str
    thread_id: str
    parent_thread_id: str

def build_team_graph():
    g = StateGraph(TeamState)
    g.add_node("plan", plan_node)        # 调 with_structured_output 生成 TeamPlan
    g.add_node("execute", execute_node)  # 用 Send fanout 到 per-subtask 节点
    g.add_node("aggregate", aggregate_node)
    g.add_edge("plan", "execute")
    g.add_conditional_edges("execute", route_after_execute)
    g.add_edge("aggregate", END)
    return g.compile(checkpointer=...)
```

### T2. `scheduler._run_subtask` 拆为 LangGraph 节点

**现状**：248 行 7 分支 if/elif（deep/code/rag/web/team-roles/custom/default）。

**设计**：每分支一个节点函数，用 `add_conditional_edges` 按 `task.agent` 分发：
```python
def route_subtask(state) -> str:
    agent = state["current_task"]["agent"]
    if agent == "deep": return "deep_node"
    if agent == "code": return "code_node"
    if agent in ("rag", "web"): return "builtin_node"
    if agent in TEAM_ROLES: return "team_role_node"
    if agent.startswith("custom-"): return "custom_node"
    return "default_node"

g.add_conditional_edges("dispatch", route_subtask, {...})
```
事件路由块（当前 3 处重复）提取为 `_route_subtask_events(event_stream, agent_name)` 公共助手。

### T3. `Blackboard` 转 `TeamState(TypedDict)`

**现状**：dataclass + 手写 dict 字段。

**设计**：
```python
def merge_findings(left: dict, right: dict) -> dict:
    return {**left, **right}
merge_errors = merge_findings

class TeamState(TypedDict):
    findings: Annotated[dict[str, str], merge_findings]
    errors: Annotated[dict[str, str], merge_errors]
    # ...
```
并行子任务节点写 `findings`/`errors` 时 LangGraph 自动合并，消除 orchestrator.py 的 `asyncio.Queue` + `Semaphore` 手写并发。

### T4. 删除 monkeypatch 兼容导入

**现状**：[orchestrator.py:32-41](file:///d:/java/agentprojects/agentx/backend/app/team/orchestrator.py#L32) 5 个 `# noqa: F401` 导入仅为测试 monkeypatch。

**设计**：测试改用依赖注入或 patch 源模块。`run_team_path` 接受可选 `subtask_runners: dict[str, Callable]` 参数，测试注入 mock。

---

## P3 — 框架合规

### F1. `trim_messages` 替代手写历史截断

**现状**：[graph.py:291-293](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py#L291) `if len(history) > max_msgs: history = history[-max_msgs:]`

**设计**：
```python
from langchain_core.messages import trim_messages
history = trim_messages(
    history, token_counter=model, max_tokens=settings.context_max_tokens,
    strategy="last", allow_partial=False
)
```

### F2. `_append_messages_to_checkpointer` 改用正确 API

**现状**：[graph.py:437-521](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py#L437) 手搓 StateGraph 写 checkpoint（R18）。

**设计**：
1. 调研 deepagents graph 是否已自动写 checkpoint（若已写则删除本函数）。
2. 若需手动写，用 `BaseCheckpointSaver.aput(config, checkpoint, metadata, new_versions)` + 正确 `checkpoint_ns=""`。
3. 删除 sync 分支的 `ThreadPoolExecutor` + `_aio.run` hack。

### F3. `rag_retrieve.py` 改用 LangChain 适配器

**现状**：[tools/rag_retrieve.py](file:///d/java/agentprojects/agentx/backend/app/tools/rag_retrieve.py) 直连 TEI/Milvus 客户端。

**设计**：改用 `LangChainTeiEmbeddings`（embedding）+ `LangChainMilvusVectorStore`（retriever），通过 `as_retriever()` 接入。

### F4. 接入 `RubricMiddleware` 到生产

**现状**：`RubricMiddleware` 仅 eval 用，生产 `create_agent` 未接入。

**设计**：
```python
def create_agent(model, tools, system_prompt, rubric=None, **kwargs):
    middleware = [SummarizationMiddleware(...), PatchToolCallsMiddleware(...)]
    if rubric:
        middleware.append(RubricMiddleware(rubric=rubric, grader_llm=model))
    return deepagents.create_deep_agent(
        model=model, tools=tools, middleware=middleware, interrupt_on=..., **kwargs
    )
```
默认 `rubric=None`（不启用），仅显式配置时启用（避免延迟）。

---

## P4 — 清理

### C1. 硬编码外置

| 值 | 位置 | 新配置项 |
|---|---|---|
| `temperature=0.3` | agent.py:94 | `Settings.agents.deep.temperature` |
| `readonly_streak_threshold=10` | agent.py/coding.py/work_supervisor.py | `Settings.agents.readonly_streak_threshold` |
| `max_iterations=100` | execution.py:90 | `Settings.agents.max_iterations` |
| `dim=1024` | milvus_client.py:268, tei_client.py:241 | `Settings.embedding_dim`（单一源） |
| HNSW `M=16`/`efConstruction=200`/`ef=64` | milvus_client.py | `Settings.milvus_hnsw_*` |
| `max_iterations=50` | approval_flow.py:499 | 删除（循环已删） |

### C2. 更新过时注释

15+ 处 `interrupt_before=["tools"]` 注释 → `interrupt_on`（实际代码已用 deepagents 0.6+ `interrupt_on`）。

### C3. 类型强化

[schemas.py:32](file:///d:/java/agentprojects/agentx/backend/app/api/schemas.py#L32) `decision: str` → `Literal["approve","deny","once","session"]`；L127 `transport: str` → `Literal["stdio","sse","http"]`。

### C4. 重复消除

- `approval_flow.py:65,70` 两个相同 frozenset 合并。
- `workspace/api.py:42-65` 删除重复 `_is_critical_path`，复用 `SessionSandbox`。
- `workspace/api.py:218` 文件名列表复用 `templates.py` 的 `TEMPLATE_FILE_NAMES`。

### C5. 配置陈旧清理

- `config/subagents.py:65` `_ALL_TOOLS` 删除 `cli_execute`（已被 `execute` 替代）。
- `security/dangerous_tools.py` `FORBIDDEN_SUBAGENT_TOOLS` 加入 `execute` + `cli_execute`（声明式禁止）。

---

## 备选方案（已否决）

1. **保留 Team 手写编排**：违反 R1/R2/R13，且是已知技术债，不解决会持续累积。
2. **asyncio.run 用 run_coroutine_threadsafe**：复杂且有死锁风险，方案 C（直接同步调用底层）更简单。
3. **保留 `approval_flow.run_approval_loop` 仅测试用**：重复代码维护成本高，测试应测生产路径。

---

## 影响面汇总

| P 级 | 文件数 | 预计行数变化 | 风险 | 测试影响 |
|---|---|---|---|---|
| P0 | 5 | -100 | 零 | 无 |
| P1 Bug | 4 | +50（修复+测试） | 低 | 新增单测 |
| P1 重复 | 5 | -250 | 中 | 迁移 10 处测试 |
| P2 Team | 4 | -300（净减） | 高 | Team 测试全跑 |
| P3 合规 | 4 | -50 | 中 | 新增集成测试 |
| P4 清理 | 10+ | ±0 | 低 | 无 |
| **合计** | ~30 | **-650** | — | — |
