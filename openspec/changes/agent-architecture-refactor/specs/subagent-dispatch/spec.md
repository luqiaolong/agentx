# Spec: subagent-dispatch

## ADDED Requirements

### Requirement: Subagent is a generic term with two categories
`Subagent` is a generic term (type) for lightweight tool-wrapping agents invoked by the Supervisor or Experts. The system SHALL classify Subagents into two categories: (1) **built-in subagents** — pre-packaged agents shipped with the system (currently `rag` and `web`); (2) **custom subagents** — user-defined agents configured via `AGENTX_SUBAGENTS_CONFIG`. Both categories are invoked via the same `delegate_to_subagent` tool and share the same dispatch mechanism.

#### Scenario: Built-in subagents
- **WHEN** the system initializes the subagent registry
- **THEN** built-in subagents SHALL include `rag` (knowledge base retrieval) and `web` (web search)
- **AND** built-in subagent keys SHALL be reserved and SHALL NOT conflict with custom subagent keys

#### Scenario: Custom subagents
- **WHEN** a user configures custom subagents via `AGENTX_SUBAGENTS_CONFIG`
- **THEN** the custom subagents SHALL be registered alongside built-in subagents
- **AND** custom subagent keys SHALL NOT duplicate built-in keys (`rag`, `web`)
- **AND** custom subagents SHALL be invokable via the same `delegate_to_subagent` tool

#### Scenario: Subagent invocation is uniform
- **WHEN** the Supervisor or an Expert invokes a subagent
- **THEN** it SHALL use the `delegate_to_subagent` tool with `agent_name` (str) and `task` (str)
- **AND** the dispatch SHALL be agnostic to whether the subagent is built-in or custom

## MODIFIED Requirements

### Requirement: Built-in subagents are reduced to rag and web
The system SHALL remove the `code` built-in subagent entirely from the subagent layer. The built-in subagents SHALL only include `rag` and `web`. The `code` subagent file (`backend/app/subagents/code_agent.py`) SHALL be deleted (no deprecation, no backward compatibility).

#### Scenario: Subagent dispatch no longer routes to code
- **WHEN** the subagent dispatch logic evaluates available built-in subagents
- **THEN** the system SHALL only consider `rag` and `web` as built-in subagent targets
- **AND** `code` SHALL NOT be a valid subagent routing target

#### Scenario: Keyword matching excludes code-related terms
- **WHEN** the keyword-based subagent selector processes a message containing "代码" or "文件"
- **THEN** it SHALL NOT match any `code` subagent (since it no longer exists)
- **AND** it SHALL return no match, letting the caller (Supervisor or coding Expert) handle the task autonomously

#### Scenario: LLM-based subagent selector excludes code
- **WHEN** the LLM-based subagent selector (`llm_select_subagent`) evaluates a message
- **THEN** the available options SHALL only include `rag` and `web`
- **AND** any response suggesting `code` SHALL be treated as invalid

#### Scenario: Custom subagents remain unaffected
- **WHEN** a user has configured custom subagents
- **THEN** they SHALL continue to work as before
- **AND** the custom subagent key SHALL NOT conflict with removed built-in keys

## MODIFIED Requirements

### Requirement: Subagent classification keywords are cleaned
The system SHALL remove code-related keywords from the subagent classification logic. The legacy Router four-path classification (CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam) is deleted entirely, so subagent classification is only used when the Supervisor or coding Expert explicitly delegates via `delegate_to_subagent` tool.

#### Scenario: Code-related messages handled by Supervisor
- **WHEN** a message contains code-related keywords like "读文件", "搜索", "代码" in work mode
- **THEN** the Supervisor SHALL handle the task autonomously (either execute itself or delegate to coding Expert)
- **AND** the legacy SINGLE_TOOL path SHALL NOT exist

#### Scenario: Dangerous tool keywords handled by Supervisor approval
- **WHEN** a message contains dangerous tool keywords like "写文件", "编辑", "删除" in work mode
- **THEN** the Supervisor SHALL execute the write tool directly with `interrupt_before` approval
- **AND** the legacy DEEP_TASK path SHALL NOT exist

#### Scenario: Subagent dispatch only via explicit delegation
- **WHEN** the Supervisor or coding Expert decides to invoke a subagent
- **THEN** it SHALL invoke the `delegate_to_subagent` tool explicitly
- **AND** the subagent SHALL be selected from `rag` or `web` (or custom subagents)
- **AND** there SHALL be no automatic subagent routing based on message classification
