# Spec: expert-agent

## ADDED Requirements

### Requirement: Expert architecture supports scenario-bound domain specialists
`Expert` is a generic term (type) for scenario-bound domain specialists. The system SHALL support a layer of Experts, each bound to a specific scenario (non-work), sitting between the Supervisor and Subagents. An Expert specializes in a specific domain with full tool access (including write operations with approval). The `coding` Expert is the first (and currently only) implementation, bound to the `coding` scenario. Future scenarios will add their own Experts (e.g., `research Expert`, `trading Expert`). Each non-work scenario binds exactly one Expert.

#### Scenario: Expert has complete domain toolset
- **WHEN** a coding Expert is initialized
- **THEN** it SHALL have access to all code-related tools including filesystem read/write, CLI, Git, web search, and RAG retrieve
- **AND** write operations SHALL trigger the `interrupt_before` approval flow

#### Scenario: Expert runs independently as scenario entry
- **WHEN** a user selects `coding` mode
- **THEN** the system SHALL bypass the Supervisor and directly invoke the coding Expert
- **AND** the Expert SHALL handle the full conversation with its own system prompt and toolset

#### Scenario: Expert can be invoked from Supervisor
- **WHEN** the Supervisor (work mode) receives a coding-related task and decides to delegate
- **THEN** it SHALL delegate to the coding Expert via the `delegate_to_expert` tool
- **AND** the Expert's result SHALL be returned to the Supervisor for final synthesis

#### Scenario: Expert does NOT trigger AgentTeam
- **WHEN** the coding Expert encounters a complex full-stack development task
- **THEN** the Expert SHALL NOT invoke any `invoke_agent_team` tool (such tool SHALL NOT exist in Expert)
- **AND** the user SHALL switch to `coding_team` mode manually for team collaboration

#### Scenario: Expert does NOT delegate to other Experts
- **WHEN** the coding Expert receives a task that might benefit from another Expert (e.g., a future research Expert)
- **THEN** the Expert SHALL NOT have a `delegate_to_expert` tool available
- **AND** it SHALL handle the task within its own domain or return a limitation notice

## ADDED Requirements

### Requirement: coding Expert replaces the legacy code subagent
The system SHALL provide a `coding` Expert that replaces the removed `code` subagent, with enhanced capabilities including file write/edit and shell execution. The legacy `code` subagent is deleted entirely (no backward compatibility).

#### Scenario: coding Expert handles code analysis
- **WHEN** a user asks "分析这个项目的代码结构" in coding mode
- **THEN** the coding Expert SHALL use read_file, list_dir, glob, grep tools to analyze the codebase
- **AND** it SHALL NOT trigger approval for read-only operations

#### Scenario: coding Expert handles file modification
- **WHEN** a user asks "帮我修改这个文件" in coding mode
- **THEN** the coding Expert SHALL use write_file or edit_file tools
- **AND** the system SHALL yield an `approval_request` event before executing the write operation
- **AND** it SHALL only proceed after user approval

#### Scenario: coding Expert handles shell commands
- **WHEN** a user asks "运行 npm install" in coding mode
- **THEN** the coding Expert SHALL use cli_execute tool
- **AND** the system SHALL yield an `approval_request` event before executing the shell command
- **AND** the command SHALL be validated against the CLI tool blacklist

#### Scenario: coding Expert can invoke subagents
- **WHEN** the coding Expert needs to retrieve knowledge base information during a coding task
- **THEN** it SHALL internally invoke the `rag` subagent via `delegate_to_subagent` tool
- **AND** the result SHALL be incorporated into the Expert's reasoning
- **AND** the subagent's SSE events SHALL carry `source="rag"` (the subagent's own identifier)

## ADDED Requirements

### Requirement: Expert uses build_deep_agent framework
The system SHALL build the coding Expert using the existing `build_deep_agent` framework, replacing the system prompt with a coding-specialized prompt and extending the toolset.

#### Scenario: Expert reuses DeepAgent approval and checkpoint
- **WHEN** the coding Expert is built
- **THEN** it SHALL reuse the `interrupt_before` approval flow, checkpoint management, and pause/resume mechanism from `build_deep_agent`
- **AND** the system prompt SHALL be the coding-specialized prompt from `config/prompts/agent.py`

#### Scenario: Expert delegates to subagents via tool
- **WHEN** the coding Expert needs to delegate to a subagent
- **THEN** it SHALL use the `delegate_to_subagent` tool with parameters `agent_name` (str) and `task` (str)
- **AND** available subagents SHALL be `rag` and `web` (code is removed)
