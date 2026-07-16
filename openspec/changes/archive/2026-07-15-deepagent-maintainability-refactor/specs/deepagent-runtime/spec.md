## ADDED Requirements

### Requirement: Agent tool surface and approval policy share one source
The system SHALL assemble each main DeepAgent run into one immutable runtime description containing
the explicit tools, excluded DeepAgents built-in tools, and the exact tool names configured in
`interrupt_on`. The approval runner MUST use the same approval-required tool names and MUST NOT
recompute a divergent set after the graph has been compiled.

#### Scenario: Untrusted MCP tool is interrupted before execution
- **WHEN** an enabled MCP server returns a tool marked `trusted=False`
- **THEN** the tool name is present in the graph's `interrupt_on` configuration
- **AND** the same tool name is classified as approval-required by the approval runner

#### Scenario: Trusted MCP tool remains automatic
- **WHEN** an enabled MCP server returns a tool marked `trusted=True` and no other policy marks it dangerous
- **THEN** the tool is exposed to the agent without an `interrupt_on` entry

#### Scenario: MCP loading failure degrades safely
- **WHEN** MCP tool loading raises an installation, connection, or configuration error
- **THEN** the runtime is built without MCP tools
- **AND** project tools and built-in tool exclusion rules remain intact

### Requirement: Built-in tool settings are enforced at the DeepAgents profile
The system SHALL apply `tools_enabled` to both project-defined tools and DeepAgents automatically
injected filesystem tools. Disabled built-in tools MUST be included in the effective
`excluded_tools` profile before `create_deep_agent` is called.

#### Scenario: Built-in write tool is disabled
- **WHEN** `tools_enabled.write_file` is `false`
- **THEN** `write_file` is absent from the model-visible tool surface
- **AND** `write_file` is absent from the effective approval-required set

#### Scenario: Subagent exclusions are combined with user settings
- **WHEN** a subagent is created with `FORBIDDEN_SUBAGENT_TOOLS` and the user disables an additional built-in read tool
- **THEN** the effective profile excludes the union of both sets
- **AND** no forbidden write, edit, delete, or execute tool becomes visible

#### Scenario: Permission request has an explicit setting
- **WHEN** the default tool settings are loaded
- **THEN** `request_permission` has an explicit enabled state instead of relying on an unknown-key fallback

### Requirement: Execution context is isolated and bounded
Every agent run SHALL bind thread ID, optional parent thread ID, and trace ID for the lifetime of
that run only. ContextVar tokens MUST be restored and temporary sandbox grants MUST be cleared on
normal completion, handled failure, cancellation, and asynchronous generator close.

#### Scenario: Concurrent threads do not share authorization context
- **WHEN** two agent runs execute concurrently with different thread and parent thread IDs
- **THEN** each backend authorization check observes only its own IDs

#### Scenario: Generator is closed early
- **WHEN** a caller closes the SSE generator before the agent completes
- **THEN** the previous ContextVar values are restored
- **AND** once-only sandbox authorizations for the run are cleared

#### Scenario: Trace binding is entered
- **WHEN** an active trace ID is propagated into the approval runner
- **THEN** nested stream and observation operations observe that trace ID for the duration of the run
- **AND** the prior trace context is restored afterward

### Requirement: HITL decisions preserve per-call approval semantics
The approval runner SHALL produce one LangGraph HITL decision for every pending tool call while
preserving the approval result of each individual call. Approving a permission request MUST NOT
implicitly approve a sibling dangerous tool call that was not presented for approval.

#### Scenario: Permission request and write call share one model message
- **WHEN** pending calls contain both `request_permission` and `write_file`
- **AND** the user approves the permission request
- **THEN** the permission request is resumed as approved
- **AND** the sibling write call is rejected or deferred with an explicit retry message
- **AND** the write call executes only after a subsequent approval check

#### Scenario: User rejects dangerous calls
- **WHEN** the user rejects one or more presented dangerous tool calls
- **THEN** all pending HITL interrupts receive aligned reject decisions
- **AND** the graph contains valid ToolMessage outcomes with no dangling tool call

#### Scenario: Temporary authorization fails
- **WHEN** sandbox authorization raises after the user approved `request_permission`
- **THEN** the permission tool outcome reports the authorization failure
- **AND** the agent MUST NOT receive a successful `路径已授权` result for that call

### Requirement: Streaming is exact-once across interrupt and resume
The system SHALL keep a shared stream-run state across initial execution and all resume streams.
It MUST emit each logical message and unchanged todo snapshot at most once per run, preserve distinct
parallel tool results, and assign monotonically increasing observation sequence numbers.

#### Scenario: Parallel tool results have identical content
- **WHEN** two ToolMessages have different `tool_call_id` values but equal content
- **THEN** two distinct `tool_result` SSE events are emitted

#### Scenario: Resume replays historical state
- **WHEN** LangGraph re-emits messages and todos already observed before an interrupt
- **THEN** no duplicate `token`, `tool_call`, `tool_result`, or unchanged `todo_update` event is emitted

#### Scenario: Observation sequence survives resume
- **WHEN** one trace produces events before and after a HITL resume
- **THEN** observation sequence numbers remain strictly increasing
- **AND** post-resume events do not replace pre-resume database rows

#### Scenario: Custom stream event is passed through
- **WHEN** a tool node writes a valid SSE dictionary through `stream_mode="custom"`
- **THEN** the event is forwarded unchanged and does not mutate message deduplication state

#### Scenario: Observation storage fails
- **WHEN** appending an observation event fails
- **THEN** the corresponding SSE event is still emitted
- **AND** the failure is available in diagnostic logging without terminating the agent run

### Requirement: Loop and termination guards match real graph behavior
Loop protection SHALL evaluate real ReAct message order and distinguish a completed run from a run
that is still interrupted at its configured approval iteration limit.

#### Scenario: Alternating read-only ReAct rounds reach the threshold
- **WHEN** the trailing history alternates AI tool-call messages and ToolMessages for read-only tools
- **AND** the configured read-only threshold is reached
- **THEN** the next model call is forced to return text without another tool call

#### Scenario: Non-read-only operation resets the streak
- **WHEN** a write, execute, unknown, or unnamed tool occurs after read-only rounds
- **THEN** the prior read-only streak no longer contributes to the stop threshold

#### Scenario: Run completes on the final allowed iteration
- **WHEN** the graph clears its interrupt during the final permitted approval iteration
- **THEN** the run completes without emitting `达到最大迭代上限`

#### Scenario: Run remains interrupted at the limit
- **WHEN** the final permitted iteration ends and the graph is still interrupted
- **THEN** the runner injects a terminal tool error and emits the iteration-limit error once

### Requirement: Shell fallback preserves backend execution boundaries
When sandbox-off fallback invokes a local subprocess, it SHALL preserve the backend working
directory, sanitized environment, timeout, and output limit. It MUST NOT silently execute from the
AgentX server process working directory.

#### Scenario: Sandbox-off retry uses workspace root
- **WHEN** a backend rooted at a workspace retries a sandbox-limited command through subprocess
- **THEN** the subprocess `cwd` equals the backend workspace unless an explicitly validated cwd was supplied

#### Scenario: Fallback output exceeds the configured limit
- **WHEN** fallback output exceeds `max_output_bytes`
- **THEN** the returned ExecuteResponse is truncated according to the backend limit
- **AND** its `truncated` flag is true

### Requirement: Public API and SSE contracts remain compatible
The refactor SHALL preserve all symbols currently exported by `app.deepagent.__all__`, their public
call signatures unless an optional backward-compatible parameter is added, and the existing SSE
event names and payload fields consumed by Work, Coding, Team, CLI, evaluation, and frontend paths.

#### Scenario: Public imports remain valid
- **WHEN** a caller imports any existing symbol from `app.deepagent`
- **THEN** the import succeeds and resolves to behavior compatible with the pre-refactor entry point

#### Scenario: Existing scenario runner consumes events
- **WHEN** Work or Coding executes through the refactored runtime
- **THEN** its SSE output remains consumable without frontend contract changes

