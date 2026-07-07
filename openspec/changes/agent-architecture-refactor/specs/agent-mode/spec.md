# Spec: agent-mode

## ADDED Requirements

### Requirement: Agent mode uses scenario-based enum
The system SHALL replace the legacy `"agent" / "agent_team"` enum with a scenario-based enum: `"work" / "coding" / "coding_team"`. Each scenario binds a unique primary agent (top-level or expert), and a scenario MAY optionally provide a team mode (`<scenario>_team`).

#### Scenario: work mode as default
- **WHEN** a chat request is sent without specifying `agent_mode`
- **THEN** the system SHALL default to `"work"` mode
- **AND** the work Supervisor (全能 agent) SHALL handle the request with full toolset and delegation capabilities

#### Scenario: coding mode invokes coding Expert directly
- **WHEN** a chat request specifies `"coding"` as `agent_mode`
- **THEN** the system SHALL bypass the Supervisor
- **AND** the coding Expert SHALL handle the request directly with code-specialized toolset

#### Scenario: coding_team mode invokes coding Agent Team
- **WHEN** a chat request specifies `"coding_team"` as `agent_mode`
- **THEN** the system SHALL invoke the coding Agent Team (scenario-level team)
- **AND** the team orchestrator SHALL coordinate multi-agent collaboration for code tasks

#### Scenario: Invalid agent_mode returns error
- **WHEN** a chat request specifies an invalid `agent_mode` value (including legacy `"agent"` / `"agent_team"` which are removed)
- **THEN** the system SHALL return a 400 error with a clear message listing valid values
- **AND** it SHALL NOT silently fall back to any mode (no backward compatibility mapping)

#### Scenario: Future scenario extension
- **WHEN** a new scenario (e.g., `research`) is added in the future
- **THEN** the system SHALL support adding `"research"` and optionally `"research_team"` to the enum
- **AND** the extension SHALL NOT require modifying the core dispatch logic (only adding new scenario handlers)

## ADDED Requirements

### Requirement: Frontend mode selector groups by scenario
The system SHALL display the mode selector grouped by scenario: `[Work] | [Coding Agent] [Coding Team]`. The AgentTeam button is a scenario sub-mode, not a top-level mode.

#### Scenario: Mode selector displays scenario groups
- **WHEN** the user views the chat interface
- **THEN** the mode selector SHALL display grouped buttons: a `Work` group with one button, and a `Coding` group with `Coding Agent` and `Coding Team` buttons
- **AND** the currently active mode SHALL be visually highlighted

#### Scenario: Coding Team button is conditionally hidden
- **WHEN** `coding_team_enabled` is set to false in settings
- **THEN** the `Coding Team` button SHALL be hidden from the Coding group
- **AND** only `[Work] | [Coding Agent]` SHALL be visible

#### Scenario: Mode selection is persisted
- **WHEN** a user selects a mode
- **THEN** the selection SHALL be persisted across sessions (localStorage)
- **AND** the next application launch SHALL restore the last selected mode

#### Scenario: Mode indicator in chat interface
- **WHEN** a user is in a non-default mode (`coding` or `coding_team`)
- **THEN** the chat interface SHALL display a mode indicator showing the current mode
- **AND** the indicator SHALL be dismissible but reappear on new sessions

#### Scenario: Legacy mode values are rejected by frontend
- **WHEN** the frontend receives a persisted legacy mode value (`"agent"` or `"agent_team"`) from old storage
- **THEN** the frontend SHALL reset to `"work"` mode
- **AND** it SHALL update the persisted value to `"work"`
