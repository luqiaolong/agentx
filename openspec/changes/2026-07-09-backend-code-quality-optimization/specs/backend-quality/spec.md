# Spec: 后端代码质量优化

> 需求规格（When/Then 形式）。每个需求映射到 proposal.md 的 P 级目标。

---

## P0 — 死代码与契约违规

### REQ-P0-1: 删除 `deep/recovery.py` 死代码

**When** 后端代码库中搜索 `from app.deep.recovery import`
**Then** 返回空结果

**When** `deep/recovery.py` 文件存在性检查
**Then** 文件不存在

**When** `uv run ruff check backend/app/deep/`
**Then** 无未使用导入错误

### REQ-P0-2: 删除 `resumed` SSE 事件

**When** `grep -r "resumed" backend/app/deep/execution.py backend/app/security/approval_flow.py`
**Then** 返回空

**When** `grep -r "resumed" frontend/renderer/`
**Then** 返回空（前端本就无处理）

**When** 审批恢复后 SSE 流发送事件序列
**Then** 序列为 `[..., "paused", "token", ...]`，不包含 `resumed`

### REQ-P0-3: 删除 `deep/agent.py` 死导入

**When** `deep/agent.py` 顶部导入区
**Then** 不存在 `_get_pending_tool_calls as _get_pending_tool_calls` / `_is_interrupted as _is_interrupted` / `_stream_agent_events as _stream_agent_events` / `_await_approval as _await_approval` / `_handle_directory_extension as _handle_directory_extension` / `_make_approval_event as _make_approval_event` 6 个别名导入

**When** `uv run ruff check backend/app/deep/agent.py`
**Then** 无 F401 未使用导入

### REQ-P0-4: 删除 `subagents/base.py` 死代码

**When** `base.py` 中搜索 `make_cli_tools`
**Then** 无定义、无 `__all__` 导出、无别名赋值

**When** `base.py` 中搜索 `_make_fs_tools = None` / `_make_rag_tools = None` / `_make_web_tools = None`
**Then** 无占位赋值

---

## P1 — Bug 修复

### REQ-P1-1: 修复 `milvus_client._on_skip` 闭包 bug

**When** `ingest` 调用时 `texts` 为空列表或无匹配文本
**Then** 不抛 `NameError`，`meta` 为 `{}`

**When** `ingest` 调用时 `texts` 含重复文本
**Then** 仅按对象身份（`is`）匹配，不误用相等（`==`）

**When** 单测 `test_ingest_empty_texts` / `test_ingest_no_match` / `test_ingest_duplicate_texts`
**Then** 通过

### REQ-P1-2: 修复 `mcp/client.py` tool_count

**When** MCP 配置 2 个 server，分别有 3 和 5 个工具
**Then** `list_servers()` 返回的每个 server 的 `tool_count` 分别为 3 和 5，而非 8

**When** 单测 `test_list_servers_per_server_tool_count`
**Then** 通过

### REQ-P1-3: 修复 `mcp/client.py` stdio 子进程泄漏

**When** `McpManager.close()` 调用后
**Then** 所有 stdio MCP server 子进程已终止（无残留进程）

**When** 单测 `test_close_terminates_stdio_subprocesses`
**Then** 通过（或集成测试验证进程数）

### REQ-P1-4: 修复 `asyncio.run()` 在 LangChain 适配器同步方法中崩溃

**When** `LangChainTeiEmbeddings.embed_documents(texts)` 在 FastAPI async 上下文调用
**Then** 不抛 `RuntimeError: cannot be called from a running event loop`，返回嵌入向量

**When** `LangChainMilvusVectorStore.similarity_search(query)` 在 FastAPI async 上下文调用
**Then** 不抛 `RuntimeError`，返回文档列表

**When** 单测 `test_sync_embed_in_async_context` / `test_sync_similarity_search_in_async_context`
**Then** 通过

### REQ-P1-5: `sandbox/store.py` 异步化

**When** `SessionSandbox` 在 async 上下文调用 `store.authorize_dir(...)`
**Then** 不阻塞事件循环（DB 操作在 `asyncio.to_thread` 中执行）

**When** `SandboxStore` 方法签名
**Then** 全部为 `async def`，返回 awaitable

---

## P1 — 重复消除

### REQ-P1-6: 合并 `rag_agent.py` + `web_agent.py`

**When** `base.py` 中存在 `build_builtin_subagent(name, thread_id, checkpointer)` 和 `run_builtin_subagent(name, thread_id, message, history, checkpointer)`
**Then** 两个函数支持 `name ∈ {"rag", "web"}`

**When** `rag_agent.py` + `web_agent.py` 总行数
**Then** 较修改前减少 ≥150 行（或两文件改为薄 re-export/删除）

**When** `run_rag_agent` / `run_web_agent` 调用（来自 `team/orchestrator.py` 和 `supervisor/work_supervisor.py`）
**Then** 行为不变（事件流、source 标识一致）

### REQ-P1-7: 删除 `custom_agent.py` 重复工具

**When** `custom_agent.py` 中搜索 `@tool` 装饰器定义的 `read_file` / `list_dir` / `glob_files` / `grep_files` / `rag_retrieve` / `web_search`
**Then** 无重复定义（应调用 `make_*_tools()` + 过滤）

**When** `_make_custom_tools` 函数行数
**Then** ≤30 行（修改前 106 行）

**When** 自定义子代理调用 `tools=["read_file","web_search"]`
**Then** 返回的工具集与修改前功能一致

### REQ-P1-8: 合并审批循环

**When** `grep -r "run_approval_loop" backend/app/security/approval_flow.py`
**Then** 无该函数定义

**When** `tests/python/unit/test_security_approval_flow.py` 中搜索 `run_approval_loop`
**Then** 10 处调用已迁移为 `run_agent_with_approval`

**When** `agents/expert/coding.py` 中搜索本地 `_is_interrupted` 定义
**Then** 无（测试改 patch `app.deep.execution._is_interrupted`）

---

## P2 — Team 路径迁移到 LangGraph

### REQ-P2-1: `run_team_path` 用 LangGraph `StateGraph`

**When** `team/orchestrator.py` 中搜索 `StateGraph` / `add_node` / `add_edge` / `add_conditional_edges`
**Then** 存在 LangGraph 图构建代码

**When** `run_team_path` 中搜索 `asyncio.Queue` / `asyncio.Semaphore` / `asyncio.create_task` / `while done_count < total`
**Then** 无手写并发原语

**When** Team 路径执行（`agent_mode="coding_team"`）
**Then** 行为与修改前一致（team_plan → 并行子任务 → team_done 事件序列）

**When** `tests/python/unit/test_team_path.py` + `test_interrupt_stream.py`
**Then** 全部通过

### REQ-P2-2: `scheduler._run_subtask` 拆为节点

**When** `scheduler.py` 中 `_run_subtask` 函数行数
**Then** ≤80 行（修改前 248 行）

**When** `scheduler.py` 中搜索 `add_conditional_edges` 或等价的节点分发
**Then** 7 个分支（deep/code/rag+web/team-roles/custom/default）拆为独立节点函数

**When** 事件路由逻辑（token/tool_result/error/passthrough 处理）
**Then** 提取为单个 `_route_subtask_events` 公共助手，无三处重复

### REQ-P2-3: `Blackboard` 转 `TeamState(TypedDict)`

**When** `team/blackboard.py` 中 `Blackboard` 定义
**Then** 为 `TypedDict`，含 `findings`/`errors` 字段，带 `Annotated[dict, reducer]` 注解

**When** 并行子任务节点写 `findings`
**Then** LangGraph 自动通过 reducer 合并，无手写聚合

### REQ-P2-4: 删除 monkeypatch 兼容导入

**When** `orchestrator.py` 中搜索 `# noqa: F401` 仅为测试 monkeypatch 的导入
**Then** 无（测试改用依赖注入或 patch 源模块）

**When** `run_team_path` 函数签名
**Then** 接受可选 `subtask_runners: dict[str, Callable] | None = None` 参数

---

## P3 — 框架合规

### REQ-P3-1: `trim_messages` 替代手写截断

**When** `router/graph.py` 中搜索 `history[-max_msgs:]`
**Then** 无

**When** `graph.py` 中搜索 `trim_messages`
**Then** 存在调用，使用 `token_counter=model` + `max_tokens=settings.context_max_tokens`

### REQ-P3-2: `_append_messages_to_checkpointer` 用正确 API

**When** `graph.py` 中搜索 `StateGraph(MessagesState)` 用于写 checkpoint 的 hack
**Then** 无

**When** checkpoint 写入
**Then** 使用 `BaseCheckpointSaver.aput(config, checkpoint, metadata, new_versions)` + 正确 `checkpoint_ns=""`，或函数已删除（因 deepagents graph 自动写）

### REQ-P3-3: `rag_retrieve.py` 改用 LangChain 适配器

**When** `tools/rag_retrieve.py` 中搜索 `TEIClient` / `MilvusClient` 直连
**Then** 无（应通过 `LangChainTeiEmbeddings` / `LangChainMilvusVectorStore`）

**When** `rag_retrieve` 工具调用
**Then** 行为一致（返回检索文档）

### REQ-P3-4: 接入 `RubricMiddleware` 到生产

**When** `deep/harness.py::create_agent` 函数签名
**Then** 接受 `rubric: Rubric | None = None` 参数

**When** `create_agent` 调用时 `rubric=None`
**Then** 不接入 `RubricMiddleware`（行为不变，避免延迟）

**When** `create_agent` 调用时 `rubric` 显式传入
**Then** `middleware` 列表含 `RubricMiddleware(rubric=rubric, grader_llm=model)`

---

## P4 — 清理

### REQ-P4-1: 硬编码外置

**When** `Settings` 类定义
**Then** 含 `agents.deep.temperature` / `agents.readonly_streak_threshold` / `agents.max_iterations` / `embedding_dim` / `milvus_hnsw_m` / `milvus_hnsw_ef_construction` / `milvus_search_ef`

**When** `agent.py`/`coding.py`/`work_supervisor.py` 中搜索 `readonly_streak_threshold=10`
**Then** 改为读取 `settings.agents.readonly_streak_threshold`

### REQ-P4-2: 更新过时注释

**When** `grep -r "interrupt_before" backend/app/ --include="*.py"`
**Then** 仅在历史 ADR 或明确的"旧 API"注释中出现，不在当前逻辑描述中出现

### REQ-P4-3: 类型强化

**When** `api/schemas.py::ApproveRequest.decision`
**Then** 类型为 `Literal["approve","deny","once","session"]`

**When** `api/schemas.py::McpServerTestRequest.transport`
**Then** 类型为 `Literal["stdio","sse","http"]`

### REQ-P4-4: 重复消除

**When** `security/approval_flow.py` 中搜索 `_READ_ONLY_FS_TOOLS` 和 `_READONLY_TOOLS`
**Then** 合并为单个 frozenset

**When** `workspace/api.py` 中搜索 `_is_critical_path`
**Then** 无（复用 `SessionSandbox` 的 critical-path 检查）

### REQ-P4-5: 配置陈旧清理

**When** `config/subagents.py::_ALL_TOOLS` 中搜索 `cli_execute`
**Then** 无（已被 `execute` 替代）

**When** `security/dangerous_tools.py::FORBIDDEN_SUBAGENT_TOOLS`
**Then** 含 `execute` 和 `cli_execute`（声明式禁止）

---

## 全局验收

### REQ-GLOBAL-1: 单元测试全绿

**When** `uv run pytest tests/python/unit -m "not integration"`
**Then** 全部通过（无新增失败）

### REQ-GLOBAL-2: 风格检查通过

**When** `uv run ruff check backend/`
**Then** 无错误

### REQ-GLOBAL-3: 无回归

**When** 现有测试套件执行
**Then** 失败数不增加（修改前 6 个已知失败保持不变或减少）
