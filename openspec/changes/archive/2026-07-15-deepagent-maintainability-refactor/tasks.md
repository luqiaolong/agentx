## 1. Baseline and impact controls

- [ ] 1.1 Record the pre-existing dirty files and ensure implementation commits never include unrelated `AGENTS.md`, `claude.md`, or renderer changes
- [ ] 1.2 Confirm the installed DeepAgents version and inspect its supported `create_deep_agent`, `interrupt_on`, `excluded_tools`, and HITL resume signatures before coding
- [ ] 1.3 Run GitNexus upstream impact for `create_agent`, `run_agent_with_approval`, `make_deep_tools`, `compute_runtime_dangerous`, `stream_agent_events`, and `SafeLocalShellBackend.execute`; record CRITICAL/HIGH callers and affected flows
- [ ] 1.4 Run the focused DeepAgent unit-test baseline and preserve the test count/output as pre-change evidence
- [ ] 1.5 Add a public facade contract test that imports every current `app.deepagent.__all__` symbol

## 2. Characterization and failing regression tests

- [ ] 2.1 Add a compiled-graph/tool-surface test proving an untrusted MCP tool is present in build-time `interrupt_on` before it can execute
- [ ] 2.2 Add tests proving disabled DeepAgents built-in fs tools are merged into effective `excluded_tools`, including union with `FORBIDDEN_SUBAGENT_TOOLS`
- [ ] 2.3 Add a settings test requiring an explicit default key for `request_permission`
- [ ] 2.4 Add a real alternating `AIMessage(tool_calls) -> ToolMessage` test for read-only streak counting and a non-readonly reset case
- [ ] 2.5 Add stream tests for two equal-content ToolMessages with different call IDs, unchanged todo replay, custom event passthrough, and resume exact-once behavior
- [ ] 2.6 Add an observation test proving sequence numbers remain monotonic and pre-resume rows survive a resume stream
- [ ] 2.7 Add context tests for concurrent thread/parent isolation, normal restoration, exception restoration, cancellation, and async-generator `aclose()`
- [ ] 2.8 Add an approval test for a mixed `request_permission + write_file` batch proving the write is not implicitly approved
- [ ] 2.9 Add approval tests proving authorization failure cannot produce a successful permission result and temporary grants clear after resume failure
- [ ] 2.10 Add boundary tests proving completion on the last allowed iteration does not emit a limit error while a still-interrupted run does
- [ ] 2.11 Add shell fallback tests asserting backend workspace `cwd`, sanitized environment, timeout, and output truncation are preserved

## 3. Single-source agent toolset

- [ ] 3.1 Add the internal immutable `AgentToolset` model with explicit tools, excluded built-ins, approval-required names, and derived `interrupt_on`
- [ ] 3.2 Implement asynchronous toolset assembly that loads project tools and MCP tools once, applies `tools_enabled`, and degrades to project tools when MCP loading fails
- [ ] 3.3 Make `app.security.dangerous_tools.compute_runtime_dangerous` the canonical formula and convert the deepagent helper into a compatibility delegate
- [ ] 3.4 Add `request_permission` to the explicit default tool configuration and update settings tests
- [ ] 3.5 Extend `create_agent` with a backward-compatible optional interrupt configuration and merge caller exclusions with disabled built-in tools
- [ ] 3.6 Update `build_deep_agent` to pass the assembled interrupt configuration into `create_deep_agent`
- [ ] 3.7 Migrate `run_deep_path`, `run_coding_expert`, and `run_work_supervisor` to consume one `AgentToolset` for both graph creation and approval execution
- [ ] 3.8 Verify built-in, custom, and declarative subagents retain forbidden-tool filtering and do not gain MCP or write capabilities

## 4. Bounded execution context

- [ ] 4.1 Implement `bind_agent_context(thread_id, parent_thread_id)` in `backend/app/deepagent/context.py` using ContextVar tokens and guaranteed reset
- [ ] 4.2 Enter the bounded context in legacy Deep, Work, Coding, and Coding Team execution generators without changing their public signatures
- [ ] 4.3 Replace the no-op `bind_trace(trace_id)` call with an entered trace context covering stream and observation work
- [ ] 4.4 Move once-only sandbox cleanup to an outer runner `finally` that executes on completion, error, cancellation, pause, abort, and generator close
- [ ] 4.5 Verify persistent session/full-trust grants are unaffected by temporary-grant cleanup

## 5. Resume-safe stream state and event mapping

- [ ] 5.1 Add internal `StreamRunState` with seen message keys, last todo snapshot, processed-message count, and observation sequence
- [ ] 5.2 Replace process-random `hash(content)` signatures with deterministic message keys containing message ID, `ToolMessage.tool_call_id`, AI tool-call IDs, type, and content digest
- [ ] 5.3 Extract content normalization and message-key generation into pure typed helpers with direct unit tests
- [ ] 5.4 Extract stateful `messages`/`values`/`custom` conversion into an internal event mapper while keeping `stream_agent_events` as the public driver
- [ ] 5.5 Reset messages-mode, ThinkFilter, token rollback, and reasoning state after each complete AIMessage
- [ ] 5.6 Make observation recording increment the shared sequence, preserve SSE on storage failure, and emit diagnostic logging
- [ ] 5.7 Update `run_agent_with_approval` to create one `StreamRunState` and reuse it across initial and all resume streams
- [ ] 5.8 Retain a compatibility adapter for custom test stream functions and the existing `seen_signatures` argument during migration

## 6. HITL and approval state-machine decomposition

- [ ] 6.1 Create `backend/app/deepagent/hitl.py` for interrupted-state detection, pending-call extraction, aligned decision construction, interrupt consumption, and message progress counts
- [ ] 6.2 Give externally consumed helpers in `app.security.approval.flow` public names within that module while preserving the documented no-re-export import-cycle constraint
- [ ] 6.3 Create an internal `ApprovalSession` model containing run dependencies, stream state, repeat history, stall counters, and terminal status
- [ ] 6.4 Move initial stream and common resume/error recovery into reusable session methods without changing SSE ordering
- [ ] 6.5 Move pause, abort, and full-trust branches into focused session methods and preserve current checkpoint consumption semantics
- [ ] 6.6 Move `request_permission` handling into a focused method that approves only permission calls and rejects/defers unpresented sibling dangerous calls with retry guidance
- [ ] 6.7 Move dangerous-tool and directory-extension handling into focused methods that preserve positional decisions for every pending call
- [ ] 6.8 Consolidate repeated reject-and-consume and resume-error blocks so all branches inject valid tool outcomes and clear residual interrupts
- [ ] 6.9 Model loop exit as completed, terminal, or still-interrupted and emit the max-iteration error only for the still-interrupted state
- [ ] 6.10 Reduce `approval_runner.py` to dependency normalization, bounded context/cleanup, and delegation to `ApprovalSession` while retaining `run_agent_with_approval` signature

## 7. Middleware and shell correctness

- [ ] 7.1 Update `ReadonlyLoopGuardMiddleware` to count trailing completed read-only ReAct rounds across alternating AIMessage and ToolMessage entries
- [ ] 7.2 Preserve threshold-disable behavior and force `tool_choice="none"` only when the corrected streak reaches the configured limit
- [ ] 7.3 Make sandbox-off subprocess fallback default to the backend `cwd` and reuse backend env, timeout, and output-limit semantics
- [ ] 7.4 Keep risk classification and SessionSandbox authorization behavior unchanged and rerun all shell/backend security tests

## 8. Compatibility and code cleanup

- [ ] 8.1 Keep all 12 existing `app.deepagent.__all__` exports and update docstrings to describe the new internal ownership boundaries
- [ ] 8.2 Replace raw `list` and avoidable `Any` annotations in touched code with existing DeepAgents/LangChain protocols or local internal type aliases
- [ ] 8.3 Remove obsolete duplicate helpers, private cross-package imports, dead comments, and stale references only after all callers use the new components
- [ ] 8.4 Confirm legacy Team `deep` routing and current SSE source behavior remain unchanged because their migration is outside this change
- [ ] 8.5 Review the final diff for unnecessary abstractions and delete compatibility code that does not protect an existing public caller or test seam

## 9. Verification and completion

- [ ] 9.1 Run focused DeepAgent, approval, streaming, sandbox, Work, Coding, and Team unit tests after each implementation phase
- [ ] 9.2 Run `uv run ruff check backend/` and fix all findings without unrelated formatting churn
- [ ] 9.3 Run `uv run python -m compileall backend/app/deepagent` plus import smoke for `app.deepagent`, Work, Coding, and Team modules
- [ ] 9.4 Run `uv run pytest tests/python/unit -m "not integration" -q` and require a green result apart from documented pre-existing xfails
- [ ] 9.5 Run the runtime-permission integration test that uses no myserver dependency and verify mixed-call approval behavior
- [ ] 9.6 Run `openspec validate deepagent-maintainability-refactor --strict` and resolve every proposal/spec/design/tasks inconsistency
- [ ] 9.7 Run `gitnexus_detect_changes(scope="all")`, review all affected processes, and confirm no unexpected frontend or API contract impact
- [ ] 9.8 Record tested and not-tested evidence in the Lore commit trailers for each atomic implementation commit

---

# Ralph 执行追踪 — deepagent-maintainability-refactor

> 以下为 ralph 编排器维护的执行追踪层（OpenSpec spec tasks 在上方）。`TASKS.md`（本文件，Windows 大小写不敏感与 `tasks.md` 同文件）是 ralph 后续所有 Phase 的唯一权威输入。

## 预期修改 / 新增文件

### 核心重构（`backend/app/deepagent/`）
- [ ] `backend/app/deepagent/tool_assembly.py` — 新增 `AgentToolset` immutable 模型 + 单次装配
- [ ] `backend/app/deepagent/factory.py` — 接受可选 interrupt 配置；合并 caller 排除 + 禁用内置
- [ ] `backend/app/deepagent/context.py` — `bind_agent_context(thread_id, parent_thread_id)` token-based
- [ ] `backend/app/deepagent/streaming.py` — 保留 public driver，拆出 stateful mapper
- [ ] `backend/app/deepagent/stream_events.py` — **新增** `StreamRunState` + SSE mapper + 纯函数 helpers
- [ ] `backend/app/deepagent/approval_runner.py` — 缩减为 facade（依赖归一化 + 上下文/清理 + 委派）
- [ ] `backend/app/deepagent/approval_session.py` — **新增** `ApprovalSession` 状态机
- [ ] `backend/app/deepagent/hitl.py` — **新增** LangGraph interrupt/resume 机制
- [ ] `backend/app/deepagent/middleware.py` — `ReadonlyLoopGuardMiddleware` 按真实 ReAct 序列计数
- [ ] `backend/app/deepagent/safe_shell_backend.py` — sandbox-off fallback 用 backend cwd + 复用 env/timeout/limit
- [ ] `backend/app/deepagent/authorized_backend.py` — 类型注解清理（如被触及）
- [ ] `backend/app/deepagent/agent.py` — 入口签名保留，内部装配调整
- [ ] `backend/app/deepagent/__init__.py` — 保留 12 个 `__all__` 符号；docstring 更新

### 安全模块（`backend/app/security/`）
- [ ] `backend/app/security/dangerous_tools.py` — `compute_runtime_dangerous` 作为 canonical 公式
- [ ] `backend/app/security/approval/flow.py` — 外部消费 helper 暴露 public 名字（保留 no-re-export 约束）

### 场景调用方（仅内部装配调整，不改入口签名）
- [ ] `backend/app/scenarios/work/agent.py`
- [ ] `backend/app/scenarios/coding/agent.py`
- [ ] `backend/app/scenarios/coding_team/agent.py`
- [ ] 旧 deep 路径（如仍存在）

### 设置 / 配置
- [ ] settings 模块 — `request_permission` 加显式 default key

### 测试（先失败回归 → 实现后通过）
- [ ] `tests/python/unit/deepagent/test_toolset_assembly.py` — 新增
- [ ] `tests/python/unit/deepagent/test_bounded_context.py` — 新增
- [ ] `tests/python/unit/deepagent/test_stream_run_state.py` — 新增
- [ ] `tests/python/unit/deepagent/test_approval_session.py` — 新增
- [ ] `tests/python/unit/deepagent/test_hitl.py` — 新增
- [ ] `tests/python/unit/deepagent/test_readonly_loop_guard.py` — 增强（alternating messages）
- [ ] `tests/python/unit/deepagent/test_shell_fallback.py` — 新增
- [ ] `tests/python/unit/deepagent/test_facade_contract.py` — 新增（12 个 `__all__` 导入）

## 规模判定

- **涉及文件数**: 20+（核心 deepagent 10 + security 2 + scenarios 3-4 + 测试 8 新增 + settings）
- **涉及模块数**: 4+（deepagent / security / scenarios / middleware；触及 sandbox/shell backend）
- **规模**: **L（大改）** — 全流程，无跳过

## 执行约束

- **入口签名保留**：所有 `app.deepagent.__all__` 12 个符号 + scenario runner 公开签名不变
- **SSE 契约不变**：`token` / `reasoning` / `tool_call` / `tool_result` / `todo_update` / `approval_request` / `paused` / `error` / `done` 字段保持
- **DeepAgents 0.6.12 不升级**：`create_deep_agent` / `HumanInTheLoopMiddleware` / 内置 fs/memory/skills 能力保留
- **SessionSandbox 授权模型不变**
- **不新增依赖**
- **隔离 dirty 文件**：AGENTS.md / claude.md / AssistantUIThread.tsx 不进实现 commits
- **TDD 顺序**：Section 2 失败回归测试先行 → Section 3-8 实现 → 测试转绿
- **迁移分阶段**：每个 phase 独立可逆，每 phase 后跑 focused 测试

## 依赖顺序（subagent 调度参考）

1. **可并行**：Section 1（baseline）+ Section 2（失败测试骨架）
2. **Section 3 → 4**：toolset 装配是 context 绑定的前提
3. **Section 5 独立**：stream state 可与 Section 6 并行（不同文件）
4. **Section 6 依赖 3+4+5**：approval session 复用 toolset + context + stream state
5. **Section 7 独立**：middleware + shell 后置
6. **Section 8 收尾**：所有 caller 迁移后清理
7. **Section 9 验证**：最后
