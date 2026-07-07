# Spec: input-at-mention

## ADDED Requirements

### Requirement: Input box supports @mention syntax only in work mode
The system SHALL support `@<agent-name>` syntax in the chat input box ONLY in `work` mode to force route the current message to a specific Expert or Subagent. In `coding` and `coding_team` modes, @mention SHALL be stripped and ignored.

#### Scenario: @mention parsing in work mode
- **WHEN** a user types "@coding 帮我review这段代码" in work mode
- **THEN** the system SHALL parse the message before sending to the backend
- **AND** it SHALL extract the agent name "coding" and the task "帮我review这段代码"
- **AND** it SHALL include the target agent in the request payload

#### Scenario: @mention overrides Supervisor routing
- **WHEN** a message contains a valid @mention in work mode
- **THEN** the system SHALL bypass the Supervisor's autonomous routing decision
- **AND** it SHALL directly route to the specified agent (Expert or Subagent)

#### Scenario: @mention in non-work mode is stripped
- **WHEN** a user sends a message with @mention in `coding` or `coding_team` mode
- **THEN** the system SHALL strip the @mention before processing
- **AND** it SHALL process the message normally in the current mode
- **AND** it SHALL log an info-level message about the stripped @mention

#### Scenario: @mention does NOT support team
- **WHEN** a user types "@team 任务" or "@coding_team 任务" in work mode
- **THEN** the system SHALL treat "team" or "coding_team" as an unknown agent name
- **AND** it SHALL strip the @mention and process the message normally through the Supervisor
- **AND** team collaboration SHALL only be accessible via mode switching

## ADDED Requirements

### Requirement: Frontend shows @mention autocomplete in work mode only
The system SHALL display an autocomplete dropdown when the user types "@" in the input box, but ONLY in work mode. In other modes, typing "@" SHALL NOT trigger autocomplete.

#### Scenario: @mention autocomplete in work mode
- **WHEN** a user types "@" in the input box in work mode
- **THEN** the system SHALL display an autocomplete dropdown with available agents
- **AND** the list SHALL include Experts (`coding`) and Subagents (built-in: `rag`, `web`; plus any custom subagents from configuration)
- **AND** the user SHALL be able to select an agent via keyboard or click

#### Scenario: No @mention autocomplete in non-work mode
- **WHEN** a user types "@" in the input box in `coding` or `coding_team` mode
- **THEN** the system SHALL NOT display any autocomplete dropdown
- **AND** the "@" character SHALL be treated as normal text

#### Scenario: @mention styling in input box
- **WHEN** a user types an @mention in the input box in work mode
- **THEN** the @mention text SHALL be visually styled (e.g., highlighted background)
- **AND** the styling SHALL distinguish between Experts (e.g., blue) and Subagents (e.g., green)

## ADDED Requirements

### Requirement: @mention agent list is dynamic
The system SHALL fetch the available @mention agent list from the backend, supporting future scenario extensions without frontend hardcoding.

#### Scenario: Backend returns available agents for @mention
- **WHEN** the frontend initializes in work mode
- **THEN** it SHALL fetch the available agent list from a backend endpoint (e.g., `GET /api/agents/mentionable`)
- **AND** the response SHALL include agent name, type (expert/subagent), and display label
- **AND** the frontend SHALL render the autocomplete based on this response

#### Scenario: @mention list reflects current configuration
- **WHEN** the backend configuration adds a new custom subagent
- **THEN** the @mention autocomplete list SHALL include the new subagent on the next fetch
- **AND** no frontend redeployment SHALL be required
