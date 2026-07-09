# Tasks: 后端代码质量优化

> 按优先级 P0→P4 分批，每个任务可独立提交。每个任务标注验证命令。

---

## P0 — 死代码与契约违规（零风险）

### [x] T-P0-1: 删除 `deep/recovery.py` 整文件
- 删除 `backend/app/deep/recovery.py`
- 检查 `backend/app/deep/__init__.py` 是否有 re-export，有则同步清理
- 验证：`grep -r "from app.deep.recovery" backend/ tests/` 返回空；`uv run ruff check backend/app/deep/`

### [x] T-P0-2: 删除 `resumed` SSE 事件
- `backend/app/deep/execution.py:159` 删除 `yield make_sse_event("resumed", {})`
- `backend/app/security/approval_flow.py:538` 删除 `yield make_sse_event("resumed", {})`
- 验证：`grep -rn "resumed" backend/app/deep/ backend/app/security/` 返回空

### [x] T-P0-3: 删除 `deep/agent.py` 死导入
- 删除 `backend/app/deep/agent.py` L19-20, 23, 34-38 的 6 个 `as _xxx` 别名导入
- 保留 L18 `run_agent_with_approval`
- 验证：`uv run ruff check backend/app/deep/agent.py` 无 F401

### [x] T-P0-4: 删除 `subagents/base.py` 死代码
- 删除 L47-49 占位赋值（`_make_fs_tools = None` 等）
- 删除 L99-124 `make_cli_tools` 函数定义
- 从 L25 `__all__` 移除 `"make_cli_tools"`
- 删除 L435 `_make_cli_tools = make_cli_tools` 别名
- 验证：`grep -n "make_cli_tools" backend/app/subagents/base.py` 返回空；`uv run ruff check`

---

## P1 — Bug 修复

### [x] T-P1-1: 修复 `milvus_client._on_skip` 闭包 bug
- 文件：`backend/app/vectorstore/milvus_client.py` L332-345
- 修改：`meta` 初始化为 `{}`，移入 `if` 块内 `break` 前；仅用 `t is text` 身份匹配
- 新增单测：`tests/python/unit/test_milvus_client.py::test_ingest_empty_texts` / `test_ingest_no_match` / `test_ingest_duplicate_texts`
- 验证：`uv run pytest tests/python/unit/test_milvus_client.py -v`

### [x] T-P1-2: 修复 `mcp/client.py` tool_count
- 文件：`backend/app/mcp/client.py` L168-184
- 修改：新增 `self._server_tools: dict[str, list[BaseTool]]` 缓存，`list_servers` 按 server 名取工具数
- 新增单测：`tests/python/unit/test_mcp_client.py::test_list_servers_per_server_tool_count`
- 验证：`uv run pytest tests/python/unit/test_mcp_client.py -v`

### [x] T-P1-3: 修复 `mcp/client.py` stdio 子进程泄漏
- 文件：`backend/app/mcp/client.py` L225-238 `_close_locked`
- 修改：遍历调用各 server 的 close hook（参照 `test_server` 的 `client.session(name)` 模式）
- 验证：集成测试或手动验证进程数

### [x] T-P1-4: 修复 `asyncio.run()` 在 LangChain 适配器中崩溃
- 文件：`backend/app/embedding/tei_client.py` L225,231
- 文件：`backend/app/vectorstore/milvus_client.py` L685,707
- 修改：同步方法直接调用底层同步实现（`_do_embed` 用同步 `httpx.post`；milvus 用同步 `pymilvus`）
- 新增单测：`test_sync_embed_in_async_context` / `test_sync_similarity_search_in_async_context`
- 验证：`uv run pytest tests/python/unit/test_tei_client.py tests/python/unit/test_milvus_client.py -v`

### [x] T-P1-5: `sandbox/store.py` 异步化
- 文件：`backend/app/sandbox/store.py`
- 修改：所有方法改 `async def`，内部 `await asyncio.to_thread(self._sync_method, ...)`
- 更新 `SessionSandbox` 调用处加 `await`
- 验证：`uv run pytest tests/python/unit/test_sandbox*.py -v`

---

## P1 — 重复消除

### [x] T-P1-6: 合并 `rag_agent.py` + `web_agent.py`
- 文件：`backend/app/subagents/base.py`
- 新增：`build_builtin_subagent(name, thread_id, checkpointer)` + `run_builtin_subagent(name, thread_id, message, history, checkpointer)`
- `rag_agent.py`/`web_agent.py` 改为薄 re-export 或删除（调用方 `team/orchestrator.py` + `supervisor/work_supervisor.py` 改用 `run_builtin_subagent("rag", ...)`）
- 验证：`uv run pytest tests/python/unit/test_subagents*.py -v`；事件流 source 标识一致

### [x] T-P1-7: 删除 `custom_agent.py` 重复工具
- 文件：`backend/app/subagents/custom_agent.py` L57-128
- 修改：`_make_custom_tools` 改为调用 `make_fs_tools()`/`make_rag_tools()`/`make_web_tools()` + 按名过滤
- 验证：`uv run pytest tests/python/unit/test_custom_agent*.py -v`；函数行数 ≤30

### [x] T-P1-8: 合并审批循环
- 删除 `backend/app/security/approval_flow.py::run_approval_loop` 及 `_inject_tool_error_messages_default`
- 迁移 `tests/python/unit/test_security_approval_flow.py` 10 处调用 → `run_agent_with_approval`
- 删除 `backend/app/agents/expert/coding.py:183-192` 本地 `_is_interrupted`
- 测试改 patch `app.deep.execution._is_interrupted`
- 验证：`uv run pytest tests/python/unit/test_security_approval_flow.py tests/python/unit/test_coding_expert*.py -v`

---

## P2 — Team 路径迁移到 LangGraph

### [x] T-P2-1: `run_team_path` 重写为 LangGraph `StateGraph`
- 文件：`backend/app/team/orchestrator.py`
- 新增：`TeamState(TypedDict)` + reducer 函数
- 重写 `run_team_path` 用 `StateGraph` + `add_node("plan"/"execute"/"aggregate")` + `add_conditional_edges`
- 删除 `asyncio.Queue`/`Semaphore`/`create_task` 手写并发
- 验证：`uv run pytest tests/python/unit/test_team_path.py tests/python/unit/test_interrupt_stream.py -v`

### [x] T-P2-2: `scheduler._run_subtask` 拆为节点
- 文件：`backend/app/team/scheduler.py`
- 7 分支拆为独立节点函数：`deep_node`/`code_node`/`builtin_node`/`team_role_node`/`custom_node`/`default_node`
- 用 `add_conditional_edges` 按 `task.agent` 分发
- 事件路由提取为 `_route_subtask_events` 公共助手（消除 3 处重复）
- 验证：`_run_subtask` ≤80 行；`uv run pytest tests/python/unit/test_team*.py -v`

### [x] T-P2-3: `Blackboard` 转 `TeamState(TypedDict)`
- 文件：`backend/app/team/blackboard.py`
- 修改：`Blackboard` 改 `TypedDict`，`findings`/`errors` 用 `Annotated[dict, reducer]`
- 并行子任务节点写 `findings` 由 LangGraph 自动合并
- 验证：`uv run pytest tests/python/unit/test_team*.py -v`

### [x] T-P2-4: 删除 monkeypatch 兼容导入
- 文件：`backend/app/team/orchestrator.py` L32-41
- 删除 5 个 `# noqa: F401` 测试兼容导入
- `run_team_path` 接受 `subtask_runners: dict | None = None` 参数，测试注入 mock
- 验证：`uv run pytest tests/python/unit/test_team*.py -v`

---

## P3 — 框架合规

### [x] T-P3-1: `trim_messages` 替代手写截断
- 文件：`backend/app/router/graph.py` L291-293
- 修改：`from langchain_core.messages import trim_messages`，用 `trim_messages(history, token_counter=model, max_tokens=settings.context_max_tokens, strategy="last")`
- 验证：`uv run pytest tests/python/unit/test_router*.py -v`

### [x] T-P3-2: `_append_messages_to_checkpointer` 用正确 API
- 文件：`backend/app/router/graph.py` L437-521
- 调研：deepagents graph 是否自动写 checkpoint（若是则删除本函数）
- 若需手动写：用 `BaseCheckpointSaver.aput(config, checkpoint, metadata, new_versions)` + `checkpoint_ns=""`
- 删除 sync 分支的 `ThreadPoolExecutor` + `_aio.run` hack
- 验证：`uv run pytest tests/python/unit/test_router*.py -v`

### [x] T-P3-3: `rag_retrieve.py` 改用 LangChain 适配器
- 文件：`backend/app/tools/rag_retrieve.py`
- 修改：用 `LangChainTeiEmbeddings` + `LangChainMilvusVectorStore.as_retriever()`
- 删除直连 `TEIClient`/`MilvusClient` 调用
- 验证：`uv run pytest tests/python/unit/test_rag_retrieve*.py -v`

### [x] T-P3-4: 接入 `RubricMiddleware` 到生产
- 文件：`backend/app/deep/harness.py`
- 修改：`create_agent` 接受 `rubric: Rubric | None = None` 参数
- `rubric` 非空时 `middleware.append(RubricMiddleware(rubric=rubric, grader_llm=model))`
- 默认 `rubric=None`（不启用）
- 验证：`uv run pytest tests/python/unit/test_harness*.py -v`

---

## P4 — 清理

### [x] T-P4-1: 硬编码外置
- 文件：`backend/app/config/settings.py` + 多个调用处
- 新增 `Settings` 字段：`agents.deep.temperature` / `agents.readonly_streak_threshold` / `agents.max_iterations` / `embedding_dim` / `milvus_hnsw_*`
- 修改调用处读取 settings
- 验证：`uv run pytest tests/python/unit -m "not integration"`

### [x] T-P4-2: 更新过时注释
- 全 backend/app/ 搜索 `interrupt_before`
- 改为 `interrupt_on`（实际代码已用）
- 验证：`grep -rn "interrupt_before" backend/app/ --include="*.py"` 仅历史注释

### [x] T-P4-3: 类型强化
- 文件：`backend/app/api/schemas.py`
- `ApproveRequest.decision` 改 `Literal["approve","deny","once","session"]`
- `McpServerTestRequest.transport` 改 `Literal["stdio","sse","http"]`
- 验证：`uv run ruff check backend/app/api/schemas.py`

### [x] T-P4-4: 重复消除
- `backend/app/security/approval_flow.py` L65,70 合并两个 frozenset
- `backend/app/workspace/api.py` L42-65 删除 `_is_critical_path`，复用 `SessionSandbox`
- `backend/app/workspace/api.py` L218 复用 `templates.py` 的 `TEMPLATE_FILE_NAMES`
- 验证：`uv run ruff check backend/app/security/ backend/app/workspace/`

### [x] T-P4-5: 配置陈旧清理
- `backend/app/config/subagents.py:65` `_ALL_TOOLS` 删除 `cli_execute`
- `backend/app/security/dangerous_tools.py` `FORBIDDEN_SUBAGENT_TOOLS` 加入 `execute` + `cli_execute`
- 验证：`uv run pytest tests/python/unit/test_subagents*.py tests/python/unit/test_security*.py -v`

---

## 全局验证

### [x] T-GLOBAL-1: 全量单元测试
- 命令：`uv run pytest tests/python/unit -m "not integration"`
- 预期：全部通过（6 个已知失败保持不变或减少）

### [x] T-GLOBAL-2: 全量风格检查
- 命令：`uv run ruff check backend/`
- 预期：无错误

### [x] T-GLOBAL-3: 集成测试（可选，需 myserver）
- 命令：`uv run pytest tests/python/integration -m requires_myserver`
- 预期：无新增失败
