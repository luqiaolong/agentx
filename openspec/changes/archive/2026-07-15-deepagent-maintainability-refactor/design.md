## Context

`backend/app/deepagent` is the project adapter around DeepAgents 0.6.12. It is used by Work,
Coding, Team, built-in subagents, custom subagents, CLI, and evaluation paths. The package currently
mixes four concerns:

1. graph construction and DeepAgents middleware configuration;
2. project/MCP tool assembly and approval classification;
3. LangGraph interrupt/resume mechanics and AgentX approval policy;
4. LangGraph stream translation into AgentX SSE and observation records.

The package has strong regression coverage, but much of it mocks the implementation shape rather
than exercising a compiled DeepAgents graph. GitNexus reports `create_agent` as CRITICAL impact and
`run_agent_with_approval`, `make_deep_tools`, and `compute_runtime_dangerous` as HIGH impact. The
design therefore preserves the recently established public API and migrates one boundary at a time.

Constraints:

- DeepAgents `create_deep_agent` remains the P0 framework entry; this change does not replace its
  HITL, filesystem, memory, skills, todo, or subagent middleware.
- SessionSandbox remains the dynamic per-thread host path authorization layer.
- Existing SSE events and frontend consumers cannot change as part of this refactor.
- No new dependency is introduced.
- Existing user worktree changes outside the OpenSpec change are not part of this work.

## Goals / Non-Goals

**Goals:**

- make tool visibility, built-in exclusions, `interrupt_on`, and runtime approval classification
  derive from one assembled object;
- fix confirmed approval, streaming, context isolation, loop guard, and shell fallback defects;
- reduce `run_agent_with_approval` and `stream_agent_events` to thin orchestration surfaces whose
  internal state is explicit and directly testable;
- preserve public imports, call signatures, checkpointer behavior, and SSE payloads;
- make every implementation phase independently testable and reversible.

**Non-Goals:**

- removing the Team `deep` role or merging `run_deep_path` into Coding Expert;
- changing frontend source taxonomy or SSE schema;
- replacing SessionSandbox with DeepAgents static `permissions=`;
- upgrading DeepAgents or LangGraph;
- redesigning Team orchestration, memory/profile extraction, or MCP manager internals;
- rotating credentials found outside `backend/app/deepagent`; that requires a separate security change.

## Decisions

### Decision 1: Assemble an immutable `AgentToolset` before graph creation

Add an internal immutable description in `tool_assembly.py` (or a focused adjacent module):

```python
@dataclass(frozen=True)
class AgentToolset:
    tools: tuple[BaseTool, ...]
    excluded_builtin_tools: frozenset[str]
    approval_required_tools: frozenset[str]

    @property
    def interrupt_on(self) -> dict[str, bool]:
        return {name: True for name in self.approval_required_tools}
```

`assemble_agent_toolset()` loads project tools and MCP tools, applies `tools_enabled`, unions
subagent restrictions with disabled built-ins, and computes approval-required names once. The same
object feeds `create_agent(interrupt_on=...)` and `run_agent_with_approval(runtime_dangerous=...)`.

The canonical dangerous-tool formula remains owned by `app.security.dangerous_tools`.
`app.deepagent.tool_assembly.compute_runtime_dangerous` remains as a compatibility wrapper but
delegates to the security implementation.

Alternatives considered:

- Keep the current post-build runtime set: rejected because an MCP tool absent from the graph's
  `HumanInTheLoopMiddleware.interrupt_on` can execute before the runner can ask for approval.
- Move all tool construction into `factory.py`: rejected because graph construction and tool/provider
  discovery change for different reasons and have different tests.

### Decision 2: Preserve `create_agent` and add an optional interrupt configuration

`create_agent` remains the stable facade over `create_deep_agent`. It accepts an optional explicit
`interrupt_on`/approval-required input from `AgentToolset`; callers without a toolset retain the
current default derived from `DANGEROUS_TOOLS`. Effective `excluded_tools` is always the union of
caller exclusions and disabled DeepAgents built-ins.

This keeps existing subagent and evaluation callers compatible while allowing Work, Coding, and
legacy Deep to use the corrected one-pass assembly.

Alternative considered: change `create_agent` to accept only `AgentToolset`. Rejected because
GitNexus identifies 18 upstream symbols and the public API was deliberately stabilized in commit
`c523a9d`.

### Decision 3: Bind execution context through tokens and `finally`

Extend `context.py` with `bind_agent_context(thread_id, parent_thread_id)` implemented as a context
manager that stores and resets both ContextVar tokens. Agent scenario generators enter this context
around graph creation and streaming. The approval runner enters `bind_trace(trace_id)` with a real
`with` statement rather than calling the contextmanager function without entering it.

The outermost execution scope owns a final `sandbox.clear_temp(thread_id)`, so cancellation and
generator close receive the same cleanup as successful resume. Persistent session/full-trust grants
are not removed by `clear_temp`.

Alternative considered: reset variables manually at each return. Rejected because the approval
runner has many early returns and future branches would easily omit cleanup.

### Decision 4: Share explicit `StreamRunState` across initial and resume streams

Replace independent closure state in each `stream_agent_events` invocation with a shared dataclass:

```python
@dataclass
class StreamRunState:
    seen_message_keys: set[str] = field(default_factory=set)
    last_todos: tuple[TodoSnapshot, ...] = ()
    processed_message_count: int = 0
    observation_sequence: int = 0
```

The message key includes message type, message ID when present, `ToolMessage.tool_call_id`, AI tool
call IDs, and a deterministic content digest. Per-AI-response flags such as messages-mode presence,
token rollback, and ThinkFilter remain short-lived mapper state and reset after each complete
AIMessage.

`run_agent_with_approval` constructs one `StreamRunState` and passes it to every initial/resume stream.
The existing `seen_signatures` argument remains accepted during migration and is adapted into the new
state for test compatibility.

Alternatives considered:

- Query the observation database for the latest sequence on every event: rejected due unnecessary IO
  and because stream state already has run scope.
- Deduplicate only by processed message count: rejected because LangGraph test doubles and some resume
  streams can emit non-cumulative state snapshots.

### Decision 5: Split streaming into driver and stateful event mapper

`streaming.py` keeps the public `stream_agent_events` driver and abort handling. A focused internal
mapper owns conversion of `messages`, `values`, and `custom` chunks into SSE events. Content
normalization and message key creation become pure helpers.

Observation recording receives `StreamRunState`, increments its sequence before append, logs append
failure at diagnostic level, and never blocks SSE output.

Alternative considered: create one function per SSE type while retaining nonlocal closure state.
Rejected because it reduces line length but leaves lifecycle and resume behavior implicit.

### Decision 6: Separate LangGraph HITL mechanics from approval policy

Keep `run_agent_with_approval` as the public async-generator facade, but move responsibilities into:

- `hitl.py`: state inspection, pending-call extraction, aligned `Command(resume=...)` construction,
  approve/reject consumption, and message-count progress checks;
- `approval_session.py`: run-scoped dependencies and loop state, pause/abort checks, permission request,
  dangerous-tool and directory-extension decisions, repeat/stall termination, and event forwarding;
- `approval_runner.py`: dependency normalization, context/cleanup boundary, and public delegation.

Security policy remains in `app.security.approval.flow`. Functions consumed by `deepagent` receive
public names within that module; they are not re-exported from `approval.__init__` because the current
config import cycle constraint remains valid.

For a mixed `request_permission` batch, decisions stay positionally aligned with all pending calls:
permission calls may be approved, while sibling dangerous calls are rejected/deferred with a message
requiring the model to retry after authorization. This prevents an unpresented write from executing.

Alternatives considered:

- Move the whole runner into `app.security`: rejected because LangGraph checkpoint and stream resume
  mechanics are runtime orchestration, not security policy.
- Keep a single class in the existing 882-line file: rejected because file ownership and dependency
  direction would remain unclear.

### Decision 7: Model loop termination explicitly

Readonly streak detection walks trailing completed ReAct rounds, skipping the AIMessage that initiated
each ToolMessage and stopping when a round contains a non-readonly/unknown tool. Tests use realistic
alternating messages rather than consecutive ToolMessages.

The approval loop records whether it exited because the graph completed, terminated, or remained
interrupted. Reaching `max_iterations` emits an error only in the last case, eliminating the final
iteration false positive.

### Decision 8: Preserve shell backend boundaries in fallback

Sandbox-off subprocess fallback uses an explicitly validated `cwd` when provided, otherwise
`self.cwd`; it reuses the backend sanitized environment, timeout, and output limit. Byte/character
limit semantics are kept consistent with the DeepAgents `ExecuteResponse` contract and covered by
tests. This decision does not redesign the broader environment-variable policy.

### Decision 9: Preserve the public facade and migrate internals incrementally

All 12 current `app.deepagent.__all__` symbols remain importable. Existing helper names may delegate
to new internal components. No compatibility alias is added for a newly invented public API; the new
types are internal until their stability is proven.

The expected internal structure is:

```text
backend/app/deepagent/
  approval_runner.py      # public thin facade
  approval_session.py     # approval state machine
  hitl.py                 # LangGraph interrupt/resume mechanics
  streaming.py            # public astream driver
  stream_events.py        # stateful SSE mapper + StreamRunState
  tool_assembly.py        # project/MCP tools + AgentToolset
  factory.py              # create_deep_agent configuration
  context.py              # bounded context binding
```

## Risks / Trade-offs

- **Compiled graph tests may expose framework-version-specific middleware details** -> Assert model-visible
  behavior and interrupt configuration through supported graph APIs where possible; isolate unavoidable
  0.6.12 inspection helpers in test utilities.
- **Changing mixed-call decisions can make the model retry one extra turn** -> Prefer one safe retry over
  executing an unpresented dangerous call; add prompt/tool result text that makes the retry deterministic.
- **Shared stream state can retain memory for long tool loops** -> Store compact deterministic keys and todo
  snapshots only; clear state when the public runner completes.
- **Context cleanup around async generators is easy to bypass** -> Put binding and cleanup in the outermost
  generator `try/finally` and test `aclose()` explicitly.
- **Approval decomposition can alter exception ordering** -> Add characterization tests before extraction and
  move one branch at a time without changing event order.
- **Public facade imports may reintroduce import cycles** -> Preserve current lazy imports for heavy runtime
  dependencies and validate both `import app.deepagent` and direct submodule imports.
- **GitNexus FTS repair is unavailable in the installed CLI** -> Use exact symbol context/impact before edits;
  do not force-reindex while user-modified guidance files are present.

## Migration Plan

1. Add failing characterization tests for tool/HITL parity, built-in settings, realistic loop streaks,
   mixed permission calls, stream exact-once behavior, observation sequence, context cleanup, final
   iteration completion, and shell cwd.
2. Introduce `AgentToolset` and optional factory interrupt input; migrate legacy Deep, Work, and Coding
   tool assembly while retaining public helper wrappers.
3. Add bounded execution context and outer temporary-grant cleanup; migrate scenario entry points.
4. Introduce `StreamRunState` and the event mapper; keep SSE output order and payloads unchanged.
5. Extract HITL helpers and approval session branch-by-branch under existing tests.
6. Apply loop/shell cleanup and strengthen type annotations/documentation.
7. Run focused tests after every phase, then full unit tests, Ruff, OpenSpec validation, and
   `gitnexus_detect_changes` before merge.

No database or API migration is required. Each phase is independently revertible. Rollback restores
the previous facade implementation while leaving public imports and persisted LangGraph checkpoints
unchanged.

## Open Questions

No unresolved decision blocks implementation. Removal of the legacy Team `deep` role, SSE source
taxonomy cleanup, and credential rotation are intentionally tracked as separate changes rather than
being inferred into this refactor.
