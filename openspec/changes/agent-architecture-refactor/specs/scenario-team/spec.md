# Spec: scenario-team

## ADDED Requirements

### Requirement: Agent Team is a scenario sub-mode, not a top-level mode
`AgentTeam` is a generic term (type) for scenario-level multi-agent teams. The system SHALL treat Agent Team as a scenario sub-mode rather than a top-level mode. Each scenario MAY optionally provide a team mode (`<scenario>_team`). The `coding_team` mode is the first (and currently only) implementation, bound to the `coding` scenario. Future scenarios may add their own teams (e.g., `research_team`). The work scenario does NOT have a team mode.

#### Scenario: coding_team mode as scenario entry
- **WHEN** a user selects `coding_team` mode
- **THEN** the system SHALL invoke the coding Agent Team orchestrator directly
- **AND** the team SHALL coordinate multi-agent collaboration for code tasks
- **AND** the team's internal roles (`frontend_dev`, `backend_dev`, etc.) SHALL remain unchanged

#### Scenario: work mode does NOT have a team variant
- **WHEN** the system defines available modes
- **THEN** there SHALL NOT be a `work_team` mode
- **AND** the Supervisor SHALL NOT have any `invoke_agent_team` tool to trigger team collaboration
- **AND** users seeking team collaboration SHALL switch to `coding_team` mode

#### Scenario: coding Expert does NOT trigger team internally
- **WHEN** the coding Expert encounters a task that might benefit from team collaboration
- **THEN** the Expert SHALL NOT invoke any `invoke_agent_team` tool (such tool SHALL NOT exist)
- **AND** the Expert SHALL handle the task itself or return a suggestion to switch to `coding_team` mode

## ADDED Requirements

### Requirement: Scenario team reuses existing team framework
The system SHALL build the coding Agent Team by reusing the existing `app/team/` framework (orchestrator, scheduler, planner, aggregator, blackboard), with adjustments to remove dependencies on the legacy `agent_team` top-level mode and the removed `code` subagent.

#### Scenario: coding team orchestrator entry
- **WHEN** `coding_team` mode is invoked
- **THEN** the system SHALL call `run_coding_team()` which delegates to the existing team orchestrator
- **AND** the orchestrator SHALL plan, schedule, and aggregate results as before

#### Scenario: team scheduler maps code references to coding Expert
- **WHEN** the team scheduler encounters a reference to the removed `code` subagent in role definitions
- **THEN** it SHALL map such references to the coding Expert
- **AND** the team's `backend_dev` / `frontend_dev` roles SHALL use the coding Expert's toolset

#### Scenario: team orchestrator does not fall back to legacy chat path
- **WHEN** the team orchestrator decides to degrade a simple task
- **THEN** it SHALL NOT fall back to the legacy CHAT path (which is removed)
- **AND** it SHALL either handle the task within the team or suggest the user switch to `coding` or `work` mode

## ADDED Requirements

### Requirement: Scenario team is conditionally enabled
The system SHALL support a `coding_team_enabled` setting to conditionally show or hide the `coding_team` mode in the frontend.

#### Scenario: coding_team enabled by default
- **WHEN** the system is configured with default settings
- **THEN** `coding_team_enabled` SHALL default to true
- **AND** the `Coding Team` button SHALL be visible in the frontend mode selector

#### Scenario: coding_team disabled hides button
- **WHEN** `coding_team_enabled` is set to false in settings
- **THEN** the `Coding Team` button SHALL be hidden from the frontend mode selector
- **AND** requests with `agent_mode="coding_team"` SHALL return a 400 error indicating the mode is disabled
