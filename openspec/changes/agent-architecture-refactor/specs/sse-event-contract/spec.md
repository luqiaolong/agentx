# Spec: sse-event-contract

## MODIFIED Requirements

### Requirement: SSE event source field supports scenario-based agent identifiers
The system SHALL extend the SSE event `source` field to support new agent identifiers introduced by the scenario-based four-layer architecture: `"work"` / `"coding"` / `"coding_team"` / `"rag"` / `"web"`.

#### Scenario: Supervisor events
- **WHEN** the Supervisor produces events (token, reasoning, tool_call, tool_result) directly
- **THEN** the `source` field SHALL be set to `"work"`
- **AND** the frontend SHALL render them as the main assistant's output

#### Scenario: coding Expert events
- **WHEN** the coding Expert produces events (token, reasoning, tool_call, tool_result)
- **THEN** the `source` field SHALL be set to `"coding"`
- **AND** the frontend SHALL render them with the coding expert's visual identity

#### Scenario: coding_team Agent Team events
- **WHEN** the coding Agent Team produces events (orchestrator, planner, role agents, aggregator)
- **THEN** the `source` field SHALL be set to `"coding_team"` for orchestrator-level events
- **AND** individual role agent events SHALL carry the role identifier (e.g., `"frontend_dev"`, `"backend_dev"`)
- **AND** the frontend SHALL render them within the team's execution block

#### Scenario: Subagent events retain own source
- **WHEN** a subagent (built-in: rag, web; or custom subagent) is invoked (either from Supervisor or from coding Expert)
- **THEN** the subagent's events SHALL carry `source` set to the subagent's own identifier (e.g., `"rag"`, `"web"`, or the custom subagent's key)
- **AND** the frontend SHALL render them as nested within the caller's execution block

#### Scenario: Legacy source values are removed
- **WHEN** the system produces events
- **THEN** legacy source values `"code"` and `"deep"` SHALL NOT be emitted (no backward compatibility)
- **AND** the frontend SHALL NOT handle these legacy values

## ADDED Requirements

### Requirement: Expert execution trace is observable
The system SHALL provide clear event boundaries for Expert execution within the SSE stream, enabling frontend rendering of expert execution traces.

#### Scenario: Expert execution starts
- **WHEN** the Supervisor delegates to an Expert
- **THEN** the system SHALL yield a `tool_call` event with `name="delegate_to_expert"` and `args.expert_name`
- **AND** the frontend SHALL render this as the start of an expert execution block

#### Scenario: Expert execution ends
- **WHEN** an Expert completes execution
- **THEN** the system SHALL yield a `tool_result` event with the expert's final result
- **AND** the frontend SHALL render this as the end of the expert execution block

#### Scenario: Expert internal subagent calls are nested
- **WHEN** an Expert internally calls a subagent (e.g., coding Expert calls rag)
- **THEN** the subagent's events SHALL be nested within the Expert's execution block
- **AND** the subagent events SHALL carry `source="rag"` (not "coding")
- **AND** the frontend SHALL render them with indentation or visual nesting

## ADDED Requirements

### Requirement: Scenario team execution trace is observable
The system SHALL provide clear event boundaries for Agent Team execution within the SSE stream, enabling frontend rendering of team collaboration traces.

#### Scenario: Team execution starts
- **WHEN** a `coding_team` mode request begins
- **THEN** the system SHALL yield a team-start event with `source="coding_team"`
- **AND** the frontend SHALL render this as the start of a team execution block

#### Scenario: Team role agent events
- **WHEN** individual role agents (frontend_dev, backend_dev) produce events
- **THEN** the events SHALL carry the role identifier as `source`
- **AND** the frontend SHALL render them as nested within the team execution block

#### Scenario: Team execution ends
- **WHEN** the team aggregator produces the final result
- **THEN** the system SHALL yield a team-end event with `source="coding_team"`
- **AND** the frontend SHALL render this as the end of the team execution block
